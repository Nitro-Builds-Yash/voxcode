"""Does the learned ranker help? Leave-one-out over the handwritten queries.

    voxcode eval --learning [--repo PATH]

Each benchmark question is treated as one piece of user feedback. For each
question the ranker is trained on the other questions (plus, in the second
variant, the repository's docstring queries) and tested on the held-out one,
so no question is ever ranked by a model that saw it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from voxcode.codebase.indexer.indexer import RepositoryIndexer
from voxcode.evaluation.retrieval_eval import DEFAULT_QUERIES, _hit
from voxcode.learning import training
from voxcode.learning.features import featurize
from voxcode.learning.ranker import LinearRanker, QueryGroup
from voxcode.retrieval.bm25.bm25_retriever import BM25Retriever


def _rank(group: QueryGroup, scores: np.ndarray) -> int | None:
    hits = np.flatnonzero(group.relevant[np.argsort(-scores, kind="stable")])
    return int(hits[0]) + 1 if hits.size else None


def _summary(ranks: list[int | None]) -> dict[str, float]:
    n = len(ranks)
    return {
        "recall@5": round(sum(1 for r in ranks if r and r <= 5) / n, 3),
        "mrr@10": round(sum(1 / r for r in ranks if r and r <= 10) / n, 3),
    }


def run(repo: Path, queries: Path = DEFAULT_QUERIES) -> dict[str, dict[str, float]]:
    indexer = RepositoryIndexer()
    stats = indexer.index(repo)
    index = indexer.store.load(stats.repo_key)
    assert index is not None
    cases = json.loads(queries.read_text())
    callers = training.caller_counts(index)
    search = training.bm25_lists(BM25Retriever(index.chunks))
    groups = []
    for case in cases:
        lists = search(case["query"])
        relevant = np.array([_hit(r, case) for r in lists.fused], dtype=bool)
        groups.append(QueryGroup(featurize(case["query"], lists, callers), relevant, 3.0))
    examples, stripped = training.self_supervised(index)
    ss_train, ss_held = training.build_groups(examples, training.bm25_lists(BM25Retriever(stripped)), callers)
    docstrings = ss_train + ss_held

    results = {
        "retrieval order (no learning)": _summary(
            [_rank(g, -np.arange(len(g.relevant), dtype=float)) for g in groups]
        )
    }
    for label, extra in (("learned from feedback", []), ("learned from feedback + docstrings", docstrings)):
        ranks: list[int | None] = []
        for i, group in enumerate(groups):
            if not group.relevant.any():
                ranks.append(None)
                continue
            train = [g for j, g in enumerate(groups) if j != i and g.relevant.any()] + extra
            ranks.append(_rank(group, LinearRanker().fit(train).score(group.features)))
        results[label] = _summary(ranks)
    return results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", type=Path)
    parser.add_argument("--queries", default=DEFAULT_QUERIES, type=Path)
    args = parser.parse_args(argv)
    results = run(args.repo, args.queries)
    print("Leave-one-out over the benchmark questions (BM25 candidates)\n")
    print(f"| {'ranking':<38} | recall@5 | MRR@10 |")
    print(f"|{'-' * 40}|----------|--------|")
    for name, m in results.items():
        print(f"| {name:<38} | {m['recall@5']:>8} | {m['mrr@10']:>6} |")


if __name__ == "__main__":
    main()
