"""Contracts and source discovery for Atlas's upstream drift watch.

The live probes and command-line entry point are added in a later task.  This
module deliberately contains no network or subprocess behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import yaml


_MAX_DETAIL_LENGTH = 500
_REPORT_MARKER = "<!-- atlas-upstream-drift-watch -->"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """The immutable result of one drift-watch probe."""

    name: str
    ok: bool
    detail: str


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"could not read YAML source {path}: {exc}") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"YAML source {path} must contain a mapping")
    return value


def _sorted_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def load_curated_ollama_models(path: Path) -> tuple[str, ...]:
    """Return all named models in the curated Ollama catalog.

    The catalog is organized into role sections (content, embeddings,
    vision, and future additions).  Names are treated as artifact references,
    so multimodal entries appearing in more than one section are collapsed.
    """

    document = _read_yaml_mapping(path)
    names: list[str] = []
    for section in document.values():
        if not isinstance(section, list):
            continue
        for entry in section:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return _sorted_unique(names)


def load_manifest_image_refs(services_dir: Path) -> tuple[str, ...]:
    """Return literal image defaults declared by service manifests."""

    refs: list[str] = []
    try:
        manifest_paths = sorted(services_dir.glob("*/service.yml"))
    except OSError as exc:
        raise ValueError(f"could not discover service manifests in {services_dir}: {exc}") from exc

    for manifest_path in manifest_paths:
        document = _read_yaml_mapping(manifest_path)
        images = document.get("images", [])
        if images is None:
            continue
        if not isinstance(images, list):
            raise ValueError(f"manifest {manifest_path} images must be a list")
        for image in images:
            if not isinstance(image, dict):
                continue
            default = image.get("default")
            if isinstance(default, str) and default.strip():
                refs.append(default.strip())
    return _sorted_unique(refs)


def _bounded_detail(detail: str) -> str:
    normalized = " ".join(str(detail).split())
    if len(normalized) <= _MAX_DETAIL_LENGTH:
        return normalized
    return normalized[: _MAX_DETAIL_LENGTH - 1] + "…"


def render_report(results: Sequence[ProbeResult], generated_at: datetime) -> str:
    """Render a stable Markdown report suitable for an issue body."""

    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    timestamp = generated_at.astimezone(timezone.utc).isoformat()
    failures = [result for result in results if not result.ok]
    lines = [
        _REPORT_MARKER,
        "# Atlas upstream drift watch",
        "",
        f"Generated at: `{timestamp}`",
        "",
        f"Status: **{'FAIL' if failures else 'OK'}**",
        "",
        "## Probe results",
        "",
    ]
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        lines.extend(
            [
                f"### {result.name}",
                "",
                f"- Status: **{status}**",
                f"- Detail: {_bounded_detail(result.detail)}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
