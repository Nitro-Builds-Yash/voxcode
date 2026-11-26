"""Agent endpoints: answer questions, propose/apply edits, stream replies."""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from voxcode.agent.agent_request import AgentRequest
from voxcode.agent.runtime import RepositoryNotIndexedError
from voxcode.api import services
from voxcode.config.settings import settings
from voxcode.core.repository import RepositoryPathError, resolve_repository_path
from voxcode.execution.diff.diff_utils import UnsafeDiffError
from voxcode.execution.patch import patch_service
from voxcode.execution.testing.test_runner import run_tests
from voxcode.generation.providers import ProviderError
from voxcode.integrations.github import GitError, commit_on_branch
from voxcode.monitoring.registry import collect_timings
from voxcode.voice.tts.tts_service import tts_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

Mode = Literal["auto", "answer", "edit", "agentic"]


class AgentQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    repository_path: str = "."
    session_id: str = "default_session"
    mode: Mode = "auto"
    run_tests: bool = False
    auto_tts: bool = False
    top_k: int = Field(default=settings.DEFAULT_TOP_K, ge=1, le=30)
    # Edits: stop after drafting a plan, or execute an approved (edited) plan.
    plan_only: bool = False
    plan: str | None = Field(default=None, max_length=8000)


class PatchRequest(BaseModel):
    repository_path: str
    diff: str = Field(min_length=1)


class ApplyRequest(PatchRequest):
    # working_tree: edit files in place (undoable); branch: commit on a new
    # branch via a temporary worktree; pull_request: branch + push + GitHub PR.
    target: Literal["working_tree", "branch", "pull_request"] = "working_tree"
    title: str = Field(default="VoxCode change", max_length=200)
    description: str = Field(default="", max_length=10000)
    # The request that produced the patch; the files it changes become
    # relevance feedback for the learned ranker.
    query: str = Field(default="", max_length=4000)


class FeedbackRequest(BaseModel):
    repository_path: str
    query: str = Field(min_length=1, max_length=4000)
    helpful: bool = True
    # Files / chunks that answered the question (usually the response's sources).
    files: list[str] = Field(default_factory=list, max_length=50)
    chunk_ids: list[str] = Field(default_factory=list, max_length=50)


class RepoRequest(BaseModel):
    repository_path: str


def _resolve(path: str):
    try:
        return resolve_repository_path(path)
    except RepositoryPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _prepare(payload: AgentQueryRequest):
    repo = _resolve(payload.repository_path)
    key = services.ensure_indexed(repo)
    emotion = services.analyze_emotion(payload.query)
    request = AgentRequest(
        session_id=payload.session_id,
        repository_key=key,
        query=payload.query,
        top_k=payload.top_k,
        directive=emotion.vox_adaptation if emotion else None,
    )
    return repo, request, emotion


def _voice_line(summary: str, emotion, files: int) -> str:
    if len(summary) <= 220:
        return summary
    state = emotion.primary_emotion if emotion else "calm"
    if files:
        return {
            "frustrated": "I found the issue and prepared a fix for you to review.",
            "urgent": "Patch ready for review.",
        }.get(state, f"I've prepared changes to {files} file{'s' if files != 1 else ''} for you to review.")
    return summary.split("\n", 1)[0][:220]


def _run_query(payload: AgentQueryRequest) -> dict:
    with collect_timings() as timings:
        result = _answer(payload)
    result["timings_ms"] = timings
    return result


def _edit(payload: AgentQueryRequest, repo, request):
    return services.edit_agent().run(
        request, repo, run_tests=payload.run_tests, plan=payload.plan, plan_only=payload.plan_only
    )


def _answer(payload: AgentQueryRequest) -> dict:
    repo, request, emotion = _prepare(payload)
    mode = payload.mode
    body: dict[str, Any]
    if mode == "auto":
        mode = "edit" if services.pipeline().plan(request).is_edit else "answer"

    try:
        if mode == "edit":
            result = _edit(payload, repo, request)
            body = {
                "plan": result.plan,
                "summary": result.summary,
                "diff": result.diff,
                "files": result.files,
                "files_modified": [f["path"] for f in result.files],
                "attempts": [a.__dict__ for a in result.attempts],
                "status": result.status,
                "task": "edit",
            }
        elif mode == "agentic":
            events = list(services.tool_agent().run(request, repo))
            body = {
                "summary": events[-1]["answer"],
                "tool_calls": [e for e in events if e["type"] == "tool"],
                "status": "success",
                "task": "agentic",
            }
        else:
            response = services.pipeline().ask(request)
            body = {
                "summary": response.answer,
                "sources": [s.model_dump() for s in response.sources],
                "status": "success",
                "task": response.task,
                "model_name": response.model_name,
                "provider": response.provider,
                "usage": response.usage,
                "quality": response.quality,
                "routing": response.routing,
                "cached": response.cached,
            }
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RepositoryNotIndexedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    body.setdefault("diff", None)
    body.setdefault("files_modified", [])
    voice_line = _voice_line(body["summary"], emotion, len(body["files_modified"]))
    if payload.auto_tts:
        tts_service.speak(voice_line, emotion.primary_emotion if emotion else "calm")
    return {
        "query": payload.query,
        **body,
        "mode": mode,
        "emotion": emotion.model_dump() if emotion else None,
        "vox_voice_feedback": voice_line,
    }


@router.post("/query")
def query_agent(payload: AgentQueryRequest) -> dict:
    """Answer a question, or (for edit requests) propose a validated patch.

    `mode`: `auto` picks `answer` or `edit` from the request's intent;
    `agentic` lets the model call code-search tools before answering.
    """
    return _run_query(payload)


@router.post("/stream")
async def stream_agent(payload: AgentQueryRequest) -> StreamingResponse:
    """Server-sent events for every mode.

    Answers: `sources`, then `token` chunks, then `done`. Agentic mode adds
    `tool` events. Edit requests (or `auto` mode on an edit intent) emit one
    `edit` event with the validated patch, since a patch is only useful whole.
    """
    repo, request, emotion = await run_in_threadpool(_prepare, payload)
    mode = payload.mode
    if mode == "auto":
        mode = "edit" if services.pipeline().plan(request).is_edit else "answer"

    def generator():
        if mode == "edit":
            result = _edit(payload, repo, request)
            yield {"type": "edit", **result.to_dict()}
        elif mode == "agentic":
            yield from services.tool_agent().run(request, repo)
        else:
            yield from services.pipeline().stream(request)

    events_queue: queue.Queue = queue.Queue()
    _END = object()

    def worker():
        # The whole run happens on one thread so per-stage timings (kept in a
        # context variable) are collected for the request.
        with collect_timings() as timings:
            try:
                for event in generator():
                    if event.get("type") == "done":
                        event = {**event, "timings_ms": dict(timings)}
                    events_queue.put(event)
            except Exception as exc:  # surface errors to the client mid-stream
                logger.warning("Stream failed: %s", exc)
                events_queue.put({"type": "error", "detail": str(exc)})
            finally:
                if mode == "edit":
                    events_queue.put({"type": "timings", "timings_ms": dict(timings)})
                events_queue.put(_END)

    threading.Thread(target=worker, name="voxcode-stream", daemon=True).start()

    def events():
        yield _sse({"type": "start", "mode": mode, "emotion": emotion.model_dump() if emotion else None})
        while (event := events_queue.get()) is not _END:
            yield _sse(event)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.post("/edit")
def propose_edit(payload: AgentQueryRequest) -> dict:
    """Always run the code-edit loop (never writes files)."""
    return _run_query(payload.model_copy(update={"mode": "edit"}))


@router.post("/preview")
def preview_patch(payload: PatchRequest) -> dict:
    repo = _resolve(payload.repository_path)
    try:
        ok, output = patch_service.check(repo, payload.diff)
        files = [c.__dict__ for c in patch_service.preview(repo, payload.diff)] if ok else []
    except (UnsafeDiffError, patch_service.PatchError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"applies": ok, "check_output": output, "files": files}


@router.post("/apply")
def apply_patch(payload: ApplyRequest) -> dict:
    """Apply a patch to the working tree (undo with /agent/undo), or commit it
    on a new branch and optionally open a GitHub pull request."""
    repo = _resolve(payload.repository_path)
    if payload.target != "working_tree":
        try:
            result = commit_on_branch(
                repo,
                payload.diff,
                payload.title,
                payload.description,
                push=payload.target == "pull_request",
                create_pr=payload.target == "pull_request",
            )
        except (UnsafeDiffError, GitError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _learn_from_patch(repo, payload.query, result.files)
        return {"status": "committed", "files_modified": result.files, **result.to_dict()}
    try:
        files = patch_service.apply(repo, payload.diff)
    except (UnsafeDiffError, patch_service.PatchError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _learn_from_patch(repo, payload.query, files)
    services.index_repository(repo)
    return {"status": "applied", "files_modified": files}


def _learn_from_patch(repo, query: str, files: list[str]) -> None:
    if query and settings.LEARNED_RANKER:
        from voxcode.core.repository import repository_key
        from voxcode.learning import feedback

        feedback.record(repository_key(repo), query, positive_files=files, kind="accepted_patch")


@router.post("/feedback")
def record_feedback(payload: FeedbackRequest) -> dict:
    """Thumbs up on an answer: its sources were the right code for the question.

    Feeds the learned ranker, which retrains in the background once enough
    feedback has been collected. A thumbs down is acknowledged but not used
    as a label (it doesn't say which code *would* have been right)."""
    from voxcode.core.repository import repository_key
    from voxcode.learning import feedback

    repo = _resolve(payload.repository_path)
    key = repository_key(repo)
    recorded = payload.helpful and feedback.record(
        key, payload.query, positive_files=payload.files, positive_chunks=payload.chunk_ids
    )
    count = len(feedback.load(key))
    if recorded and count >= settings.RANKER_MIN_FEEDBACK:
        threading.Thread(target=services.retrain_ranker, args=(key,), daemon=True).start()
    return {
        "recorded": bool(recorded),
        "feedback_entries": count,
        "min_for_training": settings.RANKER_MIN_FEEDBACK,
    }


@router.post("/undo")
def undo_patch(payload: RepoRequest) -> dict:
    repo = _resolve(payload.repository_path)
    try:
        files = patch_service.undo_last(repo)
    except patch_service.PatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    services.index_repository(repo)
    return {"status": "reverted", "files_modified": files}


@router.post("/test")
def run_repository_tests(payload: RepoRequest) -> dict:
    repo = _resolve(payload.repository_path)
    report = run_tests(repo, settings.TEST_TIMEOUT_SECONDS)
    if report is None:
        raise HTTPException(status_code=404, detail="No test command detected for this repository")
    return report.__dict__


@router.delete("/session/{session_id}")
def clear_session(session_id: str) -> dict:
    services.memory().clear_history(session_id)
    return {"status": "cleared"}
