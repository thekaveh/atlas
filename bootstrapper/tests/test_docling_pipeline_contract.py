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


def test_both_docling_apis_use_manifest_owned_chunk_defaults():
    for relative in (
        "services/docling/provider/shared/api_server.py",
        "services/docling/provider/localhost/server.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "resolve_chunk_defaults" in source
        assert "Form(default=_CHUNK_DEFAULTS.size" in source
        assert "Form(default=_CHUNK_DEFAULTS.overlap" in source
        assert "chunk_size: int = Form(default=512)" not in source
        assert "chunk_overlap: int = Form(default=50)" not in source


def test_gpu_torch_requirements_match_base_image_patch():
    dockerfile = (ROOT / "services/docling/provider/gpu/Dockerfile").read_text()
    requirements = (ROOT / "services/docling/provider/gpu/requirements.txt").read_text()

    assert "pytorch/pytorch:2.12.1-" in dockerfile
    assert "torch==2.12.1" in requirements
    assert "torchvision==0.27.1" in requirements


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
