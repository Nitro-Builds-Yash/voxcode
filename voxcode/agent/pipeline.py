"""Question-answering pipeline: plan -> retrieve -> rerank -> expand ->
context -> prompt -> generate, with conversation memory."""

from __future__ import annotations

import time
from collections.abc import Iterator

from voxcode.agent import cascade
from voxcode.agent.agent_request import AgentRequest
from voxcode.agent.agent_response import AgentResponse, SourceRef
from voxcode.agent.answer_cache import AnswerCache, answer_cache
from voxcode.agent.executor import ExecutionContext, StepExecutor
from voxcode.agent.memory.memory_manager import MemoryManager
from voxcode.agent.planner.execution_plan import ExecutionPlan
from voxcode.agent.planner.planner import Planner
from voxcode.agent.planner.planner_step import StepType
from voxcode.agent.runtime import RuntimeCache, runtime_cache
from voxcode.config.settings import settings
from voxcode.core.model_registry import ModelRegistry
from voxcode.generation import grounding
from voxcode.generation.providers.types import Usage
from voxcode.monitoring import Metrics


class AgentPipeline:
    def __init__(
        self,
        registry: ModelRegistry,
        memory: MemoryManager,
        runtimes: RuntimeCache | None = None,
        planner: Planner | None = None,
        cache: AnswerCache | None = None,
    ):
        self.registry = registry
        self.memory = memory
        self.runtimes = runtimes or runtime_cache
        self.planner = planner or Planner()
        self.cache = cache or answer_cache

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

    # ------------------------------------------------------------ helpers
    def _cache_scope(self, request: AgentRequest, context: ExecutionContext, provider) -> tuple | None:
        if not settings.ANSWER_CACHE or context.conversation_history:
            return None
        try:
            version = self.runtimes.store.path(request.repository_key).stat().st_mtime
        except OSError:
            return None
        return (
            request.repository_key,
            version,
            provider.provider_name,
            provider.model,
            request.directive or "",
        )

    def _route(self, request: AgentRequest, plan: ExecutionPlan, context: ExecutionContext, main):
        """(provider, Route): the cascade's small model for easy questions, else the main one."""
        small = self.registry.get_cascade_provider()
        score = cascade.difficulty(request.query, plan.task.value, context.reranked_results or [])
        if small is not None and score <= settings.CASCADE_MAX_DIFFICULTY:
            return small, cascade.Route(score, "small")
        return main, cascade.Route(score, "main")

    def _quality(self, answer: str, context: ExecutionContext, retriever) -> grounding.Grounding:
        names = getattr(retriever, "_repository_names", None)
        if names is None:
            names = grounding.repository_names(retriever.index.chunks)
            retriever._repository_names = names
        text = context.context.context if context.context else ""
        return grounding.check(answer, text, *names)

    # ------------------------------------------------------------- answer
    def ask(self, request: AgentRequest, plan: ExecutionPlan | None = None) -> AgentResponse:
        plan = plan or self.plan(request)
        Metrics.record_request()
        Metrics.increment_sessions()
        try:
            with Metrics.pipeline_timer():
                executor = self._executor(request)
                context = self._context(request, plan)
                scope = self._cache_scope(request, context, executor.provider)
                if scope is not None:
                    hit = self.cache.get(scope, request.query, self.registry_embedder())
                    if hit is not None:
                        Metrics.record_cache_hit()
                        self._remember(request, hit.answer)
                        return AgentResponse(answer=hit.answer, cached=True, **hit.payload)
                result = executor.execute_plan(context, stop_before=StepType.GENERATE)
                if not result.success:
                    raise result.error or RuntimeError("Execution failed")
                assert context.context and context.prompt
                provider, route = self._route(request, plan, context, executor.provider)
                generation = self._generate(provider, context)
                quality = self._quality(generation.response, context, executor.retriever)
                if route.tier == "small":
                    reason = cascade.needs_escalation(
                        generation.response, quality.groundedness, settings.CASCADE_MIN_GROUNDEDNESS
                    )
                    if reason:
                        route.escalated, route.reason = True, reason
                        provider = executor.provider
                        generation = self._generate(provider, context)
                        quality = self._quality(generation.response, context, executor.retriever)
                Metrics.record_route(route.tier if not route.escalated else "escalated")
                answer = generation.response
                self._remember(request, answer)
                payload = {
                    "task": plan.task.value,
                    "retrieved_chunks": context.context.num_chunks,
                    "context_size": context.context.context_size,
                    "prompt_size": context.prompt.prompt_size,
                    "model_name": provider.model,
                    "provider": provider.provider_name,
                    "sources": _sources(context),
                    "usage": usage_dict(
                        provider.model,
                        Usage(
                            generation.input_tokens, generation.output_tokens, generation.cache_read_tokens
                        ),
                    ),
                    "quality": quality.to_dict(),
                    "routing": route.to_dict(),
                }
                if scope is not None and not route.escalated:
                    self.cache.put(scope, request.query, answer, payload, self.registry_embedder())
                return AgentResponse(answer=answer, **payload)
        except Exception:
            Metrics.record_error()
            raise
        finally:
            Metrics.decrement_sessions()

    def registry_embedder(self):
        """The embedder if it is already loaded (never triggers a model download)."""
        from voxcode.config.settings import settings as s

        if self.registry.model_status(f"embedder:{s.EMBEDDING_MODEL}") != "loaded":
            return None
        return self.registry.get_embedder()

    def _generate(self, provider, context: ExecutionContext):
        with Metrics.generation_timer(provider.provider_name, provider.model):
            generation = provider.generate(context.prompt)
        context.generation = generation
        return generation

    def stream(self, request: AgentRequest) -> Iterator[dict]:
        """Yield `sources`, then `token` events, then `done`."""
        started = time.perf_counter()
        plan = self.plan(request)
        Metrics.record_request()
        executor = self._executor(request)
        context = self._context(request, plan)
        scope = self._cache_scope(request, context, executor.provider)
        hit = self.cache.get(scope, request.query, self.registry_embedder()) if scope is not None else None
        if hit is not None:
            Metrics.record_cache_hit()
            payload = hit.payload
            yield {
                "type": "sources",
                "task": payload["task"],
                "sources": [s.model_dump() if hasattr(s, "model_dump") else s for s in payload["sources"]],
            }
            for i in range(0, len(hit.answer), 48):
                yield {"type": "token", "text": hit.answer[i : i + 48]}
            self._remember(request, hit.answer)
            yield {
                "type": "done",
                "answer": hit.answer,
                "model_name": payload["model_name"],
                "provider": payload["provider"],
                "usage": payload["usage"],
                "quality": payload.get("quality", {}),
                "routing": payload.get("routing", {}),
                "cached": True,
            }
            return
        result = executor.execute_plan(context, stop_before=StepType.GENERATE)
        if not result.success:
            Metrics.record_error()
            raise result.error or RuntimeError("Execution failed")

        yield {
            "type": "sources",
            "task": plan.task.value,
            "sources": [s.model_dump() for s in _sources(context)],
        }
        # Streaming can't take tokens back, so the cascade routes but does not escalate here.
        provider, route = self._route(request, plan, context, executor.provider)
        Metrics.record_route(route.tier)
        parts: list[str] = []
        usage = Usage()
        with Metrics.generation_timer(provider.provider_name, provider.model):
            for text in provider.stream(context.prompt, usage):
                if not parts:
                    Metrics.record_first_token(time.perf_counter() - started)
                parts.append(text)
                yield {"type": "token", "text": text}
        answer = "".join(parts)
        self._remember(request, answer)
        quality = self._quality(answer, context, executor.retriever)
        done = {
            "type": "done",
            "answer": answer,
            "model_name": provider.model,
            "provider": provider.provider_name,
            "usage": usage_dict(provider.model, usage),
            "quality": quality.to_dict(),
            "routing": route.to_dict(),
            "cached": False,
        }
        if scope is not None:
            self.cache.put(
                scope,
                request.query,
                answer,
                {
                    "task": plan.task.value,
                    "retrieved_chunks": context.context.num_chunks if context.context else 0,
                    "context_size": context.context.context_size if context.context else 0,
                    "prompt_size": context.prompt.prompt_size if context.prompt else 0,
                    "model_name": provider.model,
                    "provider": provider.provider_name,
                    "sources": [s.model_dump() for s in _sources(context)],
                    "usage": done["usage"],
                    "quality": done["quality"],
                    "routing": done["routing"],
                },
                self.registry_embedder(),
            )
        yield done

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


def usage_dict(model: str, usage: Usage) -> dict:
    cost = Metrics.estimate_cost(model, usage)
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "estimated_cost_usd": round(cost, 6) if cost is not None else None,
    }
