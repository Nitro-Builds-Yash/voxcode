import json

import httpx
import pytest

from backend.generation.prompts.prompt_result import PromptResult
from backend.generation.providers import ProviderError
from backend.generation.providers.providers import (
    AnthropicProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)

PROMPT = PromptResult(system_prompt="sys", user_prompt="hi", full_prompt="sys hi", prompt_size=6)


def _with_transport(provider, handler):
    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    return provider


def _sse(*events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"


def test_openai_compatible_streaming():
    def handler(request):
        assert request.headers["authorization"] == "Bearer k"
        body = _sse({"choices": [{"delta": {"content": "Hel"}}]}, {"choices": [{"delta": {"content": "lo"}}]})
        return httpx.Response(200, text=body)

    provider = _with_transport(OpenAICompatibleProvider("m", "k", "https://x/v1"), handler)
    assert list(provider.stream(PROMPT)) == ["Hel", "lo"]
    assert provider.generate(PROMPT).response == "Hello"


def test_anthropic_streaming():
    def handler(request):
        assert json.loads(request.content)["system"] == "sys"
        return httpx.Response(
            200,
            text=_sse(
                {"type": "message_start"},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
            ),
        )

    provider = _with_transport(AnthropicProvider("claude", "k", "https://api"), handler)
    assert provider.generate(PROMPT).response == "Hi"


def test_gemini_key_in_header_not_url():
    def handler(request):
        assert "key=" not in str(request.url)
        assert request.headers["x-goog-api-key"] == "secret"
        return httpx.Response(200, text=_sse({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}))

    provider = _with_transport(GeminiProvider("g", "secret", "https://g"), handler)
    assert provider.generate(PROMPT).response == "ok"


def test_ollama_ndjson():
    def handler(request):
        lines = [{"message": {"content": "a"}}, {"message": {"content": "b"}, "done": True}]
        return httpx.Response(200, text="\n".join(json.dumps(line) for line in lines))

    provider = _with_transport(OllamaProvider("q", base_url="http://o"), handler)
    assert provider.generate(PROMPT).response == "ab"


def test_errors_raise_instead_of_pretending_success():
    provider = _with_transport(
        OpenAICompatibleProvider("m", "k", "https://x"), lambda r: httpx.Response(401, text="bad key")
    )
    with pytest.raises(ProviderError, match="401"):
        provider.generate(PROMPT)
    with pytest.raises(ProviderError, match="API key"):
        AnthropicProvider("m", "", "https://x").generate(PROMPT)
