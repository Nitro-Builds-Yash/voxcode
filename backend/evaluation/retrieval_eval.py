"""Retrieval quality benchmark: recall@k and MRR for each search mode.

    python -m backend.evaluation.retrieval_eval [--repo PATH] [--queries FILE]

A query counts as a hit at rank r if the r-th result is in the expected file
(and, when given, its symbol name contains the expected symbol).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from backend.codebase.indexer.indexer import RepositoryIndexer
from backend.core.model_registry import ModelRegistry
from backend.models.retrieval_result import RetrievalResult
from backend.retrieval.bm25.bm25_retriever import to_result
from backend.retrieval.hybrid.hybrid_retriever import HybridRetriever

DEFAULT_QUERIES = Path(__file__).with_name("retrieval_queries.json")


def _hit(result: RetrievalResult, case: dict) -> bool:
    if result.file_path != case["file"]:
        return False
    return "symbol" not in case or case["symbol"] in result.symbol_name


def evaluate(search, cases: list[dict], k: int = 5) -> dict:
    recalls, reciprocal_ranks, latencies = [], [], []
    for case in cases:
        start = time.perf_counter()
        results = search(case["query"])[:10]
        latencies.append((time.perf_counter() - start) * 1000)
        rank = next((i for i, r in enumerate(results, 1) if _hit(r, case)), None)
        recalls.append(1.0 if rank and rank <= k else 0.0)
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
    return {
        f"recall@{k}": round(float(np.mean(recalls)), 3),
        "mrr@10": round(float(np.mean(reciprocal_ranks)), 3),
        "p50_ms": round(float(np.median(latencies)), 1),
    }


def whitespace_bm25(chunks):
    """The original tokenizer (lowercase + split on spaces), for comparison."""
    index = BM25Okapi([f"{c.symbol_name} {c.content}".lower().split() or ["_"] for c in chunks])

    def search(query: str):
        scores = np.asarray(index.get_scores(query.lower().split()))
        return [
            to_result(chunks[i], float(scores[i]), "bm25") for i in np.argsort(-scores)[:10] if scores[i] > 0
        ]

    return search


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", type=Path)
    parser.add_argument("--queries", default=DEFAULT_QUERIES, type=Path)
    args = parser.parse_args()

    cases = json.loads(args.queries.read_text())
    registry = ModelRegistry()
    embedder = registry.get_embedder()
    indexer = RepositoryIndexer()
    stats = indexer.index(args.repo, embedder=embedder)
    index = indexer.store.load(stats.repo_key)
    assert index is not None
    retriever = HybridRetriever(index, embedder)

    modes = {
        "bm25 (whitespace tokens, old)": whitespace_bm25(index.chunks),
        "bm25 (code-aware tokens)": lambda q: retriever.bm25.search(q, 10),
    }
    if retriever.semantic is not None:
        modes["semantic"] = lambda q: retriever.semantic.search(q, 10)
        modes["hybrid (rrf)"] = lambda q: retriever.search(q, 30)
    reranker = registry.get_reranker()
    if reranker is not None:
        modes["hybrid + rerank"] = lambda q: reranker.rerank(q, retriever.search(q, 30), top_k=10)

    print(f"{len(cases)} queries over {stats.chunks_created} chunks in {args.repo.resolve()}\n")
    print(f"| {'mode':<32} | recall@5 | MRR@10 | p50 latency |")
    print(f"|{'-' * 34}|----------|--------|-------------|")
    for name, search in modes.items():
        m = evaluate(search, cases)
        print(f"| {name:<32} | {m['recall@5']:>8} | {m['mrr@10']:>6} | {m['p50_ms']:>8} ms |")
    if retriever.semantic is None:
        print("\n(semantic / hybrid modes skipped: embedding model unavailable)")


if __name__ == "__main__":
    main()
