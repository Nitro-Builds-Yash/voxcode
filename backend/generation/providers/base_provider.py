"""Shared LLM provider plumbing: a persistent HTTP client, streaming, errors."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterator

import httpx

from backend.config.settings import settings
from backend.generation.prompts.prompt_result import PromptResult
from backend.generation.providers.provider_result import ProviderResult


class ProviderError(RuntimeError):
    """The provider is misconfigured or its API returned an error."""


class BaseProvider(ABC):
    provider_name = "base"

    def __init__(self, model: str, api_key: str = "", base_url: str = ""):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client: httpx.Client | None = None

    # ------------------------------------------------------------- public
    def generate(self, prompt: PromptResult) -> ProviderResult:
        content = "".join(self.stream(prompt))
        return ProviderResult(
            response=content,
            model_name=self.model,
            provider_name=self.provider_name,
            prompt_size=prompt.prompt_size,
            completion_size=len(content),
            total_size=prompt.prompt_size + len(content),
        )

    @abstractmethod
    def stream(self, prompt: PromptResult) -> Iterator[str]:
        """Yield response text incrementally."""

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------ helpers
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(settings.PROVIDER_TIMEOUT_SECONDS, connect=10.0)
            )
        return self._client

    def _require_key(self) -> None:
        if not self.api_key:
            raise ProviderError(
                f"{self.provider_name}: API key is not configured "
                f"(set {self.provider_name.upper()}_API_KEY or use /config/update)"
            )

    def _stream_lines(self, url: str, payload: dict, headers: dict | None = None) -> Iterator[str]:
        try:
            with self.client.stream("POST", url, json=payload, headers=headers or {}) as response:
                if response.status_code >= 400:
                    body = response.read().decode("utf-8", errors="replace")[:500]
                    raise ProviderError(f"{self.provider_name} API error {response.status_code}: {body}")
                yield from response.iter_lines()
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.provider_name}: request failed: {exc}") from exc

    def _sse_json(self, url: str, payload: dict, headers: dict | None = None) -> Iterator[dict]:
        """Parse a server-sent-events stream into JSON objects."""
        for line in self._stream_lines(url, payload, headers):
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue
