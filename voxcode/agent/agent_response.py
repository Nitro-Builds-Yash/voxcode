from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    file_path: str
    symbol_name: str
    start_line: int
    end_line: int


class AgentResponse(BaseModel):
    """Final response returned by the agent pipeline."""

    answer: str
    task: str = "chat"
    retrieved_chunks: int = 0
    context_size: int = 0
    prompt_size: int = 0
    model_name: str = ""
    provider: str = ""
    sources: list[SourceRef] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)
    # Model-free hallucination check: groundedness, unsupported identifiers, bad citations.
    quality: dict = Field(default_factory=dict)
    # Cascade routing: difficulty, tier (small | main), whether it escalated and why.
    routing: dict = Field(default_factory=dict)
    cached: bool = False
