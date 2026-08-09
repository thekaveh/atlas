"""The Docling adapter bounds admission and cleans every owned artifact."""

from __future__ import annotations

import asyncio
import functools
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
