"""Voice endpoints: transcription, push-to-talk, streaming, tone, speech."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from voxcode.api import services
from voxcode.voice.commands import parse_command
from voxcode.voice.recorder import RecorderError, recorder
from voxcode.voice.tts.tts_service import tts_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PARTIAL_INTERVAL_SECONDS = 1.5


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    emotion: str = "calm"
    play: bool = True  # play on the backend machine; False only synthesizes


class ToneAnalysisRequest(BaseModel):
    text: str


def _stt():
    stt = services.registry.get_stt()
    if stt is None:
        raise HTTPException(
            status_code=503,
            detail="Speech-to-text unavailable: install the voice extra (pip install -e '.[voice]')",
        )
    return stt


def _result(text: str) -> dict:
    emotion = services.analyze_emotion(text)
    return {
        "transcription": text,
        "command": parse_command(text) if text else None,
        "emotion": emotion.model_dump() if emotion else None,
    }


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)) -> dict:
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Audio file too large")
    stt = _stt()
    # Whisper is CPU-bound: keep it off the event loop.
    text = await run_in_threadpool(stt.transcribe, content)
    return {"filename": file.filename, **_result(text), "status": "success"}


@router.post("/record/start")
def start_recording() -> dict:
    _stt()  # fail fast before opening the microphone
    try:
        recorder.start()
    except RecorderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "recording"}


@router.post("/record/stop")
def stop_recording() -> dict:
    try:
        audio = recorder.stop()
    except RecorderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    text = _stt().transcribe(audio) if audio.size else ""
    return {**_result(text), "seconds": round(audio.size / 16_000, 2), "status": "success"}


@router.post("/analyze-tone")
def analyze_tone(payload: ToneAnalysisRequest) -> dict:
    emotion = services.emotion_analyzer.analyze(payload.text)
    return emotion.model_dump()


@router.post("/speak")
def speak_text(payload: TTSRequest) -> dict:
    if payload.play:
        tts_service.speak(payload.text, payload.emotion)
    return {"status": "ok", "engine": tts_service.engine, "spoken": payload.text}


@router.post("/synthesize")
def synthesize(payload: TTSRequest) -> Response:
    """Return speech as WAV so the client can play it."""
    audio = tts_service.synthesize_wav(payload.text, payload.emotion)
    if audio is None:
        raise HTTPException(status_code=503, detail=f"TTS engine '{tts_service.engine}' unavailable")
    return Response(content=audio, media_type="audio/wav")


@router.websocket("/stream")
async def websocket_voice_endpoint(websocket: WebSocket):
    """Streaming dictation.

    Send binary audio chunks (e.g. MediaRecorder webm/opus) as they are
    recorded; partial transcripts come back about every 1.5 s. Send the text
    frame `{"type": "end"}` to get the final transcript (plus any voice
    command), after which a new utterance can start.
    """
    await websocket.accept()
    stt = services.registry.get_stt()
    if stt is None:
        await websocket.send_json({"type": "error", "detail": "Speech-to-text unavailable"})
        await websocket.close()
        return

    await websocket.send_json({"type": "status", "message": "VoxCode voice stream connected."})
    buffer = bytearray()
    last_partial = time.monotonic()
    partial_task: asyncio.Task | None = None

    async def send_partial(snapshot: bytes):
        try:
            text = await run_in_threadpool(stt.transcribe, snapshot)
            await websocket.send_json({"type": "partial", "text": text})
        except Exception as exc:  # incomplete container data is normal mid-stream
            logger.debug("Partial transcription skipped: %s", exc)

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes"):
                buffer.extend(message["bytes"])
                if len(buffer) > MAX_UPLOAD_BYTES:
                    await websocket.send_json({"type": "error", "detail": "Utterance too long"})
                    buffer.clear()
                    continue
                due = time.monotonic() - last_partial >= PARTIAL_INTERVAL_SECONDS
                if due and (partial_task is None or partial_task.done()):
                    last_partial = time.monotonic()
                    partial_task = asyncio.create_task(send_partial(bytes(buffer)))
            elif message.get("text"):
                try:
                    data = json.loads(message["text"])
                except ValueError:
                    data = {}
                if data.get("type") == "end":
                    if partial_task:
                        await partial_task
                    text = await run_in_threadpool(stt.transcribe, bytes(buffer)) if buffer else ""
                    buffer.clear()
                    await websocket.send_json({"type": "final", **_result(text)})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("Voice WebSocket error: %s", exc)
        await websocket.close()
