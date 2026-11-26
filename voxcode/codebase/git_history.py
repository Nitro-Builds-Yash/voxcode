"""Commit history for "why / when / who changed this" questions."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from voxcode.retrieval.bm25.tokenizer import tokenize

_SEP = "\x1e"  # record separator between commits
_FIELD = "\x1f"
_HISTORY_INTENT = re.compile(
    r"\b(why|when|who|history|commit|commits|changed|change log|changelog|introduced|regress\w*|blame|last (?:week|month)|recently)\b",
    re.I,
)


class Commit(BaseModel):
    sha: str
    author: str
    date: str  # ISO 8601
    subject: str
    body: str = ""
    files: list[str] = Field(default_factory=list)

    def render(self) -> str:
        files = ", ".join(self.files[:8]) + (f" (+{len(self.files) - 8} more)" if len(self.files) > 8 else "")
        body = f"\n    {self.body.strip()[:400]}" if self.body.strip() else ""
        return f"{self.sha[:10]} {self.date[:10]} {self.author}: {self.subject}{body}\n    files: {files}"


def read_commits(repo: Path, limit: int) -> list[Commit]:
    """Most recent `limit` commits (empty if not a git repo or git missing)."""
    if limit <= 0 or not (repo / ".git").exists():
        return []
    fmt = _SEP + _FIELD.join(["%H", "%an", "%aI", "%s", "%b"]) + _FIELD
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", f"-n{limit}", "--no-merges", "--name-only", f"--format={fmt}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    commits = []
    for record in out.split(_SEP)[1:]:
        parts = record.split(_FIELD)
        if len(parts) < 6:
            continue
        sha, author, date, subject, body, rest = parts[:6]
        files = [line.strip() for line in rest.splitlines() if line.strip()]
        commits.append(Commit(sha=sha, author=author, date=date, subject=subject, body=body, files=files))
    return commits


def has_history_intent(query: str) -> bool:
    return bool(_HISTORY_INTENT.search(query))


class CommitSearch:
    def __init__(self, commits: list[Commit]):
        self.commits = commits
        self._index = None
        if commits:
            from voxcode.retrieval.bm25.bm25_retriever import BM25Index

            self._index = BM25Index(
                [tokenize(f"{c.subject} {c.body} {' '.join(c.files)}") or ["_"] for c in commits]
            )

    def search(self, query: str = "", path: str = "", limit: int = 10) -> list[Commit]:
        candidates = list(range(len(self.commits)))
        if path:
            candidates = [
                i
                for i in candidates
                if any(
                    f == path or f.startswith(path.rstrip("/") + "/") or f.endswith("/" + path)
                    for f in self.commits[i].files
                )
            ]
        tokens = tokenize(query) if query else []
        if tokens and self._index is not None and candidates:
            scores = self._index.scores(tokens)
            ranked = sorted(candidates, key=lambda i: (-scores[i], i))
            matched = [i for i in ranked if scores[i] > 0]
            candidates = matched or ranked
        return [self.commits[i] for i in candidates[:limit]]
