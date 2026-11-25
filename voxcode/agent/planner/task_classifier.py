"""Rule-based intent classification on whole words (so "show" is not "how")."""

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


class TaskClassifier:
    def classify(self, query: str) -> TaskType:
        for task, pattern in _COMPILED:
            if pattern.search(query):
                return task
        return TaskType.CHAT
