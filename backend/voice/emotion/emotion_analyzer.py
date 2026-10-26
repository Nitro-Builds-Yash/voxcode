import re
from typing import Dict, Any
from pydantic import BaseModel


class EmotionProfile(BaseModel):
    primary_emotion: str  # "frustrated", "urgent", "curious", "confident", "fatigued", "calm"
    intensity: float  # 0.0 to 1.0
    sentiment: str  # "negative", "neutral", "positive"
    tone_description: str
    vox_adaptation: str  # How Vox should tailor tone and engineering support


class EmotionAnalyzer:
    """
    Analyzes natural speech transcripts and acoustic cues to determine:
    1. Emotional state (frustration, urgency, curiosity, fatigue, calm)
    2. Sentiment & intensity
    3. Custom Vox adaptive behavioral responses (calm problem resolution, incident response, architectural deep-dive)
    """

    FRUSTRATION_KEYWORDS = [
        "broken", "why does this fail", "failing", "error again", "not working",
        "stupid", "damn", "argh", "hate this", "bug", "crash", "stuck", "what is wrong",
        "keeps breaking", "fix this mess", "annoying", "garbage"
    ]

    URGENCY_KEYWORDS = [
        "quick", "fast", "urgent", "asap", "production down", "emergency",
        "right now", "hurry", "critical", "immediately", "deadline", "hotfix"
    ]

    FATIGUE_KEYWORDS = [
        "tired", "long day", "sleepy", "exhausted", "late night", "brain dead",
        "cant think", "headache", "done for today"
    ]

    CURIOSITY_KEYWORDS = [
        "how does", "what if", "explain", "wondering", "why does", "tell me about",
        "explore", "architecture", "understand", "walk me through"
    ]

    CONFIDENT_KEYWORDS = [
        "deploy", "refactor", "ship it", "optimize", "benchmark", "let's build",
        "implement", "generate patch", "execute"
    ]

    def analyze(self, text: str) -> EmotionProfile:
        text_lower = text.lower()

        # Check for emotion patterns
        frustration_hits = sum(1 for kw in self.FRUSTRATION_KEYWORDS if kw in text_lower)
        urgency_hits = sum(1 for kw in self.URGENCY_KEYWORDS if kw in text_lower)
        fatigue_hits = sum(1 for kw in self.FATIGUE_KEYWORDS if kw in text_lower)
        curiosity_hits = sum(1 for kw in self.CURIOSITY_KEYWORDS if kw in text_lower)
        confident_hits = sum(1 for kw in self.CONFIDENT_KEYWORDS if kw in text_lower)

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
                    "Provide comprehensive architectural clarity, AST relationships, "
                    "and design tradeoffs."
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
