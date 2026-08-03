"""LightRAG's pinned Docling client contract is served by the adapter."""

from __future__ import annotations

import asyncio
import functools
import io
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx2 import ASGITransport, AsyncClient
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import Response


ROOT = Path(__file__).resolve().parents[2]
PROVIDER_ROOT = ROOT / "services" / "docling" / "provider"


def _run_async(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


def _load_app_module(monkeypatch):
    monkeypatch.syspath_prepend(str(PROVIDER_ROOT))
    for name in list(sys.modules):
        if name == "adapter" or name.startswith("adapter."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    from adapter import app as adapter_app

    return adapter_app


def _bundle(stem: str = "report") -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(f"{stem}.json", '{"schema_name":"DoclingDocument"}')
        archive.writestr(f"{stem}.md", "# Converted\n")
    return stream.getvalue()


class ControlledUpstream:
    def __init__(self, result: bytes | None = None):
        self.result = result or _bundle()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[tuple[Path, str, int]] = []

    async def convert(
        self, upload_path: Path, upload_name: str, timeout_seconds: int
    ) -> bytes:
        self.calls.append((upload_path, upload_name, timeout_seconds))
        self.started.set()
        await self.release.wait()
        return self.result


async def _client(app):
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://adapter",
    )


@_run_async
async def test_request_body_limit_stops_bytes_before_multipart_parsing(monkeypatch):
    monkeypatch.syspath_prepend(str(PROVIDER_ROOT))
    from bounded_upload import RequestBodyLimitMiddleware

    delivered = bytearray()

    async def downstream(_scope, receive, _send):
        while True:
            message = await receive()
            delivered.extend(message.get("body", b""))
            if not message.get("more_body", False):
                return

    middleware = RequestBodyLimitMiddleware(
        downstream,
        max_body_bytes=5,
        body_timeout_seconds=1,
        paths={"/upload"},
    )
    incoming = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
    )
    sent = []

    async def receive():
        return next(incoming)

    async def send(message):
        sent.append(message)

    await middleware(
        {"type": "http", "method": "POST", "path": "/upload", "headers": []},
        receive,
        send,
    )

    assert delivered == b"123"
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 413


@_run_async
async def test_slow_body_timeout_releases_adapter_admission(monkeypatch, tmp_path):
    adapter_app = _load_app_module(monkeypatch)
    from bounded_upload import RequestBodyLimitMiddleware

    registry = adapter_app.JobRegistry(
        root=tmp_path, max_jobs=1, result_ttl_seconds=900
    )

    async def downstream(_scope, receive, _send):
        await receive()
        await receive()

    limiter = RequestBodyLimitMiddleware(
        downstream,
        max_body_bytes=1024,
        body_timeout_seconds=0.01,
        paths={adapter_app.SUBMIT_PATH},
    )
    middleware = adapter_app._AdmissionMiddleware(limiter, registry=registry)
    calls = 0
    sent = []

    async def receive():
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": b"123", "more_body": True}
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": adapter_app.SUBMIT_PATH,
            "headers": [],
            "state": {},
        },
        receive,
        send,
    )

    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 408
    next_reservation = await registry.reserve()
    assert next_reservation is not None
    await registry.release_reservation(next_reservation)


@_run_async
async def test_saturated_admission_does_not_read_oversized_body(monkeypatch, tmp_path):
    adapter_app = _load_app_module(monkeypatch)
    from bounded_upload import RequestBodyLimitMiddleware

    registry = adapter_app.JobRegistry(
        root=tmp_path, max_jobs=1, result_ttl_seconds=900
    )
    occupied = await registry.reserve()
    assert occupied is not None

    async def downstream(_scope, _receive, _send):
        raise AssertionError("saturated request reached body parser")

    limiter = RequestBodyLimitMiddleware(
        downstream,
        max_body_bytes=5,
        body_timeout_seconds=1,
        paths={adapter_app.SUBMIT_PATH},
    )
    middleware = adapter_app._AdmissionMiddleware(limiter, registry=registry)
    sent = []

    async def receive():
        raise AssertionError("saturated request body was inspected")

    async def send(message):
        sent.append(message)

    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": adapter_app.SUBMIT_PATH,
            "headers": [(b"content-length", b"999")],
            "state": {},
        },
        receive,
        send,
    )

    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 429
    await registry.release_reservation(occupied)


@_run_async
async def test_adapter_rejects_oversized_body_before_upstream_work(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    upstream = ControlledUpstream()
    app = adapter_app.create_app(
        upstream=upstream,
        spool_root=tmp_path,
        max_jobs=1,
        upload_max_bytes=4,
    )

    async with await _client(app) as client:
        response = await client.post(
            "/v1/convert/file/async",
            files={
                "files": (
                    "oversized.pdf",
                    b"x" * (1024 * 1024 + 5),
                    "application/pdf",
                )
            },
        )

    assert response.status_code == 413
    assert upstream.calls == []
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "value",
    ["0", "invalid", "3601", "9" * 5000],
)
def test_adapter_rejects_invalid_upload_timeout_at_startup(
    monkeypatch, tmp_path, value
):
    adapter_app = _load_app_module(monkeypatch)
    monkeypatch.setenv("DOCLING_UPLOAD_TIMEOUT_SECONDS", value)

    with pytest.raises(ValueError):
        adapter_app.create_app(
            upstream=ControlledUpstream(),
            spool_root=tmp_path,
            max_jobs=1,
            upload_max_bytes=1024,
        )


@_run_async
async def test_saturated_adapter_rejects_before_oversized_body_inspection(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    upstream = ControlledUpstream()
    app = adapter_app.create_app(
        upstream=upstream,
        spool_root=tmp_path,
        max_jobs=1,
        upload_max_bytes=4,
    )

    async with await _client(app) as client:
        first = await client.post(
            "/v1/convert/file/async",
            files={"files": ("first.pdf", b"1234", "application/pdf")},
        )
        assert first.status_code == 202
        await asyncio.wait_for(upstream.started.wait(), timeout=1)

        second = await client.post(
            "/v1/convert/file/async",
            content=b"",
            headers={"Content-Length": str(2 * 1024 * 1024)},
        )

    assert second.status_code == 429
    upstream.release.set()


@_run_async
async def test_adapter_matches_pinned_lightrag_async_contract(monkeypatch, tmp_path):
    adapter_app = _load_app_module(monkeypatch)
    upstream = ControlledUpstream(_bundle("Quarterly_Report"))
    app = adapter_app.create_app(
        upstream=upstream,
        spool_root=tmp_path,
        max_jobs=2,
        result_ttl_seconds=900,
        upload_max_bytes=1024,
        job_timeout_seconds=37,
    )

    async with await _client(app) as client:
        response = await client.post(
            "/v1/convert/file/async",
            files={"files": ("Quarterly Report.pdf", b"document", "application/pdf")},
            data={
                "pipeline": "standard",
                "target_type": "zip",
                "image_export_mode": "referenced",
                "to_formats": ["json", "md"],
                "do_ocr": "true",
            },
        )
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        assert task_id

        await asyncio.wait_for(upstream.started.wait(), timeout=1)
        status = await client.get(f"/v1/status/poll/{task_id}", params={"wait": 1})
        assert status.status_code == 200
        assert status.json() == {"task_id": task_id, "task_status": "started"}

        upload_path, upload_name, timeout_seconds = upstream.calls[0]
        assert upload_name == "Quarterly Report.pdf"
        assert upload_path.read_bytes() == b"document"
        assert timeout_seconds == 37

        upstream.release.set()
        for _ in range(20):
            status = await client.get(f"/v1/status/poll/{task_id}", params={"wait": 1})
            if status.json()["task_status"] == "success":
                break
            await asyncio.sleep(0)
        assert status.json() == {"task_id": task_id, "task_status": "success"}

        result = await client.get(f"/v1/result/{task_id}")
        assert result.status_code == 200
        assert "zip" in result.headers["content-type"]
        with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
            assert archive.namelist() == ["Quarterly_Report.json", "Quarterly_Report.md"]

        assert (await client.get(f"/v1/status/poll/{task_id}")).status_code == 404
    assert not list(tmp_path.iterdir())


@_run_async
async def test_adapter_failure_is_generic_and_releases_capacity(monkeypatch, tmp_path):
    adapter_app = _load_app_module(monkeypatch)

    class FailingUpstream:
        async def convert(self, upload_path, upload_name, timeout_seconds):
            raise RuntimeError("upstream leaked secret")

    app = adapter_app.create_app(
        upstream=FailingUpstream(),
        spool_root=tmp_path,
        max_jobs=1,
        result_ttl_seconds=900,
        upload_max_bytes=1024,
        job_timeout_seconds=10,
    )

    async with await _client(app) as client:
        first = await client.post(
            "/v1/convert/file/async", files={"files": ("a.pdf", b"a")}
        )
        assert first.status_code == 202
        first_id = first.json()["task_id"]
        for _ in range(20):
            failed = await client.get(f"/v1/status/poll/{first_id}")
            if failed.json()["task_status"] == "failure":
                break
            await asyncio.sleep(0)
        assert failed.json() == {"task_id": first_id, "task_status": "failure"}
        assert "secret" not in failed.text

        second = await client.post(
            "/v1/convert/file/async", files={"files": ("b.pdf", b"b")}
        )
        assert second.status_code == 202
    await asyncio.sleep(0)
    assert not list(tmp_path.iterdir())


@_run_async
async def test_adapter_failure_logs_safe_operator_diagnostic(
    monkeypatch, tmp_path, caplog
):
    adapter_app = _load_app_module(monkeypatch)

    class FailingUpstream:
        async def convert(self, upload_path, upload_name, timeout_seconds):
            raise RuntimeError("document-content secret-token")

    app = adapter_app.create_app(
        upstream=FailingUpstream(), spool_root=tmp_path, max_jobs=1
    )
    async with await _client(app) as client:
        submitted = await client.post(
            "/v1/convert/file/async", files={"files": ("a.pdf", b"a")}
        )
        task_id = submitted.json()["task_id"]
        for _ in range(20):
            status = await client.get(f"/v1/status/poll/{task_id}")
            if status.json()["task_status"] == "failure":
                break
            await asyncio.sleep(0)

    assert "adapter job failed" in caplog.text
    assert task_id in caplog.text
    assert "RuntimeError" in caplog.text
    assert "document-content" not in caplog.text
    assert "secret-token" not in caplog.text


@_run_async
async def test_adapter_rejects_unsupported_sync_route(monkeypatch, tmp_path):
    adapter_app = _load_app_module(monkeypatch)
    app = adapter_app.create_app(
        upstream=ControlledUpstream(), spool_root=tmp_path, max_jobs=1
    )

    async with await _client(app) as client:
        response = await client.post(
            "/v1/convert/file", files={"files": ("a.pdf", b"a")}
        )

    assert response.status_code == 404


@_run_async
async def test_upstream_authenticates_and_retries_capacity(monkeypatch, tmp_path):
    adapter_app = _load_app_module(monkeypatch)
    upstream_module = sys.modules["adapter.upstream"]
    attempts = 0
    offloaded_writes = 0
    upstream_app = FastAPI()

    async def to_thread(function, *args):
        nonlocal offloaded_writes
        offloaded_writes += 1
        return function(*args)

    monkeypatch.setattr(upstream_module.asyncio, "to_thread", to_thread)

    @upstream_app.post("/internal/lightrag/bundle")
    async def bundle(request: Request, file: UploadFile = File(...)):
        nonlocal attempts
        attempts += 1
        assert request.headers["authorization"] == "Bearer provider-token"
        assert file.filename == "report.pdf"
        assert await file.read() == b"document"
        if attempts == 1:
            return Response(status_code=429, headers={"Retry-After": "1"})
        return Response(_bundle(), media_type="application/zip")

    source = tmp_path / "upload.pdf"
    source.write_bytes(b"document")
    upstream = adapter_app.DoclingUpstream(
        endpoint="http://docling.test/internal/lightrag/bundle",
        token="provider-token",
        transport=ASGITransport(app=upstream_app, raise_app_exceptions=False),
        retry_delay_seconds=0,
    )

    result = await upstream.convert(source, "report.pdf", timeout_seconds=5)

    assert result.read_bytes() == _bundle()
    result.unlink()
    assert attempts == 2
    assert offloaded_writes >= 1


@_run_async
async def test_upstream_bounds_capacity_retries(monkeypatch, tmp_path):
    adapter_app = _load_app_module(monkeypatch)
    attempts = 0
    upstream_app = FastAPI()

    @upstream_app.post("/internal/lightrag/bundle")
    async def bundle():
        nonlocal attempts
        attempts += 1
        return Response(status_code=429)

    source = tmp_path / "upload.pdf"
    source.write_bytes(b"document")
    upstream = adapter_app.DoclingUpstream(
        endpoint="http://docling.test/internal/lightrag/bundle",
        token="provider-token",
        transport=ASGITransport(app=upstream_app, raise_app_exceptions=False),
        retry_delay_seconds=0,
        max_capacity_retries=2,
        result_root=tmp_path,
    )

    with pytest.raises(adapter_app.UpstreamConversionError):
        await upstream.convert(source, "report.pdf", timeout_seconds=5)

    assert attempts == 3
    assert sorted(path.name for path in tmp_path.iterdir()) == ["upload.pdf"]


@_run_async
async def test_upstream_rejects_oversized_result_without_partial_file(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    upstream_app = FastAPI()

    @upstream_app.post("/internal/lightrag/bundle")
    async def bundle():
        return Response(b"oversized", media_type="application/zip")

    source = tmp_path / "upload.pdf"
    source.write_bytes(b"document")
    upstream = adapter_app.DoclingUpstream(
        endpoint="http://docling.test/internal/lightrag/bundle",
        token="provider-token",
        transport=ASGITransport(app=upstream_app, raise_app_exceptions=False),
        retry_delay_seconds=0,
        result_root=tmp_path,
        max_result_bytes=4,
    )

    with pytest.raises(adapter_app.UpstreamConversionError):
        await upstream.convert(source, "report.pdf", timeout_seconds=5)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["upload.pdf"]


@_run_async
async def test_upstream_rejects_malformed_content_length(monkeypatch, tmp_path):
    adapter_app = _load_app_module(monkeypatch)
    upstream_app = FastAPI()

    @upstream_app.post("/internal/lightrag/bundle")
    async def bundle():
        return Response(
            _bundle(),
            media_type="application/zip",
            headers={"Content-Length": "not-a-number"},
        )

    source = tmp_path / "upload.pdf"
    source.write_bytes(b"document")
    upstream = adapter_app.DoclingUpstream(
        endpoint="http://docling.test/internal/lightrag/bundle",
        token="provider-token",
        transport=ASGITransport(app=upstream_app, raise_app_exceptions=False),
        result_root=tmp_path,
    )

    with pytest.raises(adapter_app.UpstreamConversionError):
        await upstream.convert(source, "report.pdf", timeout_seconds=5)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["upload.pdf"]


def test_adapter_docs_match_pinned_lightrag_route_contract():
    expected_routes = (
        "/v1/convert/file/async",
        "/v1/status/poll/{task_id}",
        "/v1/result/{task_id}",
    )
    doc_paths = (
        ROOT / "services" / "lightrag" / "README.md",
        ROOT / "services" / "doc-processor" / "README.md",
        ROOT / "services" / "docling-lightrag-adapter" / "README.md",
    )

    for path in doc_paths:
        text = path.read_text(encoding="utf-8")
        for route in expected_routes:
            assert route in text, f"{path} omits {route}"
        assert "`files`" in text, f"{path} omits the multipart field name"
        assert "/v1/documents/parse" not in text, f"{path} documents a stale route"


def test_adapter_rejects_storage_smaller_than_configured_working_set(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    monkeypatch.setenv("DOCLING_ADAPTER_MAX_RESULT_BYTES", "1")
    monkeypatch.setattr(
        adapter_app.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=300 * 1024 * 1024),
    )

    with pytest.raises(ValueError, match="temporary storage"):
        adapter_app.create_app(
            upstream=ControlledUpstream(),
            spool_root=tmp_path,
            max_jobs=1,
            upload_max_bytes=200 * 1024 * 1024,
        )


@_run_async
async def test_claimed_result_is_deleted_when_response_send_fails(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    result_path = tmp_path / "result.zip"
    result_path.write_bytes(_bundle())
    finished = False

    async def finish():
        nonlocal finished
        finished = True
        result_path.unlink(missing_ok=True)

    response = adapter_app._EphemeralFileResponse(
        result_path,
        media_type="application/zip",
        finish=finish,
        timeout_seconds=5,
    )

    async def receive():
        return {"type": "http.disconnect"}

    async def send(_message):
        raise RuntimeError("simulated client disconnect")

    scope = {
        "type": "http",
        "method": "GET",
        "headers": [],
        "extensions": {},
    }
    with pytest.raises(RuntimeError, match="client disconnect"):
        await response(scope, receive, send)

    assert not result_path.exists()
    assert finished


@_run_async
async def test_result_slot_remains_occupied_until_download_finishes(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    registry = adapter_app.JobRegistry(
        root=tmp_path, max_jobs=1, result_ttl_seconds=900
    )
    upload = tmp_path / "upload.pdf"
    upload.write_bytes(b"document")
    reservation = await registry.reserve()
    assert reservation is not None

    async def worker(_path, _name):
        return _bundle()

    task_id = await registry.start(
        reservation,
        upload_path=upload,
        upload_name="upload.pdf",
        worker=worker,
    )
    for _ in range(20):
        if await registry.status(task_id) == "success":
            break
        await asyncio.sleep(0)

    result_path = await registry.claim_result(task_id)
    assert result_path is not None
    assert await registry.reserve() is None

    await registry.finish_result(task_id)
    assert not result_path.exists()
    next_reservation = await registry.reserve()
    assert next_reservation is not None
    await registry.release_reservation(next_reservation)


@_run_async
async def test_one_shot_result_ignores_range_and_sends_complete_archive(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    payload = _bundle()
    result_path = tmp_path / "result.zip"
    result_path.write_bytes(payload)

    async def finish():
        result_path.unlink(missing_ok=True)

    response = adapter_app._EphemeralFileResponse(
        result_path,
        media_type="application/zip",
        finish=finish,
        timeout_seconds=5,
    )
    sent = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await response(
        {
            "type": "http",
            "method": "GET",
            "headers": [(b"range", b"bytes=0-1")],
            "extensions": {},
        },
        receive,
        send,
    )

    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    assert start["status"] == 200
    assert not any(
        name.lower() == b"accept-ranges" for name, _value in start["headers"]
    )
    assert body == payload
    assert not result_path.exists()


@_run_async
async def test_slow_result_download_times_out_and_releases_cleanup(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    result_path = tmp_path / "result.zip"
    result_path.write_bytes(_bundle())
    finished = False

    async def finish():
        nonlocal finished
        finished = True
        result_path.unlink(missing_ok=True)

    response = adapter_app._EphemeralFileResponse(
        result_path,
        media_type="application/zip",
        finish=finish,
        timeout_seconds=0.01,
    )

    async def receive():
        return {"type": "http.disconnect"}

    async def send(_message):
        await asyncio.Event().wait()

    with pytest.raises(asyncio.TimeoutError):
        await response(
            {
                "type": "http",
                "method": "GET",
                "headers": [],
                "extensions": {},
            },
            receive,
            send,
        )

    assert finished
    assert not result_path.exists()
