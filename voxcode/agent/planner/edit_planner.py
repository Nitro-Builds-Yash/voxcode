"""Draft a short, reviewable plan before writing a multi-file patch."""

from __future__ import annotations

import logging

from voxcode.core.json_extract import first_json_with
from voxcode.generation.context.context_result import ContextResult
from voxcode.generation.prompts.prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)

PLANNER_RULES = """
You plan code changes before they are written. Using the repository context,
list the concrete edits needed, one per file, in the order to make them.
Reply with JSON only:
{"summary": "one sentence", "steps": [{"file": "path/relative/to/repo", "change": "what to change and why"}]}
Prefer the smallest set of files. Include test files when behaviour changes.
""".strip()


def format_plan(data: dict) -> str:
    lines = [str(data.get("summary", "")).strip()]
    for number, step in enumerate(data.get("steps") or [], start=1):
        if isinstance(step, dict):
            lines.append(f"{number}. {step.get('file', '?')}: {step.get('change', '').strip()}")
    return "\n".join(line for line in lines if line)


def draft_plan(provider, context: ContextResult, directive: str | None = None) -> str | None:
    prompt = PromptBuilder().build(context, directive=directive, rules=PLANNER_RULES, task="Write the plan.")
    try:
        reply = provider.generate(prompt).response
    except Exception as exc:
        logger.warning("Planning failed, continuing without a plan: %s", exc)
        return None
    data = first_json_with(reply, "steps", "summary")
    return format_plan(data) if data else None
