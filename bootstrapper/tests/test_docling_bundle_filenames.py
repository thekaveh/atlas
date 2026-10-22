"""Bound shared LightRAG export names through real file, provider and job seams."""

from __future__ import annotations

import asyncio
import importlib.abc
import importlib.util
import io
import sys
import types
import zipfile
from pathlib import Path, PurePosixPath

import pytest
from httpx2 import ASGITransport, AsyncClient


ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "services" / "docling" / "provider"
JSON_BYTES = b'{"schema_name":"DoclingDocument"}'
MARKDOWN_BYTES = b"# Converted\n"
UPLOAD_BYTES = b"document bytes"
TOKEN = "filename-test-token"
HTTP_CASES = [
    pytest.param("report.pdf", "report", id="ordinary"),
    pytest.param("a" * 250 + ".pdf", "a" * 250, id="stem250"),
    pytest.param("a" * 251 + ".pdf", "a" * 250, id="stem251"),
    pytest.param("a" * 500 + ".pdf", "a" * 250, id="oversized-http"),
    pytest.param("漢" * 300 + ".pdf", "document", id="all-cjk"),
    pytest.param("a漢" * 260 + ".pdf", "a_" * 125, id="mixed-cjk"),
]
PATH_CASES = [
    pytest.param("../../secret.pdf", "secret", id="traversal"),
    pytest.param("/absolute/private.pdf", "private", id="absolute"),
    pytest.param("..\\..\\windows-secret.docx", "windows-secret", id="backslashes"),
    pytest.param("nul\x00name.pdf", "nulname", id="nul"),
    pytest.param("", "document", id="empty"),
    pytest.param("---.pdf", "document", id="punctuation"),
    pytest.param("   .pdf", "document", id="whitespace"),
]


class _BlockModels(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {
            "docling", "docling_core", "torch", "transformers", "huggingface_hub"
        }:
            raise AssertionError("Unexpected toolkit/model import: " + fullname)


def _load(name, path, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class _Document:
    def __init__(self):
        self.writes = []

    def save_as_json(self, filename, *, artifacts_dir, image_mode):
        assert image_mode == "referenced"
        self.writes.append(Path(filename))
        Path(filename).write_bytes(JSON_BYTES)

    def save_as_markdown(self, filename, *, artifacts_dir, image_mode):
        assert image_mode == "referenced"
        self.writes.append(Path(filename))
        Path(filename).write_bytes(MARKDOWN_BYTES)


@pytest.fixture
def bundle_runtime(monkeypatch):
    monkeypatch.setattr(sys, "meta_path", [_BlockModels(), *sys.meta_path])
    for name in ("docling_core", "docling_core.types", "docling_core.types.doc"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["docling_core.types.doc"].ImageRefMode = types.SimpleNamespace(
        REFERENCED="referenced"
    )
    return _load("lightrag_bundle", PROVIDER / "lightrag_bundle.py", monkeypatch)


def _assert_safe_name(name):
    parsed = PurePosixPath(name)
    assert not parsed.is_absolute()
    assert ".." not in parsed.parts
    assert "\x00" not in name and "\\" not in name
    assert all(len(part.encode("utf-8")) <= 255 for part in parsed.parts)


def _assert_bundle(payload, stem):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == sorted([stem + ".json", stem + ".md"])
        for member in archive.infolist():
            _assert_safe_name(member.filename)
            assert member.date_time == (1980, 1, 1, 0, 0, 0)
            assert member.external_attr == 0o100600 << 16
        assert archive.read(stem + ".json") == JSON_BYTES
        assert archive.read(stem + ".md") == MARKDOWN_BYTES


@pytest.mark.parametrize("upload_name, stem", HTTP_CASES + PATH_CASES)
def test_real_exports_have_bounded_repeatable_names(bundle_runtime, upload_name, stem):
    document = _Document()
    result = types.SimpleNamespace(document=document)
    payload = bundle_runtime.build_lightrag_bundle(result, upload_name=upload_name)
    _assert_bundle(payload, stem)
    assert bundle_runtime.build_lightrag_bundle(result, upload_name=upload_name) == payload
    assert [path.name for path in document.writes] == [stem + ".json", stem + ".md"] * 2
    assert all(not path.exists() for path in document.writes)


def test_maximum_pdf_upload_basename_exports_on_real_filesystem(bundle_runtime, tmp_path):
    source = tmp_path / ("a" * 251 + ".pdf")
    source.write_bytes(UPLOAD_BYTES)
    assert len(source.name.encode("utf-8")) == 255
    assert source.read_bytes() == UPLOAD_BYTES
    result = types.SimpleNamespace(document=_Document())
    payload = bundle_runtime.build_lightrag_bundle(result, upload_name=source.name)
    _assert_bundle(payload, "a" * 250)


@pytest.fixture(params=["shared", "localhost"])
def provider_runtime(request, monkeypatch, tmp_path):
    request.getfixturevalue("bundle_runtime")
    monkeypatch.syspath_prepend(str(PROVIDER))
    for key, value in {
        "DOCLING_API_TOKEN": TOKEN, "DOCLING_AUTH_MODE": "required",
        "DOCLING_CONCURRENCY": "1", "DOCLING_CORS_ORIGINS": "",
        "DOCLING_ADAPTER_SPOOL_ROOT": str(tmp_path / "default-adapter"),
    }.items():
        monkeypatch.setenv(key, value)
    for name, path in {
        "models": "shared/models.py", "pipeline_config": "shared/pipeline_config.py",
        "utils": "shared/utils.py", "bounded_upload": "bounded_upload.py",
        "provider_boundary": "provider_boundary.py",
    }.items():
        _load(name, PROVIDER / path, monkeypatch)
    calls = []
    documents = []
    processor = types.ModuleType("processor")

    def convert_document_once(file_path, *, use_ocr, table_mode):
        assert Path(file_path).read_bytes() == UPLOAD_BYTES
        calls.append(Path(file_path))
        document = _Document()
        documents.append(document)
        return types.SimpleNamespace(document=document)

    processor.convert_document_once = convert_document_once
    processor.processor_status = lambda: "healthy"
    processor.render_conversion = lambda *args, **kwargs: pytest.fail("Unexpected rendering")
    monkeypatch.setitem(sys.modules, "processor", processor)
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", dotenv)
    path = "shared/api_server.py" if request.param == "shared" else "localhost/server.py"
    api = _load("filename_provider_api", PROVIDER / path, monkeypatch)
    return types.SimpleNamespace(app=api.app, calls=calls, documents=documents)


def _assert_conversion_cleaned(runtime):
    assert len(runtime.calls) == 1
    assert not runtime.calls[0].exists()
    assert len(runtime.documents[0].writes) == 2
    assert all(not path.exists() for path in runtime.documents[0].writes)


@pytest.mark.parametrize("upload_name, stem", HTTP_CASES)
def test_provider_handlers_export_bounded_names(provider_runtime, upload_name, stem):
    async def scenario():
        async with AsyncClient(
            transport=ASGITransport(app=provider_runtime.app, raise_app_exceptions=False),
            base_url="http://docling.test", timeout=5,
        ) as client:
            return await client.post(
                "/internal/lightrag/bundle",
                headers={"Authorization": "Bearer " + TOKEN},
                files={"file": (upload_name, UPLOAD_BYTES, "application/pdf")},
            )

    response = asyncio.run(scenario())
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    _assert_bundle(response.content, stem)
    _assert_conversion_cleaned(provider_runtime)


@pytest.fixture
def adapter_module(provider_runtime, monkeypatch):
    for name in list(sys.modules):
        if name == "adapter" or name.startswith("adapter."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    from adapter import app

    return types.SimpleNamespace(module=app, runtime=provider_runtime)


def _upstream(adapter_module, runtime, root):
    return adapter_module.module.DoclingUpstream(
        endpoint="http://docling.test", token=TOKEN, result_root=root,
        transport=ASGITransport(app=runtime.app, raise_app_exceptions=False),
        retry_delay_seconds=0,
    )


@pytest.mark.parametrize("upload_name, stem", HTTP_CASES)
def test_actual_upstream_downloads_bounded_bundle(
    adapter_module, tmp_path, upload_name, stem
):
    runtime = adapter_module.runtime
    source = tmp_path / "input.pdf"
    source.write_bytes(UPLOAD_BYTES)
    upstream = _upstream(adapter_module, runtime, tmp_path)

    async def scenario():
        output = await upstream.convert(source, upload_name, timeout_seconds=5)
        try:
            _assert_bundle(output.read_bytes(), stem)
        finally:
            output.unlink(missing_ok=True)

    asyncio.run(scenario())
    _assert_conversion_cleaned(runtime)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["default-adapter", "input.pdf"]


async def _await_success(client, task_id):
    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get("/v1/status/poll/" + task_id)
        assert response.status_code == 200
        status = response.json()["task_status"]
        if status in {"success", "failure"}:
            assert status == "success", response.text
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Adapter job did not complete within five seconds")


@pytest.mark.parametrize("upload_name, stem", HTTP_CASES)
def test_adapter_submit_poll_download_preserves_bounded_names(
    adapter_module, tmp_path, upload_name, stem
):
    runtime = adapter_module.runtime
    spool = tmp_path / "jobs"
    adapter = adapter_module.module.create_app(
        upstream=_upstream(adapter_module, runtime, spool), spool_root=spool,
        max_jobs=1, upload_max_bytes=1024, job_timeout_seconds=5,
    )

    async def scenario():
        async with adapter.router.lifespan_context(adapter):
            async with AsyncClient(
                transport=ASGITransport(app=adapter, raise_app_exceptions=False),
                base_url="http://adapter.test", timeout=5,
            ) as client:
                submitted = await client.post(
                    "/v1/convert/file/async",
                    files={"files": (upload_name, UPLOAD_BYTES, "application/pdf")},
                )
                assert submitted.status_code == 202, submitted.text
                task_id = submitted.json()["task_id"]
                await _await_success(client, task_id)
                result = await client.get("/v1/result/" + task_id)
                assert result.status_code == 200, result.text
                _assert_bundle(result.content, stem)
                assert list(spool.iterdir()) == []

    asyncio.run(scenario())
    _assert_conversion_cleaned(runtime)
    assert list(spool.iterdir()) == []
