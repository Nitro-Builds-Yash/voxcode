"""Tone from the voice itself: loudness, pitch and speaking rate.

Keyword analysis only sees the transcript ("fix the bug" sounds the same
calm or furious). These acoustic features let a fast, loud, highly
inflected request read as urgent, and a slow, quiet, flat one as tired.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

SAMPLE_RATE = 16_000
_FRAME = 640  # 40 ms
_MIN_HZ, _MAX_HZ = 70, 400


@dataclass
class ProsodyFeatures:
    duration_s: float
    voiced_ratio: float
    rms_db: float
    pitch_hz: float  # median of voiced frames (0 if none)
    pitch_variation: float  # std / mean of voiced pitch
    words_per_second: float

    def to_dict(self) -> dict:
        return {k: round(v, 3) for k, v in asdict(self).items()}


def _frame_pitch(frame: np.ndarray, sample_rate: int) -> float:
    frame = frame - frame.mean()
    if not np.any(frame):
        return 0.0
    corr = np.correlate(frame, frame, mode="full")[len(frame) - 1 :]
    lo, hi = sample_rate // _MAX_HZ, sample_rate // _MIN_HZ
    if hi >= len(corr):
        return 0.0
    lag = lo + int(np.argmax(corr[lo:hi]))
    # Require a clear periodic peak relative to the zero-lag energy.
    return sample_rate / lag if corr[lag] > 0.3 * corr[0] else 0.0


def extract_features(audio: np.ndarray, transcript: str, sample_rate: int = SAMPLE_RATE) -> ProsodyFeatures:
    audio = np.asarray(audio, dtype=np.float32).ravel()
    duration = len(audio) / sample_rate if len(audio) else 0.0
    frames = [audio[i : i + _FRAME] for i in range(0, len(audio) - _FRAME + 1, _FRAME)]
    if not frames:
        return ProsodyFeatures(duration, 0.0, -100.0, 0.0, 0.0, 0.0)

    rms = np.array([float(np.sqrt(np.mean(f**2))) for f in frames])
    speech = rms > max(0.005, 0.15 * float(rms.max()))
    pitches = np.array([_frame_pitch(f, sample_rate) for f, s in zip(frames, speech, strict=True) if s])
    voiced = pitches[pitches > 0]
    speech_seconds = max(float(speech.sum()) * _FRAME / sample_rate, 0.25)
    words = len(transcript.split())
    return ProsodyFeatures(
        duration_s=duration,
        voiced_ratio=float(len(voiced)) / len(frames),
        rms_db=float(20 * np.log10(np.mean(rms[speech]) + 1e-9)) if speech.any() else -100.0,
        pitch_hz=float(np.median(voiced)) if len(voiced) else 0.0,
        pitch_variation=float(np.std(voiced) / np.mean(voiced)) if len(voiced) > 2 else 0.0,
        words_per_second=words / speech_seconds,
    )


def acoustic_state(features: ProsodyFeatures) -> str | None:
    """'urgent', 'fatigued' or None when the voice gives no clear signal."""
    if features.duration_s < 1.0 or features.voiced_ratio < 0.1:
        return None
    aroused = features.words_per_second > 3.4 and (features.rms_db > -18 or features.pitch_variation > 0.25)
    if aroused:
        return "urgent"
    if features.words_per_second < 1.8 and features.rms_db < -28 and features.pitch_variation < 0.12:
        return "fatigued"
    return None
