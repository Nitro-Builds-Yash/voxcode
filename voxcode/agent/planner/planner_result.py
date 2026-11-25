from pydantic import BaseModel

from voxcode.agent.planner.execution_plan import (
    ExecutionPlan,
)


class PlannerResult(BaseModel):
    query: str

    execution_plan: ExecutionPlan

    reasoning: str
