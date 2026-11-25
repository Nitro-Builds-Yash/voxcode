"""Per-repository index on disk: file hashes plus all chunks (JSON)."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, Field

from backend.config.paths import INDEXES_DIR
from backend.models.code_chunk import CodeChunk


class FileEntry(BaseModel):
    sha1: str
    chunk_ids: list[str] = Field(default_factory=list)


class RepositoryIndex(BaseModel):
    repo_key: str
    repo_path: str
    files: dict[str, FileEntry] = Field(default_factory=dict)
    chunks: list[CodeChunk] = Field(default_factory=list)
    semantic_model: str | None = None  # embedding model used for vectors, if any
    updated_at: float = 0.0


class IndexStore:
    def __init__(self, root: Path | None = None):
        self.root = root or INDEXES_DIR

    def path(self, repo_key: str) -> Path:
        return self.root / f"{repo_key}.json"

    def load(self, repo_key: str) -> RepositoryIndex | None:
        path = self.path(repo_key)
        if not path.exists():
            return None
        return RepositoryIndex.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, index: RepositoryIndex) -> None:
        index.updated_at = time.time()
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(index.model_dump_json())
        os.replace(tmp, self.path(index.repo_key))

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        repos = []
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            repos.append(
                {
                    "repo_key": data.get("repo_key"),
                    "repo_path": data.get("repo_path"),
                    "files": len(data.get("files", {})),
                    "chunks": len(data.get("chunks", [])),
                    "semantic": bool(data.get("semantic_model")),
                    "updated_at": data.get("updated_at"),
                }
            )
        return repos
