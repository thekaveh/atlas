# Setup-Wizard Tracks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a preselected-profile ("track") layer to the Atlas setup wizard so users land on a curated subset of services for their role, with an `--track` CLI flag, a Wizard step-1 picker, force-disabled out-of-track services, and `--*-source` flag overrides.

**Architecture:** A new top-level `bootstrapper/tracks.yml` registry feeds a pure-data `bootstrapper/tracks.py` module (`Track` / `TrackRegistry` dataclasses + load/validate/lookup helpers). The wizard step builder in `bootstrapper/ui/textual/integration.py` inserts a new picker `PromptStep` at index 0 and attaches `skip_if_prev` predicates to every per-service step. `_selections_to_args` synthesizes `*_SOURCE=disabled` for every configurable service that is out-of-track AND not explicitly overridden by a CLI flag. The override set is computed in `bootstrapper/start.py` from `sys.argv` and threaded into the builder.

**Tech Stack:** Python 3.10+, pytest, PyYAML, jsonschema (Draft202012Validator — same pattern as `bootstrapper/services/manifests.py`), Click (CLI), Textual (TUI; no schema changes), Rich (--list-tracks output).

**Spec:** `docs/superpowers/specs/2026-06-13-setup-wizard-tracks-design.md` (commit `e5836df`).

---

## 1. File Structure

### 1.1. New files (8)

| Path                                              | Responsibility                                                                                              |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `bootstrapper/tracks.yml`                         | Track registry (the 6 tracks). YAML + inline schema-doc comments.                                            |
| `bootstrapper/schemas/tracks.schema.json`         | JSON Schema for `tracks.yml`. Validated at load time, same pattern as `service.schema.json`.                 |
| `bootstrapper/tracks.py`                          | `Track`, `TrackRegistry` dataclasses; `load_tracks`, `compute_always_on`, `is_in_track`, `format_track_list`, `normalize_service_key`. Pure data + lookup; no Textual / Click imports. |
| `bootstrapper/tests/test_tracks.py`               | Schema validation, load happy-path, unknown-service rejection, `"*"` sentinel, `compute_always_on`, `is_in_track`, `normalize_service_key`. |
| `bootstrapper/tests/test_tracks_wizard_skip.py`   | `_make_track_skip` matrix (track × service); always-on never skipped; override re-enables.                   |
| `bootstrapper/tests/test_tracks_cli.py`           | `--list-tracks` exit 0 + stdout; `--track unknown` exits 2; off-track flag emits stderr warning; `--track all` suppresses warnings. |
| `bootstrapper/tests/test_tracks_selections.py`    | `_selections_to_args` synthesizes `*_SOURCE=disabled` for `configurable - in_track - overridden`.            |
| `scripts/check-track-membership.py`               | Audit: every service in `tracks.yml` exists; every configurable service in ≥1 track other than `all`.        |

### 1.2. Modified files (6)

| Path                                                  | Change                                                                                                       |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `bootstrapper/start.py`                               | Add `--track` + `--list-tracks` Click options; early-exit on `--list-tracks`; compute override-set; emit per-flag warnings; thread track + override-set into `run_setup_flow` / `run_launch_flow`. |
| `bootstrapper/ui/textual/integration.py`              | Accept `track` + `overridden` params on builders; insert picker `PromptStep` at index 0; attach `skip_if_prev=_make_track_skip(...)` to every per-service step; `_selections_to_args` synthesizes `*_SOURCE=disabled` for force-skipped services; pre-launch summary banner. |
| `bootstrapper/ui/textual/screens/wizard_screen.py`    | `_refresh_info_panel` includes a `track_label: str \| None` argument on `InfoBoxState` (additive); WizardScreen receives a `track_display_name` ctor arg and threads it.                                              |
| `bootstrapper/ui/state_builder.py`                    | `build_app_state` accepts optional `in_track: Callable[[str], bool] \| None` + `overridden: frozenset[str]`; ServiceEntry gains an `off_track: bool` flag used purely by the renderer. |
| `bootstrapper/ui/state.py`                            | Add `off_track: bool = False` on `ServiceEntry` (default `False` keeps existing callers source-compatible).   |
| `CLAUDE.md` + `README.md`                             | Short "Tracks" subsection (Architecture) + "Quickstart by track" section.                                    |

### 1.3. Naming conventions used below

- **Track key**: kebab-case slug stored in `tracks.yml`, e.g. `gen-ai-rag`.
- **Service folder key**: kebab-case name of `services/<name>/`, e.g. `spark`, `ray`, `airflow`.
- **Wizard svc.key**: the runtime_sc key used by `ServiceDiscovery`; sometimes equal to the folder key, sometimes the "head" of a multi-container family (`ray-head`, `spark-master`, `airflow-webserver`).
- **`tracks.yml` uses folder keys**, not wizard svc.keys. The wizard-side predicate normalizes via a small `_FAMILY_KEY_ALIASES` map in `tracks.py` so the YAML stays user-friendly.

### 1.4. Commit convention

Per `memory/feedback_commits.md`: terse third-person verb, no Claude Co-Authored-By trailer. Examples:
- `feat(tracks): add tracks.py registry + JSON schema`
- `feat(start): wire --track + --list-tracks CLI flags`
- `test(tracks): cover track-skip predicate matrix`

---

## 2. Task 1: Add the track registry YAML

**Files:**
- Create: `bootstrapper/tracks.yml`

- [ ] **Step 1: Create `bootstrapper/tracks.yml`**

```yaml
# Per-track configurable-service membership.
#
# The always-on tier (locked manifests + LLM Engine + Prometheus + Grafana
# + cloud-provider keys) is implicit and applies to every track — it is
# NOT enumerated here. Adding a new always-on cloud provider in
# bootstrapper/utils/cloud_providers.py automatically extends the implicit
# set via compute_always_on(); tracks.yml stays unchanged.
#
# `services: "*"` is the "all" sentinel — no filtering is applied.
#
# Service entries use the folder name under services/<name>/ (e.g. `spark`,
# `ray`, `airflow`). The wizard normalizes multi-container family heads
# (`ray-head`, `spark-master`, `airflow-webserver`) back to folder names
# via tracks.py::_FAMILY_KEY_ALIASES before lookup.
tracks:
  - key: gen-ai-rag
    display_name: Generative AI · RAG
    description: Retrieval-augmented generation — vectors, graph, reranker, doc ingest, web search.
    services: [open-webui, weaviate, neo4j-graph-db, lightrag, doc-processor,
               tei-reranker, searxng, local-deep-researcher]

  - key: gen-ai-eng
    display_name: Generative AI · Engineering
    description: Agentic apps + workflows with voice, vision, and search.
    services: [open-webui, n8n, hermes, openclaw, jupyterhub, comfyui,
               stt-provider, tts-provider, searxng, local-deep-researcher]

  - key: gen-ai-creative
    display_name: Generative AI · Creative
    description: Multimodal generation — image, voice, vision, doc.
    services: [open-webui, comfyui, stt-provider, tts-provider,
               multi2vec-clip, doc-processor]

  - key: ml-eng
    display_name: ML Engineering
    description: Distributed training/inference + notebooks + experiment storage.
    services: [spark, ray, jupyterhub, zeppelin, open-webui, minio, tei-reranker]

  - key: data-eng
    display_name: Data Engineering
    description: Batch + lakehouse + graph + vector with orchestration.
    services: [spark, airflow, jupyterhub, zeppelin, minio, weaviate, neo4j-graph-db]

  - key: all
    display_name: All / Custom
    description: Every configurable service — full wizard, no filtering.
    services: "*"
```

- [ ] **Step 2: Verify YAML parses**

Run: `python -c "import yaml; print(len(yaml.safe_load(open('bootstrapper/tracks.yml'))['tracks']))"`
Expected: `6`

- [ ] **Step 3: Commit**

```bash
git add bootstrapper/tracks.yml
git commit -m "feat(tracks): add tracks.yml registry"
```

---

## 3. Task 2: Add JSON Schema for tracks.yml

**Files:**
- Create: `bootstrapper/schemas/tracks.schema.json`

- [ ] **Step 1: Create the schema**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://atlas/schemas/tracks.schema.json",
  "title": "Tracks Registry",
  "description": "Schema for bootstrapper/tracks.yml — predefined wizard profiles that curate a subset of source-configurable services for a given user role.",
  "type": "object",
  "additionalProperties": false,
  "required": ["tracks"],
  "properties": {
    "tracks": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["key", "display_name", "description", "services"],
        "properties": {
          "key": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9-]*[a-z0-9]$",
            "description": "kebab-case track slug used by --track and the picker selections key."
          },
          "display_name": {
            "type": "string",
            "minLength": 1,
            "description": "Human-readable label shown in the wizard picker."
          },
          "description": {
            "type": "string",
            "minLength": 1,
            "description": "One-line description shown beneath the display name in the picker."
          },
          "services": {
            "oneOf": [
              {
                "const": "*",
                "description": "Sentinel: 'all' track — no filtering applied."
              },
              {
                "type": "array",
                "minItems": 1,
                "uniqueItems": true,
                "items": {
                  "type": "string",
                  "pattern": "^[a-z][a-z0-9-]*[a-z0-9]$"
                },
                "description": "List of services/<name> folder keys that this track curates on top of the always-on tier."
              }
            ]
          }
        }
      }
    }
  }
}
```

- [ ] **Step 2: Verify schema parses as valid Draft 2020-12**

```bash
python -c "import json; from jsonschema import Draft202012Validator; Draft202012Validator.check_schema(json.load(open('bootstrapper/schemas/tracks.schema.json')))"
```
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add bootstrapper/schemas/tracks.schema.json
git commit -m "feat(tracks): add JSON schema for tracks.yml"
```

---

## 4. Task 3: TDD — write the tracks.py loader tests (failing)

**Files:**
- Create: `bootstrapper/tests/test_tracks.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for bootstrapper/tracks.py — the track registry loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tracks import (
    Track,
    TrackRegistry,
    load_tracks,
    compute_always_on,
    is_in_track,
    normalize_service_key,
    format_track_list,
    UnknownTrackServiceError,
)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TRACKS_YML = REPO_ROOT / "bootstrapper" / "tracks.yml"


# ─── load_tracks ─────────────────────────────────────────────────────

def test_load_tracks_default_path():
    """Default path = bootstrapper/tracks.yml; returns a TrackRegistry."""
    reg = load_tracks()
    assert isinstance(reg, TrackRegistry)
    assert len(reg.tracks) == 6
    assert reg.tracks[0].key == "gen-ai-rag"
    assert reg.tracks[-1].key == "all"


def test_load_tracks_explicit_path(tmp_path: Path):
    p = tmp_path / "t.yml"
    p.write_text(yaml.safe_dump({
        "tracks": [
            {"key": "x", "display_name": "X", "description": "desc",
             "services": ["weaviate"]},
        ]
    }))
    reg = load_tracks(p)
    assert len(reg.tracks) == 1
    assert reg.tracks[0].key == "x"
    assert reg.tracks[0].services == frozenset({"weaviate"})


def test_load_tracks_all_sentinel(tmp_path: Path):
    p = tmp_path / "t.yml"
    p.write_text(yaml.safe_dump({
        "tracks": [
            {"key": "all", "display_name": "All", "description": "every",
             "services": "*"},
        ]
    }))
    reg = load_tracks(p)
    assert reg.tracks[0].services is None  # "*" → None sentinel


def test_load_tracks_schema_violation_empty(tmp_path: Path):
    p = tmp_path / "t.yml"
    p.write_text(yaml.safe_dump({"tracks": []}))   # minItems: 1
    with pytest.raises(Exception):
        load_tracks(p)


def test_load_tracks_schema_violation_missing_key(tmp_path: Path):
    p = tmp_path / "t.yml"
    p.write_text(yaml.safe_dump({
        "tracks": [
            {"display_name": "X", "description": "d", "services": ["weaviate"]},
        ]
    }))
    with pytest.raises(Exception):
        load_tracks(p)


def test_load_tracks_schema_violation_bad_key_pattern(tmp_path: Path):
    p = tmp_path / "t.yml"
    p.write_text(yaml.safe_dump({
        "tracks": [
            {"key": "BadKey", "display_name": "X", "description": "d",
             "services": ["weaviate"]},
        ]
    }))
    with pytest.raises(Exception):
        load_tracks(p)


def test_load_tracks_unknown_service_raises(tmp_path: Path):
    """Cross-check: a service that doesn't exist as services/<name>/ rejects."""
    p = tmp_path / "t.yml"
    p.write_text(yaml.safe_dump({
        "tracks": [
            {"key": "x", "display_name": "X", "description": "d",
             "services": ["nonexistent-service"]},
        ]
    }))
    with pytest.raises(UnknownTrackServiceError) as exc:
        load_tracks(p)
    assert "nonexistent-service" in str(exc.value)


def test_load_tracks_real_registry_validates():
    """The committed tracks.yml must pass cross-validation against
    the real services/ tree."""
    reg = load_tracks()  # raises on drift
    assert reg.by_key["gen-ai-rag"].display_name == "Generative AI · RAG"


# ─── normalize_service_key ──────────────────────────────────────────

def test_normalize_service_key_passthrough():
    """Plain folder keys pass through unchanged."""
    assert normalize_service_key("weaviate") == "weaviate"
    assert normalize_service_key("open-webui") == "open-webui"


def test_normalize_service_key_family_aliases():
    """Multi-container family heads normalize to the folder name."""
    assert normalize_service_key("ray-head") == "ray"
    assert normalize_service_key("spark-master") == "spark"
    assert normalize_service_key("airflow-webserver") == "airflow"


# ─── compute_always_on ──────────────────────────────────────────────

def test_compute_always_on_returns_canonical_set():
    """The three services that survive ServiceDiscovery filtering and
    must be exempt from track-skip predicates."""
    from core.config_parser import ConfigParser
    cp = ConfigParser()
    aon = compute_always_on(cp)
    assert aon == frozenset({"llm-provider", "prometheus", "grafana"})


# ─── is_in_track ────────────────────────────────────────────────────

def test_is_in_track_always_on_short_circuits():
    """Always-on service is in every track regardless of services list."""
    reg = load_tracks()
    for t in reg.tracks:
        for svc in ("llm-provider", "prometheus", "grafana"):
            assert is_in_track(t, svc, always_on=reg.always_on), \
                f"{svc} must be in-track for {t.key}"


def test_is_in_track_explicit_member():
    """Service explicitly listed is in-track."""
    reg = load_tracks()
    t = reg.by_key["gen-ai-rag"]
    assert is_in_track(t, "weaviate", always_on=reg.always_on)


def test_is_in_track_explicit_member_via_alias():
    """Family-head svc.key normalizes through aliases — Ray's head appears
    as `ray-head` from the wizard but ml-eng lists `ray` in tracks.yml."""
    reg = load_tracks()
    t = reg.by_key["ml-eng"]
    assert is_in_track(t, "ray-head", always_on=reg.always_on)
    assert is_in_track(t, "spark-master", always_on=reg.always_on)


def test_is_in_track_non_member():
    """Service not listed and not always-on is out-of-track."""
    reg = load_tracks()
    t = reg.by_key["gen-ai-rag"]
    assert not is_in_track(t, "comfyui", always_on=reg.always_on)
    assert not is_in_track(t, "spark", always_on=reg.always_on)


def test_is_in_track_all_sentinel_always_true():
    """The 'all' track has services=None → every service is in-track."""
    reg = load_tracks()
    t = reg.by_key["all"]
    for svc in ("comfyui", "spark", "ray", "weaviate", "airflow", "prometheus"):
        assert is_in_track(t, svc, always_on=reg.always_on)


# ─── format_track_list ──────────────────────────────────────────────

def test_format_track_list_contains_every_track():
    reg = load_tracks()
    out = format_track_list(reg)
    for t in reg.tracks:
        assert t.key in out
        assert t.display_name in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bootstrapper && uv run pytest tests/test_tracks.py -v`
Expected: `ModuleNotFoundError: No module named 'tracks'` (or all tests fail with ImportError).

- [ ] **Step 3: Commit**

```bash
git add bootstrapper/tests/test_tracks.py
git commit -m "test(tracks): add failing tests for tracks.py registry"
```

---

## 5. Task 4: Implement tracks.py to make tests pass

**Files:**
- Create: `bootstrapper/tracks.py`

- [ ] **Step 1: Write tracks.py**

```python
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
from typing import Iterable

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaError


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

# Multi-container service families discover via the head container's
# runtime_sc key but live under a single folder. The wizard hands the
# `service_key` arg to predicates as the head name (e.g. `ray-head`),
# while tracks.yml uses the folder name (`ray`). Normalize through here.
_FAMILY_KEY_ALIASES: dict[str, str] = {
    "ray-head": "ray",
    "spark-master": "spark",
    "airflow-webserver": "airflow",
}


def normalize_service_key(key: str) -> str:
    """Map a wizard svc.key (possibly a multi-container family head) to
    the folder name used in tracks.yml. Plain folder keys pass through."""
    return _FAMILY_KEY_ALIASES.get(key, key)


# ────────────────────────────────────────────────────────────────────
# Loader
# ────────────────────────────────────────────────────────────────────

_validator_singleton: Draft202012Validator | None = None


def _get_validator() -> Draft202012Validator:
    global _validator_singleton
    if _validator_singleton is None:
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        _validator_singleton = Draft202012Validator(schema)
    return _validator_singleton


def _canonical_service_keys() -> frozenset[str]:
    """Every services/<name>/ folder key (the universe a track can pick
    from). Used by load_tracks for cross-validation."""
    services_root = _HERE.parent / "services"
    keys: set[str] = set()
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
    """
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

    return TrackRegistry(
        tracks=tracks_tuple,
        by_key=by_key,
        always_on=always_on,
    )


# ────────────────────────────────────────────────────────────────────
# Always-on tier
# ────────────────────────────────────────────────────────────────────

def compute_always_on(config_parser) -> frozenset[str]:  # noqa: ARG001
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

    Implements the three-clause rule from the spec:
      1. Service is in the always-on tier → always in-track.
      2. Track is the "*" sentinel → always in-track.
      3. Service is explicitly listed in track.services (after family
         alias normalization) → in-track.
      4. Otherwise → out-of-track.
    """
    normalized = normalize_service_key(service_key)
    if normalized in always_on:
        return True
    if track.services is None:
        return True
    return normalized in track.services


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
    table.add_column("Services (in addition to always-on tier)")

    for t in registry.tracks:
        if t.services is None:
            svc_cell = "* (every configurable service)"
        else:
            svc_cell = ", ".join(sorted(t.services))
        table.add_row(t.key, t.display_name, svc_cell)
    console.print(table)
    console.print(
        "[dim]Always-on tier (asked in every track): LLM Engine · "
        "Prometheus · Grafana · OpenAI/Anthropic/OpenRouter cloud keys.[/dim]"
    )
    return buf.getvalue()
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `cd bootstrapper && uv run pytest tests/test_tracks.py -v`
Expected: 14 tests pass.

- [ ] **Step 3: Commit**

```bash
git add bootstrapper/tracks.py
git commit -m "feat(tracks): implement tracks.py registry loader"
```

---

## 6. Task 5: Wire `--track` + `--list-tracks` CLI flags in start.py

**Files:**
- Modify: `bootstrapper/start.py` — Click decorators between lines 1689 and 1690 (just after `--skip-hosts`); `main()` signature near line 1841; early-exit branch near top of `main()` body.

- [ ] **Step 1: Write failing CLI tests**

Create: `bootstrapper/tests/test_tracks_cli.py`

```python
"""CLI tests for --track and --list-tracks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
START_PY = REPO_ROOT / "bootstrapper" / "start.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    """Invoke start.py with isolated env so we don't accidentally touch
    docker. We rely on --list-tracks / --track foo exiting BEFORE any
    side effect."""
    return subprocess.run(
        [sys.executable, str(START_PY), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT / "bootstrapper")},
        timeout=30,
    )


def test_list_tracks_exits_zero():
    r = _run("--list-tracks")
    assert r.returncode == 0, f"--list-tracks should exit 0; stderr={r.stderr!r}"


def test_list_tracks_lists_every_track():
    r = _run("--list-tracks")
    for key in ("gen-ai-rag", "gen-ai-eng", "gen-ai-creative",
                "ml-eng", "data-eng", "all"):
        assert key in r.stdout, f"--list-tracks must mention {key}; stdout={r.stdout!r}"


def test_track_unknown_exits_two():
    r = _run("--track", "nonexistent-track")
    assert r.returncode == 2
    assert "unknown track" in r.stderr.lower()
    # Lists available tracks in the error message so the user can self-correct.
    assert "gen-ai-rag" in r.stderr
```

Run: `cd bootstrapper && uv run pytest tests/test_tracks_cli.py -v`
Expected: all three fail (`--list-tracks` and `--track` aren't recognized yet).

- [ ] **Step 2: Add the Click options to start.py**

Open `bootstrapper/start.py` and INSERT the following two `@click.option` blocks immediately AFTER line 1689 (the `--skip-hosts` option) and BEFORE line 1690 (the `--llm-provider-source` option):

```python
@click.option('--track', type=str, default=None,
              help='Pre-select a wizard profile (track) — gen-ai-rag, '
                   'gen-ai-eng, gen-ai-creative, ml-eng, data-eng, all. '
                   'Skips the wizard track-picker. In-track services are '
                   'prompted as usual; out-of-track services are disabled. '
                   'Use --list-tracks to see members.')
@click.option('--list-tracks', is_flag=True,
              help='Print the available tracks and their service '
                   'membership, then exit.')
```

- [ ] **Step 3: Add the new params to the `main(...)` signature**

In `bootstrapper/start.py` around line 1841, change the `main(...)` signature to include `track` and `list_tracks` as the second and third params (right after `base_port`):

```python
def main(base_port, track, list_tracks, cold, setup_hosts, skip_hosts, llm_provider_source,
         cloud_openai_source, cloud_anthropic_source, cloud_openrouter_source,
         # ... rest unchanged ...
```

(Order MUST match the order of `@click.option` decorators above. The two new options precede `--cold` because they're declared before `--llm-provider-source`, which itself sits before `--cloud-openai-source`, all of which appear after `--skip-hosts` in the decorator list.)

- [ ] **Step 4: Add `--list-tracks` early-exit at the top of `main()`**

In `bootstrapper/start.py`, at the very top of the `main()` body — BEFORE the `starter = GenAIStackStarter()` line (~1862) — add:

```python
    # --list-tracks is side-effect-free and runs before any other init
    # (no Supabase key gen, no env migration). Exits 0.
    if list_tracks:
        from tracks import load_tracks, format_track_list
        try:
            reg = load_tracks()
        except Exception as e:  # noqa: BLE001 — surface load errors to stderr
            print(f"Error loading tracks.yml: {e}", file=sys.stderr)
            sys.exit(2)
        print(format_track_list(reg))
        sys.exit(0)

    # Validate --track before doing anything else.
    if track is not None:
        from tracks import load_tracks
        try:
            _track_registry = load_tracks()
        except Exception as e:  # noqa: BLE001
            print(f"Error loading tracks.yml: {e}", file=sys.stderr)
            sys.exit(2)
        if track not in _track_registry.by_key:
            valid = ", ".join(t.key for t in _track_registry.tracks)
            print(
                f"Error: unknown track '{track}'. Available: {valid}.",
                file=sys.stderr,
            )
            sys.exit(2)
```

- [ ] **Step 5: Run the CLI tests to verify they pass**

Run: `cd bootstrapper && uv run pytest tests/test_tracks_cli.py -v`
Expected: all three pass.

- [ ] **Step 6: Run the full bootstrapper suite to check for regressions**

Run: `cd bootstrapper && uv run pytest -q`
Expected: previously-passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add bootstrapper/start.py bootstrapper/tests/test_tracks_cli.py
git commit -m "feat(start): wire --track + --list-tracks CLI flags"
```

---

## 7. Task 6: TDD — write the `_make_track_skip` predicate tests (failing)

**Files:**
- Create: `bootstrapper/tests/test_tracks_wizard_skip.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for _make_track_skip — the per-service skip_if_prev predicate
generator wired into bootstrapper/ui/textual/integration.py.

The predicate reads the picker selection (under
``PICKER_STEP_TITLE``) and returns True (skip) when the service is
neither in the chosen track's services nor in the always-on tier nor
in the explicit-override set.
"""

from __future__ import annotations

import pytest

from tracks import load_tracks, normalize_service_key
from ui.textual.integration import _make_track_skip, PICKER_STEP_TITLE


def test_make_track_skip_returns_callable():
    reg = load_tracks()
    skip = _make_track_skip(
        "comfyui",
        always_on=reg.always_on,
        overridden=frozenset(),
    )
    assert callable(skip)


def test_predicate_skips_off_track_service():
    """comfyui is NOT in gen-ai-rag's services list → skip = True."""
    reg = load_tracks()
    skip = _make_track_skip(
        "comfyui",
        always_on=reg.always_on,
        overridden=frozenset(),
    )
    assert skip({PICKER_STEP_TITLE: "gen-ai-rag"}) is True


def test_predicate_does_not_skip_in_track_service():
    """weaviate IS in gen-ai-rag → skip = False."""
    reg = load_tracks()
    skip = _make_track_skip(
        "weaviate",
        always_on=reg.always_on,
        overridden=frozenset(),
    )
    assert skip({PICKER_STEP_TITLE: "gen-ai-rag"}) is False


def test_predicate_does_not_skip_always_on_service():
    """LLM Engine is always-on → skip = False for every track."""
    reg = load_tracks()
    skip = _make_track_skip(
        "llm-provider",
        always_on=reg.always_on,
        overridden=frozenset(),
    )
    for t in reg.tracks:
        assert skip({PICKER_STEP_TITLE: t.key}) is False, \
            f"llm-provider must NEVER be skipped (track={t.key})"


def test_predicate_does_not_skip_overridden_service():
    """An off-track service that was explicitly overridden via
    --comfyui-source must still appear in the wizard."""
    reg = load_tracks()
    skip = _make_track_skip(
        "comfyui",
        always_on=reg.always_on,
        overridden=frozenset({"comfyui"}),
    )
    assert skip({PICKER_STEP_TITLE: "gen-ai-rag"}) is False


def test_predicate_handles_all_sentinel():
    """The 'all' track has services=None → no service ever skipped."""
    reg = load_tracks()
    for svc_key in ("comfyui", "spark", "ray-head", "weaviate"):
        skip = _make_track_skip(
            svc_key, always_on=reg.always_on, overridden=frozenset(),
        )
        assert skip({PICKER_STEP_TITLE: "all"}) is False


def test_predicate_normalizes_family_aliases():
    """ml-eng lists 'ray' in tracks.yml; the wizard hands the predicate
    'ray-head'. The predicate must normalize before lookup."""
    reg = load_tracks()
    skip = _make_track_skip(
        "ray-head",
        always_on=reg.always_on,
        overridden=frozenset(),
    )
    assert skip({PICKER_STEP_TITLE: "ml-eng"}) is False


def test_predicate_missing_selection_does_not_skip():
    """Before the user has visited the picker, the predicate must
    return False (don't pre-emptively skip)."""
    reg = load_tracks()
    skip = _make_track_skip(
        "comfyui",
        always_on=reg.always_on,
        overridden=frozenset(),
    )
    assert skip({}) is False


def test_predicate_unknown_track_does_not_skip():
    """If somehow the picker selection is garbage, fail open (don't
    skip) — a buggy predicate must not eat user prompts."""
    reg = load_tracks()
    skip = _make_track_skip(
        "comfyui",
        always_on=reg.always_on,
        overridden=frozenset(),
    )
    assert skip({PICKER_STEP_TITLE: "bogus-track"}) is False


# ─── Matrix: every track × every wizard svc.key the spec lists ─────

EXPECTED_IN_TRACK: dict[str, set[str]] = {
    "gen-ai-rag": {
        "open-webui", "weaviate", "neo4j-graph-db", "lightrag",
        "doc-processor", "tei-reranker", "searxng", "local-deep-researcher",
        # always-on:
        "llm-provider", "prometheus", "grafana",
    },
    "gen-ai-eng": {
        "open-webui", "n8n", "hermes", "openclaw", "jupyterhub", "comfyui",
        "stt-provider", "tts-provider", "searxng", "local-deep-researcher",
        "llm-provider", "prometheus", "grafana",
    },
    "gen-ai-creative": {
        "open-webui", "comfyui", "stt-provider", "tts-provider",
        "multi2vec-clip", "doc-processor",
        "llm-provider", "prometheus", "grafana",
    },
    "ml-eng": {
        "spark", "ray", "jupyterhub", "zeppelin", "open-webui", "minio",
        "tei-reranker",
        "llm-provider", "prometheus", "grafana",
    },
    "data-eng": {
        "spark", "airflow", "jupyterhub", "zeppelin", "minio", "weaviate",
        "neo4j-graph-db",
        "llm-provider", "prometheus", "grafana",
    },
}

ALL_DISCOVERED: set[str] = set().union(*EXPECTED_IN_TRACK.values())


@pytest.mark.parametrize("track_key", list(EXPECTED_IN_TRACK.keys()))
def test_track_membership_matrix(track_key: str):
    """For every (track, service) pair: predicate matches the spec table."""
    reg = load_tracks()
    expected = EXPECTED_IN_TRACK[track_key]
    for svc in ALL_DISCOVERED:
        skip = _make_track_skip(
            svc, always_on=reg.always_on, overridden=frozenset(),
        )
        is_skipped = skip({PICKER_STEP_TITLE: track_key})
        is_in_expected = svc in expected
        assert is_skipped != is_in_expected, (
            f"track={track_key} svc={svc}: "
            f"expected in_track={is_in_expected} but predicate "
            f"says skip={is_skipped}"
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd bootstrapper && uv run pytest tests/test_tracks_wizard_skip.py -v`
Expected: `ImportError: cannot import name '_make_track_skip' from 'ui.textual.integration'` — every test fails.

- [ ] **Step 3: Commit**

```bash
git add bootstrapper/tests/test_tracks_wizard_skip.py
git commit -m "test(tracks): add failing matrix test for _make_track_skip"
```

---

## 8. Task 7: Implement `_make_track_skip` + picker step in integration.py

**Files:**
- Modify: `bootstrapper/ui/textual/integration.py` — add `PICKER_STEP_TITLE` constant near top; `_make_track_skip` factory near `_option_hint`; modify `_build_steps_and_rows` signature + body around lines 137-440.

- [ ] **Step 1: Add the picker step title constant + predicate factory**

In `bootstrapper/ui/textual/integration.py`, INSERT this block AFTER line 88 (after `_option_hint` returns):

```python
# Stable title for the new track-picker step (inserted at index 0 by
# _build_steps_and_rows). Used as the selections-dict key by every
# downstream skip predicate.
PICKER_STEP_TITLE = "Track  ·  pick your profile"


def _make_track_skip(
    service_key: str,
    *,
    always_on: frozenset[str],
    overridden: frozenset[str],
):
    """Build a ``skip_if_prev`` callable for a per-service PromptStep.

    Returns True (skip) when:
        service is NOT in always_on,
        AND the picker-selected track exists and EXCLUDES the service
            (i.e. track.services is a finite set and doesn't list it),
        AND service is NOT in the override set.

    Fail-open semantics: if no picker selection has been made yet, or
    the selection doesn't resolve to a known track, return False. A
    buggy predicate must never eat user prompts.
    """
    from tracks import load_tracks, is_in_track

    # Load once at factory time; the registry is process-lifetime
    # immutable so it's safe to close over.
    try:
        _registry = load_tracks()
    except Exception:  # noqa: BLE001
        _registry = None

    def _skip(selections: dict) -> bool:
        if _registry is None:
            return False
        track_key = selections.get(PICKER_STEP_TITLE)
        if not track_key:
            return False
        track = _registry.by_key.get(track_key)
        if track is None:
            return False
        if service_key in overridden:
            return False
        return not is_in_track(track, service_key, always_on=always_on)

    return _skip
```

- [ ] **Step 2: Extend `_build_steps_and_rows` signature to accept track + override**

Find line 137 (`def _build_steps_and_rows(config_parser, hosts_manager):`) and replace it with:

```python
def _build_steps_and_rows(
    config_parser,
    hosts_manager,
    *,
    track_key: str | None = None,
    overridden_services: frozenset[str] | None = None,
):
```

The two new kwargs are passed through from `run_setup_flow` and `run_launch_flow` (see Task 8). Default `None` keeps existing direct-callers (tests) source-compatible.

- [ ] **Step 3: Insert the picker step at index 0**

In `_build_steps_and_rows` body, find the `steps: list = []` initialisation (around line 167) and the `# Base port is asked FIRST` block immediately below it. INSERT the picker step BEFORE the base-port step (so the picker becomes step 1, base port becomes step 2):

```python
    # Load track registry once; reused for the picker step + per-service
    # skip predicates.
    from tracks import load_tracks, compute_always_on
    try:
        _track_registry = load_tracks()
    except Exception:  # noqa: BLE001
        # If the registry is unloadable, fall back to no track-picker
        # (behaviour matches the pre-tracks wizard). Surface the error
        # via the existing wizard warning sink so the user can see why.
        _track_registry = None
        _wizard_warn("tracks.yml failed to load; track-picker disabled.")

    _always_on = compute_always_on(config_parser)
    _overridden = overridden_services or frozenset()

    # Picker step (only shown if the registry loaded AND no --track was
    # passed via CLI). When --track is passed (track_key != None), we
    # don't add the picker BUT we still need the selections dict to
    # carry the chosen track so the per-service skip predicates can
    # read it — emit a hidden, auto-confirmed PromptStep.
    if _track_registry is not None:
        picker_options = []
        for t in _track_registry.tracks:
            if t.services is None:
                svc_hint = "every configurable service"
            else:
                display_names = sorted(t.services)
                svc_hint = " + ".join(display_names)
            picker_options.append(PromptOption(
                value=t.key,
                label=t.display_name,
                hint=svc_hint,
                badges=[],
                description=t.description,
            ))
        # Default highlight: the CLI-passed track if present and valid,
        # else the first entry.
        if track_key and track_key in _track_registry.by_key:
            picker_default = track_key
        else:
            picker_default = _track_registry.tracks[0].key
        steps.append(PromptStep(
            title=PICKER_STEP_TITLE,
            step_index=1, step_total=total,
            heading="Which profile fits what you're building?",
            subtitle=(
                "Always-on for every track: LLM Engine + Prometheus + "
                "Grafana + cloud-provider keys."
            ),
            options=picker_options,
            default_value=picker_default,
            service_name="",
            # Skip the picker entirely when --track was set on the CLI —
            # the selection is already pinned and the user shouldn't
            # have to re-confirm.
            skip_if_prev=(
                (lambda sel, _tk=track_key: bool(_tk))
                if track_key else None
            ),
        ))
```

- [ ] **Step 4: Attach `skip_if_prev=_make_track_skip(...)` to every per-service step**

Find the `for i, svc in enumerate(services_info):` loop (around line 249). Find the `steps.append(PromptStep(...))` call inside it (around line 319) and add a `skip_if_prev` kwarg:

```python
        steps.append(PromptStep(
            title=f"{svc.display_name}  ·  source",
            step_index=i + 2, step_total=total,
            heading=f"How should {svc.display_name} run?",
            subtitle=svc.description or "",
            options=opts, default_value=default, service_name=svc.display_name,
            service_key=svc.key,
            skip_if_prev=(
                _make_track_skip(
                    svc.key,
                    always_on=_always_on,
                    overridden=_overridden,
                )
                if _track_registry is not None else None
            ),
        ))
```

- [ ] **Step 5: Run the predicate matrix test to verify it now passes**

Run: `cd bootstrapper && uv run pytest tests/test_tracks_wizard_skip.py -v`
Expected: all 14 tests pass (8 named + 6 from matrix parametrization × number of expected services per track).

- [ ] **Step 6: Commit**

```bash
git add bootstrapper/ui/textual/integration.py
git commit -m "feat(integration): add track-picker step + skip predicate"
```

---

## 9. Task 8: TDD — write the `_selections_to_args` synthesis tests

**Files:**
- Create: `bootstrapper/tests/test_tracks_selections.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the _selections_to_args track-disable synthesis pass.

When the user picks a track, every source-configurable service that is
out-of-track AND not explicitly overridden must end up with its
``*_SOURCE=disabled`` written to .env — even though its wizard step
was skipped and the selections dict never carried a value for it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ui.textual.integration import _selections_to_args, PICKER_STEP_TITLE


def _svc(key: str, display_name: str, options=("container", "disabled")):
    return SimpleNamespace(
        key=key,
        display_name=display_name,
        options=list(options),
        current_value="container",
    )


def test_off_track_service_force_disabled():
    """gen-ai-rag excludes comfyui → COMFYUI_SOURCE force-written as
    disabled in the source_args dict."""
    services_info = [
        _svc("comfyui", "ComfyUI"),
        _svc("weaviate", "Weaviate"),
    ]
    selections = {
        PICKER_STEP_TITLE: "gen-ai-rag",
        # User didn't visit the comfyui step (it was skipped).
        # User visited the weaviate step:
        "Weaviate  ·  source": "container",
    }
    source_args, _ = _selections_to_args(
        selections, services_info, current_base_port=63000, env_vars={},
    )
    assert source_args.get("comfyui_source") == "disabled", (
        f"comfyui must be force-disabled (off-track); got {source_args!r}"
    )
    assert source_args.get("weaviate_source") == "container"


def test_in_track_service_not_force_disabled():
    """weaviate is in gen-ai-rag → no synthesis. User's actual selection
    (or absence) governs the value."""
    services_info = [_svc("weaviate", "Weaviate")]
    selections = {
        PICKER_STEP_TITLE: "gen-ai-rag",
        "Weaviate  ·  source": "localhost",
    }
    source_args, _ = _selections_to_args(
        selections, services_info, current_base_port=63000, env_vars={},
    )
    assert source_args["weaviate_source"] == "localhost"


def test_all_track_no_synthesis():
    """'all' track → no service is force-disabled."""
    services_info = [_svc("comfyui", "ComfyUI"), _svc("weaviate", "Weaviate")]
    selections = {
        PICKER_STEP_TITLE: "all",
        # Neither step visited:
    }
    source_args, _ = _selections_to_args(
        selections, services_info, current_base_port=63000, env_vars={},
    )
    assert "comfyui_source" not in source_args
    assert "weaviate_source" not in source_args


def test_always_on_service_not_force_disabled():
    """LLM Engine is always-on; even if its step value is absent we
    must NOT force-write disabled."""
    services_info = [_svc("llm-provider", "LLM Engine",
                          options=("ollama-container-gpu", "none"))]
    selections = {
        PICKER_STEP_TITLE: "gen-ai-rag",
        # No "LLM Engine  ·  source" key — user skipped past it.
    }
    source_args, _ = _selections_to_args(
        selections, services_info, current_base_port=63000, env_vars={},
    )
    assert "llm_provider_source" not in source_args, (
        "Always-on LLM Engine must never get force-written; "
        f"got {source_args!r}"
    )


def test_no_picker_selection_no_synthesis():
    """If the picker step itself was skipped (e.g. no track), nothing
    is force-disabled."""
    services_info = [_svc("comfyui", "ComfyUI")]
    selections = {}  # no picker, no service step
    source_args, _ = _selections_to_args(
        selections, services_info, current_base_port=63000, env_vars={},
    )
    assert "comfyui_source" not in source_args
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd bootstrapper && uv run pytest tests/test_tracks_selections.py -v`
Expected: `test_off_track_service_force_disabled` fails (source_args doesn't include `comfyui_source`).

- [ ] **Step 3: Commit**

```bash
git add bootstrapper/tests/test_tracks_selections.py
git commit -m "test(tracks): add failing tests for selections force-disable"
```

---

## 10. Task 9: Implement `_selections_to_args` track-disable synthesis

**Files:**
- Modify: `bootstrapper/ui/textual/integration.py` — `_selections_to_args` body (lines 443-602).

- [ ] **Step 1: Add the synthesis pass at the end of `_selections_to_args`**

Find `_selections_to_args` in `bootstrapper/ui/textual/integration.py` (around line 443). After the existing `for svc in services_info:` loop (lines 466-469) that fills `source_args` from the visited selections, INSERT a new synthesis pass BEFORE the cloud-provider block:

```python
    # ─── Force-disable off-track services ────────────────────────────
    # When a track is selected, every source-configurable service that
    # is out-of-track AND not explicitly overridden gets *_SOURCE=disabled
    # force-written here. Their wizard step was skipped (track skip
    # predicate hid it), so the inner loop above didn't touch source_args
    # for them. Without this pass, .env would silently retain the user's
    # prior choice for an off-track service — defeating the track's
    # "force-disable" semantic.
    track_key = selections.get(PICKER_STEP_TITLE)
    if track_key:
        try:
            from tracks import load_tracks, is_in_track
            _reg = load_tracks()
            _track = _reg.by_key.get(track_key)
            if _track is not None and _track.services is not None:
                # "all" track → _track.services is None → no force-disable.
                for svc in services_info:
                    if is_in_track(_track, svc.key, always_on=_reg.always_on):
                        continue
                    cli_key = svc.key.replace("-", "_") + "_source"
                    # Only synthesize if the user didn't visit the step
                    # (override path stays untouched).
                    if cli_key not in source_args:
                        source_args[cli_key] = "disabled"
        except Exception:  # noqa: BLE001
            # Track-registry load failure must not block the wizard.
            pass
```

- [ ] **Step 2: Run the new tests to verify they now pass**

Run: `cd bootstrapper && uv run pytest tests/test_tracks_selections.py -v`
Expected: all 5 tests pass.

- [ ] **Step 3: Run the full bootstrapper suite to check for regressions**

Run: `cd bootstrapper && uv run pytest -q`
Expected: all previously-passing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add bootstrapper/ui/textual/integration.py
git commit -m "feat(integration): force-disable off-track services in selections"
```

---

## 11. Task 10: Compute override set + emit warning loop in start.py

**Files:**
- Modify: `bootstrapper/start.py` — after the `source_args` dict construction around line 1984.

- [ ] **Step 1: Write a failing CLI warning test**

Append to `bootstrapper/tests/test_tracks_cli.py`:

```python
def test_off_track_flag_emits_warning():
    """--track gen-ai-rag --comfyui-source container-gpu must emit
    a stderr warning since comfyui is excluded from gen-ai-rag."""
    r = _run(
        "--track", "gen-ai-rag",
        "--comfyui-source", "container-gpu",
        # Stop before the wizard launches — we just want the warning.
        # Use --list-tracks to short-circuit BEFORE wizard but AFTER
        # the override-warning loop. We need to refactor the warning to
        # fire before --list-tracks exit, OR add a no-op flag for tests.
        # Use a real flag combo and check the warning is on stderr; we
        # accept any non-zero/zero exit since the wizard would normally
        # take over.
        "--list-tracks",
    )
    # The warning fires when --track is set AND any off-track --*-source
    # flag is passed. Even though --list-tracks exits early, the warning
    # check (Task 10 step 2) runs first.
    assert "comfyui" in r.stderr.lower()
    assert "gen-ai-rag" in r.stderr


def test_all_track_suppresses_warning():
    """--track all + any --*-source flag → no warning (all includes
    everything)."""
    r = _run(
        "--track", "all",
        "--comfyui-source", "container-gpu",
        "--list-tracks",
    )
    assert "overrides the all track" not in r.stderr.lower()


def test_no_track_suppresses_warning():
    """Bare --comfyui-source with no --track → no warning."""
    r = _run(
        "--comfyui-source", "container-gpu",
        "--list-tracks",
    )
    assert "overrides the" not in r.stderr.lower()
```

Run: `cd bootstrapper && uv run pytest tests/test_tracks_cli.py::test_off_track_flag_emits_warning -v`
Expected: FAIL (no warning emitted yet).

- [ ] **Step 2: Add the override warning loop to start.py**

In `bootstrapper/start.py`, find the `source_args = {...}` dict assembly around line 1984. Immediately AFTER the dict closes (around line 2012), and BEFORE the `if ray_worker_count is not None:` block, INSERT:

```python
        # ─── Track override warnings + override-set computation ──────
        # When --track is set, any explicit --*-source flag whose service
        # is out-of-track gets an advisory stderr warning. The override
        # set is also handed to the wizard step builder so the
        # corresponding service prompts re-appear (flag wins; predicate
        # respects the override).
        overridden_services: set[str] = set()
        if track is not None:
            from tracks import load_tracks, compute_always_on, is_in_track
            try:
                _reg = load_tracks()
            except Exception as e:  # noqa: BLE001
                print(f"Warning: tracks.yml failed to load: {e}",
                      file=sys.stderr)
                _reg = None
            if _reg is not None:
                _track = _reg.by_key.get(track)
                _always_on = compute_always_on(starter.config_parser)
                if _track is not None and _track.services is not None:
                    # "all" track → never warn (services=None).
                    for cli_key, value in source_args.items():
                        if value is None:
                            continue
                        # cli_key looks like 'comfyui_source'; derive svc key.
                        svc_key = cli_key.removesuffix("_source").replace("_", "-")
                        if is_in_track(_track, svc_key,
                                       always_on=_always_on):
                            continue
                        overridden_services.add(svc_key)
                        # Look up display name from topology rows for the
                        # warning text; fall back to the svc key.
                        from services.topology import get_topology
                        _topo = get_topology()
                        derived_var = svc_key.upper().replace("-", "_") + "_SOURCE"
                        display = svc_key
                        for r in _topo.rows:
                            if r.source_var == derived_var:
                                display = r.display_name
                                break
                        print(
                            f"[warn] --{cli_key.replace('_', '-')} "
                            f"{value} overrides the {track} track, "
                            f"which excludes {display}. Enabling "
                            f"{display} anyway.",
                            file=sys.stderr,
                        )
```

- [ ] **Step 3: Move the override loop ABOVE the `--list-tracks` early exit so warnings fire even on list-tracks**

WAIT — re-read Step 2: the warning logic depends on `source_args` being built, which happens around line 1984, AFTER `if list_tracks: ... sys.exit(0)` (Task 5 step 4). The test in Step 1 (`test_off_track_flag_emits_warning`) uses `--list-tracks` to short-circuit before the wizard, but expects the warning to fire. That's incompatible with the current placement.

Reconcile: make the warning loop a separate early pass that runs BEFORE `if list_tracks:` but AFTER Click parses. Refactor: move just the warning-emission block above the `if list_tracks:` block (Task 5 Step 4). The block needs `source_args`-equivalent data but only the kwarg names of the source flags — so build a tiny inline mapping from kwarg name → value just for the warning:

REPLACE Step 2's block with this — and place it AT THE TOP of `main()`, immediately AFTER the Click-arg validation (Task 5 step 4's `--track` validation) and BEFORE the `if list_tracks:` early exit:

```python
    # ─── Track override warnings ─────────────────────────────────────
    # Fires when --track is set AND any explicit --*-source flag picks
    # a service that's out-of-track. Runs BEFORE --list-tracks early
    # exit so the warning surfaces even when the user listed tracks.
    if track is not None:
        try:
            from tracks import load_tracks as _load_tracks_for_warn
            from tracks import compute_always_on as _compute_aon_for_warn
            from tracks import is_in_track as _is_in_track_for_warn
            _reg_w = _load_tracks_for_warn()
        except Exception:  # noqa: BLE001
            _reg_w = None
        if _reg_w is not None:
            _track_w = _reg_w.by_key.get(track)
            _aon_w = _compute_aon_for_warn(None)
            if _track_w is not None and _track_w.services is not None:
                # Map of Click kwarg → value, restricted to the
                # source-style flags.
                _flag_values = {
                    'llm_provider_source': llm_provider_source,
                    'comfyui_source': comfyui_source,
                    'weaviate_source': weaviate_source,
                    'minio_source': minio_source,
                    'n8n_source': n8n_source,
                    'searxng_source': searxng_source,
                    'jupyterhub_source': jupyterhub_source,
                    'open_web_ui_source': open_web_ui_source,
                    'local_deep_researcher_source': local_deep_researcher_source,
                    'stt_provider_source': stt_provider_source,
                    'tts_provider_source': tts_provider_source,
                    'doc_processor_source': doc_processor_source,
                    'openclaw_source': openclaw_source,
                    'hermes_source': hermes_source,
                    'lightrag_source': lightrag_source,
                    'tei_reranker_source': tei_reranker_source,
                    'neo4j_graph_db_source': neo4j_graph_db_source,
                    'multi2vec_clip_source': multi2vec_clip_source,
                    'ray_source': ray_source,
                    'prometheus_source': prometheus_source,
                    'grafana_source': grafana_source,
                    'spark_source': spark_source,
                    'zeppelin_source': zeppelin_source,
                    'airflow_source': airflow_source,
                }
                for cli_key, value in _flag_values.items():
                    if value is None:
                        continue
                    svc_key = cli_key.removesuffix("_source").replace("_", "-")
                    if _is_in_track_for_warn(_track_w, svc_key,
                                             always_on=_aon_w):
                        continue
                    print(
                        f"[warn] --{cli_key.replace('_', '-')} "
                        f"{value} overrides the {track} track, "
                        f"which excludes {svc_key}. Enabling "
                        f"{svc_key} anyway.",
                        file=sys.stderr,
                    )
```

(Cloud provider toggles `cloud_openai_source`, `cloud_anthropic_source`, `cloud_openrouter_source` are NOT in this map because cloud-keys are filtered upstream and never reach the track-skip predicate — they're effectively always-on. A `--cloud-openai-source enabled` flag should never emit a track warning.)

- [ ] **Step 4: Save the overridden_services SET for the wizard builder**

In `start.py`, KEEP the original logic from Step 2 (computing `overridden_services: set[str]` from the warning-time mapping) but place it RIGHT BEFORE the wizard launch (i.e. before the call into `run_setup_flow` / `run_launch_flow`). Find the existing dispatch — `starter.start_setup_flow(...)` or similar that calls `run_setup_flow` — and emit:

```python
        # Threaded into the wizard step builder so off-track flag
        # overrides re-enable the corresponding service prompts.
        overridden_services = set()
        if track is not None:
            try:
                from tracks import load_tracks as _lt
                from tracks import compute_always_on as _ca
                from tracks import is_in_track as _iit
                _rg = _lt()
                _tk = _rg.by_key.get(track) if _rg else None
                _ao = _ca(None) if _rg else frozenset()
                _flag_values = {
                    'comfyui_source': comfyui_source,
                    'weaviate_source': weaviate_source,
                    'minio_source': minio_source,
                    'n8n_source': n8n_source,
                    'searxng_source': searxng_source,
                    'jupyterhub_source': jupyterhub_source,
                    'open_web_ui_source': open_web_ui_source,
                    'local_deep_researcher_source': local_deep_researcher_source,
                    'stt_provider_source': stt_provider_source,
                    'tts_provider_source': tts_provider_source,
                    'doc_processor_source': doc_processor_source,
                    'openclaw_source': openclaw_source,
                    'hermes_source': hermes_source,
                    'lightrag_source': lightrag_source,
                    'tei_reranker_source': tei_reranker_source,
                    'neo4j_graph_db_source': neo4j_graph_db_source,
                    'multi2vec_clip_source': multi2vec_clip_source,
                    'ray_source': ray_source,
                    'spark_source': spark_source,
                    'zeppelin_source': zeppelin_source,
                    'airflow_source': airflow_source,
                }
                if _tk is not None and _tk.services is not None:
                    for cli_key, value in _flag_values.items():
                        if value is None:
                            continue
                        svc_key = cli_key.removesuffix("_source").replace("_", "-")
                        if not _iit(_tk, svc_key, always_on=_ao):
                            overridden_services.add(svc_key)
            except Exception:  # noqa: BLE001
                overridden_services = set()
```

This block can sit right before the `run_setup_flow(...)` / `run_launch_flow(...)` calls — the precise insertion site depends on the dispatcher's exact structure. Use grep to find the call site:
```bash
grep -n "run_setup_flow\|run_launch_flow" bootstrapper/start.py
```

Pass `track=track` and `overridden_services=frozenset(overridden_services)` as kwargs at each call site. Task 11 makes both `run_setup_flow` and `run_launch_flow` accept these new kwargs.

- [ ] **Step 5: Synthesize `disabled` at the start.py level too**

The wizard path's `_selections_to_args` (Task 9) handles the TUI case. But `--no-tui --track gen-ai-rag` skips the wizard entirely, and `run_launch_flow` (`./start.sh --track X --some-flag Y` in TUI mode) also bypasses `_selections_to_args`. Both paths need an equivalent synthesis at start.py level.

Immediately AFTER the `overridden_services` computation block from Step 4, ADD a parallel synthesis pass that writes `*_SOURCE=disabled` into `source_args` for every off-track configurable service that is NOT in `overridden_services`:

```python
        # Mirror _selections_to_args (TUI wizard path): force-disable
        # every off-track configurable service in source_args so
        # --no-tui and run_launch_flow honor the track too. Overridden
        # services keep their CLI-supplied value (flag wins).
        if track is not None and overridden_services is not None:
            try:
                from tracks import load_tracks as _ld
                from tracks import is_in_track as _ii
                _rg2 = _ld()
                _t2 = _rg2.by_key.get(track)
                if _t2 is not None and _t2.services is not None:
                    # 'all' track → services is None → no synthesis.
                    for cli_key in list(source_args.keys()):
                        if cli_key.startswith("cloud_"):
                            continue  # cloud keys are always-on
                        svc_key = cli_key.removesuffix("_source").replace("_", "-")
                        if _ii(_t2, svc_key, always_on=_rg2.always_on):
                            continue
                        if svc_key in overridden_services:
                            continue
                        # User didn't override and svc is off-track —
                        # force-disable (only if no explicit value).
                        if source_args.get(cli_key) is None:
                            source_args[cli_key] = "disabled"
            except Exception:  # noqa: BLE001
                pass
```

- [ ] **Step 6: Run the override warning tests**

Run: `cd bootstrapper && uv run pytest tests/test_tracks_cli.py::test_off_track_flag_emits_warning tests/test_tracks_cli.py::test_all_track_suppresses_warning tests/test_tracks_cli.py::test_no_track_suppresses_warning -v`
Expected: all three pass.

- [ ] **Step 7: Commit**

```bash
git add bootstrapper/start.py bootstrapper/tests/test_tracks_cli.py
git commit -m "feat(start): warn on off-track --*-source overrides"
```

---

## 12. Task 11: Thread `track` + `overridden_services` through `run_setup_flow` / `run_launch_flow`

**Files:**
- Modify: `bootstrapper/ui/textual/integration.py` — `run_setup_flow` and `run_launch_flow` signatures; pass through to `_build_steps_and_rows`; seed initial selections.
- Modify: `bootstrapper/ui/textual/screens/wizard_screen.py` — add `prefilled_selections: dict | None = None` kwarg to `WizardScreen.__init__`; merge into `self._selections` before mount.

- [ ] **Step 1: Add `prefilled_selections` to WizardScreen**

In `bootstrapper/ui/textual/screens/wizard_screen.py`, find the `def __init__(` block (around line 168) and add a new kwarg:

```python
        prefilled_selections: dict | None = None,
```

Then find where `self._selections` is initialized inside `__init__`. If it's not present, search for `self._selections = ` and the first place selections is used. Initialize like:

```python
        self._selections: dict = dict(prefilled_selections or {})
```

(Adjust to merge with whatever existing init pattern looks like — search before editing.)

- [ ] **Step 2: Add `track` + `overridden_services` kwargs to `run_setup_flow`**

In `bootstrapper/ui/textual/integration.py`, find `def run_setup_flow(` (around line 608). Replace its signature:

```python
def run_setup_flow(
    config_parser, hosts_manager, *,
    starter=None,
    no_port_migrate: bool = False,
    track: str | None = None,
    overridden_services: frozenset[str] | None = None,
) -> int:
```

Update the `_build_steps_and_rows` call inside (around line 627) to pass through:

```python
    steps, rows, services_info, current_base_port, state, cloud_summaries = (
        _build_steps_and_rows(
            config_parser, hosts_manager,
            track_key=track,
            overridden_services=overridden_services or frozenset(),
        )
    )
```

Update the `WizardScreen(...)` construction inside `_SetupApp.on_mount` (around line 685) to seed the picker:

```python
            self.push_screen(WizardScreen(
                steps=steps, services=rows, brand=brand,
                starter=starter,
                stack_options_resolver=_resolve,
                on_base_port_change=_recompute_ports,
                resolve_port_for_service=_resolve_port_for_service,
                cloud_apis=cloud_summaries,
                prefilled_selections=(
                    {PICKER_STEP_TITLE: track} if track else None
                ),
            ))
```

- [ ] **Step 3: Add the same kwargs to `run_launch_flow`**

In `run_launch_flow` (around line 705), apply the parallel changes: add `track` + `overridden_services` kwargs, pass through to `_build_steps_and_rows`, seed `prefilled_selections` on the `WizardScreen` inside `_LaunchApp`.

- [ ] **Step 4: Update start.py to pass `track=track, overridden_services=...` at every dispatch site**

In `bootstrapper/start.py`, find every call to `run_setup_flow(...)` and `run_launch_flow(...)`:
```bash
grep -n "run_setup_flow\|run_launch_flow" bootstrapper/start.py
```

At each call site, append:
```python
            track=track,
            overridden_services=frozenset(overridden_services),
```

(`overridden_services` is the set computed in Task 10 Step 4; ensure that block runs BEFORE the dispatch.)

- [ ] **Step 5: Run all wizard-affected tests**

Run: `cd bootstrapper && uv run pytest tests/test_tracks_wizard_skip.py tests/test_tracks_selections.py tests/test_wizard_app_discovery.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add bootstrapper/ui/textual/integration.py bootstrapper/ui/textual/screens/wizard_screen.py bootstrapper/start.py
git commit -m "feat(wizard): thread track + override set into builders"
```

---

## 13. Task 12: Pre-launch summary banner + Track: line in InfoPanel

**Files:**
- Modify: `bootstrapper/ui/textual/widgets/info_box.py` — `InfoBoxState` dataclass gains an optional `track_label: str | None = None`.
- Modify: `bootstrapper/ui/textual/screens/wizard_screen.py` — `_refresh_info_panel` passes `track_label=self._track_display_name` when present.
- Modify: `bootstrapper/ui/textual/integration.py` — derive the display label and thread it through.

- [ ] **Step 1: Add `track_label` to InfoBoxState + render**

In `bootstrapper/ui/textual/widgets/info_box.py`, find `class InfoBoxState` (around line 85). Add a field:

```python
@dataclass
class InfoBoxState:
    # ... existing fields ...
    track_label: str | None = None
```

Find the `InfoBoxFooter` render path (search for `track` or look near the cloud_apis-count line). Insert a single-line render before the services-count line:

```python
        if state.track_label:
            lines.append(f"Track: {state.track_label}")
```

(Adapt to the actual render shape — InfoBoxFooter may use `Static.update(...)` or a render method. Search for `cloud_apis` in `info_box.py` to find the rendering site.)

- [ ] **Step 2: Pass `track_label` from WizardScreen**

In `bootstrapper/ui/textual/screens/wizard_screen.py`, add `track_display_name: str | None = None` to `__init__` (alongside `prefilled_selections` from Task 11). Store on `self._track_display_name`.

Update `_refresh_info_panel` (around line 736) to forward:

```python
    def _refresh_info_panel(self) -> None:
        summaries = [...]  # unchanged
        self._info_panel.update_state(
            InfoBoxState(
                brand=self._brand,
                services=summaries,
                cloud_apis=self._cloud_apis,
                track_label=self._track_display_name,
            )
        )
```

- [ ] **Step 3: Derive the label in integration.py and thread it**

In `run_setup_flow` and `run_launch_flow`, derive the label after loading the track registry:

```python
    track_display_name: str | None = None
    if track:
        from tracks import load_tracks as _lt
        try:
            _r = _lt()
            _t = _r.by_key.get(track)
            track_display_name = _t.display_name if _t else None
        except Exception:  # noqa: BLE001
            pass
```

Pass `track_display_name=track_display_name` into the `WizardScreen(...)` constructor in BOTH `run_setup_flow` and `run_launch_flow`.

- [ ] **Step 4: Manual smoke check (no automated test for the visual line)**

Run: `cd bootstrapper && uv run pytest -q`
Expected: previously-passing tests still pass.

(No new test for the visual: the visible-track-line is a one-liner additive change and the wizard's screen is tested via the existing widget tests. We rely on the in-terminal verification step at the end of the plan.)

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/ui/textual/widgets/info_box.py bootstrapper/ui/textual/screens/wizard_screen.py bootstrapper/ui/textual/integration.py
git commit -m "feat(wizard): show Track: <name> in InfoPanel"
```

---

## 14. Task 13: Dim off-track service rows in state_builder

**Files:**
- Modify: `bootstrapper/ui/state.py` — add `off_track: bool = False` to `ServiceEntry`.
- Modify: `bootstrapper/ui/state_builder.py` — `build_app_state` accepts `in_track` + `overridden`; sets `off_track=True` for matching rows.
- Modify: `bootstrapper/ui/textual/integration.py` — call `build_app_state(..., in_track=...)`.

- [ ] **Step 1: Add `off_track` field on ServiceEntry**

In `bootstrapper/ui/state.py`, find `@dataclass class ServiceEntry:` (around line 14). Add:

```python
    off_track: bool = False     # renderer dims + adds "disabled (off-track)" tag
```

- [ ] **Step 2: Update build_app_state**

In `bootstrapper/ui/state_builder.py`, modify `build_app_state` signature (around line 133):

```python
def build_app_state(
    config_parser: ConfigParser,
    hosts_manager=None,  # noqa: ARG001
    *,
    in_track: "Callable[[str], bool] | None" = None,
    overridden: frozenset[str] | None = None,
) -> AppState:
```

Inside the row loop (around line 152), pass `off_track`:

```python
    overridden = overridden or frozenset()
    services = []
    for r in _get_topology().rows:
        source = service_sources.get(r.source_var, env.get(r.source_var, "container"))
        # off_track is true when an in_track predicate is supplied AND
        # the row is not in-track AND not explicitly overridden.
        is_off = False
        if in_track is not None:
            if not in_track(r.display_name) and r.display_name not in overridden:
                is_off = True
        services.append(ServiceEntry(
            name=r.display_name,
            port=resolve_port(r.display_name, source, r.port_var, env),
            source=source,
            alias=r.alias,
            category=r.category,
            pending=False,
            off_track=is_off,
        ))
```

Note: `in_track` here takes a `display_name`, NOT a service key. The wizard-side `services_info` carries both — pass the right one. (Alternatively, pre-build a frozenset of off-track display names and pass that. Choose the predicate form to match the call site.)

- [ ] **Step 3: Wire up the predicate in integration.py**

In `bootstrapper/ui/textual/integration.py::_build_steps_and_rows` (around line 404 where `state = build_app_state(config_parser, hosts_manager)` is called), construct the predicate:

```python
    _off_track_display_names: frozenset[str] = frozenset()
    if _track_registry is not None and track_key:
        _track_obj = _track_registry.by_key.get(track_key)
        if _track_obj is not None and _track_obj.services is not None:
            from tracks import is_in_track as _iit
            _off_track_display_names = frozenset(
                svc.display_name for svc in services_info
                if (not _iit(_track_obj, svc.key, always_on=_always_on))
                and svc.key not in _overridden
            )

    def _in_track_display(display_name: str) -> bool:
        return display_name not in _off_track_display_names

    state = build_app_state(
        config_parser, hosts_manager,
        in_track=_in_track_display if (track_key and _track_registry) else None,
        overridden=frozenset(),  # already accounted for above
    )
```

- [ ] **Step 4: Render the off-track flag in the service table widget**

In `bootstrapper/ui/textual/widgets/service_table.py`, find where `category` colour is applied to a row. Add a fallback when `off_track` is True: use a dimmed variant of the category color and append `disabled (off-track)` to the source column. (The exact render path is widget-specific; grep `category` in that file to locate.)

NOTE: The renderer change is purely cosmetic and has no test. After this task, run the wizard manually with `./start.sh --track gen-ai-rag` and verify the right-pane service rows for ComfyUI / Spark / Airflow appear dimmed.

- [ ] **Step 5: Run the full bootstrapper suite**

Run: `cd bootstrapper && uv run pytest -q`
Expected: all previously-passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add bootstrapper/ui/state.py bootstrapper/ui/state_builder.py bootstrapper/ui/textual/integration.py bootstrapper/ui/textual/widgets/service_table.py
git commit -m "feat(wizard): dim off-track services in the live overview"
```

---

## 15. Task 14: Add the audit script

**Files:**
- Create: `scripts/check-track-membership.py`

- [ ] **Step 1: Write the audit script**

```python
#!/usr/bin/env python3
"""Audit: every service in bootstrapper/tracks.yml exists as a
services/<name>/ folder, AND every source-configurable service appears
in at least one track other than 'all'.

Run from the repo root. Exits 0 on success, 1 on drift.
"""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# Allow `from tracks import ...`
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))


def main() -> int:
    from tracks import load_tracks
    from core.config_parser import ConfigParser
    from wizard.service_discovery import ServiceDiscovery

    try:
        reg = load_tracks()
    except Exception as e:
        print(f"FAIL: tracks.yml failed to load: {e}", file=sys.stderr)
        return 1

    # Every configurable service must appear in at least one
    # non-"all" track.
    cp = ConfigParser()
    configurable_svc_keys: set[str] = set()
    for svc in ServiceDiscovery(cp).discover():
        from tracks import normalize_service_key
        configurable_svc_keys.add(normalize_service_key(svc.key))

    # Always-on keys are intentionally not in tracks.yml.
    always_on = reg.always_on

    union = set()
    for t in reg.tracks:
        if t.key == "all":
            continue
        if t.services is None:
            continue
        union |= set(t.services)

    missing = configurable_svc_keys - union - always_on
    if missing:
        print(
            f"FAIL: configurable services NOT in any non-'all' track: "
            f"{sorted(missing)}.\nAdd them to at least one track or "
            f"document the omission.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: {len(reg.tracks)} tracks, {len(configurable_svc_keys)} "
        f"configurable services. Coverage clean."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Mark executable and run**

```bash
chmod +x scripts/check-track-membership.py
python scripts/check-track-membership.py
```
Expected: `OK: 6 tracks, NN configurable services. Coverage clean.`

- [ ] **Step 3: Hook into the services-lint CI job**

Find the workflow file: `grep -l "audit\|check-doc-links" .github/workflows/`.

Open the matching workflow (likely `.github/workflows/services-lint.yml`) and add a step under the same job that currently runs `scripts/check-doc-links.py`:

```yaml
      - name: Track-membership audit
        run: python scripts/check-track-membership.py
```

- [ ] **Step 4: Commit**

```bash
git add scripts/check-track-membership.py .github/workflows/services-lint.yml
git commit -m "ci(tracks): add track-membership audit + CI step"
```

---

## 16. Task 15: Documentation — CLAUDE.md + README.md

**Files:**
- Modify: `CLAUDE.md` — add "Tracks" subsection under "Architecture".
- Modify: `README.md` — add "Quickstart by track" section.

- [ ] **Step 1: Update CLAUDE.md**

Find the `## Architecture` section in `CLAUDE.md`. After the `### SOURCE-Based Configuration System` block (or the most appropriate sibling subsection), insert:

```markdown
### Tracks

`bootstrapper/tracks.yml` defines named profiles (`gen-ai-rag`, `gen-ai-eng`,
`gen-ai-creative`, `ml-eng`, `data-eng`, `all`). Each track lists a subset of
source-configurable services the wizard should prompt for; out-of-track services
are force-disabled (`*_SOURCE=disabled`) at the end of the flow. The always-on
tier (LLM Engine + Prometheus + Grafana + cloud-provider keys) is implicit and
applies to every track.

- Pass `--track <key>` to pre-select on the CLI.
- Pass `--list-tracks` to print the registry and exit.
- Explicit `--<svc>-source` flags override the track with an advisory warning.

Source of truth: `bootstrapper/tracks.yml` + `bootstrapper/tracks.py` (registry
loader + predicates). The wizard step builder in
`bootstrapper/ui/textual/integration.py` consumes them via `_make_track_skip`.
```

- [ ] **Step 2: Update README.md**

Find the existing "Quickstart" or "Getting Started" section in `README.md`. Add a "Quickstart by track" subsection:

```markdown
### Quickstart by track

For RAG-focused work:
```bash
./start.sh --track gen-ai-rag
```

For agentic-app engineering:
```bash
./start.sh --track gen-ai-eng
```

Other tracks: `gen-ai-creative`, `ml-eng`, `data-eng`. Run `./start.sh
--list-tracks` to see service membership for each. Use `--track all` for
the full wizard (every configurable service).
```

- [ ] **Step 3: Verify the docs-drift gate is still green**

Run: `cd bootstrapper && uv run pytest tests/test_docs_drift.py -q`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs(tracks): document --track and tracks.yml registry"
```

---

## 17. Task 16: Full-suite verification + in-terminal smoke

**Files:** (no source changes)

- [ ] **Step 1: Run the full bootstrapper test suite**

Run: `cd bootstrapper && uv run pytest -q`
Expected: every test passes (including the new tracks tests).

- [ ] **Step 2: Run the audit scripts**

```bash
PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all --check
python scripts/check-track-membership.py
python scripts/check_doc_links.py
```
Expected: all exit 0.

- [ ] **Step 3: In-terminal smoke — TUI picker visible**

Per `memory/feedback_visual_iteration.md` and `memory/feedback_verify_via_production_path.md`, manually run the wizard:

```bash
./start.sh
```

Verify:
- Step 1 of N is the track picker.
- All 6 tracks listed, `gen-ai-rag` highlighted by default.
- The "Always-on for every track: ..." subtitle is shown.
- Pressing Enter on `gen-ai-rag` advances to step 2 (Base port).
- Subsequent service prompts iterate ONLY the gen-ai-rag list + the
  always-on tier (LLM Engine, Prometheus, Grafana, cloud keys).
- ComfyUI, Spark, Airflow, etc. do NOT appear as prompts.
- Pressing `Esc` from step 2 returns to the picker.
- Switching to `ml-eng` then advancing now shows Spark/Ray/Zeppelin etc.

Quit without launching the stack (Ctrl+C).

- [ ] **Step 4: In-terminal smoke — CLI flag path**

```bash
./start.sh --track gen-ai-rag --comfyui-source container-gpu
```

Verify:
- Stderr shows the warning: `[warn] --comfyui-source container-gpu overrides the gen-ai-rag track, which excludes comfyui. Enabling comfyui anyway.`
- Wizard opens directly at step 2 (Base port) — picker is skipped.
- ComfyUI prompt appears in the flow (override re-enabled the prompt).
- Other off-track services (Spark, Airflow, etc.) are still skipped.

Quit without launching (Ctrl+C).

- [ ] **Step 5: In-terminal smoke — `--list-tracks`**

```bash
./start.sh --list-tracks
```

Verify:
- Prints a Rich-formatted table with all 6 tracks + their service lists.
- Exits 0 immediately, no Supabase key gen, no docker call.

- [ ] **Step 6: Push branch (NOT main) and open PR**

Per `memory/project_main_branch_protection.md`, never push directly to main. Open a feature branch worktree first (or push the current main-divergent commits to a feature branch):

```bash
git push -u origin HEAD:feat/setup-wizard-tracks
gh pr create --title "feat: setup-wizard tracks" \
  --body "$(cat <<'EOF'
## Summary
- New \`bootstrapper/tracks.yml\` registry with 6 predefined wizard profiles
- New \`--track\` + \`--list-tracks\` CLI flags
- Wizard step 1 is a track picker; off-track services force-disabled
- \`--*-source\` flags override the track with stderr warning

## Test plan
- [ ] \`cd bootstrapper && uv run pytest -q\` passes
- [ ] \`./start.sh --list-tracks\` prints the registry
- [ ] \`./start.sh --track gen-ai-rag\` skips picker, prompts only RAG services
- [ ] \`./start.sh --track gen-ai-rag --comfyui-source container-gpu\` emits warning and re-enables ComfyUI prompt
- [ ] All 4 \`services-lint\` CI checks green
EOF
)"
```

Wait for the four required CI checks (`Manifest lint + unit tests`, `Compose merge + byte-equivalence + source-permutation matrix`, `Docs drift + audit scripts`, `Build-validation (Dockerfile + requirements.txt installability)`) to pass. Then per `memory/project_branch_workflow.md`:

```bash
gh pr merge --squash --delete-branch
```

---

## 18. Done.

Per `memory/feedback_post_ship_review.md`, expect a "full review" ask right after merge — pre-empt with a tiered self-audit of the diff (data-correctness > UX > docs > polish).

Track-related follow-ups deferred per spec §13: per-track default `*_SOURCE` values, user-defined tracks, `--track save-as`, sticky persistence. Open issues if needed.
