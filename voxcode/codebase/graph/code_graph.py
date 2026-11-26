"""Lightweight call graph used to pull related code into the context.

Built from the call sites recorded on each chunk: for a retrieved function,
its callees (definitions of names it calls) and callers (chunks that call
it) are its neighbours.
"""

from __future__ import annotations

from collections import defaultdict

from voxcode.models.code_chunk import CodeChunk

_DEFINITION_TYPES = {"function", "method", "class"}


def base_name(symbol_name: str) -> str:
    return symbol_name.split(" (part")[0].rsplit(".", 1)[-1]


class CodeGraph:
    def __init__(self, chunks: list[CodeChunk]):
        self.chunks_by_id = {c.chunk_id: c for c in chunks}
        self.definitions: dict[str, list[str]] = defaultdict(list)
        self.callers: dict[str, list[str]] = defaultdict(list)

        for chunk in chunks:
            if chunk.chunk_type in _DEFINITION_TYPES:
                self.definitions[base_name(chunk.symbol_name)].append(chunk.chunk_id)
            for name in chunk.calls:
                self.callers[name].append(chunk.chunk_id)

    def neighbors(self, chunk_id: str, limit: int = 4) -> list[str]:
        chunk = self.chunks_by_id.get(chunk_id)
        if chunk is None:
            return []
        found: list[str] = []

        def add(ids: list[str]) -> None:
            for other in ids:
                if other != chunk_id and other not in found:
                    found.append(other)

        for name in chunk.calls:
            # Skip very common names that are defined in many places.
            if len(self.definitions.get(name, [])) <= 3:
                add(self.definitions.get(name, []))
        if chunk.chunk_type in _DEFINITION_TYPES:
            add(self.callers.get(base_name(chunk.symbol_name), [])[:3])
        return found[:limit]
