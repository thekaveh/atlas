from __future__ import annotations

import os
import sys
import types

from fastapi import FastAPI


def test_configure_otel_is_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("ATLAS_OTEL_ENABLED", "false")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")

    from observability import configure_otel

    app = FastAPI()
    assert configure_otel(app) is False
    assert not hasattr(app.state, "otel_configured")


def test_configure_otel_rejects_enabled_tracing_without_endpoint(monkeypatch):
    monkeypatch.setenv("ATLAS_OTEL_ENABLED", "true")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    from observability import configure_otel

    app = FastAPI()
    import pytest

    with pytest.raises(RuntimeError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
        configure_otel(app)


def test_configure_otel_marks_app_when_dependencies_available(monkeypatch):
    calls = {}

    trace_module = types.ModuleType("opentelemetry.trace")
    trace_module.set_tracer_provider = lambda provider: calls.setdefault("provider", provider)

    exporter_module = types.ModuleType("opentelemetry.exporter.otlp.proto.http.trace_exporter")

    class FakeExporter:
        def __init__(self, *, endpoint):
            calls["endpoint"] = endpoint

    exporter_module.OTLPSpanExporter = FakeExporter

    fastapi_module = types.ModuleType("opentelemetry.instrumentation.fastapi")

    class FakeInstrumentor:
        @staticmethod
        def instrument_app(app, *, excluded_urls):
            calls["excluded_urls"] = excluded_urls

    fastapi_module.FastAPIInstrumentor = FakeInstrumentor

    celery_module = types.ModuleType("opentelemetry.instrumentation.celery")

    class FakeCeleryInstrumentor:
        def instrument(self, *, tracer_provider):
            calls["celery_provider"] = tracer_provider

    celery_module.CeleryInstrumentor = FakeCeleryInstrumentor

    resources_module = types.ModuleType("opentelemetry.sdk.resources")

    class FakeResource:
        @staticmethod
        def create(attrs):
            calls["resource"] = attrs
            return attrs

    resources_module.Resource = FakeResource

    sdk_trace_module = types.ModuleType("opentelemetry.sdk.trace")

    class FakeProvider:
        def __init__(self, *, resource):
            self.resource = resource
            self.processors = []

        def add_span_processor(self, processor):
            self.processors.append(processor)

    sdk_trace_module.TracerProvider = FakeProvider

    export_module = types.ModuleType("opentelemetry.sdk.trace.export")

    class FakeProcessor:
        def __init__(self, exporter):
            self.exporter = exporter

    export_module.BatchSpanProcessor = FakeProcessor

    monkeypatch.setitem(sys.modules, "opentelemetry", types.ModuleType("opentelemetry"))
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", trace_module)
    sys.modules["opentelemetry"].trace = trace_module
    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.otlp.proto.http.trace_exporter", exporter_module)
    monkeypatch.setitem(sys.modules, "opentelemetry.instrumentation.fastapi", fastapi_module)
    monkeypatch.setitem(sys.modules, "opentelemetry.instrumentation.celery", celery_module)
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.resources", resources_module)
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace", sdk_trace_module)
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace.export", export_module)
    monkeypatch.setenv("ATLAS_OTEL_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "backend")
    monkeypatch.setenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", "/health,/metrics")

    from observability import configure_otel

    app = FastAPI()
    assert configure_otel(app) is True
    assert app.state.otel_configured is True
    assert calls["endpoint"] == "http://otel-collector:4318/v1/traces"
    assert calls["resource"]["service.name"] == "backend"
    assert calls["excluded_urls"] == "/health,/metrics"
    assert calls["celery_provider"] is calls["provider"]


def test_celery_worker_process_init_configures_tracing(monkeypatch):
    import celery_app

    calls = []
    monkeypatch.setattr(
        celery_app,
        "configure_celery_otel",
        lambda *, service_name: calls.append(service_name),
    )

    celery_app.worker_process_init.send(sender=celery_app.celery_app)

    assert calls == ["backend-celery-worker"]
