from __future__ import annotations

import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field

AnnotationKind = Literal["comment", "delete", "replace", "insert", "line"]
Status = Literal["pending", "approved", "denied", "revising"]


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Annotation(BaseModel):
    id: str = Field(default_factory=new_id)
    kind: AnnotationKind = "comment"
    quote: str = ""  # selected text (plans) or the code line (diffs)
    comment: str = ""
    replacement: str = ""  # for replace / insert
    file: str = ""  # diffs: file path
    line: int | None = None  # diffs: new-file line number
    revision: int = 0
    via_voice: bool = False
    created: float = Field(default_factory=time.time)


class Decision(BaseModel):
    revision: int
    decision: Literal["approved", "denied"]
    feedback: str = ""
    at: float = Field(default_factory=time.time)


class Review(BaseModel):
    id: str = Field(default_factory=new_id)
    kind: Literal["plan", "diff", "document"] = "plan"
    title: str = "Plan"
    source: str = "voxcode"  # voxcode | claude-code | cli
    # Plans/documents: markdown per revision. Diffs: unified diff per revision.
    revisions: list[str] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)
    status: Status = "pending"
    general_comment: str = ""
    decisions: list[Decision] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)  # query, repository_path, session ids, next review...
    created: float = Field(default_factory=time.time)
    updated: float = Field(default_factory=time.time)

    @property
    def revision(self) -> int:
        return len(self.revisions) - 1

    @property
    def content(self) -> str:
        return self.revisions[-1] if self.revisions else ""

    def current_annotations(self) -> list[Annotation]:
        return [a for a in self.annotations if a.revision == self.revision]

    def summary(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "source": self.source,
            "status": self.status,
            "revisions": len(self.revisions),
            "annotations": len(self.annotations),
            "created": self.created,
            "updated": self.updated,
            "repository_path": self.meta.get("repository_path") or self.meta.get("cwd", ""),
        }
