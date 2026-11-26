"""Concrete LLM providers. All support streaming."""

from __future__ import annotations

import json
from collections.abc import Iterator

from voxcode.config.settings import settings
from voxcode.generation.prompts.prompt_result import PromptResult
from voxcode.generation.providers.base_provider import BaseProvider, ProviderError
from voxcode.generation.providers.types import ChatTurn, Message, ToolCall, ToolSpec, Usage


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


def _system_and_user(prompt: PromptResult) -> list[dict]:
    return [
        {"role": "system", "content": prompt.system_prompt},
        {"role": "user", "content": prompt.user_prompt},
    ]


def _openai_messages(system: str, messages: list[Message]) -> list[dict]:
    """Neutral messages -> OpenAI / Ollama chat format."""
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append(
                {
                    "role": "assistant",
                    "content": m.get("content") or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in m["tool_calls"]
                    ],
                }
            )
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m.get("content", "")})
    return out


def _openai_tools(tools: list[ToolSpec]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]


def _parse_arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


class OllamaProvider(BaseProvider):
    """Local models through the Ollama HTTP API (no extra package needed)."""

    provider_name = "ollama"

    def _stream(self, prompt: PromptResult, usage: Usage) -> Iterator[str]:
        return strip_think_blocks(self._raw_stream(prompt, usage))

    def _raw_stream(self, prompt: PromptResult, usage: Usage) -> Iterator[str]:
        payload = {"model": self.model, "stream": True, "messages": _system_and_user(prompt)}
        for line in self._stream_lines(f"{self.base_url}/api/chat", payload):
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("error"):
                raise ProviderError(f"ollama: {data['error']}")
            text = data.get("message", {}).get("content", "")
            if text:
                yield text
            if data.get("done"):
                usage.input_tokens += int(data.get("prompt_eval_count") or 0)
                usage.output_tokens += int(data.get("eval_count") or 0)

    def _chat(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> ChatTurn:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": _openai_messages(system, messages),
            "tools": _openai_tools(tools),
        }
        data = self._post_json(f"{self.base_url}/api/chat", payload)
        message = data.get("message") or {}
        calls = [
            ToolCall(
                id=f"call_{i}",
                name=c["function"]["name"],
                arguments=_parse_arguments(c["function"].get("arguments")),
            )
            for i, c in enumerate(message.get("tool_calls") or [])
        ]
        text = "".join(strip_think_blocks(iter([message.get("content") or ""])))
        return ChatTurn(
            text,
            calls,
            Usage(int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0)),
        )


class OpenAICompatibleProvider(BaseProvider):
    """OpenAI chat-completions API; also Groq, OpenRouter and local servers
    such as LM Studio, vLLM or llama.cpp (set OPENAI_BASE_URL)."""

    provider_name = "openai"
    # Newer OpenAI reasoning models reject a custom temperature, so it is only
    # sent to the providers that accept it.
    extra_payload: dict = {}
    stream_usage = True  # ask for a final usage chunk (stream_options)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _check_key(self) -> None:
        if self.is_remote:
            self._require_key()

    def _stream(self, prompt: PromptResult, usage: Usage) -> Iterator[str]:
        return strip_think_blocks(self._raw_stream(prompt, usage))

    def _raw_stream(self, prompt: PromptResult, usage: Usage) -> Iterator[str]:
        self._check_key()
        payload = {
            "model": self.model,
            "stream": True,
            **self.extra_payload,
            "messages": _system_and_user(prompt),
        }
        if self.stream_usage:
            payload["stream_options"] = {"include_usage": True}
        for event in self._sse_json(f"{self.base_url}/chat/completions", payload, self._headers()):
            if event.get("error"):
                raise ProviderError(f"{self.provider_name}: {event['error']}")
            for choice in event.get("choices") or []:
                text = (choice.get("delta") or {}).get("content")
                if text:
                    yield text
            self._read_usage(event.get("usage"), usage)

    @staticmethod
    def _read_usage(data: dict | None, usage: Usage) -> None:
        if not data:
            return
        cached = int((data.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        usage.input_tokens += int(data.get("prompt_tokens") or 0) - cached
        usage.cache_read_tokens += cached
        usage.output_tokens += int(data.get("completion_tokens") or 0)

    def _chat(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> ChatTurn:
        self._check_key()
        payload = {
            "model": self.model,
            **self.extra_payload,
            "messages": _openai_messages(system, messages),
            "tools": _openai_tools(tools),
        }
        data = self._post_json(f"{self.base_url}/chat/completions", payload, self._headers())
        message = (data.get("choices") or [{}])[0].get("message") or {}
        calls = [
            ToolCall(
                id=c.get("id") or f"call_{i}",
                name=c["function"]["name"],
                arguments=_parse_arguments(c["function"].get("arguments")),
            )
            for i, c in enumerate(message.get("tool_calls") or [])
        ]
        usage = Usage()
        self._read_usage(data.get("usage"), usage)
        text = "".join(strip_think_blocks(iter([message.get("content") or ""])))
        return ChatTurn(text, calls, usage)


class GroqProvider(OpenAICompatibleProvider):
    provider_name = "groq"
    extra_payload = {"temperature": 0.2}
    stream_usage = False


class OpenRouterProvider(OpenAICompatibleProvider):
    provider_name = "openrouter"
    extra_payload = {"temperature": 0.2}


_EPHEMERAL = {"type": "ephemeral"}


class AnthropicProvider(BaseProvider):
    """Claude via the Messages API, with prompt caching of the system prompt
    and repository context (re-used across edit retries and tool turns)."""

    provider_name = "anthropic"

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}

    @staticmethod
    def _system(system: str) -> list[dict]:
        return [{"type": "text", "text": system, "cache_control": _EPHEMERAL}]

    @staticmethod
    def _user_content(prompt: PromptResult):
        if prompt.context and prompt.task:
            return [
                {"type": "text", "text": prompt.context, "cache_control": _EPHEMERAL},
                {"type": "text", "text": prompt.task},
            ]
        return prompt.user_prompt

    @staticmethod
    def _read_usage(data: dict | None, usage: Usage) -> None:
        if not data:
            return
        usage.input_tokens += int(data.get("input_tokens") or 0)
        usage.output_tokens += int(data.get("output_tokens") or 0)
        usage.cache_read_tokens += int(data.get("cache_read_input_tokens") or 0)
        usage.cache_write_tokens += int(data.get("cache_creation_input_tokens") or 0)

    def _stream(self, prompt: PromptResult, usage: Usage) -> Iterator[str]:
        self._require_key()
        payload = {
            "model": self.model,
            "max_tokens": settings.MAX_OUTPUT_TOKENS,
            "stream": True,
            "system": self._system(prompt.system_prompt),
            "messages": [{"role": "user", "content": self._user_content(prompt)}],
        }
        for event in self._sse_json(f"{self.base_url}/v1/messages", payload, self._headers()):
            kind = event.get("type")
            if kind == "content_block_delta":
                text = event.get("delta", {}).get("text")
                if text:
                    yield text
            elif kind == "message_start":
                self._read_usage((event.get("message") or {}).get("usage"), usage)
            elif kind == "message_delta":
                usage.output_tokens += int((event.get("usage") or {}).get("output_tokens") or 0)
            elif kind == "error":
                raise ProviderError(f"anthropic: {event.get('error')}")

    def _chat(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> ChatTurn:
        self._require_key()
        out: list[dict] = []
        for m in messages:
            if m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if (
                    out
                    and out[-1]["role"] == "user"
                    and isinstance(out[-1]["content"], list)
                    and out[-1]["content"][0].get("type") == "tool_result"
                ):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif m["role"] == "assistant":
                content: list[dict] = [{"type": "text", "text": m["content"]}] if m.get("content") else []
                content += [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                    for c in m.get("tool_calls") or []
                ]
                out.append({"role": "assistant", "content": content or [{"type": "text", "text": "..."}]})
            else:
                out.append({"role": "user", "content": m["content"]})
        tool_defs = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
        ]
        if tool_defs:
            tool_defs[-1]["cache_control"] = _EPHEMERAL
        payload = {
            "model": self.model,
            "max_tokens": settings.MAX_OUTPUT_TOKENS,
            "system": self._system(system),
            "messages": out,
            "tools": tool_defs,
        }
        data = self._post_json(f"{self.base_url}/v1/messages", payload, self._headers())
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        calls = [
            ToolCall(id=b["id"], name=b["name"], arguments=b.get("input") or {})
            for b in data.get("content", [])
            if b.get("type") == "tool_use"
        ]
        usage = Usage()
        self._read_usage(data.get("usage"), usage)
        return ChatTurn(text, calls, usage)


class GeminiProvider(BaseProvider):
    provider_name = "gemini"

    def _headers(self) -> dict:
        # Key goes in a header, never in the URL (URLs end up in logs).
        return {"x-goog-api-key": self.api_key}

    @staticmethod
    def _read_usage(data: dict | None, usage: Usage) -> None:
        if not data:
            return
        cached = int(data.get("cachedContentTokenCount") or 0)
        usage.input_tokens = int(data.get("promptTokenCount") or 0) - cached
        usage.cache_read_tokens = cached
        usage.output_tokens = int(data.get("candidatesTokenCount") or 0)

    def _stream(self, prompt: PromptResult, usage: Usage) -> Iterator[str]:
        self._require_key()
        payload = {
            "systemInstruction": {"parts": [{"text": prompt.system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": prompt.user_prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": settings.MAX_OUTPUT_TOKENS},
        }
        url = f"{self.base_url}/models/{self.model}:streamGenerateContent?alt=sse"
        for event in self._sse_json(url, payload, self._headers()):
            for candidate in event.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    if part.get("text") and not part.get("thought"):
                        yield part["text"]
            # usageMetadata is cumulative; the last chunk holds the totals.
            self._read_usage(event.get("usageMetadata"), usage)

    def _chat(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> ChatTurn:
        self._require_key()
        contents: list[dict] = []
        for m in messages:
            if m["role"] == "tool":
                part = {"functionResponse": {"name": m["name"], "response": {"content": m["content"]}}}
                if (
                    contents
                    and contents[-1]["role"] == "user"
                    and "functionResponse" in contents[-1]["parts"][0]
                ):
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
            elif m["role"] == "assistant":
                parts: list[dict] = [{"text": m["content"]}] if m.get("content") else []
                parts += [
                    {"functionCall": {"name": c.name, "args": c.arguments}} for c in m.get("tool_calls") or []
                ]
                contents.append({"role": "model", "parts": parts or [{"text": "..."}]})
            else:
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "tools": [
                {
                    "functionDeclarations": [
                        {"name": t.name, "description": t.description, "parameters": t.parameters}
                        for t in tools
                    ]
                }
            ],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": settings.MAX_OUTPUT_TOKENS},
        }
        data = self._post_json(
            f"{self.base_url}/models/{self.model}:generateContent", payload, self._headers()
        )
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        calls = [
            ToolCall(
                id=f"call_{i}", name=p["functionCall"]["name"], arguments=p["functionCall"].get("args") or {}
            )
            for i, p in enumerate(parts)
            if "functionCall" in p
        ]
        usage = Usage()
        self._read_usage(data.get("usageMetadata"), usage)
        return ChatTurn(text, calls, usage)
