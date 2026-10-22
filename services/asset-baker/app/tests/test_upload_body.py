from __future__ import annotations

import asyncio
import math
import struct
import tempfile
import threading
from types import SimpleNamespace

import pytest
import starlette.formparsers as parsers

from asset_baker import api


PATH = "/assets/bake"
PREFIX = "ASSET_BAKER"
SLOT = "bake_semaphore"
TOKEN = "upload-test-token"
NORMAL_FIELDS = (("mode", "skip"), ("target_tris", "1000"))
INVALID_FIELDS = (("mode", "bad"),)
PRODUCTION_OVERHEAD = 2097152


def glb(size=256):
    doc = b'{"asset":{"version":"2.0"}}'
    doc += b" " * (size - 20 - len(doc))
    return struct.pack("<IIIII", 0x46546C67, 2, size, len(doc), 0x4E4F534A) + doc


def multipart(payload, fields=()):
    body = (
        b'--test\r\nContent-Disposition: form-data; name="file"; filename="mesh.glb"\r\n'
        b"Content-Type: model/gltf-binary\r\n\r\n" + payload + b"\r\n"
    )
    for name, value in fields:
        body += f'--test\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    return body + b"--test--\r\n"


class Stream:
    def __init__(self, body, *, clock=None, chunk=97):
        self.parts = [body[i:i + chunk] for i in range(0, len(body), chunk)]
        self.calls = 0
        self.clock = clock

    async def __call__(self):
        if self.calls >= len(self.parts):
            await asyncio.Future()
        body = self.parts[self.calls]
        self.calls += 1
        if self.clock is not None:
            self.clock.value += 0.4
        return {"type": "http.request", "body": body, "more_body": self.calls < len(self.parts)}


async def request(app, receive, *, headers=None, send_check=None):
    values = {"content-type": "multipart/form-data; boundary=test", "authorization": f"Bearer {TOKEN}"}
    values.update(headers or {})
    encoded = [(name.encode(), value.encode()) for name, value in values.items() if value is not None]
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": PATH, "raw_path": PATH.encode(), "query_string": b"",
        "root_path": "", "headers": encoded,
        "client": ("test", 1), "server": ("test", 80),
    }
    messages = []

    async def send(message):
        if send_check is not None:
            send_check(message)
        messages.append(message)

    await app(scope, receive, send)
    return messages


def status(messages):
    starts = [m["status"] for m in messages if m["type"] == "http.response.start"]
    assert len(starts) == 1
    return starts[0]


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv(f"{PREFIX}_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv(f"{PREFIX}_MINIO_ENABLED", "false")
    monkeypatch.setenv(f"{PREFIX}_MAX_UPLOAD_MB", str(256 / 1048576))
    monkeypatch.setattr(api, "_MULTIPART_OVERHEAD_BYTES", 512, raising=False)
    calls = []

    def fake_run(source, output, params):
        calls.append(source)
        output.mkdir()
        target = output / "result.glb"
        target.write_bytes(glb())
        return SimpleNamespace(glb_path=target, basecolor_path=None, normal_path=None, summary={})

    monkeypatch.setattr(api, "run_bake", fake_run)
    return api.create_app(api_token=TOKEN), calls


@pytest.fixture
def spools(monkeypatch, tmp_path):
    result = {"parser": [], "ingress": []}
    real = tempfile.SpooledTemporaryFile
    spool_root = tmp_path / "spools"
    spool_root.mkdir()

    def factory(kind):
        def create(*args, **kwargs):
            kwargs["dir"] = spool_root
            handle = real(*args, **kwargs)
            handle.written = 0
            handle.write_sizes = []
            handle.rolled_before_write = []
            write = handle.write

            def tracked(data):
                handle.written += len(data)
                handle.write_sizes.append(len(data))
                handle.rolled_before_write.append(handle._rolled)
                return write(data)

            handle.write = tracked
            result[kind].append(handle)
            return handle
        return create

    monkeypatch.setattr(parsers, "SpooledTemporaryFile", factory("parser"))
    # The API exists before implementation; the new helper is optional here so
    # RED demonstrates unbounded parsing rather than a missing-module error.
    import sys
    helper = sys.modules.get(api.__package__ + ".upload_body")
    if helper is not None:
        monkeypatch.setattr(helper, "SpooledTemporaryFile", factory("ingress"))
    yield result
    closed(result)
    assert not list(spool_root.iterdir())


def assert_slot(app):
    slot = getattr(app.state, SLOT)
    assert slot.acquire(blocking=False)
    slot.release()


def test_no_length_oversize_stops_before_parser(setup, spools):
    app, calls = setup
    stream = Stream(multipart(b"x" * 2048))
    messages = run(request(app, stream))
    assert status(messages) == 413
    assert stream.calls == math.ceil(769 / 97), (stream.calls, len(stream.parts), [h.written for h in spools["parser"]])
    assert not spools["parser"]
    assert sum(h.written for h in spools["ingress"]) <= 768
    assert all(h.closed for h in spools["ingress"])
    assert not calls
    assert_slot(app)


def test_progress_obeys_complete_body_deadline(setup, monkeypatch, spools):
    app, calls = setup
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(api, "time", SimpleNamespace(monotonic=lambda: clock.value))
    monkeypatch.setenv(f"{PREFIX}_UPLOAD_TIMEOUT_SECONDS", "1")
    stream = Stream(multipart(glb()), clock=clock)
    messages = run(request(app, stream))
    assert status(messages) == 408, (stream.calls, clock.value)
    assert not calls and not spools["parser"]
    assert all(h.closed for h in spools["ingress"])
    assert_slot(app)


def run(coroutine):
    async def bounded():
        return await asyncio.wait_for(coroutine, 5)
    return asyncio.run(bounded())


def closed(spools):
    handles = spools["ingress"] + spools["parser"]
    assert all(handle.closed for handle in handles)


async def reuse(app):
    assert status(await request(app, Stream(multipart(glb())))) == 200


@pytest.mark.parametrize("declared", [None, "1", "-1", "nonsense"])
def test_actual_size_ignores_declared_length(setup, spools, declared):
    app, calls = setup
    stream = Stream(multipart(b"x" * 2048), chunk=4096)
    def check(message):
        if message["type"] == "http.response.start":
            closed(spools)
    assert status(run(request(app, stream, headers={"content-length": declared}, send_check=check))) == 413
    assert stream.calls == 1
    assert spools["ingress"][0].written == 0
    assert not spools["parser"] and not calls
    run(reuse(app))


@pytest.mark.parametrize("declared", ["769", "9" * 4500])
def test_declared_oversize_rejects_without_resources(setup, spools, declared):
    app, calls = setup
    stream = Stream(multipart(glb()))
    assert status(run(request(app, stream, headers={"content-length": declared}))) == 413
    assert stream.calls == 0
    assert not spools["ingress"] and not spools["parser"] and not calls
    run(reuse(app))


@pytest.mark.parametrize("size,expected", [(256, 200), (257, 413)])
def test_final_cap_inside_framing_allowance(setup, spools, size, expected):
    app, calls = setup
    assert status(run(request(app, Stream(multipart(glb(size)))))) == expected
    assert len(calls) == (expected == 200)
    assert len(spools["parser"]) == 1
    closed(spools)
    run(reuse(app))


def test_normal_fields_and_real_rollover(setup, spools, monkeypatch):
    from importlib import import_module
    helper = import_module(api.__package__ + ".upload_body")
    monkeypatch.setattr(helper.UploadBody, "memory_bytes", 32)
    monkeypatch.setattr(helper.UploadBody, "replay_bytes", 32)
    monkeypatch.setattr(parsers.MultiPartParser, "spool_max_size", 32)
    app, calls = setup
    body = multipart(glb(), NORMAL_FIELDS)
    assert status(run(request(app, Stream(body)))) == 200
    assert calls and spools["parser"][0]._rolled
    assert spools["ingress"][0]._rolled
    assert all(spools["ingress"][0].rolled_before_write)
    assert max(spools["parser"][0].write_sizes) <= 32
    closed(spools)


def test_production_defaults(monkeypatch):
    monkeypatch.delenv(f"{PREFIX}_UPLOAD_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv(f"{PREFIX}_MAX_UPLOAD_MB", raising=False)
    from importlib import import_module
    helper = import_module(api.__package__ + ".upload_body")
    assert api._upload_timeout_seconds() == 300
    assert helper.UploadBody.memory_bytes == 1048576
    assert helper.UploadBody.replay_bytes == 65536
    assert api._MULTIPART_OVERHEAD_BYTES == PRODUCTION_OVERHEAD
    cap = api._max_input_bytes() if PREFIX == "ASSET_WORKER" else api._max_upload_bytes()
    assert cap == 200 * 1048576


@pytest.mark.parametrize("value", ["bad", "0", "-1", "nan", "inf", "-inf", "1e309"])
def test_invalid_timeout_releases_admission(setup, spools, monkeypatch, value):
    app, calls = setup
    monkeypatch.setenv(f"{PREFIX}_UPLOAD_TIMEOUT_SECONDS", value)
    stream = Stream(multipart(glb()))
    messages = run(request(app, stream))
    assert status(messages) == 500
    payload = b"".join(m.get("body", b"") for m in messages)
    assert f"{PREFIX}_UPLOAD_TIMEOUT_SECONDS".encode() in payload
    assert stream.calls == 0 and not calls
    assert not spools["ingress"] and not spools["parser"]
    monkeypatch.setenv(f"{PREFIX}_UPLOAD_TIMEOUT_SECONDS", "1.25")
    run(reuse(app))


@pytest.mark.parametrize("auth", [(None, TOKEN, 401), ("bad", TOKEN, 401), (TOKEN, "", 503)])
def test_auth_precedes_semaphore_and_body(monkeypatch, spools, auth):
    token, configured, expected = auth
    class ForbiddenSlot:
        def acquire(self, **kwargs):
            pytest.fail("authentication accessed admission")
    app = api.create_app(api_token=configured)
    setattr(app.state, SLOT, ForbiddenSlot())
    stream = Stream(multipart(glb()))
    assert status(run(request(app, stream, headers={"authorization": f"Bearer {token}" if token is not None else None}))) == expected
    assert stream.calls == 0
    assert not spools["ingress"] and not spools["parser"]


def test_busy_reads_and_releases_nothing(setup, spools):
    class BusySlot:
        def acquire(self, **kwargs):
            return False
        def release(self):
            pytest.fail("released unacquired slot")
    app, calls = setup
    setattr(app.state, SLOT, BusySlot())
    stream = Stream(multipart(glb()))
    assert status(run(request(app, stream))) == 429
    assert stream.calls == 0 and not calls
    assert not spools["ingress"] and not spools["parser"]


def test_stalled_receive_is_cancelled_and_closed_before_response(setup, spools, monkeypatch):
    app, calls = setup
    monkeypatch.setenv(f"{PREFIX}_UPLOAD_TIMEOUT_SECONDS", "0.02")
    async def scenario():
        pending = asyncio.Event()
        stopped = asyncio.Event()
        async def receive():
            pending.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        def check(message):
            if message["type"] == "http.response.start":
                assert pending.is_set() and stopped.is_set()
                closed(spools)
        assert status(await request(app, receive, send_check=check)) == 408
        monkeypatch.setenv(f"{PREFIX}_UPLOAD_TIMEOUT_SECONDS", "300")
        await reuse(app)
    run(scenario())
    assert len(calls) == 1


def test_empty_progress_cannot_extend_deadline(setup, spools, monkeypatch):
    app, calls = setup
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(api, "time", SimpleNamespace(monotonic=lambda: clock.value))
    monkeypatch.setenv(f"{PREFIX}_UPLOAD_TIMEOUT_SECONDS", "1")
    async def receive():
        clock.value += 0.4
        return {"type": "http.request", "body": b"", "more_body": True}
    assert status(run(request(app, receive))) == 408
    assert clock.value == pytest.approx(1.2)
    assert not spools["parser"] and not calls
    closed(spools)
    assert_slot(app)


@pytest.mark.parametrize("cancel", [False, True])
def test_aborted_ingress_closes_before_releasing(setup, spools, cancel):
    app, calls = setup
    async def scenario():
        started = asyncio.Event()
        stopped = asyncio.Event()
        count = 0
        async def receive():
            nonlocal count
            count += 1
            if count == 1:
                return {"type": "http.request", "body": b"prefix", "more_body": True}
            started.set()
            if not cancel:
                return {"type": "http.disconnect"}
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        task = asyncio.create_task(request(app, receive))
        try:
            if cancel:
                await started.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert stopped.is_set()
            else:
                assert status(await task) == 400
            closed(spools)
            assert not spools["parser"] and not calls
            await reuse(app)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    run(scenario())


def test_rolled_parser_repeated_cancellation_keeps_cleanup_owned(setup, spools, monkeypatch):
    app, calls = setup
    monkeypatch.setattr(parsers.MultiPartParser, "spool_max_size", 32)
    original = parsers.UploadFile.write
    async def scenario():
        paused = asyncio.Event()
        release = asyncio.Event()
        async def write(file, data):
            await original(file, data)
            if not paused.is_set():
                paused.set()
                await release.wait()
        monkeypatch.setattr(parsers.UploadFile, "write", write)
        task = asyncio.create_task(request(app, Stream(multipart(glb()))))
        try:
            await paused.wait()
            assert spools["parser"][0]._rolled
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert status(await request(app, Stream(multipart(glb())))) == 429
            assert not spools["parser"][0].closed
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        closed(spools)
        assert len(calls) == 1
        await reuse(app)
    run(scenario())


def test_converter_thread_cancellation_keeps_slot_and_form_owned(setup, spools, monkeypatch):
    app, calls = setup
    started, release = threading.Event(), threading.Event()
    def blocking(*args, **kwargs):
        started.set()
        assert release.wait(3)
        raise api.HTTPException(status_code=418, detail="owned work completed")
    monkeypatch.setattr(api, "_process_path", blocking)
    async def scenario():
        task = asyncio.create_task(request(app, Stream(multipart(glb()))))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            assert status(await request(app, Stream(multipart(glb())))) == 429
            assert not spools["parser"][0].closed
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        closed(spools)
        assert_slot(app)
    run(scenario())


def test_parsed_validation_failure_closes_form(setup, spools):
    app, calls = setup
    body = multipart(glb(), INVALID_FIELDS)
    assert status(run(request(app, Stream(body)))) == 422
    assert not calls
    closed(spools)
    run(reuse(app))


def test_multipart_exception_closes_partial_file(setup, spools):
    app, calls = setup
    body = multipart(glb()).removesuffix(b"--test--\r\n")
    body += b'--test\r\nContent-Disposition: form-data; filename="missing-name.glb"\r\n\r\nx\r\n--test--\r\n'
    assert status(run(request(app, Stream(body)))) == 400
    assert len(spools["parser"]) == 1 and not calls
    closed(spools)
    run(reuse(app))


def test_completed_body_deadline_does_not_limit_converter(setup, monkeypatch):
    app, calls = setup
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(api, "time", SimpleNamespace(monotonic=lambda: clock.value))
    monkeypatch.setenv(f"{PREFIX}_UPLOAD_TIMEOUT_SECONDS", "0.5")
    original = api._process_path
    def process(*args, **kwargs):
        clock.value += 1000
        return original(*args, **kwargs)
    monkeypatch.setattr(api, "_process_path", process)
    assert status(run(request(app, Stream(multipart(glb()))))) == 200
    assert calls


def test_replay_terminal_then_original_disconnect(monkeypatch, spools):
    from importlib import import_module
    helper = import_module(api.__package__ + ".upload_body")
    monkeypatch.setattr(helper.UploadBody, "replay_bytes", 3)
    async def scenario():
        messages = iter([
            {"type": "http.request", "body": b"abcdefg", "more_body": False},
            {"type": "http.disconnect"},
        ])
        async def receive():
            return next(messages)
        with helper.UploadBody(10, 1, lambda: 0) as body:
            await body.receive(receive)
            replay = [await body.replay() for _ in range(3)]
            assert [item["body"] for item in replay] == [b"abc", b"def", b"g"]
            assert [item["more_body"] for item in replay] == [True, True, False]
            assert await body.replay() == {"type": "http.disconnect"}
        closed(spools)
    run(scenario())


def test_receive_exception_closes_ingress(setup, spools):
    app, calls = setup
    async def scenario():
        count = 0
        async def receive():
            nonlocal count
            count += 1
            if count == 1:
                return {"type": "http.request", "body": b"prefix", "more_body": True}
            raise RuntimeError("synthetic receive failure")
        with pytest.raises(RuntimeError, match="synthetic receive failure"):
            await request(app, receive)
        closed(spools)
        assert not spools["parser"] and not calls
        await reuse(app)
    run(scenario())
