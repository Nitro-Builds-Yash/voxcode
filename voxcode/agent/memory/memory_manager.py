"""Conversation memory per session (Redis when available, else in-process)."""

from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone

from voxcode.agent.memory.memory_models import ConversationHistory, ConversationMessage
from voxcode.agent.memory.stores import InMemoryStore, MemoryStore, RedisStore
from voxcode.config.settings import settings

logger = logging.getLogger(__name__)


def create_store() -> MemoryStore:
    backend = settings.MEMORY_BACKEND.lower()
    if backend == "auto" and not _port_open(settings.REDIS_HOST, settings.REDIS_PORT):
        logger.warning("Redis unavailable; conversation memory is in-process only")
        return InMemoryStore(settings.MEMORY_TTL_SECONDS)
    if backend in ("redis", "auto"):
        try:
            store = RedisStore(settings.REDIS_HOST, settings.REDIS_PORT, settings.MEMORY_TTL_SECONDS)
            if store.ping():
                logger.info("Conversation memory: Redis at %s:%s", settings.REDIS_HOST, settings.REDIS_PORT)
                return store
            if backend == "redis":
                raise RuntimeError(f"Redis is not reachable at {settings.REDIS_HOST}:{settings.REDIS_PORT}")
        except ImportError:
            if backend == "redis":
                raise
        logger.warning("Redis unavailable; conversation memory is in-process only")
    return InMemoryStore(settings.MEMORY_TTL_SECONDS)


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class MemoryManager:
    def __init__(self, store: MemoryStore | None = None, max_messages: int | None = None):
        self.store = store or create_store()
        self.max_messages = max_messages or settings.MEMORY_MAX_MESSAGES

    @staticmethod
    def _key(session_id: str) -> str:
        return f"conversation:{session_id}"

    def get_history(self, session_id: str) -> ConversationHistory:
        data = self.store.load(self._key(session_id))
        if not data:
            return ConversationHistory(session_id=session_id)
        return ConversationHistory.model_validate(data)

    def add_messages(self, session_id: str, *messages: tuple[str, str]) -> None:
        history = self.get_history(session_id)
        now = datetime.now(timezone.utc)
        for role, content in messages:
            history.messages.append(ConversationMessage(role=role, content=content, timestamp=now))
        history.messages = history.messages[-self.max_messages :]
        self.store.save(self._key(session_id), history.model_dump(mode="json"))

    def add_message(self, session_id: str, role: str, content: str) -> None:
        self.add_messages(session_id, (role, content))

    def clear_history(self, session_id: str) -> None:
        self.store.delete(self._key(session_id))

    def format_history(self, session_id: str, limit: int = 6, max_chars: int = 1500) -> str:
        messages = self.get_history(session_id).messages[-limit:]
        return "\n".join(f"{m.role.upper()}: {m.content[:max_chars]}" for m in messages)
