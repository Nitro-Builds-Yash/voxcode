"""Review pages (/review/...) and the review API (/reviews/...)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from voxcode.config.settings import settings
from voxcode.core.repository import RepositoryPathError, resolve_repository_path
from voxcode.review import flow
from voxcode.review.feedback import revision_diff, title_from
from voxcode.review.models import Annotation, AnnotationKind
from voxcode.review.store import ReviewNotFound, review_store

UI_DIR = Path(__file__).resolve().parents[2] / "ui"
pages = APIRouter(include_in_schema=False)
router = APIRouter(prefix="/reviews", tags=["review"])
flow.install()


def _page() -> HTMLResponse:
    html = (UI_DIR / "review.html").read_text(encoding="utf-8")
    token_json = json.dumps(settings.API_TOKEN).replace("<", "\\u003c")
    return HTMLResponse(
        html.replace("</head>", f"<script>window.VOXCODE_TOKEN = {token_json};</script>\n</head>", 1)
    )


@pages.get("/review")
@pages.get("/review/share")
@pages.get("/review/{review_id}")
def review_page(review_id: str = "") -> HTMLResponse:
    """One page: archive, a review, or a shared (read-only, in-URL) review."""
    return _page()


class CreateReview(BaseModel):
    kind: Literal["plan", "diff", "document"] = "plan"
    content: str = Field(min_length=1, max_length=2_000_000)
    title: str = Field(default="", max_length=200)
    source: str = Field(default="cli", max_length=40)
    meta: dict = Field(default_factory=dict)


class NewAnnotation(BaseModel):
    kind: AnnotationKind = "comment"
    quote: str = Field(default="", max_length=20_000)
    comment: str = Field(default="", max_length=20_000)
    replacement: str = Field(default="", max_length=20_000)
    file: str = Field(default="", max_length=1000)
    line: int | None = None
    via_voice: bool = False


class DecisionRequest(BaseModel):
    decision: Literal["approve", "deny"]
    comment: str = Field(default="", max_length=20_000)


class Revision(BaseModel):
    content: str = Field(min_length=1, max_length=2_000_000)


class PlanRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    repository_path: str
    run_tests: bool = True


def _get(review_id: str):
    try:
        return review_store.get(review_id)
    except ReviewNotFound:
        raise HTTPException(status_code=404, detail="Review not found") from None


def _view(review) -> dict:
    body = review.model_dump()
    body["revision"] = review.revision
    body["content"] = review.content
    body["changes"] = (
        revision_diff(review.revisions[-2], review.content)
        if len(review.revisions) > 1 and review.kind != "diff"
        else []
    )
    body["url"] = f"/review/{review.id}"
    return body


@router.post("")
def create_review(payload: CreateReview) -> dict:
    title = payload.title or title_from(payload.content, payload.kind.title())
    session = payload.meta.get("claude_session")
    if session:  # the agent revised a plan it got feedback on: same review, new version
        existing = review_store.find_open(claude_session=session)
        if existing is not None:
            review = review_store.revise(existing.id, payload.content)
            review_store.update(review.id, title=title)
            return {
                "id": review.id,
                "url": f"/review/{review.id}",
                "revision": review.revision,
                "revised": True,
            }
    review = review_store.create(payload.kind, payload.content, title, payload.source, payload.meta)
    return {"id": review.id, "url": f"/review/{review.id}", "revision": 0, "revised": False}


@router.get("")
def list_reviews(limit: int = 100) -> list[dict]:
    return review_store.list(limit)


@router.post("/plan")
async def plan_and_review(payload: PlanRequest) -> dict:
    """VoxCode's own flow: plan the change and open the plan for review."""
    try:
        repo = resolve_repository_path(payload.repository_path)
    except RepositoryPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        review = await run_in_threadpool(flow.start_plan_review, payload.query, repo, payload.run_tests)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"id": review.id, "url": f"/review/{review.id}"}


@router.get("/{review_id}")
def get_review(review_id: str) -> dict:
    return _view(_get(review_id))


@router.post("/{review_id}/annotations")
def add_annotation(review_id: str, payload: NewAnnotation) -> dict:
    _get(review_id)
    if not (payload.comment.strip() or payload.kind in ("delete", "replace", "insert")):
        raise HTTPException(status_code=422, detail="A comment needs text")
    return review_store.annotate(review_id, Annotation(**payload.model_dump())).model_dump()


@router.delete("/{review_id}/annotations/{annotation_id}")
def delete_annotation(review_id: str, annotation_id: str) -> dict:
    _get(review_id)
    review_store.remove_annotation(review_id, annotation_id)
    return {"deleted": annotation_id}


@router.post("/{review_id}/decision")
def decide(review_id: str, payload: DecisionRequest) -> dict:
    _get(review_id)
    try:
        decision = review_store.decide(
            review_id, "approved" if payload.decision == "approve" else "denied", payload.comment
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return decision.model_dump()


@router.post("/{review_id}/revisions")
def add_revision(review_id: str, payload: Revision) -> dict:
    _get(review_id)
    return _view(review_store.revise(review_id, payload.content))


@router.get("/{review_id}/wait")
async def wait_for_decision(review_id: str, revision: int | None = None, timeout: float = 55.0) -> dict:
    """Long-poll until the given (default: current) revision is decided."""
    _get(review_id)
    decision = await run_in_threadpool(review_store.wait, review_id, revision, max(0.0, min(timeout, 120.0)))
    return {"decided": decision is not None, **(decision.model_dump() if decision else {})}
