"""Lexical retrieval over a repository's chunks."""

from __future__ import annotations

import numpy as np

from voxcode.models.code_chunk import CodeChunk
from voxcode.models.retrieval_result import RetrievalResult
from voxcode.native import native
from voxcode.retrieval.bm25.tokenizer import tokenize


class BM25Index:
    """BM25 Okapi over token lists: Rust inverted index when available,
    rank_bm25 otherwise. Same scores either way."""

    def __init__(self, corpus: list[list[str]], prebuilt=None, size: int = 0):
        self.size = size or len(corpus)
        self._native = prebuilt
        self._python = None
        if prebuilt is not None:
            return
        accel = native()
        self._native = accel.Bm25Index(corpus) if accel is not None and corpus else None
        self._python = None
        if self._native is None and corpus:
            from rank_bm25 import BM25Okapi

            self._python = BM25Okapi(corpus)

    @classmethod
    def from_texts(cls, texts: list[str]) -> BM25Index:
        """Tokenize and index in one step (in parallel, inside Rust, when available)."""
        accel = native()
        if accel is not None and texts:
            return cls([], prebuilt=accel.Bm25Index.from_texts(texts), size=len(texts))
        return cls([tokenize(t) or ["_empty_"] for t in texts])

    def scores(self, tokens: list[str]) -> np.ndarray:
        if self._native is not None:
            return np.asarray(self._native.scores(tokens))
        if self._python is not None:
            return np.asarray(self._python.get_scores(tokens))
        return np.zeros(0)

    def top_k(self, tokens: list[str], k: int) -> list[tuple[int, float]]:
        """Best `k` positive-score documents, best first, ties in corpus order."""
        if self._native is not None:
            return [(int(i), float(s)) for i, s in self._native.top_k(tokens, k)]
        scores = self.scores(tokens)
        top = np.argsort(-scores, kind="stable")[:k]
        return [(int(i), float(scores[i])) for i in top if scores[i] > 0]


class BM25Retriever:
    def __init__(self, chunks: list[CodeChunk]):
        self.chunks = chunks
        self._index = BM25Index.from_texts([f"{c.symbol_name} {c.file_path} {c.content}" for c in chunks])

    def search(self, query: str, top_k: int = 10) -> list[RetrievalResult]:
        tokens = tokenize(query)
        if not self._index.size or not tokens:
            return []
        return [to_result(self.chunks[i], score, "bm25") for i, score in self._index.top_k(tokens, top_k)]


def to_result(chunk: CodeChunk, score: float, source: str) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk.chunk_id,
        file_path=chunk.file_path,
        symbol_name=chunk.symbol_name,
        content=chunk.content,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        language=chunk.language,
        chunk_type=chunk.chunk_type,
        score=score,
        source=source,
    )
