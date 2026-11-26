"""Tool-using agent: the model decides which code tools to call.

Uses the provider's native tool calling (OpenAI, Anthropic, Gemini, Ollama,
Groq, OpenRouter). Small local models that cannot call tools fall back to a
provider-agnostic loop in which the model replies with one JSON object per
turn. The same `CodeTools` back the MCP server.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path

from voxcode.agent.agent_request import AgentRequest
from voxcode.agent.memory.memory_manager import MemoryManager
from voxcode.agent.runtime import RuntimeCache, runtime_cache
from voxcode.config.settings import settings
from voxcode.core.json_extract import first_json_with
from voxcode.core.model_registry import ModelRegistry
from voxcode.generation.context.context_builder import number_lines
from voxcode.generation.prompts.prompt_result import PromptResult
from voxcode.generation.prompts.system_prompts import build_system_prompt
from voxcode.generation.providers import ProviderError
from voxcode.generation.providers.types import ToolSpec

logger = logging.getLogger(__name__)

MAX_STEPS = 8
MAX_OBSERVATION_CHARS = 6000

NATIVE_RULES = """
Investigate the repository with the tools before answering: search first,
then read the relevant files. Call several tools at once when they are
independent. When you know the answer, reply with it directly, citing files
as path:line. Use at most 8 rounds of tool calls.
""".strip()

JSON_RULES = """
You can investigate the repository with tools before answering.
Reply with exactly ONE JSON object per turn and nothing else:

  {"tool": "search_code", "args": {"query": "..."}}          hybrid code search
  {"tool": "read_file", "args": {"path": "...", "start": 1, "end": 80}}
  {"tool": "find_references", "args": {"name": "symbol_name"}}
  {"tool": "list_files", "args": {"pattern": "*.py"}}
  {"tool": "git_history", "args": {"query": "...", "path": ""}}
  {"final": "your answer, citing files as path:line"}

Use as few tool calls as needed (at most 8), then give the final answer.
""".strip()


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


TOOL_SPECS = [
    ToolSpec(
        "search_code",
        "Hybrid keyword + semantic search over the repository; returns matching functions/classes with paths and lines.",
        _schema(
            {
                "query": {"type": "string", "description": "What to look for"},
                "limit": {"type": "integer", "description": "Max results (default 6)"},
            },
            ["query"],
        ),
    ),
    ToolSpec(
        "read_file",
        "Read a line range of a file (path relative to the repository root).",
        _schema(
            {
                "path": {"type": "string"},
                "start": {"type": "integer", "description": "First line, 1-based"},
                "end": {"type": "integer", "description": "Last line"},
            },
            ["path"],
        ),
    ),
    ToolSpec(
        "find_references",
        "Every line that mentions an identifier, as path:line.",
        _schema({"name": {"type": "string"}}, ["name"]),
    ),
    ToolSpec(
        "list_files",
        "Indexed files matching a glob such as '*.py' or 'src/*'.",
        _schema({"pattern": {"type": "string"}}, []),
    ),
    ToolSpec(
        "git_history",
        "Recent commits, filtered by words in the message and/or a file path. Use for why/when/who questions.",
        _schema(
            {
                "query": {"type": "string"},
                "path": {"type": "string"},
                "limit": {"type": "integer"},
            },
            [],
        ),
    ),
]


class CodeTools:
    """Read-only code tools over one indexed repository."""

    def __init__(self, repo: Path, retriever):
        self.repo = repo
        self.retriever = retriever

    def call(self, name: str, args: dict) -> str:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            return f"Unknown tool '{name}'."
        if not isinstance(args, dict):
            return f"Bad arguments for {name}: expected an object"
        try:
            return handler(**args)[:MAX_OBSERVATION_CHARS]
        except TypeError as exc:
            return f"Bad arguments for {name}: {exc}"
        except Exception as exc:
            return f"{name} failed: {exc}"

    def tool_search_code(self, query: str, limit: int = 6) -> str:
        results = self.retriever.expand(self.retriever.search(query, max(1, min(int(limit), 20))), 2)
        return (
            "\n\n".join(
                f"[{r.source}] {r.file_path}:{r.start_line}-{r.end_line} {r.symbol_name}\n{r.content[:600]}"
                for r in results
            )
            or "No results."
        )

    def tool_read_file(self, path: str, start: int = 1, end: int = 120) -> str:
        target = (self.repo / path).resolve()
        if self.repo not in target.parents:
            return "Path is outside the repository."
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start))
        end = min(len(lines), int(end), start + 300)
        return f"{path} ({len(lines)} lines)\n" + number_lines("\n".join(lines[start - 1 : end]), start)

    def tool_find_references(self, name: str) -> str:
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        hits = [
            f"{c.file_path}:{c.start_line + i} {line.strip()[:160]}"
            for c in self.retriever.index.chunks
            for i, line in enumerate(c.content.splitlines())
            if pattern.search(line)
        ]
        return "\n".join(dict.fromkeys(hits[:60])) or "No references."

    def tool_list_files(self, pattern: str = "*") -> str:
        files = sorted(self.retriever.index.files)
        matched = [
            f for f in files if fnmatch.fnmatch(f, pattern) or fnmatch.fnmatch(f.rsplit("/", 1)[-1], pattern)
        ]
        return "\n".join(matched)[:4000] or "No files."

    def tool_git_history(self, query: str = "", path: str = "", limit: int = 10) -> str:
        commits = self.retriever.commits.search(query, path, max(1, min(int(limit), 30)))
        return (
            "\n".join(c.render() for c in commits) or "No commits (not a git repository, or nothing matched)."
        )


class ToolAgent:
    def __init__(self, registry: ModelRegistry, memory: MemoryManager, runtimes: RuntimeCache | None = None):
        self.registry = registry
        self.memory = memory
        self.runtimes = runtimes or runtime_cache

    def run(self, request: AgentRequest, repo: Path) -> Iterator[dict]:
        """Yield `tool` events for each call, then a `done` event."""
        provider = self.registry.get_provider()
        tools = CodeTools(repo, self.runtimes.retriever(request.repository_key))
        history = self.memory.format_history(request.session_id)

        answer: str | None = None
        mode = "json"
        if settings.NATIVE_TOOL_CALLING and provider.supports_tools:
            try:
                answer = yield from self._native(provider, tools, request, history)
                mode = "native"
            except ProviderError as exc:
                if "does not support" not in str(exc).lower() and "tool" not in str(exc).lower():
                    raise
                logger.info("Native tool calling unavailable (%s); using JSON tool loop", exc)
        if answer is None:
            answer = yield from self._json_loop(provider, tools, request, history)

        self.memory.add_messages(request.session_id, ("user", request.query), ("assistant", answer))
        yield {
            "type": "done",
            "answer": answer,
            "provider": provider.provider_name,
            "model_name": provider.model,
            "tool_mode": mode,
        }

    # ------------------------------------------------------------ native
    def _native(self, provider, tools: CodeTools, request: AgentRequest, history: str):
        system = build_system_prompt(NATIVE_RULES, request.directive)
        first = f"CONVERSATION SO FAR:\n{history}\n\n{request.query}" if history else request.query
        messages: list[dict] = [{"role": "user", "content": first}]
        for _ in range(MAX_STEPS):
            turn = provider.chat(system, messages, TOOL_SPECS)
            if not turn.tool_calls:
                return turn.text.strip()
            messages.append({"role": "assistant", "content": turn.text, "tool_calls": turn.tool_calls})
            for call in turn.tool_calls:
                observation = tools.call(call.name, call.arguments)
                yield {
                    "type": "tool",
                    "tool": call.name,
                    "args": call.arguments,
                    "observation": observation[:500],
                }
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": observation}
                )
        messages.append(
            {"role": "user", "content": "You are out of tool calls. Answer now with what you found."}
        )
        return provider.chat(system, messages, []).text.strip()

    # -------------------------------------------------------- JSON loop
    def _json_loop(self, provider, tools: CodeTools, request: AgentRequest, history: str):
        system = build_system_prompt(JSON_RULES, request.directive)
        transcript = [
            f"CONVERSATION SO FAR:\n{history}" if history else "",
            f"USER REQUEST:\n{request.query}",
        ]
        for _ in range(MAX_STEPS):
            user = "\n\n".join(t for t in transcript if t)
            reply = provider.generate(
                PromptResult(system_prompt=system, user_prompt=user, full_prompt=user, prompt_size=len(user))
            ).response
            action = _parse(reply)
            if action is None:
                return reply.strip()
            if "final" in action:
                return str(action["final"])
            name, args = str(action.get("tool")), action.get("args") or {}
            observation = tools.call(name, args)
            yield {"type": "tool", "tool": name, "args": args, "observation": observation[:500]}
            transcript.append(f"YOU CALLED: {json.dumps(action)}\nRESULT:\n{observation}")

        transcript.append("You are out of tool calls. Give the final answer now as plain text.")
        final = provider.generate(
            PromptResult(
                system_prompt=system, user_prompt="\n\n".join(transcript), full_prompt="", prompt_size=0
            )
        ).response
        parsed = _parse(final)
        return str(parsed["final"]) if parsed and "final" in parsed else final


def _parse(reply: str) -> dict | None:
    return first_json_with(reply, "tool", "final")
