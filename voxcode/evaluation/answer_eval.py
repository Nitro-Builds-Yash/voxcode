"""Answer-quality evaluation with a model as judge.

Each question in the set has a reference answer. VoxCode answers it through
the normal pipeline, then a judge model (the configured provider, or
--judge-provider) scores correctness and grounding from 1 to 5.

    voxcode eval --answers [--repo PATH] [--min-score 3.5]

A non-zero exit code when the mean is below --min-score makes this usable
as a CI gate whenever an API key is available.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from voxcode.core.json_extract import first_json_with
from voxcode.generation.prompts.prompt_result import PromptResult

DEFAULT_QUESTIONS = Path(__file__).with_name("answer_questions.json")

JUDGE = """You grade answers about a codebase. Compare the ANSWER to the REFERENCE.
Score 1-5: 5 = correct and complete, 4 = correct with minor gaps, 3 = partly
correct, 2 = mostly wrong, 1 = wrong or no answer. Penalise claims that
contradict the reference. Reply with JSON only: {"score": N, "reason": "..."}"""


def judge(provider, question: str, reference: str, answer: str) -> tuple[float, str]:
    user = f"QUESTION:\n{question}\n\nREFERENCE:\n{reference}\n\nANSWER:\n{answer}"
    reply = provider.generate(
        PromptResult(system_prompt=JUDGE, user_prompt=user, full_prompt=user, prompt_size=len(user))
    ).response
    verdict = first_json_with(reply, "score") or {}
    try:
        score = float(verdict.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(5.0, score)), str(verdict.get("reason", ""))[:200]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", type=Path)
    parser.add_argument("--questions", default=DEFAULT_QUESTIONS, type=Path)
    parser.add_argument("--judge-provider", default=None)
    parser.add_argument("--min-score", type=float, default=0.0)
    args = parser.parse_args(argv)

    from voxcode.agent.agent_request import AgentRequest
    from voxcode.api import services
    from voxcode.core.repository import resolve_repository_path

    repo = resolve_repository_path(str(args.repo))
    key = services.ensure_indexed(repo)
    judge_provider = services.registry.get_provider(args.judge_provider)
    cases = json.loads(args.questions.read_text())

    scores = []
    for number, case in enumerate(cases, start=1):
        response = services.pipeline().ask(
            AgentRequest(session_id=f"eval-{number}", repository_key=key, query=case["question"])
        )
        score, reason = judge(judge_provider, case["question"], case["reference"], response.answer)
        scores.append(score)
        print(f"[{number}/{len(cases)}] {score:.0f}/5  {case['question']}\n        {reason}")

    mean = sum(scores) / max(len(scores), 1)
    print(f"\nMean answer score: {mean:.2f} / 5 over {len(scores)} questions")
    if mean < args.min_score:
        sys.exit(f"Below --min-score {args.min_score}")


if __name__ == "__main__":
    main()
