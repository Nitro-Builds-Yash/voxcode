"""Optional OpenTelemetry tracing.

Enabled with OTEL_ENABLED=true and the `otel` extra installed; spans are
exported over OTLP to OTEL_EXPORTER_OTLP_ENDPOINT (e.g. the Jaeger or
Tempo container in docker-compose). When disabled, `span()` is free.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager, nullcontext

logger = logging.getLogger(__name__)

_tracer = None
_initialised = False


def setup(app=None) -> bool:
    """Configure the tracer provider; instrument FastAPI and httpx if possible."""
    global _tracer, _initialised
    if _initialised:
        return _tracer is not None
    _initialised = True

    from voxcode.config.settings import settings

    if not settings.OTEL_ENABLED:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OTEL_ENABLED is set but the 'otel' extra is not installed")
        return False

    provider = TracerProvider(resource=Resource.create({"service.name": settings.OTEL_SERVICE_NAME}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT))
    )
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("voxcode")

    try:
        if app is not None:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except ImportError:
        pass
    logger.info("OpenTelemetry tracing to %s", settings.OTEL_EXPORTER_OTLP_ENDPOINT)
    return True


@contextmanager
def span(name: str, **attributes):
    if _tracer is None:
        with nullcontext():
            yield None
        return
    with _tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(f"voxcode.{key}", value)
        yield current
