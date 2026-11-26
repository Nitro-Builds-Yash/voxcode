"""Answer cache: a repeated question returns instantly and costs nothing.

Keys combine the repository, the index version (so any re-index
invalidates), the model and the question normalised to its search tokens
("Where is login handled?" == "where's the login handled"). When an
embedding model is loaded, near-duplicate phrasings (cosine >= 0.97) hit too.
Only questions asked without conversation history are cached, because
earlier turns can change the right answer.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np

from voxcode.config.settings import settings
from voxcode.retrieval.bm25.tokenizer import tokenize


@dataclass
class CachedAnswer:
    answer: str
    payload: dict  # sources, task, model and other response fields
    created: float
    vector: np.ndarray | None = None


class AnswerCache:
    def __init__(self, size: int | None = None, ttl: float | None = None, similarity: float = 0.97):
        self.size = size or settings.ANSWER_CACHE_SIZE
        self.ttl = ttl or settings.ANSWER_CACHE_TTL_SECONDS
        self.similarity = similarity
        self._items: OrderedDict[tuple, CachedAnswer] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    @staticmethod
    def normalise(query: str) -> tuple[str, ...]:
        return tuple(sorted(set(tokenize(query)))) or (query.strip().lower(),)

    def _scope(self, key: tuple) -> tuple:
        return key[:-1]

    def get(self, scope: tuple, query: str, embedder=None) -> CachedAnswer | None:
        key = (*scope, self.normalise(query))
        now = time.time()
        with self._lock:
            item = self._items.get(key)
            if item and now - item.created <= self.ttl:
                self._items.move_to_end(key)
                self.hits += 1
                return item
            if item:
                del self._items[key]
            candidates = [
                (k, v) for k, v in self._items.items() if self._scope(k) == scope and v.vector is not None
            ]
        if embedder is not None and candidates:
            vector = _embed(embedder, query)
            if vector is not None:
                best_key, best = max(candidates, key=lambda kv: float(kv[1].vector @ vector))
                if float(best.vector @ vector) >= self.similarity and now - best.created <= self.ttl:
                    with self._lock:
                        self.hits += 1
                    return best
        with self._lock:
            self.misses += 1
        return None

    def put(self, scope: tuple, query: str, answer: str, payload: dict, embedder=None) -> None:
        if not answer.strip():
            return
        item = CachedAnswer(answer, payload, time.time(), _embed(embedder, query) if embedder else None)
        with self._lock:
            self._items[(*scope, self.normalise(query))] = item
            self._items.move_to_end((*scope, self.normalise(query)))
            while len(self._items) > self.size:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def stats(self) -> dict:
        with self._lock:
            return {"entries": len(self._items), "hits": self.hits, "misses": self.misses}


def _embed(embedder, text: str) -> np.ndarray | None:
    try:
        vector = np.asarray(embedder.embed_texts([text])[0], dtype=np.float32)
    except Exception:
        return None
    norm = np.linalg.norm(vector)
    return vector / norm if norm else None


answer_cache = AnswerCache()
