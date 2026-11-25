"""Agent endpoints: answer questions, propose/apply edits, stream replies."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.agent.agent_request import AgentRequest
from backend.agent.runtime import RepositoryNotIndexedError
from backend.api import services
from backend.config.settings import settings
from backend.core.repository import RepositoryPathError, resolve_repository_path
from backend.execution.diff.diff_utils import UnsafeDiffError
from backend.execution.patch import patch_service
from backend.execution.testing.test_runner import run_tests
from backend.generation.providers import ProviderError
from backend.voice.tts.tts_service import tts_service

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


class PatchRequest(BaseModel):
    repository_path: str
    diff: str = Field(min_length=1)


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
    repo, request, emotion = _prepare(payload)
    mode = payload.mode
    body: dict[str, Any]
    if mode == "auto":
        mode = "edit" if services.pipeline().plan(request).is_edit else "answer"

    try:
        if mode == "edit":
            result = services.edit_agent().run(request, repo, run_tests=payload.run_tests)
            body = {
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
            result = services.edit_agent().run(request, repo, run_tests=payload.run_tests)
            yield {"type": "edit", **result.to_dict()}
        elif mode == "agentic":
            yield from services.tool_agent().run(request, repo)
        else:
            yield from services.pipeline().stream(request)

    def events():
        yield _sse({"type": "start", "mode": mode, "emotion": emotion.model_dump() if emotion else None})
        try:
            for event in generator():
                yield _sse(event)
        except Exception as exc:  # surface errors to the client mid-stream
            logger.warning("Stream failed: %s", exc)
            yield _sse({"type": "error", "detail": str(exc)})

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
def apply_patch(payload: PatchRequest) -> dict:
    """Apply a patch to the working tree (keeps it for /agent/undo)."""
    repo = _resolve(payload.repository_path)
    try:
        files = patch_service.apply(repo, payload.diff)
    except (UnsafeDiffError, patch_service.PatchError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    services.index_repository(repo)
    return {"status": "applied", "files_modified": files}


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
