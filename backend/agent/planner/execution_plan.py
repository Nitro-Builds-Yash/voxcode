from enum import Enum

from pydantic import BaseModel

from backend.agent.planner.planner_step import PlannerStep


class TaskType(str, Enum):
    EXPLAIN = "explain"
    SEARCH = "search"
    GENERATE = "generate"
    REFACTOR = "refactor"
    FIX = "fix"
    TEST = "test"
    EXECUTE = "execute"
    CHAT = "chat"


# Tasks whose natural output is a code change rather than an answer.
EDIT_TASKS = {TaskType.GENERATE, TaskType.REFACTOR, TaskType.FIX, TaskType.TEST}


class ExecutionPlan(BaseModel):
    task: TaskType
    steps: list[PlannerStep]

    @property
    def is_edit(self) -> bool:
        return self.task in EDIT_TASKS
