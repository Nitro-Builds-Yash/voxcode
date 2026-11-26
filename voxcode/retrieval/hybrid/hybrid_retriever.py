"""Hybrid search over one repository: BM25 + vectors, fused, reranked,
then expanded with call-graph neighbours."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from voxcode.codebase.graph.code_graph import CodeGraph
from voxcode.codebase.indexer.index_store import RepositoryIndex
from voxcode.config.settings import settings
from voxcode.models.retrieval_result import RetrievalResult
from voxcode.retrieval.bm25.bm25_retriever import BM25Retriever, to_result
from voxcode.retrieval.hybrid.fusion import reciprocal_rank_fusion
from voxcode.retrieval.semantic.semantic_retriever import SemanticRetriever
from voxcode.retrieval.semantic.vector_store import VectorStore

logger = logging.getLogger(__name__)

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="retrieval")


class HybridRetriever:
    def __init__(self, index: RepositoryIndex, embedder=None, vector_store: VectorStore | None = None):
        self.index = index
        self.chunks_by_id = {c.chunk_id: c for c in index.chunks}
        self.bm25 = BM25Retriever(index.chunks)
        self.graph = CodeGraph(index.chunks)
        self.semantic = None
        if embedder is not None and index.semantic_model:
            self.semantic = SemanticRetriever(
                embedder, vector_store or VectorStore(index.repo_key), self.chunks_by_id
            )

    def search(self, query: str, top_k: int | None = None) -> list[RetrievalResult]:
        """First-stage retrieval: BM25 and semantic search in parallel, fused."""
        top_k = top_k or settings.RETRIEVAL_CANDIDATES
        bm25_future = _pool.submit(self.bm25.search, query, top_k)
        semantic_results: list[RetrievalResult] = []
        if self.semantic is not None:
            try:
                semantic_results = self.semantic.search(query, top_k)
            except Exception as exc:
                logger.warning("Semantic search failed, using BM25 only: %s", exc)
        return reciprocal_rank_fusion([semantic_results, bm25_future.result()])[:top_k]

    def expand(self, results: list[RetrievalResult], limit: int | None = None) -> list[RetrievalResult]:
        """Append callers/callees of the top results that are not already present."""
        limit = settings.GRAPH_EXPANSION if limit is None else limit
        if limit <= 0:
            return results
        present = {r.chunk_id for r in results}
        extra: list[RetrievalResult] = []
        for result in results[:3]:
            for neighbor_id in self.graph.neighbors(result.chunk_id, limit):
                if neighbor_id not in present and len(extra) < limit:
                    present.add(neighbor_id)
                    extra.append(to_result(self.chunks_by_id[neighbor_id], 0.0, "graph"))
        return results + extra
