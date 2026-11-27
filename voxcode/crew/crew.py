"""The crew: a small multi-agent system for questions and code changes.

    Planner ──> Researchers (parallel, with code tools)
                   │
       question ───┴──> Writer ──> Reviewer (grounding check) ──┐ revise once if ungrounded
       change   ───────> Coder ──> Tester (sandboxed tests) ──> Reviewer (rubric) ──┐
                           ▲                                                        │
                           └──────── revise with the reviewer's issues and failures ┘

Each agent is a role with its own prompt and a token-metered view of the
model; all steps go into a Trace. A token budget and a round limit stop the
loop gracefully, returning the best result so far.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from voxcode.agent.agent_request import AgentRequest
from voxcode.agent.edit_agent import EditAgent
from voxcode.agent.tool_agent import ToolAgent
from voxcode.config.settings import settings
from voxcode.core.json_extract import first_json_with
from voxcode.crew.metered import AgentRegistry, MeteredProvider
from voxcode.crew.trace import Trace
from voxcode.generation import grounding
from voxcode.generation.prompts.prompt_result import PromptResult

logger = logging.getLogger(__name__)

PLANNER = """You are the PLANNER of a team of coding agents working on one repository.
Decide whether the task needs a code change, and split the investigation into at most
{n} focused research questions that other agents will answer in parallel by searching
and reading the code. Reply with JSON only:
{{"goal": "<one sentence>", "needs_change": true|false,
  "research": ["<question>", ...], "plan": ["<step>", ...]}}"""

WRITER = """You are the WRITER. Answer the user's task using ONLY the research notes from
your teammates. Name the files and functions involved (as `code`). If the notes don't
cover something, say so instead of guessing. Be concise."""

REVIEWER = """You are the REVIEWER of a proposed patch. Judge it against the goal and plan,
using the test report. Reply with JSON only:
{"approved": true|false, "score": 1-5, "issues": ["<specific, actionable problem>", ...]}
Approve only if the patch does what the goal asks and the tests pass (or none exist)."""

ROLES = ["planner", "researcher", "writer", "coder", "tester", "reviewer"]


def _prompt(system: str, user: str) -> PromptResult:
    return PromptResult(system_prompt=system, user_prompt=user, full_prompt=user, prompt_size=len(user))


class Crew:
    def __init__(
        self,
        registry,
        memory,
        runtimes,
        trace: Trace,
        repo: Path,
        repository_key: str,
        run_tests: bool = True,
    ):
        self.registry, self.memory, self.runtimes = registry, memory, runtimes
        self.trace, self.repo, self.key, self.run_tests = trace, repo, repository_key, run_tests
        self._base = registry.get_provider()

    # ------------------------------------------------------------ helpers
    def _provider(self, agent: str) -> MeteredProvider:
        return MeteredProvider(self._base, self.trace, agent)

    def _over_budget(self) -> bool:
        if self.trace.total_tokens > settings.CREW_MAX_TOKENS:
            self.trace.emit(
                "crew", "error", f"Token budget of {settings.CREW_MAX_TOKENS:,} reached; stopping here."
            )
            return True
        return False

    def _ask(self, agent: str, system: str, user: str) -> str:
        return self._provider(agent).generate(_prompt(system, user)).response.strip()

    # ------------------------------------------------------------ roles
    def plan(self) -> dict[str, Any]:
        self.trace.emit("planner", "start", "Reading the task")
        reply = self._ask("planner", PLANNER.format(n=settings.CREW_MAX_RESEARCHERS), self.trace.task)
        plan = first_json_with(reply, "research") or first_json_with(reply, "goal") or {}
        research = [str(q) for q in plan.get("research") or [] if str(q).strip()][
            : settings.CREW_MAX_RESEARCHERS
        ]
        if not research:
            research = [self.trace.task]
        needs_change = bool(plan.get("needs_change"))
        if self.trace.mode == "answer":
            needs_change = False
        elif self.trace.mode == "change":
            needs_change = True
        elif not plan:  # the model didn't follow the format: fall back to the intent classifier
            from voxcode.agent.planner.execution_plan import EDIT_TASKS
            from voxcode.agent.planner.task_classifier import TaskClassifier

            needs_change = TaskClassifier().classify(self.trace.task) in EDIT_TASKS
        result: dict[str, Any] = {
            "goal": str(plan.get("goal") or self.trace.task),
            "needs_change": needs_change,
            "research": research,
            "plan": [str(s) for s in plan.get("plan") or []],
        }
        self.trace.emit(
            "planner",
            "result",
            f"{'Change' if needs_change else 'Question'}: {result['goal']}",
            research=research,
            plan=result["plan"],
        )
        return result

    def research(self, questions: list[str]) -> list[dict]:
        def investigate(index: int, question: str) -> dict:
            agent = f"researcher-{index + 1}"
            self.trace.emit(agent, "start", question)
            registry: Any = AgentRegistry(self.registry, self._provider(agent))
            tool_agent = ToolAgent(registry, self.memory, self.runtimes)
            request = AgentRequest(
                session_id=f"crew-{self.trace.id}-{index}",
                repository_key=self.key,
                query=f"{question}\n\nReport what you found concisely, naming the files and functions.",
            )
            answer, files = "", []
            try:
                for event in tool_agent.run(request, self.repo):
                    if event["type"] == "tool":
                        path = (event.get("args") or {}).get("path")
                        if path and path not in files:
                            files.append(path)
                        self.trace.emit(
                            agent,
                            "tool",
                            event["tool"],
                            args=event.get("args"),
                            observation=event.get("observation", "")[:300],
                        )
                    elif event["type"] == "done":
                        answer = event["answer"]
            except Exception as exc:
                self.trace.emit(agent, "error", str(exc))
                return {"question": question, "answer": "", "files": files}
            self.trace.emit(agent, "result", answer[:600], files=files)
            return {"question": question, "answer": answer, "files": files}

        workers = max(1, min(len(questions), settings.CREW_MAX_RESEARCHERS))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="crew-research") as pool:
            return list(pool.map(lambda pair: investigate(*pair), enumerate(questions)))

    @staticmethod
    def _notes(findings: list[dict]) -> str:
        return "\n\n".join(f"Q: {f['question']}\nA: {f['answer'] or '(nothing found)'}" for f in findings)

    def write(self, findings: list[dict]) -> dict:
        notes = self._notes(findings)
        names = grounding.repository_names(self.runtimes.retriever(self.key).index.chunks)
        answer, quality = "", grounding.Grounding()
        for round_no in range(1, settings.CREW_MAX_ROUNDS + 1):
            self.trace.emit(
                "writer", "start", "Drafting the answer" if round_no == 1 else "Revising the answer"
            )
            extra = ""
            if round_no > 1:
                extra = (
                    "\n\nThe reviewer found names that are not in the code or the notes: "
                    + ", ".join(quality.unsupported + quality.bad_citations)
                    + ". Remove or correct them."
                )
            answer = self._ask(
                "writer", WRITER, f"TASK:\n{self.trace.task}\n\nRESEARCH NOTES:\n{notes}{extra}"
            )
            self.trace.emit("writer", "result", answer[:800])
            quality = grounding.check(answer, notes, *names)
            verdict = "grounded" if quality.groundedness >= 0.75 else "partly ungrounded"
            self.trace.emit(
                "reviewer",
                "result",
                f"Groundedness {quality.groundedness:.0%} ({verdict})",
                groundedness=quality.groundedness,
                unsupported=quality.unsupported,
            )
            if quality.groundedness >= 0.75 or self._over_budget():
                break
        return {"answer": answer, "quality": quality.to_dict()}

    def change(self, plan: dict[str, Any], findings: list[dict]) -> dict:
        from voxcode.execution.testing.test_runner import failure_files, run_tests_with_patch

        notes = self._notes(findings)
        plan_text = plan["goal"] + ("\n" + "\n".join(f"- {s}" for s in plan["plan"]) if plan["plan"] else "")
        feedback, best = "", {}
        for round_no in range(1, settings.CREW_MAX_ROUNDS + 1):
            self.trace.emit(
                "coder", "start", "Writing the patch" if round_no == 1 else f"Revision {round_no}"
            )
            coder_registry: Any = AgentRegistry(self.registry, self._provider("coder"))
            coder = EditAgent(coder_registry, self.memory, self.runtimes)
            query = f"{self.trace.task}\n\nResearch notes from teammates:\n{notes}{feedback}"
            request = AgentRequest(
                session_id=f"crew-{self.trace.id}-coder", repository_key=self.key, query=query
            )
            edit = coder.run(request, self.repo, run_tests=False, plan=plan_text)
            if not edit.diff:
                self.trace.emit("coder", "error", edit.summary or "No patch produced")
                break
            files = [f["path"] if isinstance(f, dict) else str(f) for f in edit.to_dict().get("files", [])]
            self.trace.emit(
                "coder", "result", edit.summary or "Patch ready", files=files, diff=edit.diff[:4000]
            )

            tests: dict[str, Any] = {"ran": False, "passed": None, "output": ""}
            if self.run_tests:
                self.trace.emit(
                    "tester", "start", "Running the tests against the patch in a copy of the repo"
                )
                report = run_tests_with_patch(self.repo, edit.diff, settings.TEST_TIMEOUT_SECONDS)
                if report is None:
                    self.trace.emit("tester", "result", "No test command found for this project")
                else:
                    tests = {"ran": True, "passed": report.passed, "output": report.output[-2000:]}
                    culprit = failure_files(report.output, self.repo) if not report.passed else []
                    self.trace.emit(
                        "tester",
                        "result",
                        "All tests passed"
                        if report.passed
                        else f"Tests failed{(' in ' + ', '.join(culprit)) if culprit else ''}",
                        passed=report.passed,
                    )

            self.trace.emit("reviewer", "start", "Reviewing the patch")
            test_text = (
                "not run"
                if not tests["ran"]
                else ("passed" if tests["passed"] else "FAILED:\n" + tests["output"][-1500:])
            )
            reply = self._ask(
                "reviewer",
                REVIEWER,
                f"GOAL:\n{plan_text}\n\nPATCH:\n{edit.diff[:12000]}\n\nTEST REPORT: {test_text}",
            )
            review = first_json_with(reply, "approved") or {
                "approved": tests["passed"] is not False,
                "score": 3,
                "issues": [],
            }
            approved = bool(review.get("approved")) and tests["passed"] is not False
            issues = [str(i) for i in review.get("issues") or []]
            self.trace.emit(
                "reviewer",
                "result",
                ("Approved" if approved else "Changes requested")
                + (f" (score {review.get('score')}/5)" if review.get("score") else ""),
                approved=approved,
                issues=issues,
            )
            best = {
                "diff": edit.diff,
                "files": files,
                "summary": edit.summary,
                "tests": tests,
                "review": {**review, "approved": approved},
            }
            if approved or self._over_budget():
                break
            feedback = "\n\nThe reviewer rejected the previous patch:\n" + "\n".join(f"- {i}" for i in issues)
            if tests["ran"] and not tests["passed"]:
                feedback += "\n\nFailing test output:\n" + tests["output"][-1500:]
        return best

    # ------------------------------------------------------------ run
    def run(self) -> dict:
        try:
            plan = self.plan()
            findings = [] if self._over_budget() else self.research(plan["research"])
            if plan["needs_change"]:
                result = self.change(plan, findings)
                result["kind"] = "change"
            else:
                result = self.write(findings)
                result["kind"] = "answer"
            result["plan"] = plan
            self.trace.emit("crew", "done", "Finished")
            self.trace.finish("done", **result)
        except Exception as exc:
            logger.exception("Crew run failed")
            self.trace.emit("crew", "error", str(exc))
            self.trace.finish("failed", error=str(exc))
        return self.trace.result
