"""Per-repository search runtime, cached between requests.

Loading an index and building BM25 / graph structures is the expensive part
of a request, so it happens once per index version instead of per call.
"""

from __future__ import annotations

import threading

from voxcode.codebase.indexer.index_store import IndexStore
from voxcode.core.model_registry import ModelRegistry
from voxcode.retrieval.hybrid.hybrid_retriever import HybridRetriever


class RepositoryNotIndexedError(LookupError):
    pass


class RuntimeCache:
    def __init__(self, store: IndexStore | None = None, registry: ModelRegistry | None = None):
        self.store = store or IndexStore()
        self.registry = registry or ModelRegistry()
        self._cache: dict[str, tuple[float, HybridRetriever]] = {}
        self._lock = threading.Lock()

    def retriever(self, repo_key: str) -> HybridRetriever:
        index_path = self.store.path(repo_key)
        if not index_path.exists():
            raise RepositoryNotIndexedError(
                f"Repository '{repo_key}' is not indexed yet. Run indexing first."
            )
        mtime = index_path.stat().st_mtime
        with self._lock:
            cached = self._cache.get(repo_key)
            if cached and cached[0] == mtime:
                return cached[1]
        index = self.store.load(repo_key)
        if index is None:
            raise RepositoryNotIndexedError(f"Repository '{repo_key}' is not indexed yet.")
        embedder = self.registry.get_embedder() if index.semantic_model else None
        retriever = HybridRetriever(index, embedder)
        with self._lock:
            self._cache[repo_key] = (mtime, retriever)
        return retriever

    def invalidate(self, repo_key: str) -> None:
        with self._lock:
            self._cache.pop(repo_key, None)


runtime_cache = RuntimeCache()
