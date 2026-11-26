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
    """Callers and callees of each chunk.

    Python chunks use precise edges resolved by Jedi when available; other
    languages (and Python files Jedi could not analyse) fall back to matching
    called names against definition names.
    """

    def __init__(self, chunks: list[CodeChunk], call_edges: dict[str, list[str]] | None = None):
        self.chunks_by_id = {c.chunk_id: c for c in chunks}
        self.definitions: dict[str, list[str]] = defaultdict(list)
        self.callers: dict[str, list[str]] = defaultdict(list)
        self.precise_callees = {
            k: [v for v in vs if v in self.chunks_by_id]
            for k, vs in (call_edges or {}).items()
            if k in self.chunks_by_id
        }
        self.precise_callers: dict[str, list[str]] = defaultdict(list)
        for caller, callees in self.precise_callees.items():
            for callee in callees:
                self.precise_callers[callee].append(caller)
        precise_files = {self.chunks_by_id[c].file_path for c in self.precise_callees}

        for chunk in chunks:
            if chunk.chunk_type in _DEFINITION_TYPES:
                self.definitions[base_name(chunk.symbol_name)].append(chunk.chunk_id)
            if chunk.file_path in precise_files:
                continue  # resolved precisely above
            for name in chunk.calls:
                self.callers[name].append(chunk.chunk_id)

    def callees(self, chunk_id: str) -> list[str]:
        if chunk_id in self.precise_callees:
            return self.precise_callees[chunk_id]
        chunk = self.chunks_by_id.get(chunk_id)
        if chunk is None:
            return []
        found: list[str] = []
        for name in chunk.calls:
            # Skip very common names that are defined in many places.
            candidates = self.definitions.get(name, [])
            if len(candidates) <= 3:
                found.extend(c for c in candidates if c != chunk_id and c not in found)
        return found

    def callers_of(self, chunk_id: str) -> list[str]:
        chunk = self.chunks_by_id.get(chunk_id)
        if chunk is None or chunk.chunk_type not in _DEFINITION_TYPES:
            return []
        found = list(self.precise_callers.get(chunk_id, []))
        for other in self.callers.get(base_name(chunk.symbol_name), []):
            if other != chunk_id and other not in found:
                found.append(other)
        return found

    def neighbors(self, chunk_id: str, limit: int = 4) -> list[str]:
        found: list[str] = []
        for other in self.callees(chunk_id) + self.callers_of(chunk_id)[:3]:
            if other != chunk_id and other not in found:
                found.append(other)
        return found[:limit]
