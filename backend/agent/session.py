from __future__ import annotations
import uuid
import time
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class SessionMessage(BaseModel):
    role: str
    content: str
    timestamp: float = Field(default_factory=time.time)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentSession(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    repository_name: str
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    messages: List[SessionMessage] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def add_message(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None):
        msg = SessionMessage(role=role, content=content, metadata=metadata or {})
        self.messages.append(msg)
        self.updated_at = time.time()

    def get_history_summary(self, max_turns: int = 5) -> List[Dict[str, str]]:
        recent = self.messages[-max_turns*2:] if max_turns > 0 else self.messages
        return [{"role": m.role, "content": m.content} for m in recent]
