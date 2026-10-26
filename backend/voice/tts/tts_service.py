import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class TTSService:
    """
    Local Text-To-Speech service for VoxCode voice feedback.
    Modulates speech rate, pitch, and voice cadence based on detected emotional context.
    """

    def __init__(self, engine_name: str = "pyttsx3"):
        self.engine_name = engine_name
        self._lock = threading.Lock()

    def speak(
        self,
        text: str,
        emotion: str = "calm",
        intensity: float = 0.5,
        async_mode: bool = True,
    ) -> None:
        """
        Synthesizes and speaks text to audio output with emotion-based pacing.
        """
        if not text:
            return

        def _run_speak():
            with self._lock:
                try:
                    import pyttsx3
                    engine = pyttsx3.init()

                    # Speech rate modulation based on user emotional state
                    rate = 175
                    if emotion == "urgent":
                        rate = 205
                    elif emotion == "frustrated":
                        rate = 155
                    elif emotion == "fatigued":
                        rate = 160
                    elif emotion == "curious":
                        rate = 175

                    engine.setProperty("rate", rate)

                    # Prefer a clear natural voice if present
                    voices = engine.getProperty("voices")
                    for v in voices:
                        name_lower = v.name.lower()
                        if "natural" in name_lower or "english" in name_lower or "david" in name_lower or "zira" in name_lower:
                            engine.setProperty("voice", v.id)
                            break

                    engine.say(text)
                    engine.runAndWait()
                except Exception as e:
                    logger.warning(f"TTS output failed or pyttsx3 is unavailable: {e}")

        if async_mode:
            thread = threading.Thread(target=_run_speak, daemon=True)
            thread.start()
        else:
            _run_speak()
