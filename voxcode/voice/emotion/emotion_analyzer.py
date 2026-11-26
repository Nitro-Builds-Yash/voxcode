import re

from pydantic import BaseModel


class EmotionProfile(BaseModel):
    primary_emotion: str  # "frustrated", "urgent", "curious", "confident", "fatigued", "calm"
    intensity: float  # 0.0 to 1.0
    sentiment: str  # "negative", "neutral", "positive"
    tone_description: str
    vox_adaptation: str  # How Vox should tailor tone and engineering support
    source: str = "text"  # "text", or "text+voice" when acoustic features were used
    prosody: dict | None = None


def _hits(keywords: list[str], text: str) -> int:
    """Count keywords that appear as whole words/phrases (not substrings)."""
    return sum(1 for kw in keywords if re.search(rf"\b{re.escape(kw)}\b", text))


class EmotionAnalyzer:
    """
    Analyzes natural speech transcripts and acoustic cues to determine:
    1. Emotional state (frustration, urgency, curiosity, fatigue, calm)
    2. Sentiment & intensity
    3. Custom Vox adaptive behavioral responses (calm problem resolution, incident response, architectural deep-dive)
    """

    FRUSTRATION_KEYWORDS = [
        "broken",
        "why does this fail",
        "failing",
        "error again",
        "not working",
        "stupid",
        "damn",
        "argh",
        "hate this",
        "crashes again",
        "stuck",
        "what is wrong",
        "keeps breaking",
        "fix this mess",
        "annoying",
        "garbage",
    ]

    URGENCY_KEYWORDS = [
        "quick",
        "fast",
        "urgent",
        "asap",
        "production down",
        "emergency",
        "right now",
        "hurry",
        "critical",
        "immediately",
        "deadline",
        "hotfix",
    ]

    FATIGUE_KEYWORDS = [
        "tired",
        "long day",
        "sleepy",
        "exhausted",
        "late night",
        "brain dead",
        "cant think",
        "can't think",
        "headache",
        "done for today",
    ]

    CURIOSITY_KEYWORDS = [
        "how does",
        "what if",
        "explain",
        "wondering",
        "why does",
        "tell me about",
        "explore",
        "architecture",
        "understand",
        "walk me through",
    ]

    CONFIDENT_KEYWORDS = [
        "deploy",
        "refactor",
        "ship it",
        "optimize",
        "benchmark",
        "let's build",
        "implement",
        "generate patch",
        "execute",
    ]

    def analyze(self, text: str) -> EmotionProfile:
        text_lower = text.lower()

        # Check for emotion patterns
        frustration_hits = _hits(self.FRUSTRATION_KEYWORDS, text_lower)
        urgency_hits = _hits(self.URGENCY_KEYWORDS, text_lower)
        fatigue_hits = _hits(self.FATIGUE_KEYWORDS, text_lower)
        curiosity_hits = _hits(self.CURIOSITY_KEYWORDS, text_lower)
        confident_hits = _hits(self.CONFIDENT_KEYWORDS, text_lower)

        exclamations = text.count("!")
        words = text.split()
        caps_words = sum(1 for w in words if w.isupper() and len(w) > 1)

        # Frustration
        if frustration_hits > 0 or (caps_words >= 2 and frustration_hits > 0):
            intensity = min(1.0, 0.5 + 0.15 * frustration_hits + 0.1 * exclamations)
            return EmotionProfile(
                primary_emotion="frustrated",
                intensity=intensity,
                sentiment="negative",
                tone_description="Stress or blocker detected.",
                vox_adaptation=(
                    "Adopt a steady, reassuring tone. Cut directly to the root cause without lecturing, "
                    "and provide an immediate, clear remedy to unblock the developer."
                ),
            )

        # Urgency
        if urgency_hits > 0 or exclamations >= 2:
            intensity = min(1.0, 0.6 + 0.2 * urgency_hits)
            return EmotionProfile(
                primary_emotion="urgent",
                intensity=intensity,
                sentiment="neutral",
                tone_description="Time-sensitive incident or rapid patch needed.",
                vox_adaptation=(
                    "Switch to Incident Response mode. Keep communications brief and decisive. "
                    "Provide actionable commands and code diffs first."
                ),
            )

        # Fatigue
        if fatigue_hits > 0:
            return EmotionProfile(
                primary_emotion="fatigued",
                intensity=0.7,
                sentiment="neutral",
                tone_description="Developer fatigue or late session detected.",
                vox_adaptation=(
                    "Minimize cognitive strain. Keep explanations concise, handle edge cases cleanly, "
                    "and offer clear, low-effort action steps."
                ),
            )

        # Curiosity / Exploration
        if curiosity_hits > 0:
            return EmotionProfile(
                primary_emotion="curious",
                intensity=0.8,
                sentiment="positive",
                tone_description="Exploration, learning, or architectural inquiry.",
                vox_adaptation=(
                    "Provide comprehensive architectural clarity, AST relationships, and design tradeoffs."
                ),
            )

        # Default: Composed / Flow State
        return EmotionProfile(
            primary_emotion="calm",
            intensity=0.5,
            sentiment="positive" if confident_hits > 0 else "neutral",
            tone_description="Focused, productive flow state.",
            vox_adaptation=(
                "Maintain a crisp, highly efficient engineering flow. "
                "Be proactive, clear, and mathematically accurate."
            ),
        )


_ACOUSTIC_TEXT = {
    "urgent": "production is down, we need this asap!",
    "fatigued": "i'm tired, been at this all night",
}


def analyze_with_audio(
    analyzer: EmotionAnalyzer, text: str, audio, sample_rate: int = 16_000
) -> EmotionProfile:
    """Text analysis, refined by loudness, pitch and speaking rate.

    The transcript wins when it carries a clear signal; the voice decides
    only when the words alone read as calm.
    """
    from voxcode.voice.prosody import acoustic_state, extract_features

    profile = analyzer.analyze(text)
    features = extract_features(audio, text, sample_rate)
    state = acoustic_state(features)
    if profile.primary_emotion == "calm" and state in _ACOUSTIC_TEXT:
        profile = analyzer.analyze(_ACOUSTIC_TEXT[state])
        profile = profile.model_copy(update={"intensity": round(profile.intensity * 0.8, 2)})
    return profile.model_copy(update={"source": "text+voice", "prosody": features.to_dict()})
