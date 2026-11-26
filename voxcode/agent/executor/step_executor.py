"""Executes the steps of an ExecutionPlan against an ExecutionContext."""

from __future__ import annotations

import logging

from voxcode.agent.executor.execution_context import ExecutionContext
from voxcode.agent.executor.execution_result import ExecutionResult
from voxcode.agent.planner.planner_step import PlannerStep, StepType
from voxcode.config.settings import settings
from voxcode.generation.context.context_builder import ContextBuilder
from voxcode.generation.prompts.prompt_builder import PromptBuilder
from voxcode.monitoring import Metrics

logger = logging.getLogger(__name__)


class StepExecutor:
    def __init__(self, retriever, reranker, provider, context_builder=None, prompt_builder=None):
        self.retriever = retriever
        self.reranker = reranker
        self.provider = provider
        self.context_builder = context_builder or ContextBuilder()
        self.prompt_builder = prompt_builder or PromptBuilder()
        self._handlers = {
            StepType.RETRIEVE: self._retrieve,
            StepType.RERANK: self._rerank,
            StepType.EXPAND_GRAPH: self._expand_graph,
            StepType.BUILD_CONTEXT: self._build_context,
            StepType.BUILD_PROMPT: self._build_prompt,
            StepType.GENERATE: self._generate,
        }

    def execute_step(self, step: PlannerStep, context: ExecutionContext) -> None:
        handler = self._handlers.get(step.step_type)
        if handler is None:
            raise NotImplementedError(f"Step '{step.step_type.value}' is not implemented")
        handler(context)

    def execute_plan(self, context: ExecutionContext, stop_before: StepType | None = None) -> ExecutionResult:
        executed: list[PlannerStep] = []
        for step in sorted(context.plan.steps, key=lambda s: s.order):
            if step.step_type == stop_before:
                break
            try:
                self.execute_step(step, context)
            except Exception as exc:
                return ExecutionResult(
                    success=False, context=context, executed_steps=executed, failed_step=step, error=exc
                )
            executed.append(step)
        return ExecutionResult(success=True, context=context, executed_steps=executed)

    # -------------------------------------------------------------- steps
    def _retrieve(self, context: ExecutionContext) -> None:
        with Metrics.retrieval_timer():
            context.retrieval_results = self.retriever.search(context.request.query)
        context.set_metadata("retrieval_result_count", len(context.retrieval_results))

    def _rerank(self, context: ExecutionContext) -> None:
        if context.retrieval_results is None:
            raise RuntimeError("RERANK requires RETRIEVE")
        top_k = context.request.top_k
        if self.reranker is None:
            context.reranked_results = context.retrieval_results[:top_k]
            return
        try:
            with Metrics.rerank_timer():
                context.reranked_results = self.reranker.rerank(
                    context.request.query, context.retrieval_results, top_k=top_k
                )
        except Exception as exc:  # e.g. GPU out of memory: fall back to fused order
            logger.warning("Reranking failed, using retrieval order: %s", exc)
            context.reranked_results = context.retrieval_results[:top_k]

    def _expand_graph(self, context: ExecutionContext) -> None:
        if context.reranked_results is None:
            raise RuntimeError("EXPAND_GRAPH requires RERANK")
        context.reranked_results = self.retriever.expand(context.reranked_results, settings.GRAPH_EXPANSION)

    def _build_context(self, context: ExecutionContext) -> None:
        if context.reranked_results is None:
            raise RuntimeError("BUILD_CONTEXT requires RERANK")
        with Metrics.context_timer():
            built = self.context_builder.build(
                query=context.request.query,
                retrieved_chunks=context.reranked_results,
                conversation_history=context.conversation_history,
            )
        context.context = built
        Metrics.record_context_size(built.context_size)
        Metrics.record_retrieved_chunks(built.num_chunks)

    def _build_prompt(self, context: ExecutionContext) -> None:
        if context.context is None:
            raise RuntimeError("BUILD_PROMPT requires BUILD_CONTEXT")
        with Metrics.prompt_timer():
            context.prompt = self.prompt_builder.build(context.context, directive=context.request.directive)
        Metrics.record_prompt_size(context.prompt.prompt_size)

    def _generate(self, context: ExecutionContext) -> None:
        if context.prompt is None:
            raise RuntimeError("GENERATE requires BUILD_PROMPT")
        with Metrics.generation_timer(self.provider.provider_name, self.provider.model):
            context.generation = self.provider.generate(context.prompt)
