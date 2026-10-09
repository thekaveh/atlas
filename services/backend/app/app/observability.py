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
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
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
        server_request_hook=redact_span_apikey,
    )
    CeleryInstrumentor().instrument(tracer_provider=provider)
    # Outbound calls (LiteLLM, ComfyUI, Weaviate, …) get client spans and a
    # `traceparent` header, so their own spans join the request's trace.
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    app.state.otel_configured = True
    return True


_URL_ATTRIBUTES = ("http.url", "url.full", "http.target")


def _raw_query(scope) -> str:
    return ((scope or {}).get("query_string") or b"").decode("latin-1")


def _redacted_url(value: str, raw_query: str) -> str:
    """``value`` with its query replaced by the masked raw one when known."""
    from access_log import _redact_apikey_query_values

    if not raw_query:
        return _redact_apikey_query_values(value)
    return f"{value.split('?', 1)[0]}?{_redact_apikey_query_values('?' + raw_query)[1:]}"


def redact_span_apikey(span, _scope) -> None:
    """Mask a query-string ``apikey`` in the server span's URL attributes.

    The plugin gateway key may arrive as ``?apikey=`` (browsers cannot set a
    header on a WebSocket handshake); the access log masks it, but the span
    recorded the full URL and exported the key to the collector
    (2026-10-08 run, cycle 11)."""
    if span is None or not span.is_recording():
        return
    attributes = getattr(span, "attributes", None) or {}
    # The URL attributes carry the DECODED query, so a key holding `%26`
    # split at the decoded `&` and its tail was exported (2026-10-08 run,
    # cycle 46); mask the raw query from the scope instead.
    raw = _raw_query(_scope)
    for key in _URL_ATTRIBUTES:
        value = attributes.get(key)
        if isinstance(value, str) and "?" in value:
            span.set_attribute(key, _redacted_url(value, raw))
    query = attributes.get("url.query")
    if isinstance(query, str) and query:
        span.set_attribute("url.query", _redacted_url("?" + query, "")[1:])


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
