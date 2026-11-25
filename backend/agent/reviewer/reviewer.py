"""Second-pass LLM review of a proposed patch."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from backend.generation.prompts.prompt_result import PromptResult
from backend.generation.prompts.system_prompts import REVIEWER_RULES, build_system_prompt

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class ReviewResult:
    approved: bool
    issues: list[str] = field(default_factory=list)


class Reviewer:
    def __init__(self, provider):
        self.provider = provider

    def review(self, request: str, diff: str, validation: str) -> ReviewResult:
        system = build_system_prompt(REVIEWER_RULES)
        user = (
            f"USER REQUEST:\n{request}\n\nPROPOSED PATCH:\n```diff\n{diff}```\n\n"
            f"VALIDATION OUTPUT:\n{validation or '(none)'}\n\nRespond with the JSON verdict."
        )
        raw = self.provider.generate(
            PromptResult(
                system_prompt=system,
                user_prompt=user,
                full_prompt=f"{system}\n\n{user}",
                prompt_size=len(system) + len(user),
            )
        ).response
        return parse_review(raw)


def parse_review(raw: str) -> ReviewResult:
    match = _JSON_RE.search(raw)
    if match:
        try:
            data = json.loads(match.group(0))
            issues = [str(i) for i in data.get("issues", []) if str(i).strip()]
            return ReviewResult(bool(data.get("approved")), issues)
        except (ValueError, AttributeError):
            pass
    lowered = raw.lower()
    approved = "approved" in lowered and "not approved" not in lowered
    return ReviewResult(approved, [] if approved else [raw.strip()[:500]])
