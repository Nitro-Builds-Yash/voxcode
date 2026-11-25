"""Reciprocal Rank Fusion."""

from __future__ import annotations

from collections import defaultdict

from backend.models.retrieval_result import RetrievalResult


def reciprocal_rank_fusion(result_lists: list[list[RetrievalResult]], k: int = 60) -> list[RetrievalResult]:
    """Merge ranked lists; items found by several retrievers rise to the top."""
    scores: dict[str, float] = defaultdict(float)
    best: dict[str, RetrievalResult] = {}
    sources: dict[str, set[str]] = defaultdict(set)

    for results in result_lists:
        for rank, result in enumerate(results, start=1):
            scores[result.chunk_id] += 1.0 / (k + rank)
            best.setdefault(result.chunk_id, result)
            sources[result.chunk_id].add(result.source)

    ranked = sorted(scores, key=scores.__getitem__, reverse=True)
    return [
        best[cid].model_copy(
            update={
                "score": scores[cid],
                "source": "hybrid" if len(sources[cid]) > 1 else best[cid].source,
            }
        )
        for cid in ranked
    ]
