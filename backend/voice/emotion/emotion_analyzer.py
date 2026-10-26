import re
from typing import Dict, Any
from pydantic import BaseModel


class EmotionProfile(BaseModel):
    primary_emotion: str  # "frustrated", "urgent", "curious", "confident", "fatigued", "calm"
    intensity: float  # 0.0 to 1.0
    sentiment: str  # "negative", "neutral", "positive"
    tone_description: str
    jarvis_directive: str  # How JARVIS should tailor its persona and technical response


class EmotionAnalyzer:
    """
    Analyzes natural speech transcripts and acoustic cues to determine:
    1. Emotional state (frustration, urgency, curiosity, fatigue, calm)
    2. Sentiment & intensity
    3. Custom JARVIS behavioral adaptations (calm reassurance, high-speed triage, deep technical mentorship)
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

        # Exclamation and caps detection
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
                tone_description="User appears stressed, blocked, or encountering errors.",
                jarvis_directive=(
                    "Adopt a calming, reassuring, steady JARVIS tone. Acknowledge the irritation with poise, "
                    "cut straight to the root cause without lecturing, and provide a clean, immediate remedy."
                ),
            )

        # Urgency
        if urgency_hits > 0 or exclamations >= 2:
            intensity = min(1.0, 0.6 + 0.2 * urgency_hits)
            return EmotionProfile(
                primary_emotion="urgent",
                intensity=intensity,
                sentiment="neutral",
                tone_description="High-priority, time-sensitive situation or active incident.",
                jarvis_directive=(
                    "Switch to Tactical Incident mode. Be extremely crisp, decisive, and rapid. "
                    "Lead with direct actionable commands and diffs first, explanation second."
                ),
            )

        # Fatigue
        if fatigue_hits > 0:
            return EmotionProfile(
                primary_emotion="fatigued",
                intensity=0.7,
                sentiment="neutral",
                tone_description="Late night or cognitive exhaustion detected.",
                jarvis_directive=(
                    "Adopt a supportive, highly attentive copilot demeanor. Handle the heavy lifting, "
                    "simplify choices, double-check edge cases gently, and suggest taking a break if needed."
                ),
            )

        # Curiosity / Exploration
        if curiosity_hits > 0:
            return EmotionProfile(
                primary_emotion="curious",
                intensity=0.8,
                sentiment="positive",
                tone_description="Deep dive, architectural exploration, or learning mode.",
                jarvis_directive=(
                    "Act as an insightful systems mentor and co-architect. Provide rich context, "
                    "tradeoffs, AST relationships, and design pattern analogies."
                ),
            )

        # Default: Confident / Calm Collaborator
        return EmotionProfile(
            primary_emotion="calm",
            intensity=0.5,
            sentiment="positive" if confident_hits > 0 else "neutral",
            tone_description="Composed, productive engineering flow.",
            jarvis_directive=(
                "Speak as J.A.R.V.I.S.: sophisticated, proactive, dryly witty, mathematically precise, "
                "addressing the user with high professional respect ('sir' / 'commander' when natural)."
            ),
        )
