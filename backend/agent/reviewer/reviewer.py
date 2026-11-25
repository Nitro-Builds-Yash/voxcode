"""Second-pass LLM review of a proposed patch."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.core.json_extract import first_json_with
from backend.generation.prompts.prompt_result import PromptResult
from backend.generation.prompts.system_prompts import REVIEWER_RULES, build_system_prompt


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
    """Parse the reviewer's verdict. Anything unparseable counts as a rejection."""
    verdict = first_json_with(raw, "approved")
    if verdict is None:
        return ReviewResult(False, [f"Reviewer reply was not valid JSON: {raw.strip()[:300]}"])
    issues = verdict.get("issues") or []
    if not isinstance(issues, list):
        issues = [issues]
    return ReviewResult(verdict.get("approved") is True, [str(i) for i in issues if str(i).strip()])
