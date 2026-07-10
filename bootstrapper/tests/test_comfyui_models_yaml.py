"""Tests for the ComfyUI curated catalog YAML + loader (Part C1).

Verifies:
  1. services/comfyui/models.yaml passes bootstrapper/schemas/comfyui-models.schema.json.
  2. The YAML-loaded curated entries + fallback entries faithfully reproduce the
     pre-C1 characterization snapshot in fixtures/comfyui_curated_snapshot.json
     (every field, including download-load-bearing ones: url, filename, sha256,
     target_dir).
  3. assemble_wizard_catalog() still returns a non-empty merged catalog (offline-
     tolerant: HF/civitai scrape may be empty in CI).
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from utils.comfyui_library import (
    ComfyUILibraryEntry,
    VALID_CATEGORIES,
    CATEGORY_TARGET_DIR,
    _find_comfyui_yaml,
    list_curated,
    list_fallback,
    assemble_wizard_catalog,
)


# ─── Paths ───────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_YAML_PATH = _REPO_ROOT / "services" / "comfyui" / "models.yaml"
_SCHEMA_PATH = _REPO_ROOT / "bootstrapper" / "schemas" / "comfyui-models.schema.json"
_SNAPSHOT_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "comfyui_curated_snapshot.json"
)


# ─── Helper ──────────────────────────────────────────────────────────────────

def _entry_to_comparable(e: ComfyUILibraryEntry) -> dict:
    """Convert an entry to a JSON-compatible dict for snapshot comparison.

    Excludes 'pulled' (wizard-time computed) and 'source' (set by the loader,
    differs between curated/fallback). Those are tested separately.
    """
    return json.loads(json.dumps(dataclasses.asdict(e)))


# ─── Schema validation ───────────────────────────────────────────────────────

def test_yaml_file_exists():
    assert _YAML_PATH.is_file(), f"services/comfyui/models.yaml not found at {_YAML_PATH}"


def test_schema_file_exists():
    assert _SCHEMA_PATH.is_file(), (
        f"bootstrapper/schemas/comfyui-models.schema.json not found at {_SCHEMA_PATH}"
    )


def test_yaml_passes_schema():
    """services/comfyui/models.yaml must validate against comfyui-models.schema.json."""
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    data = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8"))
    # jsonschema raises if invalid; no assertion needed
    jsonschema.validate(instance=data, schema=schema)


def test_yaml_has_expected_entry_count():
    """Curated YAML must contain exactly 15 entries (snapshot count)."""
    data = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8"))
    assert len(data["models"]) == 15, (
        f"Expected 15 curated entries (snapshot count); got {len(data['models'])}"
    )


def test_yaml_all_required_fields_present():
    """Every YAML entry must have name, category, url."""
    data = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8"))
    for idx, entry in enumerate(data["models"]):
        for field in ("name", "category", "url"):
            assert field in entry, (
                f"models.yaml entry [{idx}] ({entry.get('name', '?')}) missing '{field}'"
            )


def test_yaml_all_categories_valid():
    data = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8"))
    for entry in data["models"]:
        assert entry["category"] in VALID_CATEGORIES, (
            f"Entry '{entry['name']}' has unknown category '{entry['category']}'"
        )


# ─── Loader faithfulness (snapshot comparison) ───────────────────────────────

def test_snapshot_file_exists():
    assert _SNAPSHOT_PATH.is_file(), (
        f"Snapshot fixture not found at {_SNAPSHOT_PATH}"
    )


def test_curated_count_matches_snapshot():
    snapshot = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    curated = list_curated()
    assert len(curated) == snapshot["curated_count"], (
        f"list_curated() returned {len(curated)} entries; "
        f"snapshot expects {snapshot['curated_count']}"
    )


def test_fallback_count_matches_snapshot():
    snapshot = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    fallback = list_fallback()
    assert len(fallback) == snapshot["fallback_count"], (
        f"list_fallback() returned {len(fallback)} entries; "
        f"snapshot expects {snapshot['fallback_count']}"
    )


def test_curated_entries_match_snapshot_field_by_field():
    """Every field of every curated entry must match the snapshot exactly.

    This is the faithful-translation proof: the YAML loaded by list_curated()
    produces the same ComfyUILibraryEntry objects as the hardcoded
    _CURATED_ENTRIES did before C1.
    """
    snapshot = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    snap_curated = [e for e in snapshot["entries"] if e["source"] == "curated"]
    curated = list_curated()

    assert len(curated) == len(snap_curated), (
        f"Entry count mismatch: YAML gives {len(curated)}, "
        f"snapshot has {len(snap_curated)}"
    )

    for snap_entry, loaded_entry in zip(snap_curated, curated):
        loaded_dict = _entry_to_comparable(loaded_entry)
        for field, snap_val in snap_entry.items():
            if field in ("source", "pulled"):
                continue  # tested separately
            loaded_val = loaded_dict.get(field)
            # Normalise float precision edge cases (e.g. 0.028 vs 0.028000...)
            if isinstance(snap_val, float) and isinstance(loaded_val, float):
                assert abs(snap_val - loaded_val) < 1e-9, (
                    f"Entry '{snap_entry['name']}' field '{field}': "
                    f"snapshot={snap_val!r} loaded={loaded_val!r}"
                )
            else:
                assert snap_val == loaded_val, (
                    f"Entry '{snap_entry['name']}' field '{field}': "
                    f"snapshot={snap_val!r} loaded={loaded_val!r}"
                )

    # source must be "curated" for all loaded entries
    for e in curated:
        assert e.source == "curated", (
            f"Entry '{e.name}' has source={e.source!r}; expected 'curated'"
        )


def test_fallback_entries_match_snapshot_field_by_field():
    """Fallback entries must match the snapshot (fallback JSON still unchanged)."""
    snapshot = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    snap_fallback = [e for e in snapshot["entries"] if e["source"] == "fallback"]
    fallback = list_fallback()

    assert len(fallback) == len(snap_fallback), (
        f"Fallback count mismatch: loaded {len(fallback)}, "
        f"snapshot has {len(snap_fallback)}"
    )

    for snap_entry, loaded_entry in zip(snap_fallback, fallback):
        loaded_dict = _entry_to_comparable(loaded_entry)
        for field, snap_val in snap_entry.items():
            if field in ("source", "pulled"):
                continue
            loaded_val = loaded_dict.get(field)
            if isinstance(snap_val, float) and isinstance(loaded_val, float):
                assert abs(snap_val - loaded_val) < 1e-9, (
                    f"Fallback entry '{snap_entry['name']}' field '{field}': "
                    f"snapshot={snap_val!r} loaded={loaded_val!r}"
                )
            else:
                assert snap_val == loaded_val, (
                    f"Fallback entry '{snap_entry['name']}' field '{field}': "
                    f"snapshot={snap_val!r} loaded={loaded_val!r}"
                )

    for e in fallback:
        assert e.source == "fallback", (
            f"Entry '{e.name}' has source={e.source!r}; expected 'fallback'"
        )


# ─── Loader correctness ──────────────────────────────────────────────────────

def test_curated_all_have_valid_target_dir():
    """Each curated entry's target_dir must match CATEGORY_TARGET_DIR."""
    for e in list_curated():
        expected = CATEGORY_TARGET_DIR[e.category]
        assert e.target_dir == expected, (
            f"Entry '{e.name}': target_dir={e.target_dir!r}; "
            f"expected {expected!r} for category '{e.category}'"
        )


def test_curated_all_entries_are_ComfyUILibraryEntry():
    for e in list_curated():
        assert isinstance(e, ComfyUILibraryEntry)


def test_curated_pulled_always_false():
    """'pulled' is a wizard-time flag; the loader always sets it False."""
    for e in list_curated():
        assert e.pulled is False, f"Entry '{e.name}' has pulled={e.pulled!r}"


def test_curated_requires_custom_node_is_tuple():
    """requires_custom_node must be a tuple (frozen dataclass requirement)."""
    for e in list_curated():
        assert isinstance(e.requires_custom_node, tuple), (
            f"Entry '{e.name}': requires_custom_node is "
            f"{type(e.requires_custom_node).__name__}, expected tuple"
        )


# ─── assemble_wizard_catalog integration ─────────────────────────────────────

def test_assemble_wizard_catalog_non_empty(monkeypatch):
    """assemble_wizard_catalog() must return entries even when both scrapers
    are offline (falls back to curated + fallback).
    """
    import requests

    def _raise(*args, **kwargs):
        raise requests.ConnectionError("offline in test")

    monkeypatch.setattr(requests, "get", _raise)

    catalog = assemble_wizard_catalog()
    assert len(catalog) > 0, "assemble_wizard_catalog() returned empty list"


def test_assemble_wizard_catalog_curated_wins_dedup(monkeypatch):
    """Curated entries must win over fallback on name collision (last-wins dedup,
    curated passed last in assemble_wizard_catalog).
    """
    import requests

    def _raise(*args, **kwargs):
        raise requests.ConnectionError("offline in test")

    monkeypatch.setattr(requests, "get", _raise)

    catalog = assemble_wizard_catalog()
    by_name = {e.name: e for e in catalog}

    # v1-5-pruned-emaonly and sd_xl_base_1.0 appear in both curated + fallback;
    # curated must win (source == "curated").
    for name in ("v1-5-pruned-emaonly", "sd_xl_base_1.0"):
        assert name in by_name, f"'{name}' missing from assembled catalog"
        assert by_name[name].source == "curated", (
            f"'{name}' has source={by_name[name].source!r}; expected 'curated' "
            "(curated should win the dedup over fallback)"
        )


def test_assemble_wizard_catalog_all_valid_categories(monkeypatch):
    """All returned entries must have a valid category."""
    import requests

    def _raise(*args, **kwargs):
        raise requests.ConnectionError("offline in test")

    monkeypatch.setattr(requests, "get", _raise)

    for e in assemble_wizard_catalog():
        assert e.category in VALID_CATEGORIES, (
            f"Entry '{e.name}' has unknown category '{e.category}'"
        )


# ─── Loud-failure path (Fix 1) ───────────────────────────────────────────────

def test_list_curated_raises_on_missing_yaml(monkeypatch, tmp_path):
    """list_curated() must raise RuntimeError (not return []) when the
    curated YAML is missing.  services/comfyui/models.yaml is a REQUIRED
    file; a silent empty catalog would mask a misconfiguration.
    """
    nonexistent = tmp_path / "does_not_exist" / "comfyui-models.yaml"

    # Monkeypatch _find_comfyui_yaml to raise FileNotFoundError pointing at
    # the non-existent path, simulating a missing YAML on any machine.
    import utils.comfyui_library as _lib

    def _missing():
        raise FileNotFoundError(f"No such file or directory: '{nonexistent}'")

    monkeypatch.setattr(_lib, "_find_comfyui_yaml", _missing)

    with pytest.raises(RuntimeError, match="required but could not be located"):
        list_curated()


def test_list_curated_returns_15_entries_happy_path():
    """Happy path: list_curated() must return the 15 curated entries when
    services/comfyui/models.yaml is present and valid.
    """
    entries = list_curated()
    assert len(entries) == 15, (
        f"Expected 15 curated entries from list_curated(); got {len(entries)}"
    )
