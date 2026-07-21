"""Track registry — predefined wizard profiles that curate a subset of
source-configurable services.

`bootstrapper/tracks.yml` is the source of truth. This module parses,
validates, and exposes it as typed dataclasses, plus the lookup helpers
the wizard step builder (`bootstrapper/ui/textual/integration.py`) and
the CLI surface (`bootstrapper/start.py`) consume.

Pure data + lookup — no Textual, no Click, no Rich imports inside the
hot path. `format_track_list` does import Rich for human-readable
output but is only called from `--list-tracks` and the no-TTY fallback.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

__all__ = [
    "Track",
    "TrackRegistry",
    "TracksLoadError",
    "UnknownTrackServiceError",
    "load_tracks",
    "compute_always_on",
    "is_in_track",
    "synthesize_track_source_args",
    "normalize_service_key",
    "format_track_list",
]


# ────────────────────────────────────────────────────────────────────
# Paths
# ────────────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent
_DEFAULT_TRACKS_PATH = _HERE / "tracks.yml"
_SCHEMA_PATH = _HERE / "schemas" / "tracks.schema.json"


# ────────────────────────────────────────────────────────────────────
# Dataclasses
# ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Track:
    """One row from tracks.yml."""
    key: str                          # slug, e.g. "gen-ai-rag"
    display_name: str
    description: str
    # None == "*" sentinel (the "all" track — never filter).
    # Otherwise: folder keys for services curated by this track,
    # on top of the always-on tier.
    services: frozenset[str] | None


@dataclass(frozen=True)
class TrackRegistry:
    """All tracks + the always-on set, both consumed by the wizard."""
    tracks: tuple[Track, ...]            # canonical display order
    by_key: dict[str, Track]             # quick lookup
    always_on: frozenset[str]            # service keys exempted from skip


# ────────────────────────────────────────────────────────────────────
# Errors
# ────────────────────────────────────────────────────────────────────

class TracksLoadError(Exception):
    """Raised when tracks.yml fails to load or validate."""


class UnknownTrackServiceError(TracksLoadError):
    """Raised when a track lists a service key that doesn't exist as a
    services/<name>/ folder or as a wizard-discovered configurable
    service."""


# ────────────────────────────────────────────────────────────────────
# Family alias map
# ────────────────────────────────────────────────────────────────────

# Some services have runtime_sc keys that diverge from their folder name:
#
# 1. Multi-container families: the wizard's ServiceDiscovery anchors on the
#    head container (e.g. `ray-head`, `spark-master`, `airflow-webserver`),
#    while the folder under services/ is named after the family root
#    (`ray`, `spark`, `airflow`).
#
# 2. Single-container divergences: a couple of older services were declared
#    with a runtime_sc key that already differed from the folder name —
#    services/neo4j/ uses `neo4j-graph-db` and services/open-webui/ uses
#    `open-web-ui` (three hyphens). The wizard hands the predicate the
#    runtime_sc key; tracks.yml uses the folder name.
#
# This map bridges both cases at predicate time. Plain folder keys pass
# through unchanged.
_FAMILY_KEY_ALIASES: dict[str, str] = {
    "ray-head": "ray",
    "celery-worker": "celery",
    "spark-master": "spark",
    "airflow-webserver": "airflow",
    "langfuse-web": "langfuse",
    "llm-graph-builder-frontend": "llm-graph-builder",
    "neo4j-graph-db": "neo4j",
    "open-web-ui": "open-webui",
    # Virtual-manifest services: Click CLI param names use underscores
    # (e.g. ``llm_provider`` from ``--llm-provider-source``), but
    # tracks.yml and always_on use the folder/hyphenated form.
    "llm_provider": "llm-provider",
    "stt_provider": "stt-provider",
    "tts_provider": "tts-provider",
    "doc_processor": "doc-processor",
    "vllm_metal": "vllm-metal",
    "blender_mcp": "blender-mcp",
    "fal": "fal",
}


def normalize_service_key(key: str) -> str:
    """Map a wizard svc.key (possibly a multi-container family head or
    runtime_sc-divergent name) to the folder name used in tracks.yml.
    Plain folder keys pass through."""
    return _FAMILY_KEY_ALIASES.get(key, key)


# ────────────────────────────────────────────────────────────────────
# Loader
# ────────────────────────────────────────────────────────────────────

_validator_singleton: Draft202012Validator | None = None

# Cached registry — populated by load_tracks() on first call when no
# explicit path is given. The schema validator is already cached;
# this avoids re-reading + re-parsing tracks.yml on every helper call
# from the wizard hot path (~6 calls per --track invocation).
_REGISTRY_CACHE: "TrackRegistry | None" = None


def _get_validator() -> Draft202012Validator:
    global _validator_singleton
    if _validator_singleton is None:
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        _validator_singleton = Draft202012Validator(schema)
    return _validator_singleton


def _canonical_service_keys() -> frozenset[str]:
    """Every services/<name>/ folder key (the universe a track can pick
    from). Accepts both manifest-bearing dirs and README-only ("doc-only")
    folders like services/doc-processor/ and services/stt-provider/ —
    those still appear in the wizard via source_mapping shims and are
    legitimate track members."""
    services_root = _HERE.parent / "services"
    keys: set[str] = set()
    if not services_root.is_dir():
        return frozenset()
    for child in services_root.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith(("_", ".")):
            continue
        if not (child / "service.yml").exists() and not (child / "README.md").exists():
            continue
        keys.add(child.name)
    return frozenset(keys)


def load_tracks(path: Path | None = None) -> TrackRegistry:
    """Parse and validate the track registry.

    Path defaults to ``bootstrapper/tracks.yml``. Raises
    ``TracksLoadError`` (and subclasses) on schema / cross-validation
    failure.

    The result is cached in ``_REGISTRY_CACHE`` when called without an
    explicit path (the production hot path). Tests that pass a ``tmp_path``
    always bypass the cache and re-parse — so the cache never affects
    test isolation.
    """
    global _REGISTRY_CACHE
    if path is None and _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    p = Path(path) if path is not None else _DEFAULT_TRACKS_PATH
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise TracksLoadError(f"{p}: invalid YAML — {e}") from e

    schema_errors = sorted(
        _get_validator().iter_errors(raw),
        key=lambda e: list(e.absolute_path),
    )
    if schema_errors:
        details = "; ".join(
            f"{'.'.join(str(x) for x in e.absolute_path) or '<root>'}: {e.message}"
            for e in schema_errors
        )
        raise TracksLoadError(f"{p}: schema violation(s): {details}")

    # Build Track objects.
    tracks_list: list[Track] = []
    seen_keys: set[str] = set()
    for entry in raw["tracks"]:
        key = entry["key"]
        if key in seen_keys:
            raise TracksLoadError(f"{p}: duplicate track key '{key}'")
        seen_keys.add(key)
        svc_field = entry["services"]
        services: frozenset[str] | None
        if svc_field == "*":
            services = None
        else:
            services = frozenset(svc_field)
        tracks_list.append(Track(
            key=key,
            display_name=entry["display_name"],
            description=entry["description"],
            services=services,
        ))

    # Cross-validate: every listed service must be a real folder key.
    canonical = _canonical_service_keys()
    unknown: list[tuple[str, str]] = []
    for t in tracks_list:
        if t.services is None:
            continue
        for svc in t.services:
            if svc not in canonical:
                unknown.append((t.key, svc))
    if unknown:
        details = ", ".join(f"{tk}:{sv}" for tk, sv in unknown)
        raise UnknownTrackServiceError(
            f"{p}: services not found under services/<name>/: {details}. "
            f"Valid keys: {sorted(canonical)}"
        )

    tracks_tuple = tuple(tracks_list)
    by_key = {t.key: t for t in tracks_tuple}

    # Always-on set: the three services that ServiceDiscovery surfaces
    # but the track-skip predicate must NEVER filter. The compute_*
    # helper below derives the same set from the live ConfigParser when
    # called from the wizard; here we hard-code the canonical names so
    # the registry is self-contained without requiring a ConfigParser.
    always_on = frozenset({"llm-provider", "prometheus", "grafana"})

    registry = TrackRegistry(
        tracks=tracks_tuple,
        by_key=by_key,
        always_on=always_on,
    )
    if path is None:
        _REGISTRY_CACHE = registry
    return registry


# ────────────────────────────────────────────────────────────────────
# Always-on tier
# ────────────────────────────────────────────────────────────────────

def compute_always_on(config_parser: Any) -> frozenset[str]:  # noqa: ARG001
    """Returns the set of wizard-step ``service_key``s that must NEVER be
    skipped by a track-skip predicate.

    Today: ``{llm-provider, prometheus, grafana}`` — the three services
    that survive ``ServiceDiscovery.discover()`` filtering and must be
    exempt from track-based skipping. Locked manifests and cloud-provider
    keys are filtered out upstream and never see the predicate.

    Parameterized by ``config_parser`` so a future extension can derive
    the set from manifests instead of hard-coding it.
    """
    return frozenset({"llm-provider", "prometheus", "grafana"})


# ────────────────────────────────────────────────────────────────────
# Predicate
# ────────────────────────────────────────────────────────────────────

def is_in_track(
    track: Track,
    service_key: str,
    *,
    always_on: frozenset[str],
) -> bool:
    """True iff ``service_key`` (a wizard svc.key) is asked/enabled-by-
    default under ``track``.

    Implements the rule from the spec:
      1. Service is in the always-on tier → always in-track.
      2. Track is the "*" sentinel (services is None) → always in-track.
      3. Service is explicitly listed in track.services after family
         alias normalization → in-track.
      4. Otherwise → out-of-track.
    """
    normalized = normalize_service_key(service_key)
    if normalized in always_on:
        return True
    if track.services is None:
        return True
    return normalized in track.services


def synthesize_track_source_args(
    source_args: dict[str, str | None],
    *,
    track_key: str | None,
    registry: TrackRegistry,
    force_disable: bool,
    consumer_declared: frozenset[str] | set[str] = frozenset(),
) -> set[str]:
    """Apply track membership to Click-style ``*_source`` argument dicts.

    Returns the off-track services that the user explicitly overrode. When
    ``force_disable`` is true, off-track services without an explicit value are
    filled with ``"disabled"``. Wizard mode passes ``force_disable=False`` so
    it can record overrides without making source_args look CLI-configured.

    ``consumer_declared`` (#783): cli-style keys (e.g. ``minio_source``) whose
    SOURCE var the consumer manifest explicitly declares in ``env.values``.
    Declared intent beats the track default: those services are exempt from
    the force-disable fill (and recorded as overrides), so a manifest's
    ``MINIO_SOURCE: container`` survives an out-of-track selection instead of
    being silently reverted to ``disabled`` — an explicit CLI flag still wins
    over both.
    """
    overridden_services: set[str] = set()
    if track_key is None:
        return overridden_services
    track = registry.by_key.get(track_key)
    if track is None or track.services is None:
        return overridden_services
    for cli_key in list(source_args.keys()):
        if cli_key.startswith("cloud_"):
            continue
        svc_key = cli_key.removesuffix("_source").replace("_", "-")
        if is_in_track(track, svc_key, always_on=registry.always_on):
            continue
        explicit_value = source_args.get(cli_key)
        if explicit_value is not None and explicit_value != "disabled":
            overridden_services.add(svc_key)
        elif explicit_value is None and cli_key in consumer_declared:
            # Consumer-manifest-declared source: leave untouched so the
            # env.values declaration lands in .env unclobbered; record it as
            # an override for the advisory line.
            overridden_services.add(svc_key)
        elif explicit_value is None and force_disable:
            source_args[cli_key] = "disabled"
    return overridden_services


# ────────────────────────────────────────────────────────────────────
# Human-readable list (--list-tracks)
# ────────────────────────────────────────────────────────────────────

def format_track_list(registry: TrackRegistry) -> str:
    """Rich-formatted table for ``--list-tracks`` output and the no-TTY
    stdin prompt fallback. Returns a plain string (Rich renders into a
    string capture buffer)."""
    from io import StringIO
    from rich.console import Console
    from rich.table import Table

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    table = Table(title="Available tracks", show_lines=False)
    table.add_column("Key", style="bold cyan")
    table.add_column("Name")
    table.add_column("Track-specific services")

    for t in registry.tracks:
        if t.services is None:
            svc_cell = "* (every configurable service)"
        else:
            svc_cell = ", ".join(sorted(t.services))
        table.add_row(t.key, t.display_name, svc_cell)
    console.print(table)
    console.print(
        "[dim]Prompted in every track: LLM Engine · Prometheus · Grafana · "
        "OpenAI/Anthropic/OpenRouter cloud keys. Prometheus and Grafana remain "
        "disabled unless enabled.[/dim]"
    )
    return buf.getvalue()
