"""Acceptance coverage for the curated Krea 2 ComfyUI bundles (#337)."""

from __future__ import annotations

import json
import os
import struct
import time
from dataclasses import replace
from pathlib import Path

import pytest
import requests
import yaml

from utils.comfyui_library import list_curated
from utils.comfyui_manifest_generator import ComfyUIManifestGenerator
from utils.comfyui_resolver import manifest_dict
from wizard.comfyui_steps import _merged_comfyui_options
from tests.three_surface_test_utils import surface_text


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "services" / "comfyui" / "workflows" / "krea2-turbo-api.json"
KREA_REVISION = "8038ce89b91b042141541ad0fa51b985ca262c5f"
LICENSE_REVISION = "1161245028ef398cd0a951101b2bbf486464f841"

EXPECTED_FILES = {
    "krea2-turbo-bf16": {
        "diffusion": (
            "diffusion_models/krea2_turbo_bf16.safetensors",
            "krea2_turbo_bf16.safetensors",
            26_283_332_608,
            "78bbf8f4165eda19cea3cb06c78089221932a39e2eed8af9da741f942c47ffb3",
            "diffusion_models",
        ),
        "text_encoder": (
            "text_encoders/qwen3vl_4b_bf16.safetensors",
            "qwen3vl_4b_bf16.safetensors",
            8_875_719_384,
            "36f3ff447ef59201722e8f9ce6020c9819fdcfba6aa2608c4e09b1c0ce114e34",
            "text_encoders",
        ),
        "vae": (
            "vae/qwen_image_vae.safetensors",
            "qwen_image_vae.safetensors",
            253_806_246,
            "a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f",
            "vae",
        ),
    },
    "krea2-raw-bf16": {
        "diffusion": (
            "diffusion_models/krea2_raw_bf16.safetensors",
            "krea2_raw_bf16.safetensors",
            26_283_332_608,
            "f99bb0ff8e362b77342bc4994e0c50906fe7ef7074864b181b7d48d2fa6d03d7",
            "diffusion_models",
        ),
        "text_encoder": (
            "text_encoders/qwen3vl_4b_bf16.safetensors",
            "qwen3vl_4b_bf16.safetensors",
            8_875_719_384,
            "36f3ff447ef59201722e8f9ce6020c9819fdcfba6aa2608c4e09b1c0ce114e34",
            "text_encoders",
        ),
        "vae": (
            "vae/qwen_image_vae.safetensors",
            "qwen_image_vae.safetensors",
            253_806_246,
            "a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f",
            "vae",
        ),
    },
}


def _krea_entries():
    by_name = {entry.name: entry for entry in list_curated()}
    return [by_name[name] for name in EXPECTED_FILES]


def test_comfyui_core_pin_supports_krea2() -> None:
    service = yaml.safe_load((ROOT / "services/comfyui/service.yml").read_text())
    env = {item["name"]: item for item in service["env"]}
    assert env["COMFYUI_REF"]["default"] == "v0.27.0"
    assert env["COMFYUI_MEMORY_LIMIT"]["default"] == "40g"

    compose = (ROOT / "services/comfyui/compose.yml").read_text(encoding="utf-8")
    assert "memory: ${COMFYUI_MEMORY_LIMIT:-40g}" in compose


@pytest.mark.parametrize("entry_name", EXPECTED_FILES)
def test_krea2_bundle_artifacts_are_exactly_pinned(entry_name: str) -> None:
    entry = {item.name: item for item in _krea_entries()}[entry_name]
    assert entry.precision == "bf16"
    assert entry.requires_custom_node == ()
    assert entry.min_ram_gb == 32
    assert entry.min_vram_gb == 32
    assert entry.license_name == "Krea 2 Community License"
    assert entry.license_url == (
        "https://huggingface.co/krea/Krea-2-Turbo/blob/"
        f"{LICENSE_REVISION}/LICENSE.pdf"
    )
    restrictions = " ".join(entry.license_restrictions)
    assert "$1,000,000" in restrictions
    assert "content filtering" in restrictions.lower()
    assert "seat" not in restrictions.lower()

    files = {item.role: item for item in entry.files}
    assert set(files) == set(EXPECTED_FILES[entry_name])
    for role, (source_path, filename, size_bytes, sha256, target_dir) in EXPECTED_FILES[entry_name].items():
        artifact = files[role]
        assert artifact.url == (
            f"https://huggingface.co/Comfy-Org/Krea-2/resolve/{KREA_REVISION}/{source_path}"
        )
        assert artifact.filename == filename
        assert artifact.size_bytes == size_bytes
        assert artifact.sha256 == sha256
        assert artifact.target_dir == target_dir


def test_krea2_wizard_rows_surface_precision_memory_and_license() -> None:
    options = _merged_comfyui_options(
        catalog=_krea_entries(),
        sidecar=[],
        pulled_names=set(),
        default_selected=set(),
        gpu_mem_gb=16,
    )
    assert {option.value for option in options} == set(EXPECTED_FILES)
    for option in options:
        rendered = f"{option.hint} {' '.join(option.badges)}"
        assert "bf16" in rendered.lower()
        assert "32 GB RAM" in rendered
        assert "32 GB VRAM" in rendered
        assert "Krea 2 Community License" in rendered
        assert "$1M" in rendered
        assert "content filtering" in rendered.lower()


def test_krea2_wizard_requires_every_bundle_file_for_pulled_badge() -> None:
    partial = _merged_comfyui_options(
        catalog=_krea_entries(),
        sidecar=[],
        pulled_names={"krea2_turbo_bf16.safetensors"},
        default_selected=set(),
        gpu_mem_gb=32,
    )
    assert "[pulled]" not in next(
        option for option in partial if option.value == "krea2-turbo-bf16"
    ).hint

    complete = _merged_comfyui_options(
        catalog=_krea_entries(),
        sidecar=[],
        pulled_names={
            "krea2_turbo_bf16.safetensors",
            "qwen3vl_4b_bf16.safetensors",
            "qwen_image_vae.safetensors",
        },
        default_selected=set(),
        gpu_mem_gb=32,
    )
    by_value = {option.value: option for option in complete}
    assert "[pulled]" in by_value["krea2-turbo-bf16"].hint
    assert "[pulled]" not in by_value["krea2-raw-bf16"].hint


def test_krea2_manifest_expands_bundles_and_tsv_deduplicates_shared_files(tmp_path: Path) -> None:
    entries = _krea_entries()
    manifest = manifest_dict(entries)
    assert len(manifest["models"]) == 6
    assert {row["bundle_id"] for row in manifest["models"]} == set(EXPECTED_FILES)
    assert all(row["file_size_bytes"] for row in manifest["models"])
    assert all(row["license_name"] == "Krea 2 Community License" for row in manifest["models"])

    tsv_path = tmp_path / "active-models.tsv"
    ComfyUIManifestGenerator({})._write_tsv(entries, tsv_path)
    rows = [line.split("\t") for line in tsv_path.read_text().splitlines()]
    assert len(rows) == 4
    assert {row[2] for row in rows} == {
        "krea2_turbo_bf16.safetensors",
        "krea2_raw_bf16.safetensors",
        "qwen3vl_4b_bf16.safetensors",
        "qwen_image_vae.safetensors",
    }


def test_shared_target_rejects_conflicting_exact_size(tmp_path: Path) -> None:
    turbo, raw = _krea_entries()
    conflicting_files = tuple(
        replace(item, size_bytes=item.size_bytes + 1)
        if item.role == "text_encoder"
        else item
        for item in raw.files
    )
    conflicting_raw = replace(raw, files=conflicting_files)

    with pytest.raises(ValueError, match="file_size_bytes differs"):
        ComfyUIManifestGenerator({})._write_tsv(
            [turbo, conflicting_raw],
            tmp_path / "active-models.tsv",
        )


def test_krea2_workflow_uses_only_core_nodes_and_catalog_models() -> None:
    request = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    prompt = request["prompt"]
    assert {node["class_type"] for node in prompt.values()} == {
        "UNETLoader",
        "CLIPLoader",
        "VAELoader",
        "CLIPTextEncode",
        "ConditioningZeroOut",
        "EmptyLatentImage",
        "KSampler",
        "VAEDecode",
        "SaveImage",
    }
    sampler = next(node for node in prompt.values() if node["class_type"] == "KSampler")
    assert sampler["inputs"]["steps"] == 8
    assert sampler["inputs"]["cfg"] == 1.0
    assert sampler["inputs"]["sampler_name"] == "euler"
    assert sampler["inputs"]["scheduler"] == "simple"
    latent = next(node for node in prompt.values() if node["class_type"] == "EmptyLatentImage")
    assert (latent["inputs"]["width"], latent["inputs"]["height"]) == (1024, 1024)
    clip = next(node for node in prompt.values() if node["class_type"] == "CLIPLoader")
    assert clip["inputs"]["type"] == "krea2"

    filenames = {file.filename for entry in _krea_entries() for file in entry.files}
    assert next(node for node in prompt.values() if node["class_type"] == "UNETLoader")["inputs"]["unet_name"] in filenames
    assert clip["inputs"]["clip_name"] in filenames
    assert next(node for node in prompt.values() if node["class_type"] == "VAELoader")["inputs"]["vae_name"] in filenames


def test_krea2_is_documented_on_all_three_surfaces() -> None:
    repo = (ROOT / "services/comfyui/README.md").read_text(encoding="utf-8")
    site = surface_text("services/comfyui/README.md", "site")
    wiki = surface_text("services/comfyui/README.md", "wiki")
    for surface in (repo, site, wiki):
        assert "Krea 2 Turbo" in surface
        assert "Krea 2 RAW" in surface
        assert "$1,000,000" in surface
        assert "content filtering" in surface.lower()
        assert "krea2-turbo-api.json" in surface


@pytest.mark.live
def test_krea2_live_smoke_renders_1024_square_png() -> None:
    endpoint = os.environ.get("ATLAS_COMFYUI_LIVE_ENDPOINT")
    if not endpoint:
        pytest.skip("set ATLAS_COMFYUI_LIVE_ENDPOINT to run the paid/hardware live smoke")

    endpoint = endpoint.rstrip("/")
    clip_info = requests.get(f"{endpoint}/object_info/CLIPLoader", timeout=30).json()
    clip_types = clip_info["CLIPLoader"]["input"]["required"]["type"][0]
    assert "krea2" in clip_types

    request = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    response = requests.post(f"{endpoint}/prompt", json=request, timeout=30)
    response.raise_for_status()
    prompt_id = response.json()["prompt_id"]

    deadline = time.monotonic() + int(os.environ.get("ATLAS_COMFYUI_LIVE_TIMEOUT", "1800"))
    history = None
    while time.monotonic() < deadline:
        payload = requests.get(f"{endpoint}/history/{prompt_id}", timeout=30).json()
        history = payload.get(prompt_id)
        if history:
            break
        time.sleep(2)
    assert history is not None, f"Krea 2 generation did not finish before timeout: {prompt_id}"

    output = history["outputs"]["9"]["images"][0]
    image = requests.get(f"{endpoint}/view", params=output, timeout=60).content
    assert image[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", image[16:24])
    assert (width, height) == (1024, 1024)
