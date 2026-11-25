"""Concrete LLM providers. All support streaming."""

from __future__ import annotations

import json
from collections.abc import Iterator

from backend.config.settings import settings
from backend.generation.prompts.prompt_result import PromptResult
from backend.generation.providers.base_provider import BaseProvider, ProviderError


def strip_think_blocks(chunks: Iterator[str]) -> Iterator[str]:
    """Remove `<think>...</think>` reasoning that some local models (e.g.
    qwen3, deepseek-r1) emit inline, so it is never shown, spoken, or
    mistaken for the answer."""
    buffer = ""
    inside = False
    for chunk in chunks:
        buffer += chunk
        while True:
            if inside:
                end = buffer.find("</think>")
                if end == -1:
                    buffer = buffer[-len("</think>") :]  # keep a possible partial tag
                    break
                buffer = buffer[end + len("</think>") :].lstrip()
                inside = False
            else:
                start = buffer.find("<think>")
                if start == -1:
                    # Hold back a possible partial "<think" at the end.
                    safe = len(buffer) - (len("<think>") - 1)
                    if safe > 0:
                        yield buffer[:safe]
                        buffer = buffer[safe:]
                    break
                if start:
                    yield buffer[:start]
                buffer = buffer[start + len("<think>") :]
                inside = True
    if buffer and not inside:
        yield buffer


class OllamaProvider(BaseProvider):
    """Local models through the Ollama HTTP API (no extra package needed)."""

    provider_name = "ollama"

    def stream(self, prompt: PromptResult) -> Iterator[str]:
        return strip_think_blocks(self._raw_stream(prompt))

    def _raw_stream(self, prompt: PromptResult) -> Iterator[str]:
        payload = {
            "model": self.model,
            "stream": True,
            "messages": [
                {"role": "system", "content": prompt.system_prompt},
                {"role": "user", "content": prompt.user_prompt},
            ],
        }
        for line in self._stream_lines(f"{self.base_url}/api/chat", payload):
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("error"):
                raise ProviderError(f"ollama: {data['error']}")
            text = data.get("message", {}).get("content", "")
            if text:
                yield text


class OpenAICompatibleProvider(BaseProvider):
    """OpenAI chat-completions API; also used by Groq and OpenRouter."""

    provider_name = "openai"
    # Newer OpenAI reasoning models reject a custom temperature, so it is only
    # sent to the providers that accept it.
    extra_payload: dict = {}

    def stream(self, prompt: PromptResult) -> Iterator[str]:
        return strip_think_blocks(self._raw_stream(prompt))

    def _raw_stream(self, prompt: PromptResult) -> Iterator[str]:
        self._require_key()
        payload = {
            "model": self.model,
            "stream": True,
            **self.extra_payload,
            "messages": [
                {"role": "system", "content": prompt.system_prompt},
                {"role": "user", "content": prompt.user_prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        for event in self._sse_json(f"{self.base_url}/chat/completions", payload, headers):
            if event.get("error"):
                raise ProviderError(f"{self.provider_name}: {event['error']}")
            for choice in event.get("choices", []):
                text = (choice.get("delta") or {}).get("content")
                if text:
                    yield text


class GroqProvider(OpenAICompatibleProvider):
    provider_name = "groq"
    extra_payload = {"temperature": 0.2}


class OpenRouterProvider(OpenAICompatibleProvider):
    provider_name = "openrouter"
    extra_payload = {"temperature": 0.2}


class AnthropicProvider(BaseProvider):
    provider_name = "anthropic"

    def stream(self, prompt: PromptResult) -> Iterator[str]:
        self._require_key()
        payload = {
            "model": self.model,
            "max_tokens": settings.MAX_OUTPUT_TOKENS,
            "stream": True,
            "system": prompt.system_prompt,
            "messages": [{"role": "user", "content": prompt.user_prompt}],
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        for event in self._sse_json(f"{self.base_url}/v1/messages", payload, headers):
            if event.get("type") == "content_block_delta":
                text = event.get("delta", {}).get("text")
                if text:
                    yield text
            elif event.get("type") == "error":
                raise ProviderError(f"anthropic: {event.get('error')}")


class GeminiProvider(BaseProvider):
    provider_name = "gemini"

    def stream(self, prompt: PromptResult) -> Iterator[str]:
        self._require_key()
        payload = {
            "systemInstruction": {"parts": [{"text": prompt.system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": prompt.user_prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": settings.MAX_OUTPUT_TOKENS},
        }
        # Key goes in a header, never in the URL (URLs end up in logs).
        headers = {"x-goog-api-key": self.api_key}
        url = f"{self.base_url}/models/{self.model}:streamGenerateContent?alt=sse"
        for event in self._sse_json(url, payload, headers):
            for candidate in event.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    if part.get("text"):
                        yield part["text"]
