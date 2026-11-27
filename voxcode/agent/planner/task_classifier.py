"""Intent classification: a learned model (intent_model.py) with keyword
rules on whole words (so "show" is not "how") as the fallback."""

from __future__ import annotations

import re

from voxcode.agent.planner.execution_plan import TaskType

# Checked in order; the first matching group wins. Questions are checked
# first so "how do I add a route?" is an explanation, not an edit.
_RULES: list[tuple[TaskType, tuple[str, ...]]] = [
    (
        TaskType.EXPLAIN,
        (
            "explain",
            "describe",
            "how does",
            "how do",
            "how is",
            "why",
            "what does",
            "what is",
            "walk me through",
            "understand",
            "tell me about",
        ),
    ),
    (TaskType.SEARCH, ("find", "locate", "search", "where", "which file", "show me", "list")),
    (TaskType.TEST, ("write tests?", "add tests?", "unit tests?", "test cases?", "pytest", "test coverage")),
    (TaskType.FIX, ("fix", "debug", "resolve", "repair", "handle the error", "bug")),
    (
        TaskType.REFACTOR,
        ("refactor", "rename", "clean ?up", "simplify", "extract", "restructure", "optimi[sz]e", "improve"),
    ),
    (TaskType.EXECUTE, ("run the", "run all", "run tests?", "execute", "install")),
    (
        TaskType.GENERATE,
        (
            "add",
            "create",
            "implement",
            "generate",
            "write",
            "build",
            "make",
            "update",
            "change",
            "replace",
            "remove",
            "delete",
            "insert",
            "docstrings?",
        ),
    ),
]

_COMPILED = [(task, re.compile(r"\b(?:" + "|".join(words) + r")\b", re.IGNORECASE)) for task, words in _RULES]


def rule_classify(query: str) -> TaskType:
    for task, pattern in _COMPILED:
        if pattern.search(query):
            return task
    return TaskType.CHAT


_READ = {TaskType.EXPLAIN, TaskType.SEARCH, TaskType.CHAT}
_QUESTION = re.compile(
    r"^\s*(what|how|why|where|which|who|when|is|are|does|do|can|could|should|explain|describe|summari[sz]e|"
    r"show|list|tell|walk|give)\b",
    re.I,
)


class TaskClassifier:
    """Keyword rules decide; the learned model only refines.

    The model may (a) decide when no rule matches, and (b) pick a better
    label *within the same family* (reading: explain/search/chat; changing:
    fix/refactor/generate/test/execute). It never turns a question into an
    edit or an edit into a question on its own: a misrouted question would
    rewrite code the user only asked about.
    """

    def classify(self, query: str) -> TaskType:
        from voxcode.config.settings import settings

        rule = rule_classify(query)
        if not settings.INTENT_MODEL:
            return rule
        from voxcode.agent.planner.intent_model import default_model

        label, confidence = default_model().predict(query)
        guess = TaskType(label)
        if rule != TaskType.CHAT:
            same_family = (rule in _READ) == (guess in _READ)
            return guess if same_family and confidence >= settings.INTENT_MIN_CONFIDENCE else rule
        if confidence < settings.INTENT_MIN_CONFIDENCE:
            return rule
        if guess not in _READ and _QUESTION.match(query):
            return TaskType.EXPLAIN  # a question is never an edit
        return guess
