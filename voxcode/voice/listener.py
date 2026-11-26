"""Hands-free listening on the server's microphone.

* Energy-based voice activity detection with an adaptive noise floor finds
  where an utterance starts and ends (VAD_SILENCE_SECONDS of quiet), so the
  user never has to press "stop".
* Optional wake word ("hey jarvis" etc.) through openWakeWord: when WAKE_WORD
  is set, only speech after the wake word is captured.
* Barge-in: when the user starts talking, any reply being spoken stops.
* Partial transcripts are published about once a second while speaking.

Events go to subscribers (the /voice/listen WebSocket):
  {"type": "listening"} {"type": "wake"} {"type": "speech_start"}
  {"type": "partial", "text"} {"type": "final", "transcription", "command",
  "emotion", "timings_ms"} {"type": "stopped"} {"type": "error", "detail"}
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from voxcode.config.settings import settings

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME = 480  # 30 ms
PRE_ROLL_FRAMES = 10  # keep 300 ms before speech starts
MIN_SPEECH_SECONDS = 0.35
MAX_UTTERANCE_SECONDS = 60
PARTIAL_EVERY_SECONDS = 1.0


@dataclass
class Segment:
    kind: str  # "start" | "end"
    audio: np.ndarray | None = None


class UtteranceSegmenter:
    """Turns a stream of 30 ms frames into utterances (pure logic, testable)."""

    def __init__(self, silence_seconds: float | None = None, threshold: float | None = None):
        self.silence_frames = int((silence_seconds or settings.VAD_SILENCE_SECONDS) * SAMPLE_RATE / FRAME)
        self.min_threshold = threshold if threshold is not None else settings.VAD_ENERGY_THRESHOLD
        self.noise_floor = self.min_threshold / 3
        self.pre_roll: deque[np.ndarray] = deque(maxlen=PRE_ROLL_FRAMES)
        self.frames: list[np.ndarray] = []
        self.in_speech = False
        self.silent_run = 0
        self.voiced = 0

    @property
    def threshold(self) -> float:
        return max(self.min_threshold, self.noise_floor * 3)

    def current_audio(self) -> np.ndarray:
        return np.concatenate(self.frames) if self.frames else np.zeros(0, np.float32)

    def feed(self, frame: np.ndarray) -> Segment | None:
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float32))))
        loud = rms > self.threshold
        if not self.in_speech:
            if not loud:  # track background noise
                self.noise_floor = 0.95 * self.noise_floor + 0.05 * rms
                self.pre_roll.append(frame)
                return None
            self.in_speech = True
            self.frames = [*self.pre_roll, frame]
            self.pre_roll.clear()
            self.silent_run, self.voiced = 0, 1
            return Segment("start")

        self.frames.append(frame)
        if loud:
            self.silent_run = 0
            self.voiced += 1
        else:
            self.silent_run += 1
        too_long = len(self.frames) * FRAME >= MAX_UTTERANCE_SECONDS * SAMPLE_RATE
        if self.silent_run >= self.silence_frames or too_long:
            audio = self.current_audio()
            enough = self.voiced * FRAME >= MIN_SPEECH_SECONDS * SAMPLE_RATE
            self.in_speech, self.frames = False, []
            return Segment("end", audio if enough else None)
        return None

    def feed_many(self, audio: np.ndarray) -> list[Segment]:
        usable = len(audio) - len(audio) % FRAME
        events = (self.feed(audio[i : i + FRAME]) for i in range(0, usable, FRAME))
        return [e for e in events if e is not None]


class NativeSegmenter:
    """Same state machine in Rust (voxcode-native); same interface."""

    def __init__(self, module, silence_seconds: float | None = None, threshold: float | None = None):
        self._inner = module.Segmenter(
            silence_seconds or settings.VAD_SILENCE_SECONDS,
            threshold if threshold is not None else settings.VAD_ENERGY_THRESHOLD,
        )

    @property
    def threshold(self) -> float:
        return self._inner.threshold

    @property
    def in_speech(self) -> bool:
        return self._inner.in_speech

    def current_audio(self) -> np.ndarray:
        return np.frombuffer(self._inner.current_audio(), dtype=np.float32)

    def feed(self, frame: np.ndarray) -> Segment | None:
        event = self._inner.feed(np.ascontiguousarray(frame, dtype=np.float32).tobytes())
        return _segment(event) if event else None

    def feed_many(self, audio: np.ndarray) -> list[Segment]:
        """Process a buffer of many frames in one call (WebSocket chunks)."""
        raw = np.ascontiguousarray(audio, dtype=np.float32).tobytes()
        return [_segment(e) for e in self._inner.feed_many(raw)]


def _segment(event: tuple[str, bytes | None]) -> Segment:
    kind, audio = event
    return Segment(kind, np.frombuffer(audio, dtype=np.float32).copy() if audio is not None else None)


def make_segmenter(
    silence_seconds: float | None = None, threshold: float | None = None
) -> UtteranceSegmenter | NativeSegmenter:
    from voxcode.native import native

    module = native()
    if module is not None:
        return NativeSegmenter(module, silence_seconds, threshold)
    return UtteranceSegmenter(silence_seconds, threshold)


class WakeWord:
    """openWakeWord detector; available only when the package is installed."""

    def __init__(self, name: str, threshold: float):
        from openwakeword.model import Model

        self.name = name
        self.threshold = threshold
        self.model = Model(wakeword_models=[name])

    def detected(self, frame: np.ndarray) -> bool:
        pcm = (np.clip(frame, -1, 1) * 32767).astype(np.int16)
        scores = self.model.predict(pcm)
        return any(score >= self.threshold for score in scores.values())


class VoiceListener:
    def __init__(self, transcribe: Callable[[np.ndarray], str], on_final: Callable[[str, np.ndarray], dict]):
        self.transcribe = transcribe
        self.on_final = on_final
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ----------------------------------------------------------- pub/sub
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    # --------------------------------------------------------- lifecycle
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="voxcode-listener", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            self.publish({"type": "error", "detail": f"Microphone unavailable: {exc}"})
            return

        wake = None
        if settings.WAKE_WORD:
            try:
                wake = WakeWord(settings.WAKE_WORD, settings.WAKE_WORD_THRESHOLD)
            except Exception as exc:
                self.publish(
                    {"type": "error", "detail": f"Wake word unavailable ({exc}); listening without it"}
                )

        frames: queue.Queue = queue.Queue()
        segmenter = make_segmenter()
        armed = wake is None
        last_partial = 0.0
        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=FRAME,
                                callback=lambda data, *_: frames.put(data[:, 0].copy())):  # fmt: skip
                self.publish({"type": "listening", "wake_word": settings.WAKE_WORD or None})
                while not self._stop.is_set():
                    try:
                        frame = frames.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if not armed:
                        if wake is not None and wake.detected(frame):
                            armed = True
                            self.publish({"type": "wake"})
                        continue
                    segment = segmenter.feed(frame)
                    if segment and segment.kind == "start":
                        from voxcode.voice.tts.tts_service import tts_service

                        tts_service.stop()  # barge-in
                        self.publish({"type": "speech_start"})
                        last_partial = time.monotonic()
                    elif segment and segment.kind == "end":
                        armed = wake is None
                        if segment.audio is not None:
                            self._finish(segment.audio)
                    elif segmenter.in_speech and time.monotonic() - last_partial >= PARTIAL_EVERY_SECONDS:
                        last_partial = time.monotonic()
                        try:
                            self.publish(
                                {"type": "partial", "text": self.transcribe(segmenter.current_audio())}
                            )
                        except Exception as exc:
                            logger.debug("Partial transcription failed: %s", exc)
        except Exception as exc:
            self.publish({"type": "error", "detail": f"Listening failed: {exc}"})
        finally:
            self.publish({"type": "stopped"})

    def _finish(self, audio: np.ndarray) -> None:
        started = time.perf_counter()
        try:
            text = self.transcribe(audio)
        except Exception as exc:
            self.publish({"type": "error", "detail": f"Transcription failed: {exc}"})
            return
        stt_ms = round((time.perf_counter() - started) * 1000, 1)
        if text.strip():
            self.publish({"type": "final", **self.on_final(text, audio), "timings_ms": {"stt": stt_ms}})
