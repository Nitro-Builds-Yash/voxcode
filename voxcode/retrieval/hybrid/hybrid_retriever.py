"""Hybrid search over one repository: BM25 + vectors, fused, reranked,
then expanded with call-graph neighbours."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from voxcode.codebase.git_history import CommitSearch
from voxcode.codebase.graph.code_graph import CodeGraph
from voxcode.codebase.indexer.index_store import RepositoryIndex
from voxcode.config.settings import settings
from voxcode.learning.features import CandidateLists, featurize
from voxcode.models.retrieval_result import RetrievalResult
from voxcode.retrieval.bm25.bm25_retriever import BM25Retriever, to_result
from voxcode.retrieval.hybrid.fusion import reciprocal_rank_fusion
from voxcode.retrieval.semantic.local_lsa import LocalSemanticRetriever
from voxcode.retrieval.semantic.semantic_retriever import SemanticRetriever
from voxcode.retrieval.semantic.vector_store import VectorStore

logger = logging.getLogger(__name__)

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="retrieval")


class HybridRetriever:
    def __init__(self, index: RepositoryIndex, embedder=None, vector_store: VectorStore | None = None):
        self.index = index
        self.chunks_by_id = {c.chunk_id: c for c in index.chunks}
        self.bm25 = BM25Retriever(index.chunks)
        self.graph = CodeGraph(index.chunks, index.call_edges)
        self.commits = CommitSearch(index.commits)
        self.ranker = None
        if settings.LEARNED_RANKER:
            from voxcode.learning.training import load_ranker

            self.ranker = load_ranker(index.repo_key)
        self._callers: dict[str, int] | None = None
        self.semantic: SemanticRetriever | LocalSemanticRetriever | None = None
        if embedder is not None and index.semantic_model:
            self.semantic = SemanticRetriever(
                embedder, vector_store or VectorStore(index.repo_key), self.chunks_by_id
            )
        elif settings.LOCAL_SEMANTIC and index.chunks:
            # No neural embedder installed: a semantic model trained on this repo.
            from voxcode.retrieval.semantic.local_lsa import model_for

            model = model_for(index.repo_key, index.chunks)
            if model is not None:
                self.semantic = LocalSemanticRetriever(model, index.chunks)

    def candidate_lists(
        self, query: str, top_k: int | None = None, extra_queries: list[str] | None = None
    ) -> CandidateLists:
        """BM25 and semantic lists for `query` (searched in parallel) and their RRF fusion.

        `extra_queries` (e.g. LLM rewrites) are searched too and fused in,
        with the original query's lists weighted first.
        """
        top_k = top_k or settings.RETRIEVAL_CANDIDATES
        lists: list[list[RetrievalResult]] = []
        for q in [query, *(extra_queries or [])]:
            bm25_future = _pool.submit(self.bm25.search, q, top_k)
            semantic_results: list[RetrievalResult] = []
            if self.semantic is not None:
                try:
                    semantic_results = self.semantic.search(q, top_k)
                except Exception as exc:
                    logger.warning("Semantic search failed, using BM25 only: %s", exc)
            lists += [semantic_results, bm25_future.result()]
        return CandidateLists(bm25=lists[1], semantic=lists[0], fused=reciprocal_rank_fusion(lists)[:top_k])

    def search(
        self, query: str, top_k: int | None = None, extra_queries: list[str] | None = None
    ) -> list[RetrievalResult]:
        """First-stage retrieval, re-scored by the learned ranker when one is trained."""
        lists = self.candidate_lists(query, top_k, extra_queries)
        if self.ranker is None or len(lists.fused) < 2:
            return lists.fused
        return self.rescore(query, lists)

    def rescore(self, query: str, lists: CandidateLists) -> list[RetrievalResult]:
        if self._callers is None:
            self._callers = {c.chunk_id: len(self.graph.callers_of(c.chunk_id)) for c in self.index.chunks}
        assert self.ranker is not None
        scores = self.ranker.score(featurize(query, lists, self._callers))
        order = np.argsort(-scores, kind="stable")
        return [lists.fused[i].model_copy(update={"score": float(scores[i])}) for i in order]

    def history(self, query: str, limit: int = 8) -> str:
        """Relevant commits rendered for the prompt (empty if none)."""
        commits = self.commits.search(query, limit=limit)
        return "\n".join(c.render() for c in commits)

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
