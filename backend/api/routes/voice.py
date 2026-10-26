import logging
from fastapi import APIRouter, File, UploadFile, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from backend.voice.stt.whisper_service import WhisperSTTService
from backend.voice.tts.tts_service import TTSService
from backend.voice.emotion.emotion_analyzer import EmotionAnalyzer

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/voice",
    tags=["voice"],
)

stt_service = WhisperSTTService()
tts_service = TTSService()
emotion_analyzer = EmotionAnalyzer()


class TTSRequest(BaseModel):
    text: str
    emotion: str = "calm"
    intensity: float = 0.5


class ToneAnalysisRequest(BaseModel):
    text: str


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Accepts an audio file upload, returns transcribed text AND emotional tone analysis.
    """
    try:
        content = await file.read()
        text = stt_service.transcribe_bytes(content)
        emotion = emotion_analyzer.analyze(text) if text else None
        return {
            "filename": file.filename,
            "transcription": text,
            "emotion": emotion.model_dump() if emotion else None,
            "status": "success",
        }
    except Exception as e:
        logger.error(f"Voice transcription failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/analyze-tone")
def analyze_tone(payload: ToneAnalysisRequest):
    """
    Analyzes emotional state, sentiment, and JARVIS adaptation directives for any input text.
    """
    emotion = emotion_analyzer.analyze(payload.text)
    return emotion.model_dump()


@router.post("/speak")
def speak_text(payload: TTSRequest):
    """
    Synthesizes and speaks text out loud with emotion-tailored speech cadence.
    """
    tts_service.speak(payload.text, emotion=payload.emotion, intensity=payload.intensity)
    return {"status": "ok", "spoken": payload.text, "emotion": payload.emotion}


@router.websocket("/stream")
async def websocket_voice_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for continuous audio streaming or voice commands.
    """
    await websocket.accept()
    await websocket.send_json({"type": "status", "message": "J.A.R.V.I.S. Voice connection online, sir."})
    try:
        while True:
            data = await websocket.receive_bytes()
            if data:
                text = stt_service.transcribe_bytes(data)
                emotion = emotion_analyzer.analyze(text) if text else None
                await websocket.send_json({
                    "type": "transcription",
                    "text": text,
                    "emotion": emotion.model_dump() if emotion else None,
                })
    except WebSocketDisconnect:
        logger.info("Voice WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.close()
