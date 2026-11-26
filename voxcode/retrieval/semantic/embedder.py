"""Sentence-transformer embeddings with a persistent on-disk cache.

Embeddings are cached by (model, text) hash in SQLite, so re-indexing a
repository only embeds chunks whose text actually changed.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path

import numpy as np

from voxcode.config.paths import CACHE_DIR

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class EmbeddingCache:
    def __init__(self, path: Path | None = None):
        path = path or CACHE_DIR / "embeddings.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS emb (key TEXT PRIMARY KEY, vec BLOB)")
        self._db.commit()

    def get_many(self, keys: list[str]) -> dict[str, np.ndarray]:
        found: dict[str, np.ndarray] = {}
        with self._lock:
            for start in range(0, len(keys), 500):
                batch = keys[start : start + 500]
                marks = ",".join("?" * len(batch))
                for key, blob in self._db.execute(f"SELECT key, vec FROM emb WHERE key IN ({marks})", batch):
                    found[key] = np.frombuffer(blob, dtype=np.float32)
        return found

    def put_many(self, items: dict[str, np.ndarray]) -> None:
        with self._lock:
            self._db.executemany(
                "INSERT OR REPLACE INTO emb (key, vec) VALUES (?, ?)",
                [(k, np.asarray(v, dtype=np.float32).tobytes()) for k, v in items.items()],
            )
            self._db.commit()


class Embedder:
    """Wraps a SentenceTransformer model. Raises ImportError if unavailable."""

    def __init__(self, model_name: str, batch_size: int = 64, cache: EmbeddingCache | None = None):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.batch_size = batch_size
        self.model = SentenceTransformer(model_name)
        self.cache = cache or EmbeddingCache()
        self._query_prefix = BGE_QUERY_PREFIX if "bge" in model_name.lower() else ""

    def _encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(
            self.model.encode(
                texts,
                batch_size=self.batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            ),
            dtype=np.float32,
        )

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        keys = [hashlib.sha1(f"{self.model_name}\0{t}".encode()).hexdigest() for t in texts]
        cached = self.cache.get_many(keys)
        missing = [i for i, k in enumerate(keys) if k not in cached]
        if missing:
            fresh = self._encode([texts[i] for i in missing])
            new_items = {keys[i]: vec for i, vec in zip(missing, fresh, strict=True)}
            self.cache.put_many(new_items)
            cached.update(new_items)
        return np.stack([cached[k] for k in keys])

    def embed_query(self, query: str) -> np.ndarray:
        return self._encode([self._query_prefix + query])[0]
