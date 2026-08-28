"""Pinned ComfyUI custom-node allowlist and active install-plan builder."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import yaml

from services.manifests import load_yaml_strict

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
    # Pre-skip this node on managed-localhost-mps (e.g. CUDA-only native deps).
    # Naming mirrors ``_MPS_UNSAFE_PRECISIONS`` in comfyui_mps_manager.
    mps_unsafe: bool = False
    # True for nodes contributed by a consumer manifest (``custom_nodes.comfyui``)
    # rather than the Atlas-shipped allowlist. Consumer-declared nodes are active
    # unconditionally (a model need not ``requires_custom_node`` them) because they
    # are workflow nodes the consumer wires directly — e.g. comfyui-krea2edit is
    # not referenced by any catalog model (krea2-turbo-bf16 has requires_custom_node=[]).
    from_consumer: bool = False


def _host_repo_custom_nodes() -> Path | None:
    """Best-effort path to ``services/comfyui/custom-nodes.yaml``."""
    here = Path(__file__).resolve()
    candidate = here.parents[2] / "services" / "comfyui" / "custom-nodes.yaml"
    return candidate if candidate.is_file() else None


def _configured_paths(
    env: Mapping[str, str], explicit_path: str | None = None
) -> list[tuple[str, bool]]:
    """Ordered ``(path, is_atlas)`` resolution for the custom-node allowlist.

    Env mode (production): the Atlas-shipped file is ALWAYS present — catalog
    models reference its nodes via ``requires_custom_node``, so it cannot be
    replaced the way a custom-MODELS sidecar replaces the default. Consumer-
    declared files (carried in ``COMFYUI_CUSTOM_NODES_FILE`` as an
    ``os.pathsep``-joined list) append after it; Atlas wins on name collision
    (first-wins, Atlas first).

    Explicit ``path`` mode: load EXACTLY the given path(s) (test/isolation) —
    no Atlas prepend, so a caller asking for one file gets one file.
    """
    if explicit_path is not None:
        ordered: list[tuple[str, bool]] = []
        seen: set[str] = set()
        for candidate in (p.strip() for p in explicit_path.split(os.pathsep)):
            if candidate and candidate not in seen and os.path.isfile(candidate):
                ordered.append((candidate, False))
                seen.add(candidate)
        return ordered

    atlas_host = _host_repo_custom_nodes()
    atlas_path = str(atlas_host) if atlas_host is not None else _DEFAULT_CUSTOM_NODES_PATH
    raw = env.get("COMFYUI_CUSTOM_NODES_FILE", "").strip()
    configured = [p.strip() for p in raw.split(os.pathsep)] if raw else []

    ordered = []
    seen = set()

    def _add(candidate: str) -> None:
        if not candidate or candidate in seen or not os.path.isfile(candidate):
            return
        ordered.append((candidate, candidate == atlas_path))
        seen.add(candidate)

    _add(atlas_path)
    for candidate in configured:
        _add(candidate)
    return ordered


def _is_full_git_sha(ref: str) -> bool:
    return len(ref) == 40 and all(ch in "0123456789abcdefABCDEF" for ch in ref)


def _node_from_mapping(
    raw: object, idx: int, path: str, *, from_consumer: bool = False
) -> ComfyUICustomNode | None:
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
    mps_unsafe = bool(raw.get("mps_unsafe", False))

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
        mps_unsafe=mps_unsafe,
        from_consumer=from_consumer,
    )


def _load_nodes_from_file(path: str, *, from_consumer: bool) -> list[ComfyUICustomNode]:
    """Load and structurally validate one custom-nodes YAML file.

    Invalid entries are skipped with a warning so one bad optional node does not
    prevent the active model manifest from being written. ``from_consumer`` tags
    the node's origin (consumer-declared nodes are active unconditionally).
    """
    p = Path(path)
    if not p.is_file():
        return []
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        print(f"WARNING: custom-nodes YAML parse failed at {path}: {exc}", file=sys.stderr)
        return []
    if not isinstance(raw, dict):
        print(
            f"WARNING: custom-nodes YAML at {path} must have a top-level mapping; ignoring.",
            file=sys.stderr,
        )
        return []
    raw_nodes = raw.get("custom_nodes") or []
    if not isinstance(raw_nodes, list):
        print(
            f"WARNING: custom-nodes YAML at {path} has non-list custom_nodes; ignoring.",
            file=sys.stderr,
        )
        return []
    nodes: list[ComfyUICustomNode] = []
    for idx, raw_node in enumerate(raw_nodes, start=1):
        node = _node_from_mapping(raw_node, idx, path, from_consumer=from_consumer)
        if node is not None:
            nodes.append(node)
    return nodes


def load_custom_nodes(
    env: Mapping[str, str] | None = None,
    *,
    path: str | None = None,
) -> list[ComfyUICustomNode]:
    """Load the merged pinned custom-node allowlist (Atlas ∪ consumer).

    The Atlas-shipped file is always present; consumer-declared files
    (``COMFYUI_CUSTOM_NODES_FILE``, os.pathsep-joined) append. Duplicate names
    resolve first-wins (Atlas wins because it is loaded first).
    """
    env = env or {}
    nodes: list[ComfyUICustomNode] = []
    seen: set[str] = set()
    for resolved_path, is_atlas in _configured_paths(env, path):
        for node in _load_nodes_from_file(resolved_path, from_consumer=not is_atlas):
            if node.name in seen:
                continue
            nodes.append(node)
            seen.add(node.name)
    return nodes


def parse_custom_nodes_strict(path: str | Path) -> list[ComfyUICustomNode]:
    """Strict consumer-manifest-load parser: RAISE on any structural violation.

    Unlike ``load_custom_nodes`` (warn-and-skip, appropriate for the optional
    Atlas allowlist), consumer declarations must fail loud at ``./start.sh``
    time — a silently-dropped consumer node would surface as a missing workflow
    node at runtime. Enforces the same three invariants as ``_node_from_mapping``
    (safe name, ``https://github.com/*.git`` repo, 40-char hex SHA) but raises
    ``ValueError`` (the consumer-manifest loader wraps it in
    ``ConsumerManifestError``).
    """
    p = Path(path)
    try:
        raw = load_yaml_strict(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"custom-nodes file {path} has invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"custom-nodes file {path} must have a top-level mapping")
    raw_nodes = raw.get("custom_nodes", [])
    if not isinstance(raw_nodes, list):
        raise ValueError(f"custom-nodes file {path} has non-list custom_nodes")
    nodes: list[ComfyUICustomNode] = []
    seen: set[str] = set()
    for idx, entry in enumerate(raw_nodes, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"custom-nodes entry #{idx} in {path} is not a mapping")
        name = str(entry.get("name") or "").strip()
        repo = str(entry.get("repo") or "").strip()
        ref = str(entry.get("ref") or "").strip()
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError(f"custom-nodes entry #{idx} has an unsafe name: {name!r}")
        if not (repo.startswith("https://github.com/") and repo.endswith(".git")):
            raise ValueError(f"custom node {name!r} must use a https://github.com/*.git repo")
        if not _is_full_git_sha(ref):
            raise ValueError(f"custom node {name!r} ref must be a full 40-character SHA")
        if name in seen:
            raise ValueError(f"custom node {name!r} declared twice in {path}")
        unknown = set(entry) - {"name", "repo", "ref", "install_requirements", "mps_unsafe"}
        if unknown:
            raise ValueError(f"custom node {name!r} has unknown field(s): {sorted(unknown)}")
        for flag in ("install_requirements", "mps_unsafe"):
            if flag in entry and type(entry[flag]) is not bool:
                raise ValueError(f"custom node {name!r} {flag} must be a boolean")
        seen.add(name)
        nodes.append(
            ComfyUICustomNode(
                name=name,
                repo=repo,
                ref=ref.lower(),
                install_requirements=bool(entry.get("install_requirements", False)),
                mps_unsafe=bool(entry.get("mps_unsafe", False)),
                from_consumer=True,
            )
        )
    return nodes


def active_custom_nodes(
    entries: Iterable[ComfyUILibraryEntry],
    env: Mapping[str, str] | None = None,
) -> list[ComfyUICustomNode]:
    """Return the custom nodes to install for the active model set + consumers.

    Atlas-shipped nodes are model-gated (installed only when an active model
    declares ``requires_custom_node``). Consumer-declared nodes
    (``from_consumer``) are active unconditionally — they are workflow nodes the
    consumer wires directly, not model metadata (e.g. comfyui-krea2edit is not
    referenced by any catalog model, so model-gating would silently drop it).
    """
    env = env or {}
    allowlist = load_custom_nodes(env)
    required: set[str] = set()
    for entry in entries:
        required.update(entry.requires_custom_node)

    allowlisted_names = {node.name for node in allowlist}
    for missing in sorted(required - allowlisted_names):
        print(
            f"WARNING: active ComfyUI model requires custom node {missing!r}, "
            "but it is not in the COMFYUI_CUSTOM_NODES_FILE list; skipping auto-install.",
            file=sys.stderr,
        )
    return [node for node in allowlist if node.name in required or node.from_consumer]
