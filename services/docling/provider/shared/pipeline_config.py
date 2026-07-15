"""Translate Atlas Docling controls into the pinned Docling pipeline API."""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Mapping, NamedTuple


_OCR_MODES = {"auto", "always", "never"}
_TABLE_MODES = {"accurate", "fast"}
_DEVICES = {"auto", "cpu", "cuda", "mps", "xpu"}


class PipelineSettings(NamedTuple):
    do_ocr: bool
    force_full_page_ocr: bool
    table_mode: str
    device: str
    do_formula_enrichment: bool
    do_code_enrichment: bool


class ChunkDefaults(NamedTuple):
    size: int
    overlap: int


def validate_chunk_settings(size: int, overlap: int) -> ChunkDefaults:
    if size <= 0:
        raise ValueError("Docling chunk size must be positive")
    if overlap < 0 or overlap * 2 > size:
        raise ValueError(
            "Docling chunk overlap must be non-negative and no more than half "
            "the chunk size"
        )
    return ChunkDefaults(size=size, overlap=overlap)


def resolve_chunk_defaults(env: Mapping[str, str] | None = None) -> ChunkDefaults:
    values = os.environ if env is None else env
    try:
        size = int(values.get("DOCLING_CHUNK_SIZE", "512"))
        overlap = int(values.get("DOCLING_CHUNK_OVERLAP", "50"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Docling chunk defaults must be integers") from exc
    return validate_chunk_settings(size, overlap)


def _boolean(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError("boolean controls must be true or false")
    return normalized == "true"


def resolve_pipeline_settings(
    *,
    use_ocr: str = "auto",
    table_mode: str = "accurate",
    device: str = "auto",
    enable_formulas: str | bool = "true",
    enable_code_blocks: str | bool = "true",
) -> PipelineSettings:
    use_ocr = use_ocr.strip().lower()
    table_mode = table_mode.strip().lower()
    device = device.strip().lower()
    if use_ocr not in _OCR_MODES:
        raise ValueError(f"use_ocr must be one of: {', '.join(sorted(_OCR_MODES))}")
    if table_mode not in _TABLE_MODES:
        raise ValueError(f"table_mode must be one of: {', '.join(sorted(_TABLE_MODES))}")
    if device not in _DEVICES:
        raise ValueError(f"device must be one of: {', '.join(sorted(_DEVICES))}")
    return PipelineSettings(
        do_ocr=use_ocr != "never",
        force_full_page_ocr=use_ocr == "always",
        table_mode=table_mode,
        device=device,
        do_formula_enrichment=_boolean(enable_formulas),
        do_code_enrichment=_boolean(enable_code_blocks),
    )


def build_converter(settings: PipelineSettings):
    with _converter_lock:
        cached = _converters.get(settings)
        if cached is not None:
            return cached
        converter = _construct_converter(settings)
        _converters[settings] = converter
        return converter


def _construct_converter(settings: PipelineSettings):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        AcceleratorDevice,
        AcceleratorOptions,
        PdfPipelineOptions,
        TableFormerMode,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = settings.do_ocr
    options.ocr_options.force_full_page_ocr = settings.force_full_page_ocr
    options.do_table_structure = True
    options.table_structure_options.mode = TableFormerMode(settings.table_mode)
    options.accelerator_options = AcceleratorOptions(
        device=AcceleratorDevice(settings.device)
    )
    options.do_formula_enrichment = settings.do_formula_enrichment
    options.do_code_enrichment = settings.do_code_enrichment
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
        }
    )


_converter_lock = threading.Lock()
_converters: dict[PipelineSettings, object] = {}


_readiness_tasks: dict[PipelineSettings, asyncio.Task] = {}


async def converter_status(settings: PipelineSettings) -> str:
    """Start converter initialization once and report without blocking health."""
    task = _readiness_tasks.get(settings)
    if task is None:
        task = asyncio.create_task(asyncio.to_thread(build_converter, settings))
        _readiness_tasks[settings] = task
    if not task.done():
        return "starting"
    try:
        await task
    except Exception:
        if _readiness_tasks.get(settings) is task:
            _readiness_tasks.pop(settings, None)
        return "unavailable"
    return "healthy"
