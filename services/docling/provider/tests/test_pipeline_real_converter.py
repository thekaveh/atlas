"""Shared Docling factory against the real pinned converter (#1765).

The bootstrapper suite checks the factory with stub modules. These tests
import the actual Docling release from a provider lock, so they skip where
Docling is not installed. Run them in a Linux container with the localhost
lock, for example:

    uv sync --locked --no-install-project   # in services/docling/provider/localhost
    python -m pytest services/docling/provider/tests/test_pipeline_real_converter.py
"""

from __future__ import annotations

import importlib.util
from itertools import product
from pathlib import Path

import pytest

pytest.importorskip("docling.document_converter")

from docling.backend.image_backend import ImageDocumentBackend  # noqa: E402
from docling.datamodel.base_models import InputFormat  # noqa: E402

PROVIDER = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "atlas_pipeline_config", PROVIDER / "shared" / "pipeline_config.py"
)
pipeline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pipeline)

_GRID = list(product(
    ("auto", "always", "never"), ("accurate", "fast"),
    ("auto", "cpu", "cuda", "mps", "xpu"), ("true", "false"), ("true", "false"),
))


def _options(converter, input_format):
    return converter.format_to_options[input_format].pipeline_options


def _value(field):
    """Docling's defaults hold plain strings; Atlas sets enums."""
    return getattr(field, "value", field)


@pytest.mark.parametrize("ocr,table,device,formulas,code", _GRID)
def test_image_and_pdf_carry_every_selected_control(ocr, table, device, formulas, code):
    settings = pipeline.resolve_pipeline_settings(
        use_ocr=ocr, table_mode=table, device=device,
        enable_formulas=formulas, enable_code_blocks=code,
    )
    converter = pipeline._construct_converter(settings)

    for input_format in (InputFormat.PDF, InputFormat.IMAGE):
        options = _options(converter, input_format)
        assert options.do_ocr is (ocr != "never"), input_format
        assert options.ocr_options.force_full_page_ocr is (ocr == "always"), input_format
        assert _value(options.table_structure_options.mode) == table, input_format
        assert _value(options.accelerator_options.device) == device, input_format
        assert options.do_formula_enrichment is (formulas == "true"), input_format
        assert options.do_code_enrichment is (code == "true"), input_format
    # The image keeps Docling's image backend; only the options are Atlas's.
    assert issubclass(converter.format_to_options[InputFormat.IMAGE].backend, ImageDocumentBackend)


def test_json_input_keeps_the_guarded_backend():
    settings = pipeline.resolve_pipeline_settings()
    backend = pipeline._construct_converter(settings).format_to_options[InputFormat.JSON_DOCLING].backend
    assert backend.__name__ == "GuardedJSONBackend"


def test_cached_converters_do_not_share_options_across_settings():
    never = pipeline.resolve_pipeline_settings(use_ocr="never", table_mode="fast", device="cpu")
    always = pipeline.resolve_pipeline_settings(use_ocr="always", table_mode="accurate", device="mps")
    first, second = pipeline.build_converter(never), pipeline.build_converter(always)
    assert first is not second
    assert pipeline.build_converter(never) is first
    assert _options(first, InputFormat.IMAGE).do_ocr is False
    assert _options(second, InputFormat.IMAGE).ocr_options.force_full_page_ocr is True
    assert _options(first, InputFormat.IMAGE) is not _options(second, InputFormat.IMAGE)


@pytest.mark.parametrize("compression", ["raw", "tiff_lzw", "tiff_adobe_deflate", "packbits"])
def test_ordinary_tiff_converts_on_the_pinned_pillow(tmp_path, compression):
    """Ordinary TIFFs decode and convert on the fixed Pillow (#1780).

    Conversion loads Docling's layout and table models, so this runs only
    with ATLAS_TEST_DOCLING_CONVERT=1."""
    import os

    if os.environ.get("ATLAS_TEST_DOCLING_CONVERT") != "1":
        pytest.skip("set ATLAS_TEST_DOCLING_CONVERT=1 to run a real conversion")
    from docling.datamodel.base_models import ConversionStatus
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(image).text((20, 80), "Atlas TIFF probe", fill="black")
    path = tmp_path / f"probe-{compression}.tiff"
    image.save(path, compression=compression, dpi=(72, 72))
    settings = pipeline.resolve_pipeline_settings(use_ocr="never", table_mode="fast", device="cpu")

    result = pipeline.build_converter(settings).convert(path)

    assert result.status == ConversionStatus.SUCCESS
    pages = list(result.document.pages.values())
    assert len(pages) == 1
    assert (round(pages[0].size.width), round(pages[0].size.height)) == (400, 200)
