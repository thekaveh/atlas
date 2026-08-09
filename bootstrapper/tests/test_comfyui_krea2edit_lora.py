"""The comfyui-krea2edit node pack's LoRA is declared, not just documented.

The pack exists to run `krea2_identity_edit_v1_2.safetensors` — its own
README lists that LoRA as a hard requirement. Without it a consumer gets
the nodes registered and no edit behaviour, while `provision-nodes`
reports success and `doctor` reports the node present: installed-but-inert,
a worse failure shape than a clean error because every signal Atlas emits
says it is installed (#909).

The catalog schema can express the linkage (`requires_custom_node`), so it
is declared rather than left to prose that nothing enforces.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

from utils.comfyui_library import list_curated  # noqa: E402


NODE = "comfyui-krea2edit"
LORA = "krea2-identity-edit-v1-2"


def _entry(name: str):
    return next((e for e in list_curated() if e.name == name), None)


def test_the_identity_edit_lora_is_in_the_curated_catalog() -> None:
    entry = _entry(LORA)
    assert entry is not None, f"{LORA} missing from the curated catalog"
    assert entry.category == "lora"
    assert entry.filename == "krea2_identity_edit_v1_2.safetensors"


def test_the_lora_declares_the_node_that_requires_it() -> None:
    """The linkage is the whole point — prose alone cannot be enforced."""
    entry = _entry(LORA)
    assert NODE in list(entry.requires_custom_node), entry.requires_custom_node


def test_the_lora_lands_in_the_loras_directory() -> None:
    """category `lora` must resolve to ComfyUI's models/loras, or the node
    registers and still cannot find its weights."""
    assert _entry(LORA).target_dir == "loras"


def test_the_lora_is_pinned_by_revision_and_digest() -> None:
    """A floating `main` URL would silently change what ships, and the
    sha256 is what the init script verifies on download."""
    entry = _entry(LORA)
    assert "/resolve/main/" not in entry.url, "pin the revision, not main"
    assert "/resolve/89e9e7a09ee2e5c9331e952063d79b1b8a703280/" in entry.url
    assert entry.sha256 == (
        "6adf9a69cc9502d286db7b69964d37da7e9cfe4b05b4d004bc275f087d3fd3cf"
    )


def test_the_lora_carries_the_same_licence_as_the_base_weights() -> None:
    """It is a Derivative Model of Krea 2 Raw under the same agreement, so
    selecting it must not read as accepting something new."""
    lora = _entry(LORA)
    base = _entry("krea2-raw-bf16")
    assert lora.license_name == base.license_name
    assert list(lora.license_restrictions) == list(base.license_restrictions)


def test_no_fixture_points_at_the_nonexistent_krea_ai_repo() -> None:
    """`krea-ai/comfyui-krea2edit` 404s; the real pack is `lbouaraba/…`.

    It was only ever in test fixtures, but those were the sole place in the
    tree naming a URL for this node, and it reads as canonical — a consumer
    copying it into custom-nodes.yaml gets a clone failure at provision time
    rather than an obviously-wrong value.
    """
    # Split the needle so this guard does not match its own source.
    dead = "krea-ai" + "/comfyui-krea2edit"
    hits = []
    for path in (REPO_ROOT / "bootstrapper").rglob("*.py"):
        if path.resolve() == Path(__file__).resolve():
            continue
        if dead in path.read_text(encoding="utf-8"):
            hits.append(str(path.relative_to(REPO_ROOT)))
    assert not hits, f"dead {dead} URL still present in: {hits}"
