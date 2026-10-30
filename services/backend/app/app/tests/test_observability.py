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
        def instrument_app(app, *, excluded_urls, server_request_hook):
            calls["excluded_urls"] = excluded_urls
            calls["server_request_hook"] = server_request_hook

    fastapi_module.FastAPIInstrumentor = FakeInstrumentor

    celery_module = types.ModuleType("opentelemetry.instrumentation.celery")

    class FakeCeleryInstrumentor:
        def instrument(self, *, tracer_provider):
            calls["celery_provider"] = tracer_provider

    celery_module.CeleryInstrumentor = FakeCeleryInstrumentor

    httpx_module = types.ModuleType("opentelemetry.instrumentation.httpx")

    class FakeHttpxInstrumentor:
        def instrument(self, *, tracer_provider):
            calls["httpx_provider"] = tracer_provider

    httpx_module.HTTPXClientInstrumentor = FakeHttpxInstrumentor

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
    monkeypatch.setitem(sys.modules, "opentelemetry.instrumentation.httpx", httpx_module)
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
    from observability import redact_span_apikey

    assert calls["server_request_hook"] is redact_span_apikey
    assert calls["celery_provider"] is calls["provider"]
    # Outbound httpx calls propagate traceparent to LiteLLM and the rest.
    assert calls["httpx_provider"] is calls["provider"]


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


def test_the_server_span_masks_a_query_string_apikey():
    """The span recorded ?apikey=<plugin gateway key> and exported it to the
    collector; the access log already masked it (2026-10-08 run, cycle 11)."""
    from observability import redact_span_apikey

    class Span:
        def __init__(self):
            self.attributes = {
                "http.url": "http://backend:8000/bigup/upload?x=1&apikey=SECRET",
                "http.target": "/bigup/upload?apikey=SECRET",
                "url.query": "apikey=SECRET&x=1",
            }

        def is_recording(self):
            return True

        def set_attribute(self, key, value):
            self.attributes[key] = value

    span = Span()
    redact_span_apikey(span, {})
    assert "SECRET" not in repr(span.attributes)
    assert span.attributes["url.query"] == "apikey=***&x=1"


def test_a_key_with_an_encoded_ampersand_is_masked_in_full():
    """http.url carries the decoded query, so `?apikey=SEC%26RET2` split at
    the decoded `&` and exported `RET2` (2026-10-08 run, cycle 46)."""
    from observability import redact_span_apikey

    class Span:
        def __init__(self):
            self.attributes = {"http.url": "http://t/p/x?apikey=SEC&RET2", "url.query": "apikey=SEC%26RET2"}

        def is_recording(self):
            return True

        def set_attribute(self, key, value):
            self.attributes[key] = value

    span = Span()
    redact_span_apikey(span, {"query_string": b"apikey=SEC%26RET2"})
    assert "RET2" not in repr(span.attributes) and "SEC" not in repr(span.attributes)
    assert span.attributes["http.url"] == "http://t/p/x?apikey=***"


def _record_otlp_requests(exported):
    import requests

    def send(_session, request, **_kwargs):
        exported.append(request)
        response = requests.Response()
        response.status_code = 200
        response._content = b""
        response.request = request
        return response

    return send


def _real_otel_fixture_app(outbound):
    import httpx

    app = FastAPI()

    @app.get("/trace")
    async def traced_request():
        async with httpx.AsyncClient() as client:
            response = await client.get("http://upstream.invalid/check")
        return response.json()

    @app.get("/health")
    async def health():
        return {"ok": True}

    async def upstream(_transport, request):
        outbound.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)

    return app, upstream


async def _request_real_otel_fixture(app):
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend"
    ) as client:
        response = await client.get("/trace?apikey=SEC%26RET2&x=1")
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert (await client.get("/health")).status_code == 200


def _decode_real_otlp_export(exported):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    assert exported
    payload = ExportTraceServiceRequest()
    for request in exported:
        assert request.url == "http://collector.invalid:4318/v1/traces"
        assert request.headers["Content-Type"] == "application/x-protobuf"
        assert b"SEC" not in request.body and b"RET2" not in request.body
        payload.MergeFrom(ExportTraceServiceRequest.FromString(request.body))
    return payload


def _single_exported_span(spans, kind):
    matches = [span for span in spans if span.kind == kind]
    assert len(matches) == 1
    return matches[0]


def _assert_exported_trace_context(spans, outbound):
    from opentelemetry.proto.trace.v1.trace_pb2 import Span

    server = _single_exported_span(spans, Span.SpanKind.SPAN_KIND_SERVER)
    client = _single_exported_span(spans, Span.SpanKind.SPAN_KIND_CLIENT)
    assert server.name == "GET /trace"
    assert not any("health" in span.name for span in spans)
    assert len(outbound) == 1
    parent = outbound[0].headers["traceparent"].split("-")
    assert parent[1] == server.trace_id.hex()
    assert client.parent_span_id == server.span_id
    assert parent[2] == client.span_id.hex()


def _assert_real_otlp_export(exported, outbound):
    payload = _decode_real_otlp_export(exported)
    spans = [span for resource in payload.resource_spans
             for scope in resource.scope_spans for span in scope.spans]
    _assert_exported_trace_context(spans, outbound)
    resources = [attr for resource in payload.resource_spans
                 for attr in resource.resource.attributes]
    assert any(attr.key == "service.name" and attr.value.string_value == "backend"
               for attr in resources)


def _exercise_real_otel_export():
    """Run in a fresh process: provider and instrumentors have global state."""
    import asyncio
    from unittest.mock import patch

    import httpx
    from opentelemetry import trace
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from observability import configure_otel

    os.environ["ATLAS_OTEL_ENABLED"] = "true"
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://collector.invalid:4318"
    os.environ["OTEL_SERVICE_NAME"] = "backend"
    exported, outbound = [], []
    app, upstream = _real_otel_fixture_app(outbound)
    with patch("requests.Session.send", _record_otlp_requests(exported)), patch.object(
        httpx.AsyncHTTPTransport, "handle_async_request", upstream
    ):
        assert configure_otel(app) is True
        assert configure_otel(app) is True
        assert CeleryInstrumentor().is_instrumented_by_opentelemetry
        provider = trace.get_tracer_provider()
        try:
            asyncio.run(_request_real_otel_fixture(app))
            assert provider.force_flush(timeout_millis=5000)
        finally:
            HTTPXClientInstrumentor().uninstrument()
            CeleryInstrumentor().uninstrument()
            provider.shutdown()
    _assert_real_otlp_export(exported, outbound)


def test_real_otel_stack_exports_redacted_spans_and_propagates_context():
    import subprocess
    from pathlib import Path

    code = "import runpy,sys; runpy.run_path(sys.argv[1])['_exercise_real_otel_export']()"
    result = subprocess.run(
        [sys.executable, "-B", "-W", "error", "-c", code, str(Path(__file__).resolve())],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
