"""Process-wide cache of heavy models and LLM provider clients.

Every model is loaded lazily, once. If an optional ML dependency is missing
or a model cannot be downloaded, the getter returns None and the system
degrades gracefully (e.g. BM25-only search, no reranking).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from typing import Any

from backend.config.settings import settings

logger = logging.getLogger(__name__)

_UNAVAILABLE = object()

PROVIDER_NAMES = ("ollama", "gemini", "groq", "anthropic", "openai", "openrouter")


class ModelRegistry:
    _instance: ModelRegistry | None = None
    _instance_lock = threading.Lock()
    _lock: threading.RLock
    _models: dict[str, Any]
    _providers: dict[str, tuple[str, Any]]

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._lock = threading.RLock()
                instance._models = {}
                instance._providers = {}
                cls._instance = instance
        return cls._instance

    # -------------------------------------------------------------- models
    def _load(self, key: str, factory):
        with self._lock:
            value = self._models.get(key)
            if value is None:
                try:
                    value = factory()
                    logger.info("Loaded %s", key)
                except Exception as exc:  # missing package, no network, bad model
                    logger.warning("%s unavailable (%s); continuing without it", key, exc)
                    value = _UNAVAILABLE
                self._models[key] = value
        return None if value is _UNAVAILABLE else value

    def get_embedder(self):
        from backend.retrieval.semantic.embedder import Embedder

        return self._load(
            f"embedder:{settings.EMBEDDING_MODEL}",
            lambda: Embedder(settings.EMBEDDING_MODEL, settings.EMBEDDING_BATCH_SIZE),
        )

    def get_reranker(self):
        if not settings.RERANK_ENABLED:
            return None
        from backend.retrieval.rerank.reranker import CrossEncoderReranker

        return self._load(
            f"reranker:{settings.RERANK_MODEL}",
            lambda: CrossEncoderReranker(settings.RERANK_MODEL),
        )

    def get_stt(self):
        from backend.voice.stt.whisper_service import WhisperSTTService

        return self._load(f"whisper:{settings.WHISPER_MODEL}", WhisperSTTService)

    def set_model(self, key: str, value) -> None:
        """Inject a model (used by tests)."""
        with self._lock:
            self._models[key] = value

    # ----------------------------------------------------------- providers
    def get_provider(self, provider_name: str | None = None):
        """Provider client for the given (or default) provider.

        Clients are cached per configuration, so changing a key or model via
        /config/update takes effect on the next request automatically.
        """
        from backend.generation.providers import create_provider

        name = (provider_name or settings.DEFAULT_PROVIDER).lower()
        fingerprint = hashlib.sha1(repr(_provider_config(name)).encode()).hexdigest()
        with self._lock:
            cached = self._providers.get(name)
            if cached and cached[0] == fingerprint:
                return cached[1]
            if cached:
                cached[1].close()
            provider = create_provider(name)
            self._providers[name] = (fingerprint, provider)
            return provider

    def set_provider(self, name: str, provider) -> None:
        with self._lock:
            self._providers[name.lower()] = (
                hashlib.sha1(repr(_provider_config(name.lower())).encode()).hexdigest(),
                provider,
            )

    def unload(self) -> None:
        with self._lock:
            self._models.clear()
            for _, provider in self._providers.values():
                provider.close()
            self._providers.clear()


def _provider_config(name: str) -> tuple:
    upper = name.upper()
    return (
        getattr(settings, f"{upper}_MODEL", None),
        getattr(settings, f"{upper}_API_KEY", None),
        settings.OLLAMA_HOST if name == "ollama" else None,
    )
