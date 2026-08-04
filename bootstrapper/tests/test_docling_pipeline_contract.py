from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "services/docling/provider/shared/pipeline_config.py"


def _load_pipeline_module():
    spec = importlib.util.spec_from_file_location("docling_pipeline_config", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pipeline_settings_honor_request_and_environment_contracts():
    pipeline = _load_pipeline_module()

    settings = pipeline.resolve_pipeline_settings(
        use_ocr="always",
        table_mode="fast",
        device="mps",
        enable_formulas="true",
        enable_code_blocks="false",
    )

    assert settings.do_ocr is True
    assert settings.force_full_page_ocr is True
    assert settings.table_mode == "fast"
    assert settings.device == "mps"
    assert settings.do_formula_enrichment is True
    assert settings.do_code_enrichment is False


def test_pipeline_settings_reject_unsupported_values():
    pipeline = _load_pipeline_module()

    for field, kwargs in (
        ("use_ocr", {"use_ocr": "sometimes"}),
        ("table_mode", {"table_mode": "turbo"}),
        ("device", {"device": "quantum"}),
    ):
        try:
            pipeline.resolve_pipeline_settings(**kwargs)
        except ValueError as exc:
            assert field in str(exc)
        else:
            raise AssertionError(f"unsupported {field} value was accepted")


def test_chunk_defaults_honor_environment_and_reject_invalid_pairs():
    pipeline = _load_pipeline_module()

    assert pipeline.resolve_chunk_defaults(
        {
            "DOCLING_CHUNK_SIZE": "900",
            "DOCLING_CHUNK_OVERLAP": "120",
        }
    ) == (900, 120)

    for values in (
        {"DOCLING_CHUNK_SIZE": "0", "DOCLING_CHUNK_OVERLAP": "0"},
        {"DOCLING_CHUNK_SIZE": "100", "DOCLING_CHUNK_OVERLAP": "100"},
        {"DOCLING_CHUNK_SIZE": "invalid", "DOCLING_CHUNK_OVERLAP": "10"},
    ):
        try:
            pipeline.resolve_chunk_defaults(values)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid chunk defaults were accepted: {values}")


def test_chunk_settings_reject_overlap_that_consumes_the_chunk():
    pipeline = _load_pipeline_module()

    assert pipeline.validate_chunk_settings(512, 50) == (512, 50)
    for size, overlap in (
        (0, 0),
        (100, -1),
        (100, 51),
        (100, 100),
        (100, 101),
    ):
        try:
            pipeline.validate_chunk_settings(size, overlap)
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"invalid request chunk settings were accepted: {(size, overlap)}"
            )


def test_both_chunkers_bound_overlap_and_total_chunk_count():
    for relative, name in (
        ("services/docling/provider/shared/utils.py", "docling_shared_utils"),
        ("services/docling/provider/localhost/utils.py", "docling_local_utils"),
    ):
        utils = _load_module(ROOT / relative, name)
        chunks = utils.chunk_text("x" * 10_000, 512, 511)
        assert len(chunks) == 20

        utils.MAX_CHUNKS = 5
        try:
            utils.chunk_text("abcdef", 1, 0)
        except utils.ChunkLimitError:
            pass
        else:
            raise AssertionError(f"{relative} accepted more than MAX_CHUNKS")


def test_both_docling_apis_use_manifest_owned_chunk_defaults():
    for relative in (
        "services/docling/provider/shared/api_server.py",
        "services/docling/provider/localhost/server.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "resolve_chunk_defaults" in source
        assert "Form(default=_CHUNK_DEFAULTS.size" in source
        assert "Form(default=_CHUNK_DEFAULTS.overlap" in source
        assert "validate_chunk_settings(chunk_size, chunk_overlap)" in source
        assert "status_code=422" in source
        assert "except ChunkLimitError" in source
        assert "status_code=413" in source
        assert "chunk_size: int = Form(default=512)" not in source
        assert "chunk_overlap: int = Form(default=50)" not in source


def test_gpu_torch_requirements_match_base_image_patch():
    dockerfile = (ROOT / "services/docling/provider/gpu/Dockerfile").read_text()
    requirements = (ROOT / "services/docling/provider/gpu/requirements.txt").read_text()

    assert "pytorch/pytorch:2.13.0-" in dockerfile
    assert "torch==2.13.0" in requirements
    assert "torchvision==0.28.0" in requirements


def test_converter_readiness_is_nonblocking_and_truthful(monkeypatch):
    pipeline = _load_pipeline_module()
    settings = pipeline.resolve_pipeline_settings()

    async def run():
        monkeypatch.setattr(pipeline, "build_converter", lambda _settings: object())
        assert await pipeline.converter_status(settings) == "starting"
        await asyncio.sleep(0.01)
        assert await pipeline.converter_status(settings) == "healthy"

    asyncio.run(run())


def test_provider_health_uses_converter_status():
    for relative in (
        "services/docling/provider/gpu/processor.py",
        "services/docling/provider/localhost/processor.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "async def processor_status" in source
        assert "await converter_status(settings)" in source
        assert 'return "unavailable"' in source
    api_source = (
        ROOT / "services/docling/provider/shared/api_server.py"
    ).read_text(encoding="utf-8")
    assert 'models_loaded=["DocumentConverter"] if ready else []' in api_source
    assert 'models_loaded=["DocLayNet", "TableFormer"]' not in api_source


def test_both_docling_processors_separate_conversion_from_rendering():
    for relative in (
        "services/docling/provider/gpu/processor.py",
        "services/docling/provider/localhost/processor.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "def convert_document_once(" in source
        assert "def render_conversion(" in source
        assert "result = await asyncio.to_thread(" in source
        assert "convert_document_once," in source
        assert "return render_conversion(" in source


def test_both_docling_apis_install_boundary_and_deadline_expensive_routes():
    for relative in (
        "services/docling/provider/shared/api_server.py",
        "services/docling/provider/localhost/server.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "load_boundary_settings(" in source
        assert '"/v1/document/convert"' in source
        assert '"/internal/lightrag/bundle"' in source
        assert "install_provider_boundary(app," in source
        assert "run_with_deadline(" in source
        assert "fatal_timeout_response(" in source
        assert "CORSMiddleware" not in source


def test_docling_gpu_image_copies_bundle_and_boundary_modules():
    dockerfile = (ROOT / "services/docling/provider/gpu/Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "COPY provider_boundary.py /app/" in dockerfile
    assert "COPY lightrag_bundle.py /app/" in dockerfile


def test_converter_construction_is_single_flight_across_threads(monkeypatch):
    pipeline = _load_pipeline_module()
    settings = pipeline.resolve_pipeline_settings()
    calls = 0

    def construct(_settings):
        nonlocal calls
        calls += 1
        return object()

    monkeypatch.setattr(pipeline, "_construct_converter", construct)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(
            executor.map(pipeline.build_converter, [settings, settings])
        )

    assert first is second
    assert calls == 1
