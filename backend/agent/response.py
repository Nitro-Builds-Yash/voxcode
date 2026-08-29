from __future__ import annotations
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class AgentResponse(BaseModel):
    """
    Standardized response payload from the Agent pipeline.
    """
    answer: str
    retrieved_chunks: int = 0
    context_size: int = 0
    prompt_size: int = 0
    model_name: str = ""
    session_id: Optional[str] = None
    execution_time_ms: float = 0.0
    status: str = "success"
    metadata: Dict[str, Any] = Field(default_factory=dict)
