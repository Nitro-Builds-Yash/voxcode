"""Voice endpoints: transcription, push-to-talk, streaming, tone, speech."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from voxcode.api import services
from voxcode.voice.commands import parse_command
from voxcode.voice.listener import VoiceListener
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
    reply_id: str | None = None  # sentences of one reply; dropped after barge-in


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


def _result(text: str, audio=None) -> dict:
    emotion = services.analyze_emotion(text, audio)
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
    started = time.perf_counter()
    text = _stt().transcribe(audio) if audio.size else ""
    stt_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        **_result(text, audio if audio.size else None),
        "seconds": round(audio.size / 16_000, 2),
        "timings_ms": {"stt": stt_ms},
        "status": "success",
    }


@router.post("/analyze-tone")
def analyze_tone(payload: ToneAnalysisRequest) -> dict:
    emotion = services.emotion_analyzer.analyze(payload.text)
    return emotion.model_dump()


@router.post("/speak")
def speak_text(payload: TTSRequest) -> dict:
    if payload.play and not tts_service.available():
        # Used to answer "ok" and stay silent.
        raise HTTPException(
            status_code=503,
            detail=f"Text-to-speech engine '{tts_service.engine}' is not installed: pip install -e '.[voice]'",
        )
    if payload.play:
        tts_service.speak(payload.text, payload.emotion, payload.reply_id)
    return {"status": "ok", "engine": tts_service.engine, "spoken": payload.text}


@router.post("/stop-speaking")
def stop_speaking() -> dict:
    """Barge-in: stop the reply being spoken (and its queued sentences)."""
    tts_service.stop()
    return {"status": "stopped"}


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


_listener: VoiceListener | None = None


def _get_listener() -> VoiceListener:
    global _listener
    if _listener is None:
        stt = _stt()
        _listener = VoiceListener(stt.transcribe, _result)
    return _listener


@router.websocket("/listen")
async def websocket_listen(websocket: WebSocket):
    """Hands-free mode on the server's microphone.

    Send `{"type": "start"}` to begin listening and `{"type": "stop"}` to end.
    Events: listening, wake, speech_start, partial, final (with transcription,
    command, emotion incl. prosody, timings_ms), stopped, error.
    """
    await websocket.accept()
    try:
        listener = await run_in_threadpool(_get_listener)
    except HTTPException as exc:
        await websocket.send_json({"type": "error", "detail": exc.detail})
        await websocket.close()
        return
    events = listener.subscribe()

    async def pump():
        while True:
            event = await run_in_threadpool(_next_event, events)
            if event is not None:
                await websocket.send_json(event)

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            message = await websocket.receive_json()
            kind = message.get("type")
            if kind == "start":
                listener.start()
            elif kind == "stop":
                listener.stop()
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        pump_task.cancel()
        listener.unsubscribe(events)
        if not listener._subscribers:
            listener.stop()


def _next_event(events):
    try:
        return events.get(timeout=0.5)
    except Exception:
        return None


@router.post("/listen/start")
def listen_start() -> dict:
    _get_listener().start()
    return {"status": "listening"}


@router.post("/listen/stop")
def listen_stop() -> dict:
    if _listener is not None:
        _listener.stop()
    return {"status": "stopped"}


@router.get("/listen/events")
def listen_events() -> StreamingResponse:
    """Server-sent events from the hands-free listener (same events as the
    /voice/listen WebSocket), for clients without WebSocket support."""
    listener = _get_listener()
    events = listener.subscribe()

    def stream():
        try:
            yield ": connected\n\n"
            while True:
                event = _next_event(events)
                yield f"data: {json.dumps(event)}\n\n" if event is not None else ": keep-alive\n\n"
        finally:
            listener.unsubscribe(events)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
