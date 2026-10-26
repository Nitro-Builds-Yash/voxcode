import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class TTSService:
    """
    Local Text-To-Speech service for voice feedback.
    """

    def __init__(self, engine_name: str = "pyttsx3"):
        self.engine_name = engine_name
        self._lock = threading.Lock()

    def speak(self, text: str, async_mode: bool = True) -> None:
        """
        Synthesizes and speaks text to default audio output.
        """
        if not text:
            return

        def _run_speak():
            with self._lock:
                try:
                    import pyttsx3
                    engine = pyttsx3.init()
                    engine.say(text)
                    engine.runAndWait()
                except Exception as e:
                    logger.warning(f"TTS output failed or pyttsx3 is unavailable: {e}")

        if async_mode:
            thread = threading.Thread(target=_run_speak, daemon=True)
            thread.start()
        else:
            _run_speak()
