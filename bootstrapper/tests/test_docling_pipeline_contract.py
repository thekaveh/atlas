from __future__ import annotations

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


def test_gpu_torch_requirements_match_base_image_patch():
    dockerfile = (ROOT / "services/docling/provider/gpu/Dockerfile").read_text()
    requirements = (ROOT / "services/docling/provider/gpu/requirements.txt").read_text()

    assert "pytorch/pytorch:2.12.1-" in dockerfile
    assert "torch==2.12.1" in requirements
    assert "torchvision==0.27.1" in requirements
