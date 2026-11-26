"""Precise call edges for Python using Jedi's static analysis.

Name matching links `save()` to *every* function called `save`; Jedi follows
imports, classes and attributes to the one definition a call resolves to.
Edges are recomputed only for files that changed (and files whose edges
pointed into them).
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from pathlib import Path

from voxcode.models.code_chunk import CodeChunk

logger = logging.getLogger(__name__)

_CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
MAX_SECONDS = 120.0  # time budget per indexing run


class _ChunkLocator:
    def __init__(self, chunks: list[CodeChunk]):
        self.by_file: dict[str, list[CodeChunk]] = defaultdict(list)
        for chunk in chunks:
            if chunk.chunk_type in ("function", "method", "class"):
                self.by_file[chunk.file_path].append(chunk)

    def find(self, path: str, line: int) -> CodeChunk | None:
        """Innermost definition chunk containing `line`."""
        return min(
            (c for c in self.by_file.get(path, []) if c.start_line <= line <= c.end_line),
            key=lambda c: c.end_line - c.start_line,
            default=None,
        )


def build_call_edges(
    repo: Path,
    chunks: list[CodeChunk],
    changed_files: set[str],
    previous_edges: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    try:
        import jedi
    except ImportError:
        return {}

    chunk_ids = {c.chunk_id for c in chunks}
    previous_edges = previous_edges or {}
    owner = {c.chunk_id: c.file_path for c in chunks}

    # Keep edges of untouched files unless they point at chunks that vanished.
    edges: dict[str, list[str]] = {}
    stale_files: set[str] = set(changed_files)
    for caller, old_callees in previous_edges.items():
        if caller not in chunk_ids or owner.get(caller) in changed_files:
            continue
        if all(c in chunk_ids for c in old_callees):
            edges[caller] = old_callees
        else:
            stale_files.add(owner[caller])

    python_files = {c.file_path for c in chunks if c.language == "python"}
    targets = sorted(f for f in stale_files if f in python_files)
    if not targets:
        return edges

    locator = _ChunkLocator(chunks)
    # Resolve imports against the repository root (and a src/ layout).
    roots = [str(repo)] + ([str(repo / "src")] if (repo / "src").is_dir() else [])
    project = jedi.Project(str(repo), smart_sys_path=False, added_sys_path=roots)
    started = time.perf_counter()
    by_file: dict[str, list[CodeChunk]] = defaultdict(list)
    for chunk in chunks:
        if (
            chunk.file_path in targets
            and chunk.calls
            and chunk.chunk_type in ("function", "method", "module")
        ):
            by_file[chunk.file_path].append(chunk)

    for rel in targets:
        if time.perf_counter() - started > MAX_SECONDS:
            logger.warning("Precise call graph: time budget reached; remaining files use name matching")
            break
        path = repo / rel
        try:
            source = path.read_text(encoding="utf-8")
            script = jedi.Script(source, path=str(path), project=project)
        except Exception as exc:  # syntax errors, encoding, jedi internals
            logger.debug("jedi failed on %s: %s", rel, exc)
            continue
        for chunk in by_file.get(rel, []):
            callees: list[str] = []
            for offset, line_text in enumerate(chunk.content.splitlines()):
                line_no = chunk.start_line + offset
                for match in _CALL_RE.finditer(line_text):
                    try:
                        definitions = script.goto(line_no, match.start(1), follow_imports=True)
                    except Exception:
                        continue
                    for definition in definitions:
                        if definition.module_path is None or definition.line is None:
                            continue
                        try:
                            target_rel = Path(definition.module_path).resolve().relative_to(repo).as_posix()
                        except ValueError:
                            continue  # stdlib / site-packages
                        target = locator.find(target_rel, definition.line)
                        if target and target.chunk_id != chunk.chunk_id and target.chunk_id not in callees:
                            callees.append(target.chunk_id)
            if callees:
                edges[chunk.chunk_id] = callees
    return edges
