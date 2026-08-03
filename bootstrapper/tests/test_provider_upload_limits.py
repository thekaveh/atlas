from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest


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
        downstream, max_body_bytes=5, paths={"/upload"}
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
