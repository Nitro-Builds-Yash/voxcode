from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Dict, Any

from backend.config.settings import settings
from backend.core.model_registry import ModelRegistry

router = APIRouter(
    prefix="/config",
    tags=["config"],
)

registry = ModelRegistry()


class AIConfigUpdate(BaseModel):
    default_provider: Optional[str] = None
    ollama_model: Optional[str] = None
    ollama_host: Optional[str] = None
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    groq_api_key: Optional[str] = None
    groq_model: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    anthropic_model: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_model: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    openrouter_model: Optional[str] = None


@router.get("/")
def get_ai_config() -> Dict[str, Any]:
    """
    Returns current active providers, selected models, and connection status.
    Masks secrets for security.
    """
    return {
        "active_provider": settings.DEFAULT_PROVIDER,
        "providers": {
            "gemini": {
                "name": "Google Gemini",
                "model": settings.GEMINI_MODEL,
                "configured": bool(settings.GEMINI_API_KEY),
                "is_active": settings.DEFAULT_PROVIDER == "gemini",
            },
            "groq": {
                "name": "Groq LPU (Ultra-Fast)",
                "model": settings.GROQ_MODEL,
                "configured": bool(settings.GROQ_API_KEY),
                "is_active": settings.DEFAULT_PROVIDER == "groq",
            },
            "ollama": {
                "name": "Ollama (Local)",
                "model": settings.OLLAMA_MODEL,
                "host": settings.OLLAMA_HOST,
                "configured": True,
                "is_active": settings.DEFAULT_PROVIDER == "ollama",
            },
            "anthropic": {
                "name": "Anthropic Claude",
                "model": settings.ANTHROPIC_MODEL,
                "configured": bool(settings.ANTHROPIC_API_KEY),
                "is_active": settings.DEFAULT_PROVIDER == "anthropic",
            },
            "openai": {
                "name": "OpenAI",
                "model": settings.OPENAI_MODEL,
                "configured": bool(settings.OPENAI_API_KEY),
                "is_active": settings.DEFAULT_PROVIDER == "openai",
            },
            "openrouter": {
                "name": "OpenRouter",
                "model": settings.OPENROUTER_MODEL,
                "configured": bool(settings.OPENROUTER_API_KEY),
                "is_active": settings.DEFAULT_PROVIDER == "openrouter",
            },
        },
        "retrieval": {
            "embedding_model": settings.EMBEDDING_MODEL,
            "rerank_model": settings.RERANK_MODEL,
            "top_k": settings.DEFAULT_TOP_K,
        },
    }


@router.post("/update")
def update_ai_config(payload: AIConfigUpdate):
    """
    Updates AI provider configuration at runtime without needing a server restart.
    """
    if payload.default_provider:
        settings.DEFAULT_PROVIDER = payload.default_provider.lower()

    if payload.gemini_api_key is not None:
        settings.GEMINI_API_KEY = payload.gemini_api_key
    if payload.gemini_model:
        settings.GEMINI_MODEL = payload.gemini_model

    if payload.groq_api_key is not None:
        settings.GROQ_API_KEY = payload.groq_api_key
    if payload.groq_model:
        settings.GROQ_MODEL = payload.groq_model

    if payload.anthropic_api_key is not None:
        settings.ANTHROPIC_API_KEY = payload.anthropic_api_key
    if payload.anthropic_model:
        settings.ANTHROPIC_MODEL = payload.anthropic_model

    if payload.ollama_model:
        settings.OLLAMA_MODEL = payload.ollama_model
    if payload.ollama_host:
        settings.OLLAMA_HOST = payload.ollama_host

    if payload.openai_api_key is not None:
        settings.OPENAI_API_KEY = payload.openai_api_key
    if payload.openai_model:
        settings.OPENAI_MODEL = payload.openai_model

    if payload.openrouter_api_key is not None:
        settings.OPENROUTER_API_KEY = payload.openrouter_api_key
    if payload.openrouter_model:
        settings.OPENROUTER_MODEL = payload.openrouter_model

    # Clear cached instances so updated credentials take effect
    registry.unload()

    return {
        "status": "updated",
        "active_provider": settings.DEFAULT_PROVIDER,
        "message": f"Active AI Provider is now {settings.DEFAULT_PROVIDER.upper()}",
    }
