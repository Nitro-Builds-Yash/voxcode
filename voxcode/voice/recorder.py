"""Push-to-talk microphone capture on the machine running the backend.

VS Code webviews cannot reliably access the microphone, so the extension
asks the (local) backend to record instead.
"""

from __future__ import annotations

import threading

import numpy as np

SAMPLE_RATE = 16_000
MAX_SECONDS = 120


class RecorderError(RuntimeError):
    pass


class MicrophoneRecorder:
    def __init__(self):
        self._lock = threading.Lock()
        self._stream = None
        self._frames: list[np.ndarray] = []

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            raise RecorderError(f"Microphone capture unavailable: {exc}") from exc

        with self._lock:
            if self._stream is not None:
                return
            self._frames = []

            def callback(indata, frames, _time_info, status):
                if sum(len(f) for f in self._frames) < SAMPLE_RATE * MAX_SECONDS:
                    self._frames.append(indata[:, 0].copy())

            try:
                self._stream = sd.InputStream(
                    samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback
                )
                self._stream.start()
            except Exception as exc:
                self._stream = None
                raise RecorderError(f"Could not open the microphone: {exc}") from exc

    def stop(self) -> np.ndarray:
        with self._lock:
            if self._stream is None:
                raise RecorderError("Not recording")
            self._stream.stop()
            self._stream.close()
            self._stream = None
            audio = np.concatenate(self._frames) if self._frames else np.zeros(0, dtype=np.float32)
            self._frames = []
            return audio


recorder = MicrophoneRecorder()
