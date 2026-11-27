"""Agent crew: pages (/crew/...) and API (/crews/...)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from voxcode.config.settings import settings
from voxcode.core.repository import RepositoryPathError, resolve_repository_path
from voxcode.crew.trace import Trace, trace_store

UI_DIR = Path(__file__).resolve().parents[2] / "ui"
pages = APIRouter(include_in_schema=False)
router = APIRouter(prefix="/crews", tags=["crew"])


@pages.get("/crew")
@pages.get("/crew/{run_id}")
def crew_page(run_id: str = "") -> HTMLResponse:
    html = (UI_DIR / "crew.html").read_text(encoding="utf-8")
    token_json = json.dumps(settings.API_TOKEN).replace("<", "\\u003c")
    return HTMLResponse(
        html.replace("</head>", f"<script>window.VOXCODE_TOKEN = {token_json};</script>\n</head>", 1)
    )


class CrewRequest(BaseModel):
    task: str = Field(min_length=1, max_length=4000)
    repository_path: str
    mode: Literal["auto", "answer", "change"] = "auto"
    run_tests: bool = True


def start_run(
    task: str, repo: Path, mode: str = "auto", run_tests: bool = True, background: bool = True
) -> Trace:
    from voxcode.agent.runtime import runtime_cache
    from voxcode.api import services
    from voxcode.crew.crew import Crew

    key = services.ensure_indexed(repo)
    trace = Trace(task, str(repo), mode)
    trace_store.add(trace)
    crew = Crew(services.registry, services.memory(), runtime_cache, trace, repo, key, run_tests)

    def run() -> None:
        crew.run()
        trace_store.save(trace)

    if background:
        threading.Thread(target=run, daemon=True, name=f"crew-{trace.id}").start()
    else:
        run()
    return trace


@router.post("")
async def create_run(payload: CrewRequest) -> dict:
    try:
        repo = resolve_repository_path(payload.repository_path)
    except RepositoryPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trace = await run_in_threadpool(start_run, payload.task, repo, payload.mode, payload.run_tests)
    return {"id": trace.id, "url": f"/crew/{trace.id}"}


@router.get("")
def list_runs(limit: int = 50) -> list[dict]:
    return trace_store.list(limit)


@router.get("/{run_id}")
def get_run(run_id: str) -> dict:
    data = trace_store.get(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return data


@router.get("/{run_id}/events")
def stream_events(run_id: str, after: int = -1) -> StreamingResponse:
    """Server-sent events: every event after `after`, then live ones until the run ends."""
    if trace_store.get(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")

    def generate():
        seq = after
        while True:
            trace = trace_store.live(run_id)
            if trace is None:  # finished in an earlier server process: replay from disk
                data = trace_store.get(run_id) or {"events": [], "status": "failed"}
                for event in data["events"][seq + 1 :]:
                    yield f"data: {json.dumps({'type': 'event', **event})}\n\n"
                yield f"data: {json.dumps({'type': 'end', 'status': data['status']})}\n\n"
                return
            for event in trace.events_after(seq, timeout=15):
                seq = event.seq
                yield f"data: {json.dumps({'type': 'event', **event.__dict__})}\n\n"
            if trace.status != "running" and seq >= len(trace.events) - 1:
                yield f"data: {json.dumps({'type': 'end', 'status': trace.status})}\n\n"
                return
            yield ": keep-alive\n\n"

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.post("/{run_id}/apply")
def apply_run(run_id: str) -> dict:
    """Apply the crew's final patch to the working tree (the run's request becomes ranker feedback)."""
    from voxcode.api import services
    from voxcode.core.repository import repository_key
    from voxcode.execution.diff.diff_utils import UnsafeDiffError
    from voxcode.execution.patch import patch_service
    from voxcode.learning import feedback

    data = trace_store.get(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Run not found")
    diff = (data.get("result") or {}).get("diff")
    if not diff:
        raise HTTPException(status_code=409, detail="This run has no patch to apply")
    repo = Path(data["repository_path"])
    try:
        files = patch_service.apply(repo, diff)
    except (UnsafeDiffError, patch_service.PatchError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    feedback.record(repository_key(repo), data["task"], positive_files=files, kind="accepted_patch")
    services.index_repository(repo)
    return {"status": "applied", "files_modified": files}
