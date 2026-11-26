"""VoxCode's own plan → patch review loop.

    request ─> plan ─(review)─ deny ─> re-plan with the feedback ─(review)─ …
                          └─ approve ─> patch (plan as the spec) ─(code review)─ deny ─> regenerate
                                                                            └─ approve ─> apply

Plans and patches are produced in background threads; the review page polls
and follows `meta.next` from the plan review to the code review.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from voxcode.agent.agent_request import AgentRequest
from voxcode.review.feedback import title_from
from voxcode.review.models import Decision, Review
from voxcode.review.store import ReviewStore, review_store

logger = logging.getLogger(__name__)
FLOW = "voxcode-edit"


def _request(review_id: str, repo: Path, query: str) -> AgentRequest:
    from voxcode.api import services

    return AgentRequest(
        session_id=f"review-{review_id}", repository_key=services.ensure_indexed(repo), query=query
    )


def _edit_agent():
    from voxcode.api import services

    return services.edit_agent()


def start_plan_review(
    query: str, repo: Path, run_tests: bool = True, store: ReviewStore = review_store
) -> Review:
    """Plan the change and open it for review (raises if planning fails)."""
    result = _edit_agent().run(_request("new", repo, query), repo, run_tests=False, plan_only=True)
    if not result.plan:
        raise RuntimeError(result.summary or "The model did not produce a plan")
    return store.create(
        "plan",
        result.plan,
        title_from(result.plan, query[:120]),
        source="voxcode",
        meta={"flow": FLOW, "query": query, "repository_path": str(repo), "run_tests": run_tests},
    )


def _background(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True, name="review-flow").start()


def _replan(store: ReviewStore, review: Review, decision: Decision) -> None:
    repo = Path(review.meta["repository_path"])
    query = f"{review.meta['query']}\n\nThe reviewer rejected the previous plan:\n\n{review.content}\n\n{decision.feedback}"
    try:
        result = _edit_agent().run(_request(review.id, repo, query), repo, run_tests=False, plan_only=True)
        if not result.plan:
            raise RuntimeError(result.summary or "no plan")
        store.revise(review.id, result.plan)
    except Exception as exc:
        store.update(review.id, status="denied", meta={"error": f"Re-planning failed: {exc}"})


def _implement(
    store: ReviewStore, review: Review, plan: str, feedback: str = "", diff_review: str | None = None
) -> None:
    repo = Path(review.meta["repository_path"])
    query = review.meta["query"]
    if feedback:
        query += f"\n\nThe reviewer rejected the previous patch:\n\n{feedback}"
    try:
        result = _edit_agent().run(
            _request(review.id, repo, query), repo, run_tests=review.meta.get("run_tests", True), plan=plan
        )
        if not result.diff:
            raise RuntimeError(result.summary or "no patch")
        if diff_review:
            store.revise(diff_review, result.diff)
            store.update(diff_review, meta={"summary": result.summary, "edit_status": result.status})
            return
        child = store.create(
            "diff",
            result.diff,
            f"Changes: {review.title}",
            source="voxcode",
            meta={
                "flow": FLOW,
                "query": review.meta["query"],
                "repository_path": str(repo),
                "run_tests": review.meta.get("run_tests", True),
                "plan": plan,
                "parent": review.id,
                "summary": result.summary,
                "edit_status": result.status,
            },
        )
        store.update(review.id, meta={"next": child.id})
    except Exception as exc:
        target = diff_review or review.id
        store.update(target, meta={"error": f"Writing the patch failed: {exc}"})


def _apply(store: ReviewStore, review: Review) -> None:
    from voxcode.api import services
    from voxcode.core.repository import repository_key
    from voxcode.execution.patch import patch_service
    from voxcode.learning import feedback

    repo = Path(review.meta["repository_path"])
    try:
        files = patch_service.apply(repo, review.content)
        feedback.record(
            repository_key(repo), review.meta["query"], positive_files=files, kind="accepted_patch"
        )
        services.index_repository(repo)
        store.update(review.id, meta={"applied": files})
    except Exception as exc:
        store.update(review.id, meta={"error": f"Applying the patch failed: {exc}"})


def on_decision(review: Review, decision: Decision, store: ReviewStore = review_store) -> None:
    if review.meta.get("flow") != FLOW:
        return  # external agents (Claude Code) read the decision themselves
    if review.kind == "plan":
        if decision.decision == "denied":
            store.update(review.id, status="revising")
            _background(_replan, store, review, decision)
        else:
            store.update(review.id, meta={"implementing": True})
            _background(_implement, store, review, review.content)
    elif review.kind == "diff":
        if decision.decision == "denied":
            store.update(review.id, status="revising")
            _background(_implement, store, review, review.meta.get("plan", ""), decision.feedback, review.id)
        else:
            _apply(store, review)


def install(store: ReviewStore = review_store) -> None:
    if on_decision not in store._listeners:
        store.on_decision(on_decision)
