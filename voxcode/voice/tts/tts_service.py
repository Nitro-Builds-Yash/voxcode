"""Text-to-speech: Piper (neural, offline) when configured, else pyttsx3.

`synthesize_wav` returns audio bytes so clients (browser UI, VS Code) can
play the reply themselves; `speak` plays it on the backend machine.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
import wave

from voxcode.config.settings import settings

logger = logging.getLogger(__name__)

# Speaking rate per detected state (1.0 = normal).
_SPEED = {"urgent": 1.15, "frustrated": 0.9, "fatigued": 0.92}


class TTSService:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pyttsx3 = None
        # Barge-in silences the rest of the reply being spoken, so sentences
        # that arrive after the user interrupted are dropped too.
        self._current_reply: str | None = None
        self._interrupted: set[str] = set()
        self._piper = None
        self._piper_failed = False

    @property
    def engine(self) -> str:
        choice = settings.TTS_ENGINE.lower()
        if choice == "auto":
            return "piper" if settings.PIPER_MODEL else "pyttsx3"
        return choice

    # ------------------------------------------------------------ public
    def synthesize_wav(self, text: str, emotion: str = "calm") -> bytes | None:
        if not text:
            return None
        speed = _SPEED.get(emotion, 1.0)
        try:
            with self._lock:
                if self.engine == "piper":
                    return self._piper_wav(text, speed)
                if self.engine == "pyttsx3":
                    return self._pyttsx3_wav(text, speed)
        except Exception as exc:
            logger.warning("TTS synthesis failed (%s): %s", self.engine, exc)
        return None

    def speak(self, text: str, emotion: str = "calm", reply_id: str | None = None) -> None:
        """Play speech on the backend machine without blocking the caller.

        Sentences of one streamed reply share a `reply_id`; once the user
        interrupts that reply, its remaining sentences are skipped.
        """
        if reply_id is not None:
            if reply_id in self._interrupted:
                return
            self._current_reply = reply_id
        self._stop.clear()
        threading.Thread(target=self._speak_blocking, args=(text, emotion), daemon=True).start()

    def stop(self) -> None:
        """Barge-in: stop any speech that is playing or queued."""
        self._stop.set()
        if self._current_reply is not None:
            self._interrupted.add(self._current_reply)
            if len(self._interrupted) > 1000:
                self._interrupted.clear()
        try:
            if self._pyttsx3 is not None:
                self._pyttsx3.stop()
        except Exception:
            pass
        try:
            import sounddevice as sd

            sd.stop()
        except (ImportError, OSError):
            pass

    # ----------------------------------------------------------- engines
    def _speak_blocking(self, text: str, emotion: str) -> None:
        if self._stop.is_set():
            return
        if self.engine == "pyttsx3":
            try:
                with self._lock:
                    if self._stop.is_set():
                        return
                    engine = self._pyttsx3_engine(_SPEED.get(emotion, 1.0))
                    self._pyttsx3 = engine
                    engine.say(text)
                    engine.runAndWait()
                    self._pyttsx3 = None
                return
            except Exception as exc:
                logger.warning("pyttsx3 playback failed: %s", exc)
                return
        audio = self.synthesize_wav(text, emotion)
        if audio and not self._stop.is_set():
            try:
                import numpy as np
                import sounddevice as sd

                with wave.open(io.BytesIO(audio)) as wav:
                    frames: np.ndarray = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
                    sd.play(frames, wav.getframerate(), blocking=True)
            except Exception as exc:
                logger.warning("Audio playback failed: %s", exc)

    def _piper_wav(self, text: str, speed: float) -> bytes:
        if self._piper is None:
            if self._piper_failed:
                raise RuntimeError("Piper unavailable")
            try:
                from piper import PiperVoice

                self._piper = PiperVoice.load(settings.PIPER_MODEL)
            except Exception:
                self._piper_failed = True
                raise
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            try:  # piper-tts >= 1.3
                from piper import SynthesisConfig

                self._piper.synthesize_wav(text, wav, syn_config=SynthesisConfig(length_scale=1 / speed))
            except ImportError:  # piper-tts 1.2
                self._piper.synthesize(text, wav, length_scale=1 / speed)
        return buffer.getvalue()

    def _pyttsx3_engine(self, speed: float):
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", int(175 * speed))
        return engine

    def _pyttsx3_wav(self, text: str, speed: float) -> bytes:
        engine = self._pyttsx3_engine(speed)
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            engine.save_to_file(text, path)
            engine.runAndWait()
            with open(path, "rb") as handle:
                return handle.read()
        finally:
            os.unlink(path)


tts_service = TTSService()
