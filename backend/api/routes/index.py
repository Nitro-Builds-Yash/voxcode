"""Repository indexing endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.api import services
from backend.core.repository import RepositoryPathError, resolve_repository_path

router = APIRouter(prefix="/index", tags=["index"])


class IndexRequest(BaseModel):
    repository_path: str
    full: bool = False  # ignore the previous index and rebuild everything
    watch: bool | None = None  # start/stop re-indexing on file changes


@router.post("/")
def index_workspace(payload: IndexRequest) -> dict:
    """Incrementally index a repository: chunks, BM25, vectors, call graph."""
    try:
        repo = resolve_repository_path(payload.repository_path)
    except RepositoryPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    stats = services.index_repository(repo, full=payload.full)
    if payload.watch is True:
        services.watcher.watch(repo, services.index_repository)
    elif payload.watch is False:
        services.watcher.unwatch(repo)

    return {
        **stats.to_dict(),
        "watching": services.watcher.is_watching(repo),
        "status": "completed",
    }


@router.get("/repos")
def list_indexed_repositories() -> list[dict]:
    return services.indexer.store.list()
