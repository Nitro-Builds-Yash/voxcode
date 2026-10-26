import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class TTSService:
    """
    Local Text-To-Speech service for voice feedback with emotional vocal modulation.
    Modulates speech rate, pitch, and voice choice based on detected emotions (JARVIS style).
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
        Synthesizes and speaks text to default audio output with emotion-based pacing.
        """
        if not text:
            return

        def _run_speak():
            with self._lock:
                try:
                    import pyttsx3
                    engine = pyttsx3.init()

                    # Modulation based on emotion
                    # Calm/JARVIS standard: ~170 WPM, composed British or English tone
                    # Urgent: ~200 WPM, brisk
                    # Frustrated (calming down): ~155 WPM, slow, deliberate
                    # Fatigue: ~160 WPM, warm
                    rate = 170
                    if emotion == "urgent":
                        rate = 205
                    elif emotion == "frustrated":
                        rate = 155
                    elif emotion == "fatigued":
                        rate = 160
                    elif emotion == "curious":
                        rate = 175

                    engine.setProperty("rate", rate)

                    # Look for British English voice (George / Hazel / Jarvis style) if present
                    voices = engine.getProperty("voices")
                    for v in voices:
                        name_lower = v.name.lower()
                        if "british" in name_lower or "uk" in name_lower or "george" in name_lower or "david" in name_lower:
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
