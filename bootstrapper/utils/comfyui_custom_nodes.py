"""Pinned ComfyUI custom-node allowlist and active install-plan builder."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import yaml

try:
    from utils.comfyui_library import ComfyUILibraryEntry
except ImportError:  # pragma: no cover - defensive loose-module fallback
    from comfyui_library import ComfyUILibraryEntry  # type: ignore[no-redef]


_DEFAULT_CUSTOM_NODES_PATH = "/custom-nodes.yaml"


@dataclass(frozen=True)
class ComfyUICustomNode:
    """One allowlisted custom-node repository pinned to an exact ref."""

    name: str
    repo: str
    ref: str
    install_requirements: bool = False


def _host_repo_custom_nodes() -> Path | None:
    """Best-effort path to ``services/comfyui/custom-nodes.yaml``."""
    here = Path(__file__).resolve()
    candidate = here.parents[2] / "services" / "comfyui" / "custom-nodes.yaml"
    return candidate if candidate.is_file() else None


def _configured_path(env: Mapping[str, str], explicit_path: str | None = None) -> str:
    if explicit_path is not None:
        return explicit_path
    configured = env.get("COMFYUI_CUSTOM_NODES_FILE", "").strip() or _DEFAULT_CUSTOM_NODES_PATH
    if os.path.isfile(configured):
        return configured
    host_path = _host_repo_custom_nodes()
    return str(host_path) if host_path is not None else configured


def _is_full_git_sha(ref: str) -> bool:
    return len(ref) == 40 and all(ch in "0123456789abcdefABCDEF" for ch in ref)


def _node_from_mapping(raw: object, idx: int, path: str) -> ComfyUICustomNode | None:
    if not isinstance(raw, dict):
        print(
            f"WARNING: custom-nodes entry #{idx} in {path} is not a mapping; skipping.",
            file=sys.stderr,
        )
        return None
    name = str(raw.get("name") or "").strip()
    repo = str(raw.get("repo") or "").strip()
    ref = str(raw.get("ref") or "").strip()
    install_requirements = bool(raw.get("install_requirements", False))

    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        print(f"WARNING: custom-nodes entry #{idx} has an unsafe name; skipping.", file=sys.stderr)
        return None
    if not (repo.startswith("https://github.com/") and repo.endswith(".git")):
        print(
            f"WARNING: custom node {name!r} must use a https://github.com/*.git repo; skipping.",
            file=sys.stderr,
        )
        return None
    if not _is_full_git_sha(ref):
        print(
            f"WARNING: custom node {name!r} ref must be a full 40-character commit SHA; skipping.",
            file=sys.stderr,
        )
        return None
    return ComfyUICustomNode(
        name=name,
        repo=repo,
        ref=ref.lower(),
        install_requirements=install_requirements,
    )


def load_custom_nodes(
    env: Mapping[str, str] | None = None,
    *,
    path: str | None = None,
) -> list[ComfyUICustomNode]:
    """Load the pinned custom-node allowlist.

    The file shape is ``custom_nodes: [{name, repo, ref, install_requirements}]``.
    Invalid entries are skipped with warnings so one bad optional node does not
    prevent the active model manifest from being written.
    """
    env = env or {}
    resolved_path = _configured_path(env, path)
    p = Path(resolved_path)
    if not p.is_file():
        return []
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        print(f"WARNING: custom-nodes YAML parse failed at {resolved_path}: {exc}", file=sys.stderr)
        return []
    if not isinstance(raw, dict):
        print(
            f"WARNING: custom-nodes YAML at {resolved_path} must have a top-level mapping; ignoring.",
            file=sys.stderr,
        )
        return []
    raw_nodes = raw.get("custom_nodes") or []
    if not isinstance(raw_nodes, list):
        print(
            f"WARNING: custom-nodes YAML at {resolved_path} has non-list custom_nodes; ignoring.",
            file=sys.stderr,
        )
        return []
    nodes: list[ComfyUICustomNode] = []
    seen: set[str] = set()
    for idx, raw_node in enumerate(raw_nodes, start=1):
        node = _node_from_mapping(raw_node, idx, resolved_path)
        if node is None or node.name in seen:
            continue
        nodes.append(node)
        seen.add(node.name)
    return nodes


def active_custom_nodes(
    entries: Iterable[ComfyUILibraryEntry],
    env: Mapping[str, str] | None = None,
) -> list[ComfyUICustomNode]:
    """Return allowlisted custom nodes required by the active model entries."""
    env = env or {}
    allowlist = load_custom_nodes(env)
    required: set[str] = set()
    for entry in entries:
        required.update(entry.requires_custom_node)
    if not required:
        return []

    allowlisted_names = {node.name for node in allowlist}
    for missing in sorted(required - allowlisted_names):
        print(
            f"WARNING: active ComfyUI model requires custom node {missing!r}, "
            "but it is not in COMFYUI_CUSTOM_NODES_FILE; skipping auto-install.",
            file=sys.stderr,
        )
    return [node for node in allowlist if node.name in required]
