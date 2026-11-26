"""Model cascade: send easy questions to a small model, escalate when needed.

Routing uses signals available before generation: the task, how long and
open-ended the question is, and how confident retrieval was (a clear top
hit vs. many similar scores spread over files). After the small model
answers, the answer is checked; weak or ungrounded answers are regenerated
by the main model, so cost drops without accepting worse answers.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass

from voxcode.models.retrieval_result import RetrievalResult

_OPEN_ENDED = re.compile(
    r"\b(why|design|architecture|trade-?offs?|compare|difference|pros|cons|should|best way|refactor|"
    r"security|performance|race|concurren\w+|deadlock|memory leak|end.to.end|overall)\b",
    re.I,
)
_LOOKUP = re.compile(r"\b(where|which file|find|locate|list|show me|what does|what is|signature)\b", re.I)
_REFUSAL = re.compile(
    r"\b(i (?:don't|do not|cannot|can't) (?:know|find|see|determine)|not enough (?:context|information)|unable to)\b",
    re.I,
)


@dataclass
class Route:
    difficulty: float
    tier: str  # small | main
    escalated: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def difficulty(query: str, task: str, results: list[RetrievalResult]) -> float:
    """0 (lookup) .. 1 (open-ended, multi-file reasoning)."""
    z = -0.4
    words = len(query.split())
    z += 0.04 * min(words, 40)
    if _OPEN_ENDED.search(query):
        z += 1.2
    if _LOOKUP.search(query):
        z -= 0.9
    z += {"search": -0.8, "chat": -0.3, "explain": 0.2}.get(task, 0.6)  # edits are harder
    top = results[:5]
    if len(top) >= 2 and top[0].score > 0:
        margin = (top[0].score - top[1].score) / top[0].score
        z -= 1.2 * margin  # one obvious hit: easy
        z += 0.25 * (len({r.file_path for r in top}) - 1)  # spread across files: harder
    return round(1 / (1 + math.exp(-z)), 3)


def needs_escalation(answer: str, groundedness: float, min_groundedness: float) -> str:
    """Reason to hand the question to the main model, or '' if the answer is fine."""
    text = answer.strip()
    if len(text) < 20:
        return "answer too short"
    if _REFUSAL.search(text[:400]):
        return "small model was unsure"
    if groundedness < min_groundedness:
        return f"groundedness {groundedness:.2f} < {min_groundedness:.2f}"
    return ""
