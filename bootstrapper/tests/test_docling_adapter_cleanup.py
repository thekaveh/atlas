"""The Docling adapter bounds admission and cleans every owned artifact."""

from __future__ import annotations

import asyncio
import functools
import io
import sys
import threading
from pathlib import Path

import pytest
from httpx2 import ASGITransport, AsyncClient


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


class GateUpstream:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def convert(self, upload_path, upload_name, timeout_seconds):
        self.started.set()
        await self.release.wait()
        return b"zip-result"


@_run_async
async def test_admission_release_survives_repeated_request_cancellation(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    registry = adapter_app.JobRegistry(
        root=tmp_path, max_jobs=1, result_ttl_seconds=900
    )
    downstream_started = asyncio.Event()

    async def blocked_downstream(_scope, _receive, _send):
        downstream_started.set()
        await asyncio.Event().wait()

    middleware = adapter_app._AdmissionMiddleware(
        blocked_downstream, registry=registry
    )
    scope = {"type": "http", "method": "POST", "path": adapter_app.SUBMIT_PATH}

    async def unused_channel():
        await asyncio.Event().wait()

    request = asyncio.create_task(
        middleware(scope, unused_channel, unused_channel)
    )
    await asyncio.wait_for(downstream_started.wait(), timeout=1)
    await registry._lock.acquire()
    try:
        request.cancel()
        await asyncio.sleep(0)
        request.cancel()
        await asyncio.sleep(0)
        assert request.done() is False
    finally:
        registry._lock.release()
    with pytest.raises(asyncio.CancelledError):
        await request
    replacement = await registry.reserve()
    assert replacement is not None
    await registry.release_reservation(replacement)


@_run_async
async def test_submit_cleanup_owns_spool_across_repeated_close_cancellation(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    app = adapter_app.create_app(spool_root=tmp_path, max_jobs=1)
    registry = app.state.job_registry
    reservation = await registry.reserve()
    assert reservation is not None
    upload_path = tmp_path / "orphan.bin"
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def fake_spool(*_args, **_kwargs):
        upload_path.write_bytes(b"sensitive")
        return upload_path

    async def blocked_close(self):
        close_started.set()
        await release_close.wait()

    monkeypatch.setattr(adapter_app, "spool_upload", fake_spool)
    monkeypatch.setattr(adapter_app.UploadFile, "close", blocked_close)
    submit = next(route.endpoint for route in app.routes if route.path == adapter_app.SUBMIT_PATH)
    request = adapter_app.Request(
        {"type": "http", "state": {"adapter_reservation": reservation}}
    )
    upload = adapter_app.UploadFile(file=io.BytesIO(b"input"), filename="input.pdf")
    task = asyncio.create_task(submit(request, upload))
    await asyncio.wait_for(close_started.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release_close.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not upload_path.exists()
    replacement = await registry.reserve()
    assert replacement is not None
    await registry.release_reservation(replacement)


@_run_async
async def test_unstarted_upload_cleanup_failure_retains_slot_until_retry(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    registry = adapter_app.JobRegistry(
        root=tmp_path, max_jobs=1, result_ttl_seconds=900
    )
    reservation = await registry.reserve()
    assert reservation is not None
    upload_path = tmp_path / "unstarted.bin"
    upload_path.write_bytes(b"sensitive")
    real_unlink = Path.unlink
    denied = True

    def controlled_unlink(path, *args, **kwargs):
        if path == upload_path and denied:
            raise PermissionError("simulated cleanup denial")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", controlled_unlink)
    await registry.abandon_upload(reservation, upload_path=upload_path)

    assert await registry.reserve() is None
    assert registry._occupied == 1
    denied = False
    replacement = await registry.reserve()
    assert replacement is not None
    assert not upload_path.exists()
    await registry.release_reservation(replacement)


@_run_async
async def test_repeated_cancellation_cleans_late_result_file(monkeypatch, tmp_path):
    adapter_app = _load_app_module(monkeypatch)
    registry = adapter_app.JobRegistry(
        root=tmp_path, max_jobs=1, result_ttl_seconds=900
    )
    upload = tmp_path / "upload.pdf"
    upload.write_bytes(b"document")
    reservation = await registry.reserve()
    assert reservation is not None
    writer_started = threading.Event()
    release_writer = threading.Event()
    writer_finished = threading.Event()
    original_write = registry._write_result

    def gated_write(payload):
        writer_started.set()
        release_writer.wait(timeout=1)
        try:
            return original_write(payload)
        finally:
            writer_finished.set()

    monkeypatch.setattr(registry, "_write_result", gated_write)

    async def worker(_path, _name):
        return b"zip-result"

    task_id = await registry.start(
        reservation,
        upload_path=upload,
        upload_name=upload.name,
        worker=worker,
    )
    job = registry._jobs[task_id]
    assert job.task is not None
    assert await asyncio.to_thread(writer_started.wait, 1)

    job.task.cancel()
    await asyncio.sleep(0)
    job.task.cancel()
    await asyncio.sleep(0)
    release_writer.set()
    assert await asyncio.to_thread(writer_finished.wait, 1)
    with pytest.raises(asyncio.CancelledError):
        await job.task

    assert list(tmp_path.iterdir()) == []


@_run_async
async def test_capacity_rejection_happens_before_body_read(monkeypatch, tmp_path):
    adapter_app = _load_app_module(monkeypatch)
    upstream = GateUpstream()
    app = adapter_app.create_app(
        upstream=upstream,
        spool_root=tmp_path,
        max_jobs=1,
        upload_max_bytes=1024,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://adapter",
    ) as client:
        first = await client.post(
            "/v1/convert/file/async", files={"files": ("a.pdf", b"a")}
        )
        assert first.status_code == 202
        await asyncio.wait_for(upstream.started.wait(), timeout=1)

        body_read = False

        async def forbidden_receive():
            nonlocal body_read
            body_read = True
            raise AssertionError("over-capacity request body was read")

        messages = []

        async def send(message):
            messages.append(message)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/v1/convert/file/async",
                "raw_path": b"/v1/convert/file/async",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 1),
                "server": ("adapter", 80),
            },
            forbidden_receive,
            send,
        )
        assert body_read is False
        assert messages[0]["status"] == 429
        upstream.release.set()


@_run_async
async def test_expired_success_result_is_deleted_and_releases_slot(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    now = [100.0]

    class ImmediateUpstream:
        async def convert(self, upload_path, upload_name, timeout_seconds):
            return b"zip-result"

    app = adapter_app.create_app(
        upstream=ImmediateUpstream(),
        spool_root=tmp_path,
        max_jobs=1,
        result_ttl_seconds=5,
        upload_max_bytes=1024,
        clock=lambda: now[0],
    )

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://adapter",
    ) as client:
        first = await client.post(
            "/v1/convert/file/async", files={"files": ("a.pdf", b"a")}
        )
        task_id = first.json()["task_id"]

        async def wait_for_success():
            deadline = asyncio.get_running_loop().time() + 10.0
            status = None
            while asyncio.get_running_loop().time() < deadline:
                status = await client.get(f"/v1/status/poll/{task_id}")
                if status.json()["task_status"] == "success":
                    return status
                await asyncio.sleep(0.01)
            raise AssertionError(
                "timed out waiting for task_status='success'; last response was "
                f"{status.json() if status is not None else None!r}"
            )

        status = await asyncio.wait_for(wait_for_success(), timeout=1)
        assert status.json()["task_status"] == "success"
        assert list(tmp_path.iterdir())

        now[0] += 6
        assert (await client.get(f"/v1/status/poll/{task_id}")).status_code == 404
        second = await client.post(
            "/v1/convert/file/async", files={"files": ("b.pdf", b"b")}
        )
        assert second.status_code == 202
    await app.state.job_registry.close()
    assert list(tmp_path.iterdir()) == []


@_run_async
async def test_expired_cleanup_error_releases_slot_and_retries_file(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    now = [100.0]
    registry = adapter_app.JobRegistry(
        root=tmp_path,
        max_jobs=1,
        result_ttl_seconds=5,
        clock=lambda: now[0],
    )
    upload = tmp_path / "expired-upload.pdf"
    result = tmp_path / "expired-result.zip"
    upload.write_bytes(b"upload")
    result.write_bytes(b"result")
    reservation = await registry.reserve()
    assert reservation is not None
    reservation.claimed = True
    job = sys.modules["adapter.jobs"].Job(
        task_id="expired",
        upload_path=upload,
        upload_name=upload.name,
        status="success",
        result_path=result,
        completed_at=now[0],
    )
    registry._jobs[job.task_id] = job
    real_unlink = Path.unlink
    failed_once = False

    def flaky_unlink(path, *args, **kwargs):
        nonlocal failed_once
        if path == result and not failed_once:
            failed_once = True
            raise PermissionError("simulated cleanup denial")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    now[0] += 6
    await registry.cleanup_expired()

    assert result.exists()
    assert "expired" in registry._cleanup_pending
    replacement = await registry.reserve()
    assert replacement is not None
    assert not result.exists()
    await registry.release_reservation(replacement)


@_run_async
async def test_finish_result_cleanup_error_releases_slot_and_retries_file(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    registry = adapter_app.JobRegistry(
        root=tmp_path, max_jobs=1, result_ttl_seconds=900
    )
    upload = tmp_path / "claimed-upload.pdf"
    result = tmp_path / "claimed-result.zip"
    upload.write_bytes(b"upload")
    result.write_bytes(b"result")
    reservation = await registry.reserve()
    assert reservation is not None
    reservation.claimed = True
    registry._jobs["claimed"] = sys.modules["adapter.jobs"].Job(
        task_id="claimed",
        upload_path=upload,
        upload_name=upload.name,
        status="success",
        result_path=result,
        completed_at=100.0,
        result_claimed=True,
    )
    real_unlink = Path.unlink
    failed_once = False

    def flaky_unlink(path, *args, **kwargs):
        nonlocal failed_once
        if path == result and not failed_once:
            failed_once = True
            raise PermissionError("simulated response cleanup denial")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    await registry.finish_result("claimed")

    assert result.exists()
    assert "claimed" in registry._cleanup_pending
    replacement = await registry.reserve()
    assert replacement is not None
    assert not result.exists()
    await registry.release_reservation(replacement)


@_run_async
async def test_persistent_cleanup_failure_retains_capacity_until_retry_succeeds(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    now = [100.0]
    registry = adapter_app.JobRegistry(
        root=tmp_path, max_jobs=1, result_ttl_seconds=5, clock=lambda: now[0]
    )
    upload = tmp_path / "expired-upload.pdf"
    result = tmp_path / "expired-result.zip"
    upload.write_bytes(b"upload")
    result.write_bytes(b"result")
    reservation = await registry.reserve()
    assert reservation is not None
    reservation.claimed = True
    registry._jobs["expired"] = sys.modules["adapter.jobs"].Job(
        task_id="expired", upload_path=upload, upload_name=upload.name,
        status="success", result_path=result, completed_at=now[0],
    )
    real_unlink = Path.unlink
    denied = True

    def controlled_unlink(path, *args, **kwargs):
        if path == result and denied:
            raise PermissionError("persistent cleanup denial")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", controlled_unlink)
    now[0] += 6
    await registry.cleanup_expired()

    assert await registry.reserve() is None
    assert registry._occupied == 1
    denied = False
    replacement = await registry.reserve()
    assert replacement is not None
    assert not result.exists()
    await registry.release_reservation(replacement)


@_run_async
async def test_cancelled_job_retains_failed_result_cleanup_for_ttl_retry(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    now = [100.0]
    registry = adapter_app.JobRegistry(
        root=tmp_path, max_jobs=1, result_ttl_seconds=5, clock=lambda: now[0]
    )
    upload = tmp_path / "upload.pdf"
    result = tmp_path / "result.zip"
    upload.write_bytes(b"upload")
    result.write_bytes(b"result")
    reservation = await registry.reserve()
    assert reservation is not None

    async def worker(_path, _name):
        return result

    task_id = await registry.start(
        reservation, upload_path=upload, upload_name=upload.name, worker=worker
    )
    job = registry._jobs[task_id]
    assert job.task is not None
    await registry._lock.acquire()
    await asyncio.sleep(0)
    real_unlink = Path.unlink
    failed_once = False

    def flaky_unlink(path, *args, **kwargs):
        nonlocal failed_once
        if path == result and not failed_once:
            failed_once = True
            raise PermissionError("simulated cleanup denial")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    job.task.cancel()
    registry._lock.release()
    with pytest.raises(asyncio.CancelledError):
        await job.task

    assert job.result_path == result
    assert registry._occupied == 1
    now[0] += 6
    await registry.cleanup_expired()
    assert not result.exists()
    assert registry._occupied == 0


@_run_async
async def test_ephemeral_response_joins_finish_across_repeated_cancellation(
    monkeypatch, tmp_path
):
    adapter_app = _load_app_module(monkeypatch)
    payload = tmp_path / "result.zip"
    payload.write_bytes(b"zip")
    finish_started = asyncio.Event()
    release_finish = asyncio.Event()
    finish_completed = False

    async def finish():
        nonlocal finish_completed
        finish_started.set()
        await release_finish.wait()
        finish_completed = True

    response = adapter_app._EphemeralFileResponse(
        payload, finish=finish, timeout_seconds=1
    )

    async def send(_message):
        return None

    task = asyncio.create_task(
        response({"type": "http", "method": "GET", "headers": []}, None, send)
    )
    await asyncio.wait_for(finish_started.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False
    release_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finish_completed is True


@_run_async
async def test_cleanup_sweeper_survives_one_registry_failure(monkeypatch, tmp_path):
    adapter_app = _load_app_module(monkeypatch)
    app = adapter_app.create_app(
        upstream=GateUpstream(),
        spool_root=tmp_path,
        max_jobs=1,
        result_ttl_seconds=1,
        upload_max_bytes=1024,
    )
    calls = 0
    recovered = asyncio.Event()

    async def flaky_cleanup():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("simulated sweep failure")
        recovered.set()

    monkeypatch.setattr(app.state.job_registry, "cleanup_expired", flaky_cleanup)
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(recovered.wait(), timeout=3)

    assert calls >= 2


@_run_async
async def test_oversized_upload_leaves_no_partial_file(monkeypatch, tmp_path):
    adapter_app = _load_app_module(monkeypatch)
    app = adapter_app.create_app(
        upstream=GateUpstream(),
        spool_root=tmp_path,
        max_jobs=1,
        upload_max_bytes=3,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://adapter",
    ) as client:
        response = await client.post(
            "/v1/convert/file/async", files={"files": ("large.pdf", b"large")}
        )

    assert response.status_code == 413
    assert not list(tmp_path.iterdir())
