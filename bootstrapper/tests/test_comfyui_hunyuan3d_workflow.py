"""Offline tests for the Hunyuan3D-2 native image→3D catalog entry + workflow (#338).

Covers the acceptance matrix that can be verified WITHOUT a GPU:
- the curated catalog entry is present, pinned (revision + sha256), staged to
  models/checkpoints, native (no custom node), non-default, and license-labelled;
- the committed example workflow is a valid ComfyUI API graph that uses ONLY
  ComfyUI-core native nodes (the whole point of #338 — MPS-runnable, no custom
  node / CUDA sparse kernels) and terminates in SaveGLB;
- a reusable GLB-container structure validator (exercised here against a synthetic
  GLB) that the marked live-MPS smoke reuses on the real ComfyUI output.

The live MPS smoke that actually renders a mesh is opt-in (the ``live`` marker +
ATLAS_COMFYUI_LIVE_ENDPOINT pointing at a reachable ComfyUI), so the default suite
needs no Apple-Silicon hardware and downloads no ~5 GB checkpoint.
"""

from __future__ import annotations

import json
import os
import re
import struct
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODELS_YAML = _REPO_ROOT / "services" / "comfyui" / "models.yaml"
_WORKFLOW = (
    _REPO_ROOT / "services" / "comfyui" / "workflows"
    / "hunyuan3d-2-image-to-glb-api.json"
)

_ENTRY_NAME = "hunyuan3d-2"
_CKPT_FILENAME = "hunyuan3d-dit-v2.safetensors"

# The exact ComfyUI-CORE nodes this native workflow is allowed to use. If the
# workflow ever referenced a node outside this set it would need a custom node,
# defeating #338's "rely on ComfyUI core's native Hunyuan3D support" mandate.
_NATIVE_CORE_NODES = frozenset(
    {
        "ImageOnlyCheckpointLoader",
        "LoadImage",
        "CLIPVisionEncode",
        "Hunyuan3Dv2Conditioning",
        "Hunyuan3Dv2ConditioningMultiView",
        "EmptyLatentHunyuan3Dv2",
        "KSampler",
        "VAEDecodeHunyuan3D",
        "VoxelToMeshBasic",
        "VoxelToMesh",
        "SaveGLB",
    }
)

# Nodes that MUST appear for a faithful single-image shape→GLB pipeline.
_REQUIRED_NODES = frozenset(
    {
        "ImageOnlyCheckpointLoader",
        "LoadImage",
        "CLIPVisionEncode",
        "Hunyuan3Dv2Conditioning",
        "EmptyLatentHunyuan3Dv2",
        "KSampler",
        "VAEDecodeHunyuan3D",
        "VoxelToMeshBasic",
        "SaveGLB",
    }
)


def _load_entry() -> dict:
    data = yaml.safe_load(_MODELS_YAML.read_text(encoding="utf-8"))
    entries = [m for m in data["models"] if m.get("name") == _ENTRY_NAME]
    assert len(entries) == 1, f"expected exactly one {_ENTRY_NAME!r} catalog entry"
    return entries[0]


def _load_workflow() -> dict:
    return json.loads(_WORKFLOW.read_text(encoding="utf-8"))


# ── catalog entry ────────────────────────────────────────────────────

def test_catalog_entry_present_and_shaped():
    e = _load_entry()
    assert e["category"] == "mesh_model"
    # dit checkpoint loads from models/checkpoints (ImageOnlyCheckpointLoader),
    # overriding the mesh_model default.
    assert e["target_dir"] == "checkpoints"
    assert e["filename"] == _CKPT_FILENAME


def test_catalog_entry_is_pinned():
    e = _load_entry()
    url = e["url"]
    # URL pins an immutable 40-hex commit revision (not a mutable branch).
    m = re.search(r"/resolve/([0-9a-f]{40})/", url)
    assert m, f"url must pin a 40-hex revision, got {url!r}"
    assert re.fullmatch(r"[0-9a-f]{64}", e["sha256"]), "sha256 must be 64 hex chars"
    assert e["size_bytes"] == 4928151562
    # license URL pins the same revision for immutability.
    assert m.group(1) in e["license_url"]


def test_catalog_entry_is_native_core():
    """No custom node — the whole point of #338 (pure-torch, MPS-runnable)."""
    e = _load_entry()
    assert e.get("requires_custom_node", []) == []


def test_catalog_entry_not_default_download():
    """Large optional bundle: must NOT be essential (never auto-downloaded)."""
    e = _load_entry()
    assert e.get("essential", False) is False


def test_catalog_entry_license_surfaced():
    e = _load_entry()
    assert "Tencent" in e["license_name"]
    assert e["license_url"].startswith("https://")
    restrictions = " ".join(e["license_restrictions"]).lower()
    # Territory restriction + the MAU threshold must be surfaced to the operator.
    assert "european union" in restrictions or "territory" in restrictions
    assert "monthly active users" in restrictions


def test_catalog_entry_notes_shape_only():
    e = _load_entry()
    notes = e["notes"].lower()
    assert "shape-only" in notes or "shape only" in notes
    assert "mps" in notes


# ── workflow structure ───────────────────────────────────────────────

def test_workflow_is_valid_api_graph():
    wf = _load_workflow()
    assert "prompt" in wf and isinstance(wf["prompt"], dict)
    nodes = wf["prompt"]
    assert nodes, "workflow has no nodes"
    for nid, node in nodes.items():
        assert "class_type" in node, f"node {nid} missing class_type"
        assert isinstance(node.get("inputs"), dict), f"node {nid} missing inputs"
    # every link [node_id, out_index] must reference an existing node
    for nid, node in nodes.items():
        for key, val in node["inputs"].items():
            if isinstance(val, list) and len(val) == 2 and isinstance(val[0], str):
                assert val[0] in nodes, (
                    f"node {nid}.{key} links to missing node {val[0]!r}"
                )
                assert isinstance(val[1], int), f"node {nid}.{key} bad output index"


def test_workflow_uses_only_native_core_nodes():
    nodes = _load_workflow()["prompt"]
    used = {n["class_type"] for n in nodes.values()}
    extraneous = used - _NATIVE_CORE_NODES
    assert not extraneous, (
        f"workflow uses non-core node(s) {sorted(extraneous)} — #338 requires "
        f"ComfyUI-core native support only (no custom nodes)"
    )


def test_workflow_has_required_nodes():
    nodes = _load_workflow()["prompt"]
    used = {n["class_type"] for n in nodes.values()}
    missing = _REQUIRED_NODES - used
    assert not missing, f"workflow missing required node(s) {sorted(missing)}"


def test_workflow_terminates_in_saveglb():
    nodes = _load_workflow()["prompt"]
    saveglb_ids = [nid for nid, n in nodes.items() if n["class_type"] == "SaveGLB"]
    assert len(saveglb_ids) == 1, "expected exactly one SaveGLB sink"
    sink = saveglb_ids[0]
    # nothing consumes SaveGLB's output (it is the terminal sink)
    for nid, node in nodes.items():
        for val in node["inputs"].values():
            if isinstance(val, list) and len(val) == 2:
                assert val[0] != sink, f"node {nid} consumes SaveGLB output"
    # SaveGLB.mesh is wired from a VoxelToMesh* node
    mesh_src = nodes[sink]["inputs"]["mesh"][0]
    assert nodes[mesh_src]["class_type"] in ("VoxelToMeshBasic", "VoxelToMesh")


def test_workflow_checkpoint_matches_catalog_filename():
    nodes = _load_workflow()["prompt"]
    loaders = [n for n in nodes.values() if n["class_type"] == "ImageOnlyCheckpointLoader"]
    assert len(loaders) == 1
    assert loaders[0]["inputs"]["ckpt_name"] == _CKPT_FILENAME, (
        "workflow ckpt_name must match the catalog filename so the staged "
        "checkpoint resolves"
    )


def test_workflow_checkpoint_outputs_wired_correctly():
    """ImageOnlyCheckpointLoader outputs MODEL(0)/CLIP_VISION(1)/VAE(2); verify
    each is consumed by the right node (guards against a swapped-index graph)."""
    nodes = _load_workflow()["prompt"]
    loader_id = next(
        nid for nid, n in nodes.items() if n["class_type"] == "ImageOnlyCheckpointLoader"
    )
    ksampler = next(n for n in nodes.values() if n["class_type"] == "KSampler")
    clipvis = next(n for n in nodes.values() if n["class_type"] == "CLIPVisionEncode")
    vaedec = next(n for n in nodes.values() if n["class_type"] == "VAEDecodeHunyuan3D")
    assert ksampler["inputs"]["model"] == [loader_id, 0]
    assert clipvis["inputs"]["clip_vision"] == [loader_id, 1]
    assert vaedec["inputs"]["vae"] == [loader_id, 2]
    # conditioning: positive(0)/negative(1) both from Hunyuan3Dv2Conditioning
    cond_id = next(
        nid for nid, n in nodes.items() if n["class_type"] == "Hunyuan3Dv2Conditioning"
    )
    assert ksampler["inputs"]["positive"] == [cond_id, 0]
    assert ksampler["inputs"]["negative"] == [cond_id, 1]


# ── GLB container structure validator (reused by the live smoke) ──────

def assert_valid_glb(data: bytes) -> dict:
    """Validate GLB (glTF binary) container structure and return the parsed glTF
    JSON. Raises AssertionError on any structural violation. This is the exact
    check the live MPS smoke runs on the ComfyUI-produced mesh."""
    assert len(data) >= 12, "GLB shorter than 12-byte header"
    magic, version, length = struct.unpack("<4sII", data[:12])
    assert magic == b"glTF", f"bad GLB magic {magic!r}"
    assert version == 2, f"unsupported GLB version {version}"
    assert length == len(data), f"GLB length {length} != actual {len(data)}"
    # first chunk must be the JSON chunk
    assert len(data) >= 20, "GLB missing chunk header"
    chunk_len, chunk_type = struct.unpack("<I4s", data[12:20])
    assert chunk_type == b"JSON", f"first chunk type {chunk_type!r} is not JSON"
    json_bytes = data[20:20 + chunk_len]
    gltf = json.loads(json_bytes.decode("utf-8"))
    assert gltf.get("asset", {}).get("version") == "2.0", "glTF asset.version != 2.0"
    return gltf


def _synthetic_glb() -> bytes:
    gltf = {"asset": {"version": "2.0"}, "scenes": [{"nodes": []}], "scene": 0}
    body = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    body += b" " * ((4 - len(body) % 4) % 4)  # 4-byte align (JSON pads with spaces)
    chunk = struct.pack("<I4s", len(body), b"JSON") + body
    header = struct.pack("<4sII", b"glTF", 2, 12 + len(chunk))
    return header + chunk


def test_glb_validator_accepts_valid_container():
    gltf = assert_valid_glb(_synthetic_glb())
    assert gltf["asset"]["version"] == "2.0"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b[4:],                              # missing magic
        lambda b: b"gltf" + b[4:],                    # wrong magic
        lambda b: b[:4] + struct.pack("<I", 1) + b[8:],  # wrong version
        lambda b: b[:8] + struct.pack("<I", 999) + b[12:],  # wrong length
        lambda b: b[:16] + b"BIN\x00" + b[20:],       # first chunk not JSON
    ],
)
def test_glb_validator_rejects_corruption(mutate):
    with pytest.raises((AssertionError, Exception)):
        assert_valid_glb(mutate(_synthetic_glb()))


# ── three-surface documentation (test-enforced, mirrors Krea) ─────────

def test_hunyuan3d_is_documented_on_all_three_surfaces():
    repo = (_REPO_ROOT / "services/comfyui/README.md").read_text(encoding="utf-8")
    site = (_REPO_ROOT / "docs/site/services/comfyui.md").read_text(encoding="utf-8")
    wiki = (_REPO_ROOT / "docs/wiki/Services.md").read_text(encoding="utf-8")
    for surface in (repo, site, wiki):
        assert "Hunyuan3D-2" in surface
        assert "hunyuan3d-2-image-to-glb-api.json" in surface
        assert "shape-only" in surface.lower()
        assert "Tencent Hunyuan Community License" in surface
        assert "monthly active users" in surface.lower()


# ── optional live MPS smoke (opt-in; renders a real mesh) ─────────────

@pytest.mark.live
def test_hunyuan3d_live_mps_smoke_produces_valid_glb():  # pragma: no cover - hardware-gated
    """Submit the committed workflow to a running ComfyUI, poll for completion,
    download the produced GLB, and validate its container structure.

    Opt-in (needs ATLAS_COMFYUI_LIVE_ENDPOINT + the hunyuan3d-2 model staged on a
    managed-MPS host); never part of generic CI. Proves the #335 managed-MPS path
    actually emits a shape-only GLB — official docs alone do not prove MPS support."""
    import time

    import requests

    endpoint = os.environ.get("ATLAS_COMFYUI_LIVE_ENDPOINT")
    if not endpoint:
        pytest.skip("set ATLAS_COMFYUI_LIVE_ENDPOINT to run the hardware live smoke")
    endpoint = endpoint.rstrip("/")

    # The native Hunyuan3D nodes must exist on the target ComfyUI (proves the pin
    # supports native Hunyuan3D-2).
    info = requests.get(f"{endpoint}/object_info/SaveGLB", timeout=30).json()
    assert "SaveGLB" in info

    request = _load_workflow()
    response = requests.post(f"{endpoint}/prompt", json={"prompt": request["prompt"]}, timeout=30)
    response.raise_for_status()
    prompt_id = response.json()["prompt_id"]

    deadline = time.monotonic() + int(os.environ.get("ATLAS_COMFYUI_LIVE_TIMEOUT", "1800"))
    history = None
    while time.monotonic() < deadline:
        payload = requests.get(f"{endpoint}/history/{prompt_id}", timeout=30).json()
        history = payload.get(prompt_id)
        if history:
            break
        time.sleep(5)
    assert history is not None, f"Hunyuan3D generation did not finish: {prompt_id}"

    outputs = history["outputs"]
    glb_ref = next(
        (f for node in outputs.values() for f in (node.get("gltf") or node.get("3d") or [])),
        None,
    )
    assert glb_ref, f"no GLB in outputs: {outputs}"
    data = requests.get(f"{endpoint}/view", params=glb_ref, timeout=60).content
    assert_valid_glb(data)
