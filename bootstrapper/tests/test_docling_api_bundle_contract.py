"""Exercise the hardened Docling API without loading model dependencies."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import sys
import types
import zipfile
from pathlib import Path

from httpx2 import ASGITransport, AsyncClient


ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "services" / "docling" / "provider"
SHARED = PROVIDER / "shared"


def _load_named(name: str, path: Path, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_docling_api_authenticates_and_converts_once_per_response(monkeypatch):
    monkeypatch.setenv("DOCLING_API_TOKEN", "docling-test-token")
    monkeypatch.setenv("DOCLING_AUTH_MODE", "required")
    monkeypatch.setenv("DOCLING_CONCURRENCY", "1")
    monkeypatch.setenv("DOCLING_CORS_ORIGINS", "")

    models = _load_named("models", SHARED / "models.py", monkeypatch)
    _load_named("pipeline_config", SHARED / "pipeline_config.py", monkeypatch)
    _load_named("utils", SHARED / "utils.py", monkeypatch)
    _load_named("bounded_upload", PROVIDER / "bounded_upload.py", monkeypatch)
    _load_named("provider_boundary", PROVIDER / "provider_boundary.py", monkeypatch)
    _load_named("lightrag_bundle", PROVIDER / "lightrag_bundle.py", monkeypatch)

    doc_module = types.ModuleType("docling_core.types.doc")

    class ImageRefMode:
        REFERENCED = "referenced"

    doc_module.ImageRefMode = ImageRefMode
    monkeypatch.setitem(sys.modules, "docling_core", types.ModuleType("docling_core"))
    monkeypatch.setitem(
        sys.modules, "docling_core.types", types.ModuleType("docling_core.types")
    )
    monkeypatch.setitem(sys.modules, "docling_core.types.doc", doc_module)

    conversion_calls = 0

    class FakeDocument:
        def save_as_json(self, filename, *, artifacts_dir, image_mode):
            Path(filename).write_text('{"schema_name":"DoclingDocument"}', encoding="utf-8")

        def save_as_markdown(self, filename, *, artifacts_dir, image_mode):
            Path(filename).write_text("# API bundle\n", encoding="utf-8")

    class FakeResult:
        document = FakeDocument()

    processor = types.ModuleType("processor")

    def convert_document_once(file_path, *, use_ocr, table_mode):
        nonlocal conversion_calls
        conversion_calls += 1
        assert Path(file_path).read_bytes() == b"document bytes"
        return FakeResult()

    def render_conversion(result, *, file_path, output_format, **kwargs):
        return models.ConversionResponse(
            content="# Synchronous response",
            format=output_format,
            metadata=models.DocumentMetadata(
                pages=1,
                tables=0,
                images=0,
                formulas=0,
                processing_time=0.01,
                source_format="pdf",
                file_size=Path(file_path).stat().st_size,
            ),
            chunks=None,
        )

    async def processor_status():
        return "healthy"

    processor.convert_document_once = convert_document_once
    processor.render_conversion = render_conversion
    processor.processor_status = processor_status
    monkeypatch.setitem(sys.modules, "processor", processor)

    api = _load_named(
        "docling_api_under_test", SHARED / "api_server.py", monkeypatch
    )

    async def scenario():
        async with AsyncClient(
            transport=ASGITransport(app=api.app, raise_app_exceptions=False),
            base_url="http://docling.test",
        ) as client:
            health = await client.get("/health")
            rejected = await client.post(
                "/internal/lightrag/bundle",
                files={"file": ("report.pdf", b"document bytes", "application/pdf")},
            )
            headers = {"Authorization": "Bearer docling-test-token"}
            bundle = await client.post(
                "/internal/lightrag/bundle",
                headers=headers,
                files={"file": ("report.pdf", b"document bytes", "application/pdf")},
            )
            converted = await client.post(
                "/v1/document/convert",
                headers=headers,
                files={"file": ("report.pdf", b"document bytes", "application/pdf")},
            )
            return health, rejected, bundle, converted

    health, rejected, bundle, converted = asyncio.run(scenario())

    assert health.status_code == 200
    assert rejected.status_code == 401
    assert bundle.status_code == 200
    assert bundle.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert archive.namelist() == ["report.json", "report.md"]
    assert converted.status_code == 200
    assert converted.json()["content"] == "# Synchronous response"
    assert converted.json()["format"] == "markdown"
    assert conversion_calls == 2
