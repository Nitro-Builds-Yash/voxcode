"""Code-aware tokenizer for BM25.

`getUserById(user_id)` -> getuserbyid, get, user, by, id, user_id
"""

from __future__ import annotations

import re

from voxcode.native import native

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
STOPWORDS = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "be",
    "to",
    "of",
    "and",
    "or",
    "in",
    "on",
    "for",
    "with",
    "this",
    "that",
    "it",
    "do",
    "does",
    "how",
    "what",
    "where",
    "which",
    "who",
    "why",
    "me",
    "my",
    "i",
    "we",
    "you",
    "show",
    "find",
    "explain",
    "please",
    "can",
    "code",
    "self",
    "return",
}


def tokenize(text: str) -> list[str]:
    accel = native()
    if accel is not None:
        return accel.tokenize(text)
    return tokenize_python(text)


def tokenize_python(text: str) -> list[str]:
    tokens: list[str] = []
    for identifier in _IDENTIFIER_RE.findall(text):
        lowered = identifier.lower()
        parts = [p.lower() for piece in identifier.split("_") for p in _CAMEL_RE.findall(piece)]
        if len(parts) > 1 or lowered not in parts:
            tokens.append(lowered)
        tokens.extend(parts)
    return [t for t in tokens if len(t) > 1 and t not in STOPWORDS]
