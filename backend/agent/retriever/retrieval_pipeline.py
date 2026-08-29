from __future__ import annotations
from typing import List, Optional, Dict, Any
from backend.models.retrieval_result import RetrievalResult
from backend.retrieval.hybrid.hybrid_retriever import HybridRetriever
from backend.retrieval.rerank.reranker import CrossEncoderReranker


class RetrievalPipeline:
    """
    End-to-end retrieval and reranking pipeline for coding agent workflows.
    """

    def __init__(
        self,
        repository_name: str,
        retriever: Optional[HybridRetriever] = None,
        reranker: Optional[CrossEncoderReranker] = None,
    ):
        self.repository_name = repository_name
        self.retriever = retriever or HybridRetriever(repository_name)
        self.reranker = reranker

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        rerank_top_n: Optional[int] = None,
    ) -> List[RetrievalResult]:
        candidate_count = top_k * 2 if self.reranker else top_k
        raw_results = self.retriever.search(query=query, top_k=candidate_count)

        if not self.reranker or not raw_results:
            return raw_results[:top_k]

        reranked = self.reranker.rerank(
            query=query,
            results=raw_results,
            top_k=rerank_top_n or top_k,
        )
        return reranked
