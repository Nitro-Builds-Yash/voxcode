import os
import tempfile

# Keep test data out of ~/.voxcode. Must run before voxcode is imported.
os.environ.setdefault("VOXCODE_HOME", tempfile.mkdtemp(prefix="voxcode-tests-"))

import hashlib
import textwrap
from pathlib import Path

import numpy as np
import pytest

from voxcode.config.settings import settings
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
    """Provider that replies from a script; a callable gets the prompt.

    For native tool calling, script entries may be ChatTurn objects (or
    callables returning one), consumed by `chat()`.
    """

    provider_name = "scripted"

    def __init__(self, replies, turns=None, supports_tools=False):
        super().__init__("scripted-model", base_url="http://localhost")
        self.replies = list(replies)
        self.turns = list(turns or [])
        self.supports_tools = supports_tools
        self.prompts = []
        self.chats = []

    def _stream(self, prompt, usage):
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        text = reply(prompt) if callable(reply) else reply
        usage.input_tokens += prompt.prompt_size // 4
        usage.output_tokens += len(text) // 4
        for i in range(0, len(text), 7):
            yield text[i : i + 7]

    def _chat(self, system, messages, tools):
        self.chats.append((system, [dict(m) for m in messages], tools))
        turn = self.turns.pop(0) if len(self.turns) > 1 else self.turns[0]
        return turn(messages) if callable(turn) else turn


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
    from voxcode.agent.answer_cache import answer_cache
    from voxcode.config import paths

    monkeypatch.setattr(paths, "LEARNING_DIR", tmp_path / "learning")
    monkeypatch.setattr(settings, "BACKGROUND_CALL_GRAPH", False)  # deterministic graphs in tests
    monkeypatch.setattr(settings, "ALLOWED_HOSTS", "testserver")  # Starlette's TestClient host
    monkeypatch.setattr(settings, "LOCAL_SEMANTIC", False)  # pure BM25 unless a test opts in
    answer_cache.clear()
    return store


RUST_FILES = {
    "Cargo.toml": '[package]\nname = "mini-store"\nversion = "0.1.0"\nedition = "2021"\n',
    "src/lib.rs": """pub mod store;
mod util;

use crate::store::Store;

/// Open the default store.
pub fn open_default() -> Store {
    util::log("open");
    Store::open("default")
}
""",
    "src/store.rs": """use crate::util::{log, normalize as norm};

pub struct Store {
    name: String,
    items: Vec<String>,
}

impl Store {
    pub fn open(name: &str) -> Self {
        log("opening");
        Store { name: norm(name), items: Vec::new() }
    }

    pub fn add(&mut self, item: &str) -> usize {
        self.validate(item);
        self.items.push(norm(item));
        self.items.len()
    }

    fn validate(&self, item: &str) {
        assert!(!item.is_empty(), "empty item in {}", self.name);
    }

    pub fn count(&self) -> usize {
        self.items.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn adds_items() {
        let mut store = Store::open("x");
        store.add("A");
        assert_eq!(store.count(), 1);
    }
}
""",
    "src/util.rs": """/// Trim and lowercase a name.
pub fn normalize(value: &str) -> String {
    value.trim().to_lowercase()
}

pub fn log(message: &str) {
    let _ = message;
}
""",
    "src/main.rs": """use mini_store::open_default;

fn main() {
    let store = open_default();
    println!("{}", store.count());
}
""",
    "tests/integration.rs": """use mini_store::store::Store;

#[test]
fn opens_empty() {
    assert_eq!(Store::open(" A ").count(), 0);
}
""",
}


@pytest.fixture
def rust_repo(tmp_path):
    root = tmp_path / "mini-store"
    for rel, text in RUST_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root
