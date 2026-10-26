import io
import logging
from typing import BinaryIO, Optional

logger = logging.getLogger(__name__)


class WhisperSTTService:
    """
    Speech-To-Text service using faster-whisper or fallbacks for local transcription.
    """

    def __init__(self, model_size: str = "base", device: str = "cpu", compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _get_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
                logger.info(f"Loading Whisper STT model '{self.model_size}' on {self.device}...")
                self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
            except Exception as e:
                logger.warning(f"Could not load faster-whisper model: {e}. Transcription will run in mock/fallback mode.")
                self._model = False
        return self._model

    def transcribe_audio_file(self, audio_file: BinaryIO) -> str:
        """
        Transcribes binary audio data (wav, mp3, webm, etc.).
        """
        model = self._get_model()
        if not model:
            return "Fallback: Whisper STT model not installed or failed to initialize."

        try:
            segments, info = model.transcribe(audio_file, beam_size=5)
            transcribed_text = " ".join([segment.text.strip() for segment in segments]).strip()
            logger.info(f"Detected speech ({info.language} with prob {info.language_probability:.2f}): {transcribed_text}")
            return transcribed_text
        except Exception as e:
            logger.error(f"Error during transcription: {e}")
            return ""

    def transcribe_bytes(self, audio_bytes: bytes) -> str:
        buffer = io.BytesIO(audio_bytes)
        return self.transcribe_audio_file(buffer)
