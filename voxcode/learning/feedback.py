"""Relevance feedback: which code actually answered a question.

Every accepted patch and every "this answer helped" vote is appended to a
per-repository JSONL log; `voxcode train-ranker` turns it into training
pairs for the learned ranker.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from voxcode.config import paths

_lock = threading.Lock()


def feedback_path(repo_key: str) -> Path:
    return paths.LEARNING_DIR / "feedback" / f"{repo_key}.jsonl"


def record(
    repo_key: str,
    query: str,
    *,
    positive_files: list[str] | None = None,
    positive_chunks: list[str] | None = None,
    kind: str = "vote",
) -> bool:
    query = query.strip()
    if not query or not (positive_files or positive_chunks):
        return False
    entry = {
        "ts": time.time(),
        "kind": kind,
        "query": query,
        "positive_files": sorted(set(positive_files or [])),
        "positive_chunks": sorted(set(positive_chunks or [])),
    }
    path = feedback_path(repo_key)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    return True


def load(repo_key: str) -> list[dict]:
    path = feedback_path(repo_key)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries
