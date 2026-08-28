from __future__ import annotations

import os
from typing import Any


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _traces_endpoint(base: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/v1/traces"):
        return base
    return f"{base}/v1/traces"


def _create_tracer_provider(service_name: str) -> Any:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError(
            "ATLAS_OTEL_ENABLED=true requires OTEL_EXPORTER_OTLP_ENDPOINT"
        )

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:
        raise RuntimeError(
            "ATLAS_OTEL_ENABLED=true but OpenTelemetry dependencies are unavailable"
        ) from exc

    resource = Resource.create(
        {"service.name": service_name, "service.namespace": "atlas"}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=_traces_endpoint(endpoint)))
    )
    trace.set_tracer_provider(provider)
    return provider


def configure_otel(app: Any) -> bool:
    """Configure optional OpenTelemetry tracing for the FastAPI app.

    Returns True when tracing was enabled. An explicitly enabled but invalid
    tracing configuration fails startup instead of silently losing spans.
    """
    if not _truthy(os.getenv("ATLAS_OTEL_ENABLED")):
        return False
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip():
        raise RuntimeError(
            "ATLAS_OTEL_ENABLED=true requires OTEL_EXPORTER_OTLP_ENDPOINT"
        )

    if getattr(app.state, "otel_configured", False):
        return True

    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except Exception as exc:
        raise RuntimeError(
            "ATLAS_OTEL_ENABLED=true but OpenTelemetry dependencies are unavailable"
        ) from exc

    provider = _create_tracer_provider(os.getenv("OTEL_SERVICE_NAME", "backend"))
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls=os.getenv(
            "OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", "/metrics,/health,/ready"
        ),
    )
    CeleryInstrumentor().instrument(tracer_provider=provider)
    app.state.otel_configured = True
    return True


def configure_celery_otel(*, service_name: str) -> bool:
    """Configure producer/worker Celery spans and queue context propagation."""
    if not _truthy(os.getenv("ATLAS_OTEL_ENABLED")):
        return False
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip():
        raise RuntimeError(
            "ATLAS_OTEL_ENABLED=true requires OTEL_EXPORTER_OTLP_ENDPOINT"
        )
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
    except Exception as exc:
        raise RuntimeError(
            "ATLAS_OTEL_ENABLED=true but Celery tracing is unavailable"
        ) from exc
    provider = _create_tracer_provider(service_name)
    CeleryInstrumentor().instrument(tracer_provider=provider)
    return True
