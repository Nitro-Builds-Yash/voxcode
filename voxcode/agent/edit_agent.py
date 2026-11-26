"""Code-edit loop: coder -> git apply --check -> tests -> reviewer, with retries.

Nothing is written to the user's files here; the result is a validated diff
plus before/after previews. Applying is a separate, explicit step.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from voxcode.agent.agent_request import AgentRequest
from voxcode.agent.coder.coder import Coder
from voxcode.agent.memory.memory_manager import MemoryManager
from voxcode.agent.planner.edit_planner import draft_plan
from voxcode.agent.reviewer.reviewer import Reviewer
from voxcode.agent.runtime import RuntimeCache, runtime_cache
from voxcode.config.settings import settings
from voxcode.core.model_registry import ModelRegistry
from voxcode.execution.diff.diff_utils import UnsafeDiffError, touched_paths
from voxcode.execution.patch import patch_service
from voxcode.execution.testing.test_runner import failure_files, run_tests_with_patch
from voxcode.generation.context.context_builder import ContextBuilder, number_lines
from voxcode.monitoring import Metrics

logger = logging.getLogger(__name__)

FULL_FILE_LIMIT = 3
FULL_FILE_MAX_LINES = 600


@dataclass
class Attempt:
    number: int
    patch_applies: bool
    check_output: str = ""
    tests_passed: bool | None = None
    test_output: str = ""
    approved: bool | None = None
    issues: list[str] = field(default_factory=list)


@dataclass
class EditResult:
    status: str  # ready | needs_review | failed | planned
    summary: str
    diff: str | None
    files: list[dict]
    attempts: list[Attempt]
    test_command: str | None = None
    plan: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class EditAgent:
    def __init__(
        self,
        registry: ModelRegistry,
        memory: MemoryManager,
        runtimes: RuntimeCache | None = None,
    ):
        self.registry = registry
        self.memory = memory
        self.runtimes = runtimes or runtime_cache

    def run(
        self,
        request: AgentRequest,
        repo: Path,
        run_tests: bool = False,
        plan: str | None = None,
        plan_only: bool = False,
    ) -> EditResult:
        """Propose a validated patch.

        plan_only=True stops after drafting the plan so the user can approve
        or edit it; pass the approved text back as `plan` to execute it.
        """
        provider = self.registry.get_provider()
        with Metrics.stage("edit_context"):
            context = self._build_context(request, repo)
        if plan is None and (settings.PLAN_EDITS or plan_only):
            with Metrics.stage("edit_plan"):
                plan = draft_plan(provider, context, request.directive)
        if plan_only:
            return EditResult("planned", plan or "Could not draft a plan.", None, [], [], plan=plan)

        coder, reviewer = Coder(provider), Reviewer(provider)
        attempts: list[Attempt] = []
        feedback: str | None = None
        best: tuple[str, str] | None = None  # (diff, explanation) that applies
        test_command = None
        repair_files: list[str] = []

        for number in range(1, settings.EDIT_MAX_ATTEMPTS + 1):
            with Metrics.stage("edit_code"):
                output = coder.propose(context, feedback, request.directive, plan=plan)
            if not output.diff:
                attempts.append(Attempt(number, False, "No unified diff found in the reply."))
                feedback = "Your reply did not contain a ```diff block with a unified diff."
                continue

            try:
                ok, check_output = patch_service.check(repo, output.diff)
            except UnsafeDiffError as exc:
                ok, check_output = False, str(exc)
            attempt = Attempt(number, ok, check_output)
            attempts.append(attempt)
            if not ok:
                feedback = (
                    f"`git apply --check` failed:\n{check_output}\n\nThe rejected patch was:\n"
                    f"```diff\n{output.diff}```\nCopy context lines exactly from the FULL FILE sections."
                )
                continue
            best = (output.diff, output.explanation)

            validation = "git apply --check: OK"
            if run_tests:
                with Metrics.stage("edit_tests"):
                    report = run_tests_with_patch(repo, output.diff, settings.TEST_TIMEOUT_SECONDS)
                if report is not None:
                    test_command = report.command
                    attempt.tests_passed = report.passed
                    attempt.test_output = report.output[-2000:]
                    validation += f"\nTests ({report.command}): {'PASSED' if report.passed else 'FAILED'}\n{attempt.test_output}"

            with Metrics.stage("edit_review"):
                review = reviewer.review(request.query, output.diff, validation)
            attempt.approved, attempt.issues = review.approved, review.issues
            if review.approved and attempt.tests_passed is not False:
                return self._result(
                    "ready", output.explanation, output.diff, repo, attempts, test_command, request, plan
                )

            problems = list(review.issues)
            if attempt.tests_passed is False:
                problems.insert(0, f"Tests failed:\n{attempt.test_output}")
                # Repair: show the model the failing tests and traced files in full.
                new_files = [f for f in failure_files(attempt.test_output, repo) if f not in repair_files]
                if new_files:
                    repair_files += new_files
                    context = self._build_context(request, repo, extra_files=repair_files)
            feedback = (
                "\n".join(f"- {p}" for p in problems) or "Reviewer rejected the patch."
            ) + f"\n\nThe rejected patch was:\n```diff\n{output.diff}```"

        if best:
            return self._result("needs_review", best[1], best[0], repo, attempts, test_command, request, plan)
        return EditResult(
            "failed",
            "Could not produce a patch that applies cleanly.",
            None,
            [],
            attempts,
            test_command,
            plan,
        )

    # ------------------------------------------------------------ helpers
    def _build_context(self, request: AgentRequest, repo: Path, extra_files: list[str] | None = None):
        retriever = self.runtimes.retriever(request.repository_key)
        results = retriever.search(request.query)
        reranker = self.registry.get_reranker()
        try:
            results = (
                reranker.rerank(request.query, results, request.top_k)
                if reranker
                else results[: request.top_k]
            )
        except Exception as exc:
            logger.warning("Reranking failed, using retrieval order: %s", exc)
            results = results[: request.top_k]
        results = retriever.expand(results)

        # Diffs need exact surrounding lines, so include the most relevant
        # files in full (line-numbered) in addition to the retrieved chunks.
        full_files: list[str] = []
        wanted = list(dict.fromkeys([*(extra_files or []), *(r.file_path for r in results)]))
        limit = FULL_FILE_LIMIT + len(extra_files or [])
        for path in wanted:
            if len(full_files) >= limit:
                break
            file = repo / path
            try:
                lines = file.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            if len(lines) <= FULL_FILE_MAX_LINES:
                full_files.append(f"FULL FILE: {path}\n{number_lines(chr(10).join(lines), 1)}\n")

        # Chunks from files already included in full would only repeat code.
        full_paths = {section.split("\n", 1)[0].removeprefix("FULL FILE: ") for section in full_files}
        results = [r for r in results if r.file_path not in full_paths]
        history = self.memory.format_history(request.session_id)
        return ContextBuilder().build(request.query, results, history, extra_sections=full_files)

    def _result(self, status, summary, diff, repo, attempts, test_command, request, plan=None) -> EditResult:
        try:
            files = [asdict(change) for change in patch_service.preview(repo, diff)]
        except patch_service.PatchError as exc:
            logger.warning("Preview failed: %s", exc)
            files = [
                {"path": p, "original": "", "proposed": "", "status": "modified"} for p in touched_paths(diff)
            ]
        self.memory.add_messages(
            request.session_id,
            ("user", request.query),
            ("assistant", f"{summary}\n(proposed patch for {', '.join(f['path'] for f in files)})"),
        )
        return EditResult(status, summary, diff, files, attempts, test_command, plan)
