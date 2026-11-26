import os
import tempfile

# Keep test data out of ~/.voxcode. Must run before voxcode is imported.
os.environ.setdefault("VOXCODE_HOME", tempfile.mkdtemp(prefix="voxcode-tests-"))

import hashlib
import textwrap
from pathlib import Path

import numpy as np
import pytest

from voxcode.generation.providers.base_provider import BaseProvider

SAMPLE_FILES = {
    "app/auth.py": '''
        """Authentication helpers."""
        import hashlib

        SECRET_SALT = "x"


        class AuthService:
            """Logs users in."""

            def login(self, username, password):
                user = find_user(username)
                return verify_password(user, password)

            def logout(self, session):
                session.clear()


        def verify_password(user, password):
            digest = hashlib.sha256((SECRET_SALT + password).encode()).hexdigest()
            return user.password_hash == digest


        def find_user(username):
            return None
    ''',
    "web/api.ts": """
        import { request } from "./http";

        export class UserClient {
          fetchUserProfile(id: string) {
            return request(`/users/${id}`);
          }
        }

        export const deleteAccount = async (id: string) => {
          return request(`/users/${id}`, { method: "DELETE" });
        };
    """,
    "docs/guide.md": """
        # Guide

        Intro text.

        ## Password reset

        Users reset passwords by email.
    """,
    "node_modules/lib/index.js": "function ignored() {}\n",
}


def write_repo(root: Path, files: dict[str, str] = SAMPLE_FILES) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip("\n"))
    return root


@pytest.fixture
def sample_repo(tmp_path) -> Path:
    return write_repo(tmp_path / "repo")


class FakeEmbedder:
    """Deterministic bag-of-words vectors, so semantic search is testable offline."""

    model_name = "fake-embedder"
    dim = 64

    def _vec(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for word in text.lower().replace("_", " ").split():
            vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
        norm = np.linalg.norm(vec)
        return vec / norm if norm else vec + 1 / np.sqrt(self.dim)

    def embed_texts(self, texts):
        return np.stack([self._vec(t) for t in texts])

    def embed_query(self, query):
        return self._vec(query)


@pytest.fixture
def fake_embedder():
    return FakeEmbedder()


class ScriptedProvider(BaseProvider):
    """Provider that replies from a script; a callable gets the prompt."""

    provider_name = "scripted"

    def __init__(self, replies):
        super().__init__("scripted-model")
        self.replies = list(replies)
        self.prompts = []

    def stream(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        text = reply(prompt) if callable(reply) else reply
        for i in range(0, len(text), 7):
            yield text[i : i + 7]


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    """Point every on-disk store at a temp dir and use in-memory Qdrant."""
    from qdrant_client import QdrantClient

    from voxcode.agent import runtime
    from voxcode.api import services
    from voxcode.codebase.indexer.index_store import IndexStore
    from voxcode.execution.patch import patch_service
    from voxcode.retrieval.semantic import vector_store

    store = IndexStore(tmp_path / "indexes")
    monkeypatch.setattr(services.indexer, "store", store)
    monkeypatch.setattr(runtime.runtime_cache, "store", store)
    runtime.runtime_cache._cache.clear()
    monkeypatch.setattr(patch_service, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(vector_store, "_client", QdrantClient(":memory:"))
    return store
