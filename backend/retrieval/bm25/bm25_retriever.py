"""Lexical retrieval over a repository's chunks."""

from __future__ import annotations

import numpy as np
from rank_bm25 import BM25Okapi

from backend.models.code_chunk import CodeChunk
from backend.models.retrieval_result import RetrievalResult
from backend.retrieval.bm25.tokenizer import tokenize


class BM25Retriever:
    def __init__(self, chunks: list[CodeChunk]):
        self.chunks = chunks
        corpus = [tokenize(f"{c.symbol_name} {c.file_path} {c.content}") or ["_empty_"] for c in chunks]
        self._index = BM25Okapi(corpus) if corpus else None

    def search(self, query: str, top_k: int = 10) -> list[RetrievalResult]:
        tokens = tokenize(query)
        if self._index is None or not tokens:
            return []
        scores = np.asarray(self._index.get_scores(tokens))
        top = np.argsort(-scores)[:top_k]
        return [to_result(self.chunks[i], float(scores[i]), "bm25") for i in top if scores[i] > 0]


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
