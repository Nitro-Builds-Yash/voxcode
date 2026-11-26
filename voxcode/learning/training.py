"""Train the learned ranker for one repository.

Training data needs no labelling:

* **Self-supervised (inverse cloze task).** Each documented function's
  docstring summary becomes a query and the function is the answer. The
  docstrings are removed from the search index while training, so a query
  can't simply match its own text; the ranker has to learn from names,
  paths, signatures and retrieval agreement, as with a real question.
* **Feedback.** Accepted patches and "helpful" votes (weighted 3x).

The model is saved only if it beats the plain retrieval order on held-out
queries, so training can never make search worse on that data.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from voxcode.codebase.graph.code_graph import CodeGraph
from voxcode.codebase.indexer.index_store import RepositoryIndex
from voxcode.config import paths
from voxcode.learning import feedback
from voxcode.learning.features import FEATURE_NAMES, CandidateLists, featurize
from voxcode.learning.ranker import LinearRanker, QueryGroup, ranking_metrics
from voxcode.models.code_chunk import CodeChunk
from voxcode.retrieval.bm25.bm25_retriever import BM25Retriever

logger = logging.getLogger(__name__)

CANDIDATES = 30
_PY_DOC = re.compile(
    r'^(\s*(?:async\s+)?(?:def|class)\s[^\n]*\n)(\s*)(?:[rRbBuU]{0,2})("""|\'\'\')(.*?)\3', re.S
)
_BLOCK_DOC = re.compile(r"/\*\*(.*?)\*/", re.S)


def ranker_path(repo_key: str) -> Path:
    return paths.LEARNING_DIR / "rankers" / f"{repo_key}.json"


def split_docstring(chunk: CodeChunk) -> tuple[str, str] | None:
    """(summary sentence, content without the docstring), or None."""
    if chunk.chunk_type not in ("function", "method", "class"):
        return None
    content = chunk.content
    match = _PY_DOC.match(content)
    if match:
        doc = match.group(4)
        stripped = content[: match.start(3)] + content[match.end() :]
    else:
        block = _BLOCK_DOC.search("\n".join(content.splitlines()[:12]))
        if block is None:
            return None
        doc = re.sub(r"^\s*\*\s?", "", block.group(1), flags=re.M)
        stripped = content.replace(block.group(0), "", 1)
    summary = re.split(r"(?<=[.!?])\s|\n\s*\n", " ".join(doc.split("\n\n")[0].split()).strip())[0]
    summary = summary.strip().rstrip(".")
    words = summary.split()
    if not 4 <= len(words) <= 30 or summary.startswith((":", "@", "Args", "Returns")):
        return None
    return summary, stripped


@dataclass
class Example:
    query: str
    answer_ids: set[str]
    answer_files: set[str]
    weight: float
    holdout: bool


def _holdout(key: str, fraction: float = 0.2) -> bool:
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % 1000 < fraction * 1000


def self_supervised(index: RepositoryIndex) -> tuple[list[Example], list[CodeChunk]]:
    """Docstring queries + a copy of the chunks with docstrings removed."""
    examples, stripped_chunks = [], []
    for chunk in index.chunks:
        parts = split_docstring(chunk)
        if parts is None:
            stripped_chunks.append(chunk)
            continue
        summary, stripped = parts
        stripped_chunks.append(chunk.model_copy(update={"content": stripped}))
        examples.append(Example(summary, {chunk.chunk_id}, set(), 1.0, _holdout(chunk.chunk_id)))
    return examples, stripped_chunks


def from_feedback(repo_key: str) -> list[Example]:
    return [
        Example(e["query"], set(e.get("positive_chunks", [])), set(e.get("positive_files", [])), 3.0, False)
        for e in feedback.load(repo_key)
    ]


def build_groups(examples: list[Example], search, callers) -> tuple[list[QueryGroup], list[QueryGroup]]:
    train: list[QueryGroup] = []
    held: list[QueryGroup] = []
    for ex in examples:
        lists = search(ex.query)
        if not lists.fused:
            continue
        relevant = np.array(
            [r.chunk_id in ex.answer_ids or r.file_path in ex.answer_files for r in lists.fused], dtype=bool
        )
        if not relevant.any():
            continue  # the answer was not retrieved at all: nothing to re-rank
        group = QueryGroup(featurize(ex.query, lists, callers), relevant, ex.weight)
        (held if ex.holdout else train).append(group)
    return train, held


def bm25_lists(retriever: BM25Retriever, k: int = CANDIDATES):
    def search(query: str) -> CandidateLists:
        results = retriever.search(query, k)
        fused = [r.model_copy(update={"score": 1.0 / (60 + i)}) for i, r in enumerate(results, 1)]
        return CandidateLists(results, [], fused)

    return search


def caller_counts(index: RepositoryIndex) -> dict[str, int]:
    graph = CodeGraph(index.chunks, index.call_edges)
    return {c.chunk_id: len(graph.callers_of(c.chunk_id)) for c in index.chunks}


def train(index: RepositoryIndex, hybrid_search=None, save: bool = True, min_feedback: int = 0) -> dict:
    """Train, validate on held-out queries, and save if it helps.

    `hybrid_search(query) -> CandidateLists` is used for feedback queries
    (the live index, docstrings included); defaults to BM25 over it.
    """
    started = time.perf_counter()
    callers = caller_counts(index)
    examples, stripped = self_supervised(index)
    train_groups, held_groups = build_groups(examples, bm25_lists(BM25Retriever(stripped)), callers)
    fb = from_feedback(index.repo_key)
    if fb:
        fb_train, _ = build_groups(fb, hybrid_search or bm25_lists(BM25Retriever(index.chunks)), callers)
        train_groups += fb_train
    report: dict = {
        "repo_key": index.repo_key,
        "docstring_queries": len(examples),
        "feedback_queries": len(fb),
        "train_groups": len(train_groups),
        "heldout_groups": len(held_groups),
        "saved": False,
    }
    if len(fb) < min_feedback:
        report["reason"] = f"waiting for feedback ({len(fb)}/{min_feedback} entries)"
        return report
    if len(train_groups) < 10:
        report["reason"] = "not enough training queries (need 10 documented functions or feedback entries)"
        return report
    ranker = LinearRanker().fit(train_groups)
    if held_groups:
        base = ranking_metrics(held_groups, [-np.arange(len(g.relevant), dtype=float) for g in held_groups])
        learned = ranking_metrics(held_groups, [ranker.score(g.features) for g in held_groups])
    else:  # tiny repos: validate on the training data
        base = ranking_metrics(train_groups, [-np.arange(len(g.relevant), dtype=float) for g in train_groups])
        learned = ranking_metrics(train_groups, [ranker.score(g.features) for g in train_groups])
    report.update(
        {
            "heldout_before": {k: round(v, 3) for k, v in base.items()},
            "heldout_after": {k: round(v, 3) for k, v in learned.items()},
            "weights": ranker.weights(),
            "seconds": round(time.perf_counter() - started, 2),
        }
    )
    ranker.meta = {
        k: report[k] for k in ("docstring_queries", "feedback_queries", "heldout_before", "heldout_after")
    }
    ranker.meta["trained_at"] = time.time()
    if learned["mrr@10"] >= base["mrr@10"]:
        if save:
            ranker.save(ranker_path(index.repo_key))
        report["saved"] = save
    else:
        report["reason"] = "did not beat the retrieval order on held-out queries; keeping the old ranking"
    report["features"] = FEATURE_NAMES
    return report


_training_lock = __import__("threading").Lock()


def auto_train(index: RepositoryIndex) -> dict | None:
    """Background-safe retraining used after indexing and after feedback."""
    from voxcode.config.settings import settings

    if not (settings.LEARNED_RANKER and settings.RANKER_AUTO_TRAIN):
        return None
    if not _training_lock.acquire(blocking=False):
        return None  # already training this process
    try:
        report = train(index, min_feedback=settings.RANKER_MIN_FEEDBACK)
        if report.get("saved"):
            logger.info("Learned ranker retrained for %s: %s", index.repo_key, report.get("heldout_after"))
        return report
    except Exception as exc:  # never break indexing or a feedback call
        logger.warning("Ranker training failed: %s", exc)
        return None
    finally:
        _training_lock.release()


def load_ranker(repo_key: str) -> LinearRanker | None:
    path = ranker_path(repo_key)
    return LinearRanker.load(path) if path.exists() else None
