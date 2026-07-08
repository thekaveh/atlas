"""Tests for ComfyUILibraryEntry dataclass + category enum.

Pins the schema that the wizard, sidecar loader, comfyui_resolver,
and comfyui_manifest_generator all consume. Changes here ripple to
each of them.
"""
from __future__ import annotations

import pytest

from utils.comfyui_library import (
    ComfyUILibraryEntry,
    CATEGORY_TARGET_DIR,
    CATEGORY_DISPLAY_GROUPS,
    VALID_CATEGORIES,
)


def test_entry_minimal_construction():
    entry = ComfyUILibraryEntry(
        name="sdxl-base-1.0",
        family="SDXL",
        category="checkpoint",
        size_gb=6.94,
        url="https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors",
        sha256=None,
        target_dir="checkpoints",
        min_vram_gb=8.0,
        cpu_supported=False,
        requires_custom_node=(),
        popularity=95,
        source="huggingface",
        pulled=False,
    )
    assert entry.name == "sdxl-base-1.0"
    assert entry.cloud_only is False  # default
    assert entry.requires_custom_node == ()


def test_entry_is_frozen():
    entry = _example_entry()
    with pytest.raises(AttributeError):
        entry.name = "mutated"  # type: ignore[misc]


def test_category_target_dir_covers_all_17_categories():
    # 17 categories total: checkpoint, vae, lora, controlnet, ipadapter,
    # instantid, upscaler, embedding, clip, animatediff, motion_lora,
    # video_model, voice_model, audio_model, mesh_model,
    # diffusion_models (FLUX/DiT/Krea 2/SD3), text_encoders (t5xxl, Qwen3-VL).
    assert set(CATEGORY_TARGET_DIR.keys()) == {
        "checkpoint", "vae", "lora", "controlnet",
        "ipadapter", "instantid", "upscaler", "embedding",
        "clip", "animatediff", "motion_lora", "video_model",
        "voice_model", "audio_model", "mesh_model",
        "diffusion_models", "text_encoders",
    }


def test_category_target_dir_values():
    assert CATEGORY_TARGET_DIR["checkpoint"] == "checkpoints"
    assert CATEGORY_TARGET_DIR["mesh_model"] == "mesh_models"
    assert CATEGORY_TARGET_DIR["video_model"] == "checkpoints"  # upstream convention
    assert CATEGORY_TARGET_DIR["diffusion_models"] == "diffusion_models"  # FLUX/DiT
    assert CATEGORY_TARGET_DIR["text_encoders"] == "text_encoders"  # t5xxl, Qwen3-VL


def test_display_groups_partition_categories():
    """Every category lives in exactly one display group; All is a virtual group."""
    grouped = set()
    for group, cats in CATEGORY_DISPLAY_GROUPS.items():
        if group == "All":
            continue
        for c in cats:
            assert c not in grouped, f"{c} in multiple groups"
            grouped.add(c)
    assert grouped == VALID_CATEGORIES


def test_valid_categories_matches_target_dir_keys():
    assert VALID_CATEGORIES == frozenset(CATEGORY_TARGET_DIR.keys())


def _example_entry() -> ComfyUILibraryEntry:
    return ComfyUILibraryEntry(
        name="x", family="X", category="checkpoint", size_gb=1.0,
        url="https://e.com/x", sha256=None, target_dir="checkpoints",
        min_vram_gb=None, cpu_supported=True, requires_custom_node=(),
        popularity=0, source="huggingface", pulled=False,
    )
