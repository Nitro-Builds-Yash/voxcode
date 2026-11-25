"""Runtime AI provider configuration."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.api import services
from backend.config.settings import settings
from backend.core.model_registry import PROVIDER_NAMES

router = APIRouter(prefix="/config", tags=["config"])

_LABELS = {
    "gemini": "Google Gemini",
    "groq": "Groq",
    "ollama": "Ollama (local)",
    "anthropic": "Anthropic Claude",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
}


class AIConfigUpdate(BaseModel):
    default_provider: str | None = None
    ollama_model: str | None = None
    ollama_host: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    groq_api_key: str | None = None
    groq_model: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    openrouter_api_key: str | None = None
    openrouter_model: str | None = None


@router.get("/")
def get_ai_config() -> dict[str, Any]:
    """Active provider, models and whether keys are set (keys never returned)."""
    providers = {}
    for name in PROVIDER_NAMES:
        upper = name.upper()
        providers[name] = {
            "name": _LABELS[name],
            "model": getattr(settings, f"{upper}_MODEL"),
            "configured": True if name == "ollama" else bool(getattr(settings, f"{upper}_API_KEY")),
            "is_active": name == settings.DEFAULT_PROVIDER,
        }
    providers["ollama"]["host"] = settings.OLLAMA_HOST
    return {
        "active_provider": settings.DEFAULT_PROVIDER,
        "providers": providers,
        "retrieval": {
            "embedding_model": settings.EMBEDDING_MODEL,
            "embedder_loaded": services.registry.get_embedder() is not None
            if settings.PRELOAD_MODELS
            else None,
            "rerank_model": settings.RERANK_MODEL if settings.RERANK_ENABLED else None,
            "top_k": settings.DEFAULT_TOP_K,
        },
        "emotion_enabled": settings.EMOTION_ENABLED,
    }


@router.post("/update")
def update_ai_config(payload: AIConfigUpdate) -> dict:
    """Change providers, models or keys at runtime (no restart needed)."""
    if payload.default_provider:
        name = payload.default_provider.lower()
        if name not in PROVIDER_NAMES:
            raise HTTPException(status_code=400, detail=f"Unknown provider '{name}'")
        settings.DEFAULT_PROVIDER = name

    if payload.ollama_host:
        parsed = urlparse(payload.ollama_host)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(status_code=400, detail="ollama_host must be an http(s) URL")
        settings.OLLAMA_HOST = payload.ollama_host.rstrip("/")

    for name in PROVIDER_NAMES:
        model = getattr(payload, f"{name}_model", None)
        if model:
            setattr(settings, f"{name.upper()}_MODEL", model.strip())
        key = getattr(payload, f"{name}_api_key", None)
        if key is not None:
            setattr(settings, f"{name.upper()}_API_KEY", key.strip())

    # Provider clients are keyed by their config, so the change applies on the
    # next request without unloading the embedding/reranker models.
    return {
        "status": "updated",
        "active_provider": settings.DEFAULT_PROVIDER,
        "message": f"Active AI provider is now {settings.DEFAULT_PROVIDER.upper()}",
    }
