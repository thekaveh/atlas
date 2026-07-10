"""Tests for pinned ComfyUI custom-node allowlist handling."""
from __future__ import annotations

from pathlib import Path

from utils.comfyui_custom_nodes import active_custom_nodes, load_custom_nodes
from utils.comfyui_library import ComfyUILibraryEntry


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


def _allowlist(path: Path) -> Path:
    path.write_text(
        "\n".join([
            "custom_nodes:",
            "  - name: ComfyUI-GGUF",
            "    repo: https://github.com/city96/ComfyUI-GGUF.git",
            "    ref: 6ea2651e7df66d7585f6ffee804b20e92fb38b8a",
            "    install_requirements: true",
            "  - name: FloatingTag",
            "    repo: https://github.com/example/FloatingTag.git",
            "    ref: main",
            "  - name: UnsafeRepo",
            "    repo: git://github.com/example/UnsafeRepo.git",
            "    ref: 1111111111111111111111111111111111111111",
            "",
        ]),
        encoding="utf-8",
    )
    return path


def test_load_custom_nodes_requires_github_repo_and_full_commit(tmp_path, capsys):
    nodes = load_custom_nodes(path=str(_allowlist(tmp_path / "custom-nodes.yaml")))

    assert [node.name for node in nodes] == ["ComfyUI-GGUF"]
    assert nodes[0].ref == "6ea2651e7df66d7585f6ffee804b20e92fb38b8a"
    assert nodes[0].install_requirements is True
    captured = capsys.readouterr()
    assert "FloatingTag" in captured.err
    assert "UnsafeRepo" in captured.err


def test_active_custom_nodes_skips_unallowlisted_requirements(tmp_path, capsys):
    env = {"COMFYUI_CUSTOM_NODES_FILE": str(_allowlist(tmp_path / "custom-nodes.yaml"))}

    nodes = active_custom_nodes([_entry("ComfyUI-GGUF", "UnknownNode")], env)

    assert [node.name for node in nodes] == ["ComfyUI-GGUF"]
    captured = capsys.readouterr()
    assert "UnknownNode" in captured.err
