from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ConversationMessage(BaseModel):
    role: str
    content: str
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationHistory(BaseModel):
    session_id: str
    messages: list[ConversationMessage] = Field(default_factory=list)
