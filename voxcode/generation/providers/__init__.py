"""LLM providers."""

from voxcode.config.settings import settings
from voxcode.generation.providers.base_provider import BaseProvider, ProviderError
from voxcode.generation.providers.providers import (
    AnthropicProvider,
    GeminiProvider,
    GroqProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    OpenRouterProvider,
)

__all__ = ["BaseProvider", "ProviderError", "create_provider"]


def create_provider(name: str, model: str | None = None) -> BaseProvider:
    """Client for `name`, using its configured model unless `model` is given."""
    name = name.lower()
    if name == "ollama":
        return OllamaProvider(model or settings.OLLAMA_MODEL, base_url=settings.OLLAMA_HOST)
    if name == "openai":
        return OpenAICompatibleProvider(
            model or settings.OPENAI_MODEL, settings.OPENAI_API_KEY, settings.OPENAI_BASE_URL
        )
    if name == "groq":
        return GroqProvider(
            model or settings.GROQ_MODEL, settings.GROQ_API_KEY, "https://api.groq.com/openai/v1"
        )
    if name == "openrouter":
        return OpenRouterProvider(
            model or settings.OPENROUTER_MODEL, settings.OPENROUTER_API_KEY, "https://openrouter.ai/api/v1"
        )
    if name == "anthropic":
        return AnthropicProvider(
            model or settings.ANTHROPIC_MODEL, settings.ANTHROPIC_API_KEY, "https://api.anthropic.com"
        )
    if name == "gemini":
        return GeminiProvider(
            model or settings.GEMINI_MODEL,
            settings.GEMINI_API_KEY,
            "https://generativelanguage.googleapis.com/v1beta",
        )
    raise ProviderError(f"Unsupported provider: {name}")
