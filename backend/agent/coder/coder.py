"""Turns a request plus code context into a unified diff."""

from __future__ import annotations

from dataclasses import dataclass

from backend.execution.diff.diff_utils import extract_diff
from backend.generation.context.context_result import ContextResult
from backend.generation.prompts.prompt_builder import PromptBuilder
from backend.generation.prompts.system_prompts import CODER_RULES


@dataclass
class CoderOutput:
    explanation: str
    diff: str | None
    raw: str


class Coder:
    def __init__(self, provider, prompt_builder: PromptBuilder | None = None):
        self.provider = provider
        self.prompt_builder = prompt_builder or PromptBuilder()

    def propose(
        self,
        context: ContextResult,
        feedback: str | None = None,
        directive: str | None = None,
    ) -> CoderOutput:
        task = "Write the patch that implements the user's request."
        if feedback:
            task += (
                "\n\nYour previous attempt was rejected. Fix these problems and output a "
                f"complete new patch:\n{feedback}"
            )
        prompt = self.prompt_builder.build(context, directive=directive, rules=CODER_RULES, task=task)
        raw = self.provider.generate(prompt).response
        diff = extract_diff(raw)
        explanation = raw.split("```", 1)[0].strip() if diff else raw.strip()
        return CoderOutput(explanation=explanation[:1000], diff=diff, raw=raw)
