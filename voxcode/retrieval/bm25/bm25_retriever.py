"""Lexical retrieval over a repository's chunks."""

from __future__ import annotations

import math

import numpy as np

from voxcode.models.code_chunk import CodeChunk
from voxcode.models.retrieval_result import RetrievalResult
from voxcode.native import native
from voxcode.retrieval.bm25.tokenizer import tokenize


class Okapi:
    """BM25 with Lucene's IDF, ln(1 + (N - df + 0.5) / (df + 0.5)), which is never negative.

    The classic Okapi IDF (used by rank_bm25) goes negative for a word in more
    than half the documents, so in small repositories, or for a name used across
    many files, a perfect match scored below zero and search returned nothing.
    """

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.size = len(corpus)
        self.postings: dict[str, list[tuple[int, float]]] = {}
        for doc, tokens in enumerate(corpus):
            counts: dict[str, float] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0.0) + 1.0
            for token, count in counts.items():
                self.postings.setdefault(token, []).append((doc, count))
        avgdl = sum(len(d) for d in corpus) / self.size if self.size else 0.0
        self.norm = [k1 * (1.0 - b + b * len(d) / avgdl) if avgdl else k1 for d in corpus]
        self.idf = {
            t: math.log(1.0 + (self.size - len(docs) + 0.5) / (len(docs) + 0.5))
            for t, docs in self.postings.items()
        }

    def get_scores(self, query: list[str]) -> np.ndarray:
        scores = np.zeros(self.size)
        for token in query:
            docs = self.postings.get(token)
            if not docs:
                continue
            idf = self.idf[token]
            for doc, tf in docs:
                scores[doc] += idf * (tf * (self.k1 + 1.0) / (tf + self.norm[doc]))
        return scores


class BM25Index:
    """BM25 over token lists: Rust inverted index when available, Python
    otherwise. Same scores either way."""

    def __init__(self, corpus: list[list[str]], prebuilt=None, size: int = 0):
        self.size = size or len(corpus)
        self._native = prebuilt
        self._python: Okapi | None = None
        if prebuilt is not None:
            return
        accel = native()
        self._native = accel.Bm25Index(corpus) if accel is not None and corpus else None
        if self._native is None and corpus:
            self._python = Okapi(corpus)

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
            return self._python.get_scores(tokens)
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
        self._index = BM25Index.from_texts(
            [f"{c.symbol_name} {c.file_path} {c.context} {c.content}" for c in chunks]
        )

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
