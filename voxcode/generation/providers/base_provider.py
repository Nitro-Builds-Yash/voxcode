"""Shared LLM provider plumbing: HTTP client, streaming, tool calling,
token accounting, secret redaction and the local-only guard."""

from __future__ import annotations

import ipaddress
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from urllib.parse import urlparse

import httpx

from voxcode.config.settings import settings
from voxcode.core.redaction import redact
from voxcode.generation.prompts.prompt_result import PromptResult
from voxcode.generation.providers.provider_result import ProviderResult
from voxcode.generation.providers.types import ChatTurn, Message, ToolSpec, Usage
from voxcode.monitoring import Metrics

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """The provider is misconfigured or its API returned an error."""


def is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


class BaseProvider(ABC):
    provider_name = "base"
    supports_tools = True

    def __init__(self, model: str, api_key: str = "", base_url: str = ""):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client: httpx.Client | None = None

    # ------------------------------------------------------------- public
    @property
    def is_remote(self) -> bool:
        return not is_local_url(self.base_url)

    def stream(self, prompt: PromptResult, usage: Usage | None = None) -> Iterator[str]:
        """Yield response text incrementally; fills `usage` when given."""
        usage = usage if usage is not None else Usage()
        yield from self._stream(self._prepare(prompt), usage)
        self._record(usage)

    def generate(self, prompt: PromptResult) -> ProviderResult:
        usage = Usage()
        content = "".join(self.stream(prompt, usage))
        return ProviderResult(
            response=content,
            model_name=self.model,
            provider_name=self.provider_name,
            prompt_size=prompt.prompt_size,
            completion_size=len(content),
            total_size=prompt.prompt_size + len(content),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
        )

    def chat(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> ChatTurn:
        """One non-streaming turn with native tool calling."""
        self._guard()
        if self.is_remote and settings.REDACT_SECRETS:
            system = self._redact(system)
            messages = [
                {**m, "content": self._redact(m["content"])} if isinstance(m.get("content"), str) else m
                for m in messages
            ]
        turn = self._chat(system, messages, tools)
        self._record(turn.usage)
        return turn

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------- provider-specific
    @abstractmethod
    def _stream(self, prompt: PromptResult, usage: Usage) -> Iterator[str]: ...

    def _chat(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> ChatTurn:
        raise ProviderError(f"{self.provider_name} does not support native tool calling")

    # ------------------------------------------------------------ helpers
    def _guard(self) -> None:
        if settings.LOCAL_ONLY and self.is_remote:
            raise ProviderError(
                f"LOCAL_ONLY is on: refusing to send code to {self.provider_name} at {self.base_url}. "
                "Use Ollama or a local OpenAI-compatible server."
            )

    def _redact(self, text: str) -> str:
        cleaned, count = redact(text)
        if count:
            logger.info("Redacted %d secret(s) before sending to %s", count, self.provider_name)
            Metrics.record_redactions(count)
        return cleaned

    def _prepare(self, prompt: PromptResult) -> PromptResult:
        self._guard()
        if not (self.is_remote and settings.REDACT_SECRETS):
            return prompt
        update = {"user_prompt": self._redact(prompt.user_prompt)}
        if prompt.context:
            update["context"] = self._redact(prompt.context)
        return prompt.model_copy(update=update)

    def _record(self, usage: Usage) -> None:
        Metrics.record_tokens(self.provider_name, self.model, usage)

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

    def _post_json(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        try:
            response = self.client.post(url, json=payload, headers=headers or {})
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.provider_name}: request failed: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(
                f"{self.provider_name} API error {response.status_code}: {response.text[:500]}"
            )
        return response.json()

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
