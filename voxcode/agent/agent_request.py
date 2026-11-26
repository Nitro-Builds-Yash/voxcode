from pydantic import BaseModel


class AgentRequest(BaseModel):
    """A single user request handled by the agent."""

    session_id: str
    repository_key: str
    query: str
    top_k: int = 8
    # Optional delivery hint (e.g. from tone analysis); goes into the system
    # prompt only, never into the search query.
    directive: str | None = None
