import os
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.agent.agent_request import AgentRequest
from backend.agent.pipeline import AgentPipeline
from backend.core.model_registry import ModelRegistry
from backend.agent.memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/agent",
    tags=["agent"],
)


class AgentQueryRequest(BaseModel):
    query: str
    repository_path: Optional[str] = "."
    session_id: Optional[str] = "default_session"


class AgentQueryResponse(BaseModel):
    query: str
    summary: str
    diff: Optional[str] = None
    files_modified: list[str] = []
    status: str


registry = ModelRegistry()
memory = MemoryManager()


@router.post("/query", response_model=AgentQueryResponse)
def query_agent(payload: AgentQueryRequest):
    """
    Executes a high-level coding query or voice command against the agent pipeline.
    """
    try:
        repo_name = os.path.basename(os.path.abspath(payload.repository_path)) or "default_repo"
        pipeline = AgentPipeline(
            repository_name=repo_name,
            registry=registry,
            memory=memory,
        )

        request = AgentRequest(
            instruction=payload.query,
            session_id=payload.session_id,
        )

        response = pipeline.run(request)

        return AgentQueryResponse(
            query=payload.query,
            summary=getattr(response, "message", "Query processed successfully."),
            diff=getattr(response, "diff", None),
            files_modified=getattr(response, "files_modified", []),
            status="success",
        )
    except Exception as e:
        logger.error(f"Agent pipeline execution error: {e}")
        # Return graceful response if full agent models are not initialized
        return AgentQueryResponse(
            query=payload.query,
            summary=f"Processed voice command: '{payload.query}'. (Pipeline status: {str(e)})",
            diff=None,
            files_modified=[],
            status="partial_or_error",
        )
