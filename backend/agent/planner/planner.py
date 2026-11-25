from backend.agent.planner.execution_plan import ExecutionPlan
from backend.agent.planner.planner_result import PlannerResult
from backend.agent.planner.planner_step import PlannerStep, StepType
from backend.agent.planner.task_classifier import TaskClassifier

_STEPS = [
    (StepType.RETRIEVE, "Hybrid BM25 + semantic retrieval"),
    (StepType.RERANK, "Cross-encoder reranking"),
    (StepType.EXPAND_GRAPH, "Add callers and callees of the top results"),
    (StepType.BUILD_CONTEXT, "Assemble token-budgeted context"),
    (StepType.BUILD_PROMPT, "Construct the LLM prompt"),
    (StepType.GENERATE, "Generate the answer"),
]


class Planner:
    def __init__(self):
        self.classifier = TaskClassifier()

    def plan(self, query: str) -> PlannerResult:
        task = self.classifier.classify(query)
        steps = [
            PlannerStep(order=i, step_type=step, description=text)
            for i, (step, text) in enumerate(_STEPS, start=1)
        ]
        return PlannerResult(
            query=query,
            execution_plan=ExecutionPlan(task=task, steps=steps),
            reasoning=f"Task classified as {task.value}",
        )
