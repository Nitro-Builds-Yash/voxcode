import numpy as np

from voxcode.voice.emotion.emotion_analyzer import EmotionAnalyzer, analyze_with_audio
from voxcode.voice.listener import FRAME, SAMPLE_RATE, UtteranceSegmenter
from voxcode.voice.prosody import acoustic_state, extract_features
from voxcode.voice.tts.tts_service import TTSService


def tone(seconds, amplitude, hz=180.0, sweep=0.0):
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    freq = hz + sweep * np.sin(2 * np.pi * 1.5 * t)
    return (amplitude * np.sin(2 * np.pi * np.cumsum(freq) / SAMPLE_RATE)).astype(np.float32)


def frames(audio):
    return [audio[i : i + FRAME] for i in range(0, len(audio) - FRAME + 1, FRAME)]


def test_segmenter_finds_utterance_and_ignores_blips():
    segmenter = UtteranceSegmenter(silence_seconds=0.5, threshold=0.02)
    stream = np.concatenate(
        [tone(1.0, 0.002), tone(1.2, 0.3), tone(1.0, 0.002), tone(0.06, 0.3), tone(1.0, 0.002)]
    )
    events = [e for f in frames(stream) if (e := segmenter.feed(f))]
    assert [e.kind for e in events] == ["start", "end", "start", "end"]
    speech = events[1].audio
    assert (
        speech is not None and 1.2 <= len(speech) / SAMPLE_RATE <= 2.1
    )  # speech + pre-roll + trailing silence
    assert events[3].audio is None  # a 60 ms click is not an utterance


def test_prosody_reads_urgent_and_tired_voices():
    urgent = extract_features(
        tone(2.5, 0.5, 150, 60), "fix the login bug now we need it deployed before the demo starts"
    )
    tired = extract_features(tone(2.5, 0.02, 110), "um maybe look")
    assert acoustic_state(urgent) == "urgent"
    assert acoustic_state(tired) == "fatigued"
    assert 140 < urgent.pitch_hz < 220


def test_voice_refines_calm_text_but_never_overrides_clear_words():
    analyzer = EmotionAnalyzer()
    calm_words = "look at the parser and the indexer and tell me what changed please"
    profile = analyze_with_audio(analyzer, calm_words, tone(2.5, 0.5, 150, 60))
    assert profile.primary_emotion == "urgent" and profile.source == "text+voice" and profile.prosody
    curious = analyze_with_audio(analyzer, "explain the architecture", tone(2.5, 0.5, 150, 60))
    assert curious.primary_emotion == "curious"


def test_barge_in_drops_rest_of_interrupted_reply(monkeypatch):
    tts = TTSService()
    spoken = []
    monkeypatch.setattr(tts, "_speak_blocking", lambda text, emotion: spoken.append(text))
    monkeypatch.setattr(
        "threading.Thread",
        lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})(),
    )
    tts.speak("First sentence.", reply_id="r1")
    tts.stop()
    tts.speak("Second sentence.", reply_id="r1")
    tts.speak("New reply.", reply_id="r2")
    assert spoken == ["First sentence.", "New reply."]
