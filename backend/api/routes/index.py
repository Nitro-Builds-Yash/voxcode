import os
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from backend.codebase.indexer.repository_indexer import RepositoryIndexer

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/index",
    tags=["index"],
)


class IndexRequest(BaseModel):
    repository_path: str


class IndexResponse(BaseModel):
    repository_path: str
    files_indexed: int
    chunks_created: int
    functions_found: int
    classes_found: int
    status: str


indexer = RepositoryIndexer()


@router.post("/", response_model=IndexResponse)
def index_workspace(payload: IndexRequest):
    """
    Scans and indexes the repository at the given local file path.
    Extracts AST symbols, builds chunks, and updates code intelligence.
    """
    path = os.path.abspath(payload.repository_path)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")

    try:
        chunks, total_funcs, total_classes = indexer.index_repository(path)
        return IndexResponse(
            repository_path=path,
            files_indexed=len({getattr(c, "file_path", "") for c in chunks}),
            chunks_created=len(chunks),
            functions_found=total_funcs,
            classes_found=total_classes,
            status="completed",
        )
    except Exception as e:
        logger.error(f"Error during workspace indexing: {e}")
        raise HTTPException(status_code=500, detail=str(e))
