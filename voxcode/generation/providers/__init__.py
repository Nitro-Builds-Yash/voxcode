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


def create_provider(name: str) -> BaseProvider:
    name = name.lower()
    if name == "ollama":
        return OllamaProvider(settings.OLLAMA_MODEL, base_url=settings.OLLAMA_HOST)
    if name == "openai":
        return OpenAICompatibleProvider(
            settings.OPENAI_MODEL, settings.OPENAI_API_KEY, settings.OPENAI_BASE_URL
        )
    if name == "groq":
        return GroqProvider(settings.GROQ_MODEL, settings.GROQ_API_KEY, "https://api.groq.com/openai/v1")
    if name == "openrouter":
        return OpenRouterProvider(
            settings.OPENROUTER_MODEL, settings.OPENROUTER_API_KEY, "https://openrouter.ai/api/v1"
        )
    if name == "anthropic":
        return AnthropicProvider(
            settings.ANTHROPIC_MODEL, settings.ANTHROPIC_API_KEY, "https://api.anthropic.com"
        )
    if name == "gemini":
        return GeminiProvider(
            settings.GEMINI_MODEL,
            settings.GEMINI_API_KEY,
            "https://generativelanguage.googleapis.com/v1beta",
        )
    raise ProviderError(f"Unsupported provider: {name}")
