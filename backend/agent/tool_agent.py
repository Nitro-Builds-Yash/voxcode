"""Tool-using agent: the model decides which code tools to call.

Provider-agnostic: the model replies with one JSON object per turn, either a
tool call or a final answer, so it works with every configured provider
(including small local models) without native function-calling APIs.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Iterator
from pathlib import Path

from backend.agent.agent_request import AgentRequest
from backend.agent.memory.memory_manager import MemoryManager
from backend.agent.runtime import RuntimeCache, runtime_cache
from backend.core.json_extract import first_json_with
from backend.core.model_registry import ModelRegistry
from backend.generation.context.context_builder import number_lines
from backend.generation.prompts.prompt_result import PromptResult
from backend.generation.prompts.system_prompts import build_system_prompt

MAX_STEPS = 6
MAX_OBSERVATION_CHARS = 6000

TOOL_RULES = """
You can investigate the repository with tools before answering.
Reply with exactly ONE JSON object per turn and nothing else:

  {"tool": "search_code", "args": {"query": "..."}}          hybrid code search
  {"tool": "read_file", "args": {"path": "...", "start": 1, "end": 80}}
  {"tool": "find_references", "args": {"name": "symbol_name"}}
  {"tool": "list_files", "args": {"pattern": "*.py"}}
  {"final": "your answer, citing files as path:line"}

Use as few tool calls as needed (at most 6), then give the final answer.
""".strip()


class ToolAgent:
    def __init__(self, registry: ModelRegistry, memory: MemoryManager, runtimes: RuntimeCache | None = None):
        self.registry = registry
        self.memory = memory
        self.runtimes = runtimes or runtime_cache

    def run(self, request: AgentRequest, repo: Path) -> Iterator[dict]:
        """Yield `tool` events for each call, then a `done` event."""
        provider = self.registry.get_provider()
        retriever = self.runtimes.retriever(request.repository_key)
        tools = _Tools(repo, retriever)
        system = build_system_prompt(TOOL_RULES, request.directive)

        history = self.memory.format_history(request.session_id)
        transcript = [
            f"CONVERSATION SO FAR:\n{history}" if history else "",
            f"USER REQUEST:\n{request.query}",
        ]

        answer = None
        for _ in range(MAX_STEPS):
            user = "\n\n".join(t for t in transcript if t)
            reply = provider.generate(
                PromptResult(system_prompt=system, user_prompt=user, full_prompt=user, prompt_size=len(user))
            ).response
            action = _parse(reply)
            if action is None:
                answer = reply.strip()
                break
            if "final" in action:
                answer = str(action["final"])
                break
            name, args = str(action.get("tool")), action.get("args") or {}
            observation = tools.call(name, args)
            yield {"type": "tool", "tool": name, "args": args, "observation": observation[:500]}
            transcript.append(f"YOU CALLED: {json.dumps(action)}\nRESULT:\n{observation}")

        if answer is None:
            transcript.append("You are out of tool calls. Give the final answer now as plain text.")
            answer = provider.generate(
                PromptResult(
                    system_prompt=system, user_prompt="\n\n".join(transcript), full_prompt="", prompt_size=0
                )
            ).response
            parsed = _parse(answer)
            if parsed and "final" in parsed:
                answer = str(parsed["final"])

        self.memory.add_messages(request.session_id, ("user", request.query), ("assistant", answer))
        yield {
            "type": "done",
            "answer": answer,
            "provider": provider.provider_name,
            "model_name": provider.model,
        }


def _parse(reply: str) -> dict | None:
    return first_json_with(reply, "tool", "final")


class _Tools:
    def __init__(self, repo: Path, retriever):
        self.repo = repo
        self.retriever = retriever

    def call(self, name: str, args: dict) -> str:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            return f"Unknown tool '{name}'."
        try:
            return handler(**args)[:MAX_OBSERVATION_CHARS]
        except TypeError as exc:
            return f"Bad arguments for {name}: {exc}"
        except Exception as exc:
            return f"{name} failed: {exc}"

    def tool_search_code(self, query: str) -> str:
        results = self.retriever.search(query, 6)
        return (
            "\n\n".join(
                f"{r.file_path}:{r.start_line}-{r.end_line} {r.symbol_name}\n{r.content[:600]}"
                for r in results
            )
            or "No results."
        )

    def tool_read_file(self, path: str, start: int = 1, end: int = 120) -> str:
        target = (self.repo / path).resolve()
        if self.repo not in target.parents:
            return "Path is outside the repository."
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start, end = max(1, int(start)), min(len(lines), int(end), int(start) + 300)
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
        return (
            "\n".join(
                f
                for f in files
                if fnmatch.fnmatch(f, pattern) or fnmatch.fnmatch(f.rsplit("/", 1)[-1], pattern)
            )[:4000]
            or "No files."
        )
