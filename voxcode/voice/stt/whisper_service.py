"""Speech-to-text with faster-whisper (loaded once, run off the event loop)."""

from __future__ import annotations

import io
import logging
import threading

import numpy as np

from voxcode.config.settings import settings

logger = logging.getLogger(__name__)


class WhisperSTTService:
    """Raises ImportError at construction if faster-whisper is not installed."""

    def __init__(self):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(
            settings.WHISPER_MODEL,
            device=settings.WHISPER_DEVICE,
            compute_type=settings.WHISPER_COMPUTE_TYPE,
        )
        # CTranslate2 models are not safe for concurrent transcribe() calls.
        self._lock = threading.Lock()

    def transcribe(self, audio: bytes | np.ndarray) -> str:
        """Transcribe encoded audio bytes (wav/webm/mp3...) or 16 kHz float32 PCM."""
        source = io.BytesIO(audio) if isinstance(audio, (bytes, bytearray)) else audio
        with self._lock:
            segments, _ = self.model.transcribe(
                source,
                beam_size=settings.WHISPER_BEAM_SIZE,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            return " ".join(segment.text.strip() for segment in segments).strip()
