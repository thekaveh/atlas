from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI, File, UploadFile
from starlette import formparsers


ROOT = Path(__file__).resolve().parents[2]


def test_provider_upload_boundaries_remain_byte_equivalent():
    docling = ROOT / "services/docling/provider/bounded_upload.py"
    parakeet = ROOT / "services/parakeet/provider/bounded_upload.py"
    assert docling.read_bytes() == parakeet.read_bytes()


@pytest.mark.parametrize(
    "relative_path",
    [
        "services/docling/provider/shared/api_server.py",
        "services/docling/provider/localhost/server.py",
        "services/parakeet/provider/shared/api_server.py",
        "services/parakeet/provider/mlx/api_server.py",
    ],
)
def test_provider_apps_cap_request_bodies_before_multipart(relative_path):
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    assert "RequestBodyLimitMiddleware" in source
    assert "max_body_bytes=multipart_body_limit(_MAX_UPLOAD_BYTES)" in source
    assert "body_timeout_seconds=_UPLOAD_TIMEOUT_SECONDS" in source


class FakeUpload:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = iter(chunks)
        self.read_sizes: list[int] = []

    async def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return next(self._chunks, b"")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "relative_path",
    [
        "services/docling/provider/bounded_upload.py",
        "services/parakeet/provider/bounded_upload.py",
    ],
)
def test_provider_upload_spool_is_chunked_and_bounded(relative_path, tmp_path):
    module = _load(ROOT / relative_path, relative_path.replace("/", "_"))
    upload = FakeUpload([b"abcd", b"efgh"])

    path = asyncio.run(
        module.spool_upload(upload, max_bytes=8, suffix=".bin", directory=tmp_path)
    )

    assert path.read_bytes() == b"abcdefgh"
    assert all(size > 0 for size in upload.read_sizes)
    path.unlink()


@pytest.mark.parametrize(
    "relative_path",
    [
        "services/docling/provider/bounded_upload.py",
        "services/parakeet/provider/bounded_upload.py",
    ],
)
def test_provider_upload_spool_removes_partial_file_when_limit_is_exceeded(
    relative_path, tmp_path
):
    module = _load(ROOT / relative_path, relative_path.replace("/", "_"))
    upload = FakeUpload([b"abcd", b"efgh", b"i"])

    with pytest.raises(module.UploadTooLargeError):
        asyncio.run(
            module.spool_upload(
                upload, max_bytes=7, suffix=".bin", directory=tmp_path
            )
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "relative_path",
    [
        "services/docling/provider/bounded_upload.py",
        "services/parakeet/provider/bounded_upload.py",
    ],
)
def test_provider_request_body_limit_precedes_multipart_spooling(relative_path):
    module = _load(ROOT / relative_path, relative_path.replace("/", "_") + "_body")
    delivered = bytearray()
    sent = []
    incoming = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
    )

    async def downstream(_scope, receive, _send):
        while True:
            message = await receive()
            delivered.extend(message.get("body", b""))
            if not message.get("more_body", False):
                return

    async def receive():
        return next(incoming)

    async def send(message):
        sent.append(message)

    middleware = module.RequestBodyLimitMiddleware(
        downstream,
        max_body_bytes=5,
        body_timeout_seconds=1,
        paths={"/upload"},
    )
    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/upload",
                "headers": [],
            },
            receive,
            send,
        )
    )

    assert delivered == b"123"
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 413


@pytest.mark.parametrize(
    "relative_path",
    [
        "services/docling/provider/bounded_upload.py",
        "services/parakeet/provider/bounded_upload.py",
    ],
)
def test_provider_request_body_limit_has_total_read_deadline(relative_path):
    module = _load(ROOT / relative_path, relative_path.replace("/", "_") + "_timeout")
    delivered = bytearray()
    sent = []
    calls = 0

    async def downstream(_scope, receive, _send):
        while True:
            message = await receive()
            delivered.extend(message.get("body", b""))

    async def receive():
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": b"123", "more_body": True}
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    middleware = module.RequestBodyLimitMiddleware(
        downstream,
        max_body_bytes=5,
        body_timeout_seconds=0.01,
        paths={"/upload"},
    )
    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/upload",
                "headers": [],
            },
            receive,
            send,
        )
    )

    assert delivered == b"123"
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 408


def test_body_timeout_closes_starlette_multipart_tempfiles(monkeypatch):
    module = _load(
        ROOT / "services/docling/provider/bounded_upload.py",
        "docling_bounded_upload_tempfile_cleanup",
    )
    created = []
    original = formparsers.SpooledTemporaryFile

    def tracking_tempfile(*args, **kwargs):
        stream = original(*args, **kwargs)
        created.append(stream)
        return stream

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", tracking_tempfile)
    inner = FastAPI()

    @inner.post("/upload")
    async def upload(file: UploadFile = File(...)):
        return {"filename": file.filename}

    app = module.RequestBodyLimitMiddleware(
        inner,
        max_body_bytes=3 * 1024 * 1024,
        body_timeout_seconds=0.01,
        paths={"/upload"},
    )
    boundary = b"atlas-boundary"
    first_chunk = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="large.bin"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
        + b"x" * (1024 * 1024 + 1)
    )
    calls = 0
    sent = []

    async def receive():
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "type": "http.request",
                "body": first_chunk,
                "more_body": True,
            }
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    asyncio.run(
        app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/upload",
                "raw_path": b"/upload",
                "query_string": b"",
                "headers": [
                    (
                        b"content-type",
                        b"multipart/form-data; boundary=" + boundary,
                    )
                ],
                "client": ("127.0.0.1", 1),
                "server": ("provider", 80),
            },
            receive,
            send,
        )
    )

    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 408
    assert created
    assert all(stream.closed for stream in created)


@pytest.mark.parametrize("variant", ["gpu", "localhost"])
def test_docling_converter_failure_is_not_returned_as_successful_markdown(
    monkeypatch, tmp_path, variant
):
    provider = ROOT / "services/docling/provider"
    processor_path = provider / variant / "processor.py"
    class Model:
        def __init__(self, **values):
            self.__dict__.update(values)

    models = types.ModuleType("models")
    for name in (
        "ConversionResponse",
        "DocumentMetadata",
        "DocumentChunk",
        "ChunkMetadata",
    ):
        setattr(models, name, Model)
    utils = types.ModuleType("utils")
    utils.get_file_size = lambda path: Path(path).stat().st_size
    utils.detect_format = lambda path: Path(path).suffix.lstrip(".")
    utils.chunk_text = lambda *args: []
    monkeypatch.setitem(sys.modules, "models", models)
    monkeypatch.setitem(sys.modules, "utils", utils)
    processor = _load(processor_path, f"docling_{variant}_processor")

    class BrokenConverter:
        def convert(self, file_path):
            raise RuntimeError("malformed document")

    processor.DocumentConverter = BrokenConverter
    monkeypatch.setattr(processor, "build_converter", lambda _settings: BrokenConverter())
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"%PDF-broken")

    with pytest.raises(RuntimeError, match="Docling processing failed"):
        asyncio.run(processor.process_document(str(source)))
