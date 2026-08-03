"""The Docling adapter bounds admission and cleans every owned artifact."""

from __future__ import annotations

import asyncio
import functools
import sys
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
        for _ in range(20):
            status = await client.get(f"/v1/status/poll/{task_id}")
            if status.json()["task_status"] == "success":
                break
            await asyncio.sleep(0)
        assert list(tmp_path.iterdir())

        now[0] += 6
        assert (await client.get(f"/v1/status/poll/{task_id}")).status_code == 404
        second = await client.post(
            "/v1/convert/file/async", files={"files": ("b.pdf", b"b")}
        )
        assert second.status_code == 202
    await asyncio.sleep(0)
    assert len(list(tmp_path.iterdir())) <= 1


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
