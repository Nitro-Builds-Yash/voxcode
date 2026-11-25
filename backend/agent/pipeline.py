"""Question-answering pipeline: plan -> retrieve -> rerank -> expand ->
context -> prompt -> generate, with conversation memory."""

from __future__ import annotations

from collections.abc import Iterator

from backend.agent.agent_request import AgentRequest
from backend.agent.agent_response import AgentResponse, SourceRef
from backend.agent.executor import ExecutionContext, StepExecutor
from backend.agent.memory.memory_manager import MemoryManager
from backend.agent.planner.execution_plan import ExecutionPlan
from backend.agent.planner.planner import Planner
from backend.agent.planner.planner_step import StepType
from backend.agent.runtime import RuntimeCache, runtime_cache
from backend.core.model_registry import ModelRegistry
from backend.monitoring import Metrics


class AgentPipeline:
    def __init__(
        self,
        registry: ModelRegistry,
        memory: MemoryManager,
        runtimes: RuntimeCache | None = None,
        planner: Planner | None = None,
    ):
        self.registry = registry
        self.memory = memory
        self.runtimes = runtimes or runtime_cache
        self.planner = planner or Planner()

    def plan(self, request: AgentRequest) -> ExecutionPlan:
        return self.planner.plan(request.query).execution_plan

    def _executor(self, request: AgentRequest) -> StepExecutor:
        # Provider is resolved per request, so /config/update applies immediately.
        return StepExecutor(
            retriever=self.runtimes.retriever(request.repository_key),
            reranker=self.registry.get_reranker(),
            provider=self.registry.get_provider(),
        )

    def _context(self, request: AgentRequest, plan: ExecutionPlan) -> ExecutionContext:
        with Metrics.memory_read_timer():
            history = self.memory.format_history(request.session_id)
        return ExecutionContext(request=request, plan=plan, conversation_history=history)

    # ------------------------------------------------------------- answer
    def ask(self, request: AgentRequest, plan: ExecutionPlan | None = None) -> AgentResponse:
        plan = plan or self.plan(request)
        Metrics.record_request()
        Metrics.increment_sessions()
        try:
            with Metrics.pipeline_timer():
                executor = self._executor(request)
                context = self._context(request, plan)
                result = executor.execute_plan(context)
                if not result.success:
                    raise result.error or RuntimeError("Execution failed")
                assert context.generation and context.context and context.prompt
                answer = context.generation.response
                self._remember(request, answer)
                return AgentResponse(
                    answer=answer,
                    task=plan.task.value,
                    retrieved_chunks=context.context.num_chunks,
                    context_size=context.context.context_size,
                    prompt_size=context.prompt.prompt_size,
                    model_name=executor.provider.model,
                    provider=executor.provider.provider_name,
                    sources=_sources(context),
                )
        except Exception:
            Metrics.record_error()
            raise
        finally:
            Metrics.decrement_sessions()

    def stream(self, request: AgentRequest) -> Iterator[dict]:
        """Yield `sources`, then `token` events, then `done`."""
        plan = self.plan(request)
        Metrics.record_request()
        executor = self._executor(request)
        context = self._context(request, plan)
        result = executor.execute_plan(context, stop_before=StepType.GENERATE)
        if not result.success:
            Metrics.record_error()
            raise result.error or RuntimeError("Execution failed")

        yield {
            "type": "sources",
            "task": plan.task.value,
            "sources": [s.model_dump() for s in _sources(context)],
        }
        parts: list[str] = []
        with Metrics.generation_timer(executor.provider.provider_name, executor.provider.model):
            for text in executor.provider.stream(context.prompt):
                parts.append(text)
                yield {"type": "token", "text": text}
        answer = "".join(parts)
        self._remember(request, answer)
        yield {
            "type": "done",
            "answer": answer,
            "model_name": executor.provider.model,
            "provider": executor.provider.provider_name,
        }

    def _remember(self, request: AgentRequest, answer: str) -> None:
        with Metrics.memory_write_timer():
            self.memory.add_messages(request.session_id, ("user", request.query), ("assistant", answer))


def _sources(context: ExecutionContext) -> list[SourceRef]:
    included = set(context.context.files) if context.context else set()
    return [
        SourceRef(
            file_path=r.file_path,
            symbol_name=r.symbol_name,
            start_line=r.start_line,
            end_line=r.end_line,
        )
        for r in (context.reranked_results or [])
        if r.file_path in included
    ]
