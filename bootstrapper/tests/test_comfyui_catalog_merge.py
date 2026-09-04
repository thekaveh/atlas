"""Tests for assemble_wizard_catalog() — merge, partial-failure, fallback.

The wizard calls this once per invocation. There is no host-side file
cache; the bootstrapper writes the active set to volumes/comfyui/ via
comfyui_manifest_generator. The previous cache-related tests were retired
when the DB flow was removed.
"""
from __future__ import annotations

from unittest.mock import patch

from utils.comfyui_library import (
    assemble_wizard_catalog,
    ComfyUILibraryEntry,
)


def _fake_entry(name: str, source: str) -> ComfyUILibraryEntry:
    return ComfyUILibraryEntry(
        name=name, family="X", category="checkpoint", size_gb=1.0,
        url=f"https://e.com/{name}.safetensors", sha256=None,
        target_dir="checkpoints", min_vram_gb=None, cpu_supported=True,
        requires_custom_node=(), popularity=0, source=source, pulled=False,
    )


def test_assemble_merges_hf_civitai_curated():
    with patch("utils.comfyui_library.list_huggingface_models") as mock_hf, \
         patch("utils.comfyui_library.list_civitai_loras") as mock_civ:
        mock_hf.return_value = [_fake_entry("hf-a", "huggingface")]
        mock_civ.return_value = [_fake_entry("civ-b", "civitai")]
        entries = assemble_wizard_catalog()

    names = {e.name for e in entries}
    assert "hf-a" in names
    assert "civ-b" in names
    # curated entries always merged in.
    assert any(e.source == "curated" for e in entries)


def test_partial_failure_civitai_down(capsys):
    with patch("utils.comfyui_library.list_huggingface_models") as mock_hf, \
         patch("utils.comfyui_library.list_civitai_loras",
               side_effect=ConnectionError("civitai 503")):
        mock_hf.return_value = [_fake_entry("hf-x", "huggingface")]
        entries = assemble_wizard_catalog()

    sources = {e.source for e in entries}
    assert "huggingface" in sources
    assert "curated" in sources  # curated always in
    # No fallback when at least one scraper succeeded.
    assert "fallback" not in sources
    captured = capsys.readouterr()
    assert "civitai" in captured.err.lower()


def test_partial_failure_civitai_invalid_payload_keeps_healthy_catalog(capsys):
    with patch("utils.comfyui_library.list_huggingface_models") as mock_hf, \
         patch(
             "utils.comfyui_library.list_civitai_loras",
             side_effect=ValueError("malformed Civitai SHA256"),
         ):
        mock_hf.return_value = [_fake_entry("hf-healthy", "huggingface")]
        entries = assemble_wizard_catalog()

    names = {entry.name for entry in entries}
    assert "hf-healthy" in names
    assert any(entry.source == "curated" for entry in entries)
    assert "civitai" in capsys.readouterr().err.lower()


def test_partial_failure_huggingface_down(capsys):
    """HuggingFace 503 → civitai entries still surface + curated still in."""
    with patch("utils.comfyui_library.list_huggingface_models",
               side_effect=ConnectionError("hf 503")), \
         patch("utils.comfyui_library.list_civitai_loras") as mock_civ:
        mock_civ.return_value = [_fake_entry("civ-only", "civitai")]
        entries = assemble_wizard_catalog()

    sources = {e.source for e in entries}
    assert "civitai" in sources
    assert "curated" in sources  # curated always in
    # No fallback when at least one scraper succeeded.
    assert "fallback" not in sources
    captured = capsys.readouterr()
    assert "huggingface" in captured.err.lower()


def test_full_failure_loads_fallback():
    """Both scrapers down → fall back to the bundled JSON snapshot."""
    with patch("utils.comfyui_library.list_huggingface_models",
               side_effect=ConnectionError("hf 503")), \
         patch("utils.comfyui_library.list_civitai_loras",
               side_effect=ConnectionError("civ 503")):
        entries = assemble_wizard_catalog()
    assert any(e.source == "fallback" for e in entries)


def test_dedupe_curated_wins_over_huggingface():
    """Name collision: curated must win — its metadata is hand-vetted."""
    with patch("utils.comfyui_library.list_huggingface_models") as mock_hf, \
         patch("utils.comfyui_library.list_civitai_loras") as mock_civ, \
         patch("utils.comfyui_library.list_curated") as mock_cur:
        mock_hf.return_value = [_fake_entry("clash", "huggingface")]
        mock_civ.return_value = []
        mock_cur.return_value = [_fake_entry("clash", "curated")]
        entries = assemble_wizard_catalog()
    clash = [e for e in entries if e.name == "clash"]
    assert len(clash) == 1
    assert clash[0].source == "curated"


def test_force_refresh_param_is_no_op_and_accepted():
    """assemble_wizard_catalog accepts force_refresh for API parity but it
    has no observable effect (no cache to bypass)."""
    with patch("utils.comfyui_library.list_huggingface_models") as mock_hf, \
         patch("utils.comfyui_library.list_civitai_loras") as mock_civ:
        mock_hf.return_value = []
        mock_civ.return_value = []
        # Both calls should produce identical results.
        a = assemble_wizard_catalog()
        b = assemble_wizard_catalog(force_refresh=True)
    assert {e.name for e in a} == {e.name for e in b}
    # And both should still hit the scrapers (no cache).
    assert mock_hf.call_count == 2
    assert mock_civ.call_count == 2
