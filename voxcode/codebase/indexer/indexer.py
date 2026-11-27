"""Incremental repository indexing.

Only files whose content hash changed are re-parsed and re-embedded; deleted
files are removed from both the chunk index and the vector store.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from voxcode.codebase.chunker.chunker import build_chunks
from voxcode.codebase.git_history import read_commits
from voxcode.codebase.graph.precise import build_call_edges
from voxcode.codebase.indexer.index_store import (
    INDEX_FORMAT_VERSION,
    FileEntry,
    IndexStore,
    RepositoryIndex,
)
from voxcode.codebase.indexer.scanner import scan_repository
from voxcode.codebase.parser.languages import detect_language
from voxcode.config.settings import settings
from voxcode.core.repository import repository_key
from voxcode.retrieval.semantic.vector_store import VectorStore

logger = logging.getLogger(__name__)

_repo_locks: dict[str, threading.Lock] = {}
_repo_locks_guard = threading.Lock()


def _repo_lock(key: str) -> threading.Lock:
    with _repo_locks_guard:
        return _repo_locks.setdefault(key, threading.Lock())


@dataclass
class IndexStats:
    repo_key: str
    repository_path: str
    files_indexed: int
    files_changed: int
    files_removed: int
    chunks_created: int
    functions_found: int
    classes_found: int
    semantic_indexed: bool
    duration_seconds: float

    def to_dict(self) -> dict:
        return asdict(self)


class RepositoryIndexer:
    def __init__(self, store: IndexStore | None = None, vector_store_factory=VectorStore):
        self.store = store or IndexStore()
        self.vector_store_factory = vector_store_factory

    def index(
        self,
        repo_path: Path,
        embedder=None,
        full: bool = False,
        contextualizer=None,
        precise_graph: bool = True,
    ) -> IndexStats:
        """Index (incrementally) and save.

        precise_graph=False saves at once and leaves the slow Jedi / rust-analyzer
        pass to `complete_call_graph` (the server runs it in the background), so
        the first question about a repository doesn't wait for it.
        """
        started = time.perf_counter()
        repo_path = repo_path.resolve()
        key = repository_key(repo_path)

        with _repo_lock(key):
            previous = None if full else self.store.load(key)
            if previous and (
                previous.format_version != INDEX_FORMAT_VERSION
                or previous.chunk_max_lines != settings.MAX_CHUNK_LINES
                or previous.contextual_model != (contextualizer.provider.model if contextualizer else None)
            ):
                previous = None  # chunking changed: rebuild everything
            index = RepositoryIndex(
                repo_key=key,
                repo_path=str(repo_path),
                chunk_max_lines=settings.MAX_CHUNK_LINES,
                contextual_model=contextualizer.provider.model if contextualizer else None,
            )
            old_chunks = {c.chunk_id: c for c in previous.chunks} if previous else {}

            changed: list[str] = []
            new_chunks = []
            for path in scan_repository(repo_path, settings.MAX_FILE_BYTES):
                rel = path.relative_to(repo_path).as_posix()
                try:
                    raw = path.read_bytes()
                except OSError:
                    continue
                sha1 = hashlib.sha1(raw).hexdigest()
                old_entry = previous.files.get(rel) if previous else None

                if old_entry and old_entry.sha1 == sha1:
                    kept = [old_chunks[i] for i in old_entry.chunk_ids if i in old_chunks]
                    index.files[rel] = old_entry
                    index.chunks.extend(kept)
                    continue

                text = raw.decode("utf-8", errors="replace")
                chunks = build_chunks(rel, text, detect_language(rel) or "text", settings.MAX_CHUNK_LINES)
                if contextualizer is not None:
                    contextualizer.annotate(rel, text, chunks)
                index.files[rel] = FileEntry(sha1=sha1, chunk_ids=[c.chunk_id for c in chunks])
                index.chunks.extend(chunks)
                new_chunks.extend(chunks)
                changed.append(rel)

            removed = sorted(set(previous.files) - set(index.files)) if previous else []

            index.semantic_model = self._update_vectors(
                key, index, previous, embedder, new_chunks, changed, removed, full
            )
            if settings.PRECISE_CALL_GRAPH:
                stale = set(changed) | set(removed) | set(previous.graph_pending if previous else [])
                previous_edges = previous.call_edges if previous else None
                if precise_graph:
                    index.call_edges = build_call_edges(repo_path, index.chunks, stale, previous_edges)
                else:
                    index.call_edges = _surviving_edges(index.chunks, stale, previous_edges or {})
                    index.graph_pending = sorted(stale & set(index.files))
            index.commits = read_commits(repo_path, settings.GIT_HISTORY_COMMITS)
            self.store.save(index)

        stats = IndexStats(
            repo_key=key,
            repository_path=str(repo_path),
            files_indexed=len(index.files),
            files_changed=len(changed),
            files_removed=len(removed),
            chunks_created=len(index.chunks),
            functions_found=sum(c.chunk_type in ("function", "method") for c in index.chunks),
            classes_found=sum(c.chunk_type == "class" for c in index.chunks),
            semantic_indexed=index.semantic_model is not None,
            duration_seconds=round(time.perf_counter() - started, 3),
        )
        logger.info("Indexed %s: %s", repo_path, stats)
        return stats

    def _update_vectors(self, key, index, previous, embedder, new_chunks, changed, removed, full):
        if embedder is None:
            return None

        store = self.vector_store_factory(key)
        model = getattr(embedder, "model_name", "unknown")
        rebuild = full or previous is None or previous.semantic_model != model or not store.exists()

        if rebuild:
            store.drop()
            to_embed = index.chunks
        else:
            store.delete_files(changed + removed)
            to_embed = new_chunks

        if to_embed:
            vectors = embedder.embed_texts([c.embedding_text() for c in to_embed])
            store.ensure(vectors.shape[1])
            store.upsert([c.chunk_id for c in to_embed], [c.file_path for c in to_embed], vectors)
        return model


def _surviving_edges(chunks, stale: set[str], edges: dict[str, list[str]]) -> dict[str, list[str]]:
    """Previous precise edges that are still valid: neither end in a changed file."""
    file_of = {c.chunk_id: c.file_path for c in chunks}
    kept = {}
    for caller, callees in edges.items():
        if file_of.get(caller) in (None, *stale):
            continue
        kept[caller] = [c for c in callees if c in file_of and file_of[c] not in stale]
    return kept


def complete_call_graph(store, repo_key: str, lock=None) -> bool:
    """Compute the pending precise call edges and save them if the index hasn't changed meanwhile."""
    import contextlib

    index = store.load(repo_key)
    if index is None or not index.graph_pending:
        return False
    snapshot = {path: entry.sha1 for path, entry in index.files.items()}
    edges = build_call_edges(Path(index.repo_path), index.chunks, set(index.graph_pending), index.call_edges)
    with lock or contextlib.nullcontext():
        latest = store.load(repo_key)
        if latest is None or {p: e.sha1 for p, e in latest.files.items()} != snapshot:
            return False  # re-indexed meanwhile; that run schedules its own pass
        latest.call_edges = edges
        latest.graph_pending = []
        store.save(latest)
    return True
