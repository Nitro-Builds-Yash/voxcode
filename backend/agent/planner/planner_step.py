from enum import Enum

from pydantic import BaseModel


class StepType(str, Enum):
    RETRIEVE = "retrieve"
    RERANK = "rerank"
    EXPAND_GRAPH = "expand_graph"
    BUILD_CONTEXT = "build_context"
    BUILD_PROMPT = "build_prompt"
    GENERATE = "generate"


class PlannerStep(BaseModel):
    order: int
    step_type: StepType
    description: str
