"""Find JSON objects embedded in model replies."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

_decoder = json.JSONDecoder()


def iter_json_objects(text: str) -> Iterator[dict[str, Any]]:
    """Yield every top-level JSON object in `text`, in order."""
    index = text.find("{")
    while index != -1:
        try:
            value, end = _decoder.raw_decode(text, index)
        except ValueError:
            index = text.find("{", index + 1)
            continue
        if isinstance(value, dict):
            yield value
        index = text.find("{", end)


def first_json_with(text: str, *keys: str) -> dict[str, Any] | None:
    """First JSON object containing any of `keys`."""
    for obj in iter_json_objects(text):
        if any(key in obj for key in keys):
            return obj
    return None
