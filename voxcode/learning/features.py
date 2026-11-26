"""Features for learning-to-rank over first-stage retrieval candidates.

Each candidate (query, chunk) becomes a small vector describing how each
retriever ranked it and how its name, path and shape relate to the query.
The learned ranker combines them instead of the fixed RRF formula.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from voxcode.models.retrieval_result import RetrievalResult
from voxcode.retrieval.bm25.tokenizer import tokenize

FEATURE_NAMES = [
    "bm25_score_rel",  # score / best score in the list
    "bm25_rrank",  # 1 / rank (0 if not retrieved)
    "semantic_score",
    "semantic_rrank",
    "both_retrievers",
    "rrf_score_rel",
    "symbol_overlap",  # share of query tokens in the symbol name
    "path_overlap",  # share of query tokens in the file path
    "symbol_mentioned",  # the symbol's own name appears in the query
    "head_overlap",  # share of query tokens in the first 3 lines (signature, docstring start)
    # No chunk-type flags: self-supervised positives are always documented
    # definitions, so such a flag would learn the data's bias, not relevance.
    "is_test",
    "log_lines",
    "log_callers",
]


@dataclass
class CandidateLists:
    """First-stage lists for one query, before learned re-scoring."""

    bm25: list[RetrievalResult]
    semantic: list[RetrievalResult]
    fused: list[RetrievalResult]


def _overlap(query_tokens: set[str], text: str) -> float:
    if not query_tokens:
        return 0.0
    return len(query_tokens & set(tokenize(text))) / len(query_tokens)


def featurize(query: str, lists: CandidateLists, callers: dict[str, int] | None = None) -> np.ndarray:
    """One row per candidate in `lists.fused` (same order)."""
    query_tokens = set(tokenize(query))
    query_lower = query.lower()
    bm25_rank = {r.chunk_id: i for i, r in enumerate(lists.bm25, 1)}
    bm25_score = {r.chunk_id: r.score for r in lists.bm25}
    best_bm25 = max((r.score for r in lists.bm25), default=0.0) or 1.0
    sem_rank = {r.chunk_id: i for i, r in enumerate(lists.semantic, 1)}
    sem_score = {r.chunk_id: r.score for r in lists.semantic}
    best_rrf = max((r.score for r in lists.fused), default=0.0) or 1.0
    callers = callers or {}

    rows = []
    for r in lists.fused:
        name = r.symbol_name.split(" (part")[0]
        short = name.rsplit(".", 1)[-1]
        head = "\n".join(r.content.splitlines()[:3])
        lines = max(1, r.end_line - r.start_line + 1)
        path = r.file_path.lower()
        rows.append(
            [
                bm25_score.get(r.chunk_id, 0.0) / best_bm25,
                1.0 / bm25_rank[r.chunk_id] if r.chunk_id in bm25_rank else 0.0,
                sem_score.get(r.chunk_id, 0.0),
                1.0 / sem_rank[r.chunk_id] if r.chunk_id in sem_rank else 0.0,
                float(r.chunk_id in bm25_rank and r.chunk_id in sem_rank),
                r.score / best_rrf,
                _overlap(query_tokens, name),
                _overlap(query_tokens, r.file_path),
                float(len(short) > 2 and short.lower() in query_lower),
                _overlap(query_tokens, head),
                float("test" in path.rsplit("/", 1)[-1] or "/tests/" in f"/{path}"),
                math.log1p(lines),
                math.log1p(callers.get(r.chunk_id, 0)),
            ]
        )
    return np.asarray(rows, dtype=np.float64).reshape(len(rows), len(FEATURE_NAMES))
