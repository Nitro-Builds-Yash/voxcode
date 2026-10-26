import logging
from fastapi import APIRouter, File, UploadFile, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from backend.voice.stt.whisper_service import WhisperSTTService
from backend.voice.tts.tts_service import TTSService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/voice",
    tags=["voice"],
)

stt_service = WhisperSTTService()
tts_service = TTSService()


class TTSRequest(BaseModel):
    text: str


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Accepts an audio file upload and returns the transcribed text.
    """
    try:
        content = await file.read()
        text = stt_service.transcribe_bytes(content)
        return {
            "filename": file.filename,
            "transcription": text,
            "status": "success",
        }
    except Exception as e:
        logger.error(f"Voice transcription failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/speak")
def speak_text(payload: TTSRequest):
    """
    Synthesizes and speaks text out loud on the host system.
    """
    tts_service.speak(payload.text)
    return {"status": "ok", "spoken": payload.text}


@router.websocket("/stream")
async def websocket_voice_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for continuous audio streaming or voice commands.
    """
    await websocket.accept()
    await websocket.send_json({"type": "status", "message": "Voice connection established"})
    try:
        while True:
            data = await websocket.receive_bytes()
            if data:
                text = stt_service.transcribe_bytes(data)
                await websocket.send_json({
                    "type": "transcription",
                    "text": text,
                })
    except WebSocketDisconnect:
        logger.info("Voice WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.close()
