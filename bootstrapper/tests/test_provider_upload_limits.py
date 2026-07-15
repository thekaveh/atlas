from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


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
