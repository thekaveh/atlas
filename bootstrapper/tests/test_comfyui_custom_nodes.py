"""Tests for pinned ComfyUI custom-node allowlist handling (#905)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from utils.comfyui_custom_nodes import (
    active_custom_nodes,
    load_custom_nodes,
    parse_custom_nodes_strict,
)
from utils.comfyui_library import ComfyUILibraryEntry

_SHA = "6ea2651e7df66d7585f6ffee804b20e92fb38b8a"
_SHA2 = "1111111111111111111111111111111111111111"


def _entry(*nodes: str) -> ComfyUILibraryEntry:
    return ComfyUILibraryEntry(
        name="needs-nodes",
        family="Test",
        category="checkpoint",
        size_gb=1.0,
        url="https://huggingface.co/example/model.safetensors",
        sha256=None,
        target_dir="checkpoints",
        min_vram_gb=None,
        cpu_supported=True,
        requires_custom_node=tuple(nodes),
        popularity=0,
        source="curated",
        pulled=False,
    )


def _write_nodes(path: Path, nodes: list[dict]) -> Path:
    lines = ["custom_nodes:"]
    for n in nodes:
        lines.append(f"  - name: {n['name']}")
        lines.append(f"    repo: {n['repo']}")
        lines.append(f"    ref: {n['ref']}")
        if n.get("install_requirements"):
            lines.append("    install_requirements: true")
        if n.get("mps_unsafe"):
            lines.append("    mps_unsafe: true")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _allowlist(path: Path) -> Path:
    # Mixed valid/invalid entries; preserved from the original test.
    path.write_text(
        "\n".join([
            "custom_nodes:",
            "  - name: ComfyUI-GGUF",
            "    repo: https://github.com/city96/ComfyUI-GGUF.git",
            f"    ref: {_SHA}",
            "    install_requirements: true",
            "  - name: FloatingTag",
            "    repo: https://github.com/example/FloatingTag.git",
            "    ref: main",
            "  - name: UnsafeRepo",
            "    repo: git://github.com/example/UnsafeRepo.git",
            f"    ref: {_SHA2}",
            "",
        ]),
        encoding="utf-8",
    )
    return path


def test_load_custom_nodes_requires_github_repo_and_full_commit(tmp_path, capsys):
    # Explicit path mode: load EXACTLY this file (no Atlas prepend).
    nodes = load_custom_nodes(path=str(_allowlist(tmp_path / "custom-nodes.yaml")))

    assert [node.name for node in nodes] == ["ComfyUI-GGUF"]
    assert nodes[0].ref == _SHA
    assert nodes[0].install_requirements is True
    captured = capsys.readouterr()
    assert "FloatingTag" in captured.err
    assert "UnsafeRepo" in captured.err


def test_active_custom_nodes_skips_unallowlisted_requirements(tmp_path, capsys):
    # Explicit path mode keeps this isolated from the repo's real Atlas allowlist.
    env = {"COMFYUI_CUSTOM_NODES_FILE": str(_allowlist(tmp_path / "custom-nodes.yaml"))}

    nodes = active_custom_nodes([_entry("ComfyUI-GGUF", "UnknownNode")], env)

    assert [node.name for node in nodes] == ["ComfyUI-GGUF"]
    captured = capsys.readouterr()
    assert "UnknownNode" in captured.err


def test_load_custom_nodes_path_list_concatenates(tmp_path):
    a = _write_nodes(tmp_path / "a.yaml", [{"name": "NodeA", "repo": "https://github.com/o/a.git", "ref": _SHA}])
    b = _write_nodes(tmp_path / "b.yaml", [{"name": "NodeB", "repo": "https://github.com/o/b.git", "ref": _SHA2}])

    nodes = load_custom_nodes(path=f"{a}{os.pathsep}{b}")

    assert [n.name for n in nodes] == ["NodeA", "NodeB"]


def test_load_custom_nodes_atlas_always_present_and_first_wins(tmp_path, monkeypatch):
    atlas = _write_nodes(
        tmp_path / "atlas.yaml",
        [{"name": "Shared", "repo": "https://github.com/atlas/shared.git", "ref": _SHA}],
    )
    consumer = _write_nodes(
        tmp_path / "consumer.yaml",
        [
            # Same name as an Atlas node -> Atlas wins (first-wins).
            {"name": "Shared", "repo": "https://github.com/consumer/shadow.git", "ref": _SHA2},
            {"name": "ConsumerOnly", "repo": "https://github.com/o/c.git", "ref": _SHA2},
        ],
    )
    monkeypatch.setattr(
        "utils.comfyui_custom_nodes._host_repo_custom_nodes", lambda: atlas
    )

    nodes = load_custom_nodes({"COMFYUI_CUSTOM_NODES_FILE": str(consumer)})

    by_name = {n.name: n for n in nodes}
    assert set(by_name) == {"Shared", "ConsumerOnly"}
    # Atlas won the collision (its repo/ref), and only Atlas nodes are not from_consumer.
    assert by_name["Shared"].repo == "https://github.com/atlas/shared.git"
    assert by_name["Shared"].from_consumer is False
    assert by_name["ConsumerOnly"].from_consumer is True


def test_active_custom_nodes_returns_consumer_nodes_unconditionally(tmp_path, monkeypatch):
    # The load-bearing #905 fix: a consumer-declared node is active even when NO
    # model requires it (krea2-turbo-bf16 has requires_custom_node=[]).
    atlas = _write_nodes(tmp_path / "atlas.yaml", [])
    consumer = _write_nodes(
        tmp_path / "consumer.yaml",
        [{"name": "comfyui-krea2edit", "repo": "https://github.com/o/k.git", "ref": _SHA}],
    )
    monkeypatch.setattr(
        "utils.comfyui_custom_nodes._host_repo_custom_nodes", lambda: atlas
    )

    nodes = active_custom_nodes([], {"COMFYUI_CUSTOM_NODES_FILE": str(consumer)})

    assert [n.name for n in nodes] == ["comfyui-krea2edit"]
    assert nodes[0].from_consumer is True


def test_mps_unsafe_parses_and_defaults_false(tmp_path):
    _write_nodes(
        tmp_path / "n.yaml",
        [
            {"name": "Plain", "repo": "https://github.com/o/p.git", "ref": _SHA},
            {"name": "CudaOnly", "repo": "https://github.com/o/c.git", "ref": _SHA2, "mps_unsafe": True},
        ],
    )

    nodes = {n.name: n for n in load_custom_nodes(path=str(tmp_path / "n.yaml"))}

    assert nodes["Plain"].mps_unsafe is False
    assert nodes["CudaOnly"].mps_unsafe is True


def test_parse_custom_nodes_strict_valid(tmp_path):
    path = _write_nodes(
        tmp_path / "c.yaml",
        [{"name": "comfyui-krea2edit", "repo": "https://github.com/o/k.git", "ref": _SHA}],
    )

    nodes = parse_custom_nodes_strict(path)

    assert len(nodes) == 1
    assert nodes[0].name == "comfyui-krea2edit"
    assert nodes[0].from_consumer is True


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "bad/sh", "repo": "https://github.com/o/a.git", "ref": _SHA},  # unsafe name
        {"name": "ok", "repo": "https://gitlab.com/o/a.git", "ref": _SHA},  # non-github
        {"name": "ok", "repo": "https://github.com/o/a.git", "ref": "notasha"},  # bad sha
        {"name": "ok", "repo": "https://github.com/o/a.git", "ref": _SHA, "extra": "x"},  # unknown field
    ],
)
def test_parse_custom_nodes_strict_rejects_invalid(tmp_path, bad):
    # Build VALID yaml (multi-line) so the strict parser rejects for the right
    # reason (bad sha/name/repo/unknown field), not a YAML parse error.
    items = list(bad.items())
    first = f"  - {items[0][0]}: {items[0][1]}"
    rest = "\n".join(f"    {k}: {v}" for k, v in items[1:])
    (tmp_path / "c.yaml").write_text("custom_nodes:\n" + first + "\n" + rest + "\n")

    with pytest.raises(ValueError):
        parse_custom_nodes_strict(tmp_path / "c.yaml")
