"""Metrics facade: Prometheus metrics, per-request stage timings, and
OpenTelemetry spans (when OTEL is enabled)."""

from __future__ import annotations

import contextvars
import json
import time
from contextlib import contextmanager
from functools import lru_cache

from voxcode.monitoring import tracing
from voxcode.monitoring.metrics import (
    ACTIVE_SESSIONS,
    ANSWER_CACHE_HITS_TOTAL,
    CASCADE_ROUTES_TOTAL,
    CONTEXT_DURATION,
    CONTEXT_SIZE_BYTES,
    GENERATION_DURATION,
    LLM_COST_USD_TOTAL,
    LLM_TOKENS_TOTAL,
    MEMORY_READ_DURATION,
    MEMORY_WRITE_DURATION,
    PIPELINE_DURATION,
    PROMPT_DURATION,
    PROMPT_SIZE_BYTES,
    REQUEST_ERRORS_TOTAL,
    REQUESTS_TOTAL,
    RERANK_DURATION,
    RETRIEVAL_DURATION,
    RETRIEVED_CHUNKS_COUNT,
    SECRETS_REDACTED_TOTAL,
    TIME_TO_FIRST_TOKEN,
)

# Stage name -> milliseconds, for the request being handled in this context.
_timings: contextvars.ContextVar[dict[str, float] | None] = contextvars.ContextVar(
    "voxcode_timings", default=None
)


@contextmanager
def collect_timings():
    """Collect per-stage timings for everything run inside the block."""
    timings: dict[str, float] = {}
    token = _timings.set(timings)
    try:
        yield timings
    finally:
        _timings.reset(token)


@contextmanager
def _stage(name: str, histogram=None, **attributes):
    started = time.perf_counter()
    with tracing.span(f"voxcode.{name}", **attributes):
        try:
            yield
        finally:
            elapsed = time.perf_counter() - started
            if histogram is not None:
                histogram.observe(elapsed)
            timings = _timings.get()
            if timings is not None:
                timings[name] = round(timings.get(name, 0.0) + elapsed * 1000, 1)


@lru_cache(maxsize=1)
def _prices() -> dict[str, dict[str, float]]:
    from voxcode.config.settings import settings

    try:
        return json.loads(settings.MODEL_PRICES) if settings.MODEL_PRICES else {}
    except ValueError:
        return {}


class Metrics:
    # ------------------------------------------------------- requests
    @staticmethod
    def record_request():
        REQUESTS_TOTAL.inc()

    @staticmethod
    def record_error():
        REQUEST_ERRORS_TOTAL.inc()

    @staticmethod
    def increment_sessions():
        ACTIVE_SESSIONS.inc()

    @staticmethod
    def decrement_sessions():
        ACTIVE_SESSIONS.dec()

    # --------------------------------------------------------- stages
    @staticmethod
    def stage(name: str, **attributes):
        return _stage(name, **attributes)

    @staticmethod
    def pipeline_timer():
        return _stage("pipeline", PIPELINE_DURATION)

    @staticmethod
    def memory_read_timer():
        return _stage("memory_read", MEMORY_READ_DURATION)

    @staticmethod
    def memory_write_timer():
        return _stage("memory_write", MEMORY_WRITE_DURATION)

    @staticmethod
    def retrieval_timer():
        return _stage("retrieval", RETRIEVAL_DURATION)

    @staticmethod
    def rerank_timer():
        return _stage("rerank", RERANK_DURATION)

    @staticmethod
    def context_timer():
        return _stage("context", CONTEXT_DURATION)

    @staticmethod
    def prompt_timer():
        return _stage("prompt", PROMPT_DURATION)

    @staticmethod
    def generation_timer(provider: str, model: str):
        return _stage(
            "generation",
            GENERATION_DURATION.labels(provider=provider, model=model),
            provider=provider,
            model=model,
        )

    @staticmethod
    def record_first_token(seconds: float) -> None:
        TIME_TO_FIRST_TOKEN.observe(seconds)
        timings = _timings.get()
        if timings is not None:
            timings["first_token"] = round(seconds * 1000, 1)

    # ---------------------------------------------------------- sizes
    @staticmethod
    def record_context_size(size: int):
        CONTEXT_SIZE_BYTES.observe(size)

    @staticmethod
    def record_prompt_size(size: int):
        PROMPT_SIZE_BYTES.observe(size)

    @staticmethod
    def record_retrieved_chunks(chunks: int):
        RETRIEVED_CHUNKS_COUNT.observe(chunks)

    # -------------------------------------------------- tokens / cost
    @staticmethod
    def record_tokens(provider: str, model: str, usage) -> None:
        for kind in ("input", "output", "cache_read", "cache_write"):
            value = getattr(usage, f"{kind}_tokens", 0)
            if value:
                LLM_TOKENS_TOTAL.labels(provider=provider, model=model, kind=kind).inc(value)
        cost = Metrics.estimate_cost(model, usage)
        if cost:
            LLM_COST_USD_TOTAL.labels(provider=provider, model=model).inc(cost)

    @staticmethod
    def estimate_cost(model: str, usage) -> float | None:
        """USD cost from MODEL_PRICES ({"model": {"input": $/1M, "output": $/1M,
        "cache_read": $/1M, "cache_write": $/1M}}); None if the model is unpriced."""
        price = _prices().get(model)
        if not price:
            return None
        per_million = (
            usage.input_tokens * price.get("input", 0)
            + usage.output_tokens * price.get("output", 0)
            + usage.cache_read_tokens * price.get("cache_read", price.get("input", 0))
            + usage.cache_write_tokens * price.get("cache_write", price.get("input", 0))
        )
        return per_million / 1_000_000

    @staticmethod
    def record_redactions(count: int) -> None:
        SECRETS_REDACTED_TOTAL.inc(count)

    @staticmethod
    def record_cache_hit() -> None:
        ANSWER_CACHE_HITS_TOTAL.inc()

    @staticmethod
    def record_route(tier: str) -> None:
        CASCADE_ROUTES_TOTAL.labels(tier=tier).inc()
