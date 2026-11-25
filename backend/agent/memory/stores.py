"""Key-value stores for conversation memory."""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from typing import Any, Protocol


class MemoryStore(Protocol):
    def load(self, key: str) -> dict[str, Any] | None: ...
    def save(self, key: str, value: dict[str, Any]) -> None: ...
    def delete(self, key: str) -> None: ...


class InMemoryStore:
    """Process-local store with TTL and an LRU cap. Lost on restart."""

    def __init__(self, ttl_seconds: int, max_keys: int = 1000):
        self.ttl = ttl_seconds
        self.max_keys = max_keys
        self._data: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._lock = threading.Lock()

    def load(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires, value = item
            if expires < time.time():
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return json.loads(json.dumps(value))

    def save(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._data[key] = (time.time() + self.ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_keys:
                self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


class RedisStore:
    def __init__(self, host: str, port: int, ttl_seconds: int, db: int = 0):
        import redis

        self.ttl = ttl_seconds
        self.client = redis.Redis(
            host=host, port=port, db=db, decode_responses=True, socket_connect_timeout=2
        )

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def load(self, key: str) -> dict[str, Any] | None:
        data = self.client.get(key)
        return json.loads(data) if data else None

    def save(self, key: str, value: dict[str, Any]) -> None:
        self.client.set(key, json.dumps(value), ex=self.ttl)

    def delete(self, key: str) -> None:
        self.client.delete(key)
