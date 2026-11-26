import json

import httpx
import pytest

from voxcode.config.settings import settings
from voxcode.core.redaction import redact
from voxcode.generation.prompts.prompt_result import PromptResult
from voxcode.generation.providers import ProviderError
from voxcode.generation.providers.providers import (
    AnthropicProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)
from voxcode.generation.providers.types import ToolCall, ToolSpec, Usage
from voxcode.monitoring.registry import Metrics

TOOLS = [
    ToolSpec(
        "search_code",
        "Search",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    )
]
PROMPT = PromptResult(
    system_prompt="sys", user_prompt="CTX\n\nTASK", full_prompt="", prompt_size=10, context="CTX", task="TASK"
)


def _client(provider, handler):
    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    return provider


def test_redaction_removes_secrets_but_keeps_placeholders():
    text = (
        'OPENAI_API_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"\n'
        "aws = AKIAABCDEFGHIJKLMNOP\n"
        'password: "hunter2hunter2"\n'
        "token = os.environ['TOKEN']\n"
        'api_key = "changeme"\n'
        "db = postgres://admin:s3cretpw@db.example.com/app\n"
    )
    cleaned, count = redact(text)
    assert "sk-proj-abc" not in cleaned and "AKIAABCD" not in cleaned and "hunter2" not in cleaned
    assert "s3cretpw" not in cleaned
    assert "os.environ['TOKEN']" in cleaned and '"changeme"' in cleaned
    assert count == 4


def test_remote_provider_redacts_and_local_only_blocks(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')

    secret = PromptResult(
        system_prompt="s",
        user_prompt='key = "sk-ant-api03-abcdefghijklmnopqrstuv"',
        full_prompt="",
        prompt_size=1,
    )
    remote = _client(OpenAICompatibleProvider("m", "k", "https://api.openai.com/v1"), handler)
    remote.generate(secret)
    assert "sk-ant" not in json.dumps(seen["body"])

    local = _client(OpenAICompatibleProvider("m", "", "http://localhost:1234/v1"), handler)
    local.generate(secret)
    assert "sk-ant" in json.dumps(seen["body"])  # nothing leaves the machine

    monkeypatch.setattr(settings, "LOCAL_ONLY", True)
    with pytest.raises(ProviderError, match="LOCAL_ONLY"):
        remote.generate(secret)
    assert local.generate(secret).response == "ok"


def test_openai_native_tool_calls_and_cached_usage():
    def handler(request):
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "search_code"
        assert body["messages"][0] == {"role": "system", "content": "sys"}
        tool_msgs = [m for m in body["messages"] if m["role"] == "tool"]
        if tool_msgs:
            assert tool_msgs[0]["tool_call_id"] == "c1"
            return httpx.Response(200, json={"choices": [{"message": {"content": "Found it."}}]})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "search_code", "arguments": '{"query": "login"}'},
                                }
                            ],
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 7,
                    "prompt_tokens_details": {"cached_tokens": 60},
                },
            },
        )

    provider = _client(OpenAICompatibleProvider("m", "k", "https://api.openai.com/v1"), handler)
    turn = provider.chat("sys", [{"role": "user", "content": "where is login?"}], TOOLS)
    assert turn.tool_calls == [ToolCall("c1", "search_code", {"query": "login"})]
    assert (turn.usage.input_tokens, turn.usage.cache_read_tokens, turn.usage.output_tokens) == (40, 60, 7)
    messages = [
        {"role": "user", "content": "where is login?"},
        {"role": "assistant", "content": "", "tool_calls": turn.tool_calls},
        {"role": "tool", "tool_call_id": "c1", "name": "search_code", "content": "auth.py:10"},
    ]
    assert provider.chat("sys", messages, TOOLS).text == "Found it."


def test_anthropic_caches_context_and_merges_tool_results():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if body.get("stream"):
            events = [
                {
                    "type": "message_start",
                    "message": {"usage": {"input_tokens": 12, "cache_read_input_tokens": 900}},
                },
                {"type": "content_block_delta", "delta": {"text": "Hi"}},
                {"type": "message_delta", "usage": {"output_tokens": 3}},
            ]
            return httpx.Response(200, text="".join(f"data: {json.dumps(e)}\n\n" for e in events))
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "Checking."},
                    {"type": "tool_use", "id": "t1", "name": "search_code", "input": {"query": "x"}},
                ],
                "usage": {"input_tokens": 5, "output_tokens": 2, "cache_creation_input_tokens": 50},
            },
        )

    provider = _client(AnthropicProvider("claude", "k", "https://api.anthropic.com"), handler)
    usage = Usage()
    assert "".join(provider.stream(PROMPT, usage)) == "Hi"
    content = bodies[0]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "CTX", "cache_control": {"type": "ephemeral"}}
    assert (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens) == (12, 900, 3)

    messages = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [ToolCall("t1", "a", {}), ToolCall("t2", "b", {})],
        },
        {"role": "tool", "tool_call_id": "t1", "name": "a", "content": "r1"},
        {"role": "tool", "tool_call_id": "t2", "name": "b", "content": "r2"},
    ]
    turn = provider.chat("sys", messages, TOOLS)
    sent = bodies[-1]
    assert sent["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert [b["tool_use_id"] for b in sent["messages"][-1]["content"]] == ["t1", "t2"]
    assert turn.tool_calls[0].arguments == {"query": "x"} and turn.usage.cache_write_tokens == 50


def test_gemini_and_ollama_tool_calls():
    gemini = _client(
        GeminiProvider("g", "k", "https://g"),
        lambda r: httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"functionCall": {"name": "search_code", "args": {"query": "a"}}}]
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2},
            },
        ),
    )
    assert gemini.chat("s", [{"role": "user", "content": "q"}], TOOLS).tool_calls[0].name == "search_code"

    ollama = _client(
        OllamaProvider("q", base_url="http://localhost:11434"),
        lambda r: httpx.Response(
            200,
            json={
                "message": {
                    "content": "<think>hmm</think>",
                    "tool_calls": [{"function": {"name": "search_code", "arguments": {"query": "b"}}}],
                },
                "prompt_eval_count": 9,
                "eval_count": 4,
            },
        ),
    )
    turn = ollama.chat("s", [{"role": "user", "content": "q"}], TOOLS)
    assert (
        turn.tool_calls[0].arguments == {"query": "b"} and turn.text == "" and turn.usage.output_tokens == 4
    )


def test_cost_estimate_uses_configured_prices(monkeypatch):
    from voxcode.monitoring import registry

    monkeypatch.setattr(settings, "MODEL_PRICES", '{"m": {"input": 3, "output": 15, "cache_read": 0.3}}')
    registry._prices.cache_clear()
    try:
        cost = Metrics.estimate_cost("m", Usage(1_000_000, 100_000, 1_000_000))
        assert cost == pytest.approx(3 + 1.5 + 0.3)
        assert Metrics.estimate_cost("unknown", Usage(10, 10)) is None
    finally:
        registry._prices.cache_clear()
