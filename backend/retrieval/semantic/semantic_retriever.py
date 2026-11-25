"""Dense-vector retrieval."""

from __future__ import annotations

from backend.models.code_chunk import CodeChunk
from backend.models.retrieval_result import RetrievalResult
from backend.retrieval.bm25.bm25_retriever import to_result
from backend.retrieval.semantic.vector_store import VectorStore


class SemanticRetriever:
    def __init__(self, embedder, store: VectorStore, chunks_by_id: dict[str, CodeChunk]):
        self.embedder = embedder
        self.store = store
        self.chunks_by_id = chunks_by_id

    def search(self, query: str, top_k: int = 10) -> list[RetrievalResult]:
        if self.embedder is None:
            return []
        hits = self.store.search(self.embedder.embed_query(query), top_k)
        return [
            to_result(self.chunks_by_id[chunk_id], score, "semantic")
            for chunk_id, score in hits
            if chunk_id in self.chunks_by_id
        ]
