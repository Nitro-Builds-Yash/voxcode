"""Token budgeting for prompts."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _encoder():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # not installed or no cached encoding
        return None


def count_tokens(text: str) -> int:
    """Token count via tiktoken when available, else a ~4 chars/token estimate."""
    encoder = _encoder()
    if encoder is not None:
        return len(encoder.encode(text, disallowed_special=()))
    return (len(text) + 3) // 4


class ContextWindow:
    def __init__(self, max_tokens: int):
        self.max_tokens = max_tokens
        self.used = 0

    def try_add(self, text: str) -> bool:
        tokens = count_tokens(text)
        if self.used + tokens > self.max_tokens:
            return False
        self.used += tokens
        return True
