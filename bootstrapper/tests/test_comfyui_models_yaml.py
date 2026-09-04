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

# Curated entries whose loader reads a directory different from their category
# default, so ``target_dir`` is intentionally overridden. Pinned by name so the
# per-category target_dir invariant still catches a mislabeled entry everywhere
# else. Keyed by catalog ``name`` → expected ``target_dir``.
KNOWN_TARGET_DIR_OVERRIDES = {
    # Hunyuan3D-2's dit checkpoint is a mesh_model but loads from models/checkpoints
    # via ImageOnlyCheckpointLoader (#338).
    "hunyuan3d-2": "checkpoints",
}


# ─── Helper ──────────────────────────────────────────────────────────────────

def _entry_to_comparable(e: ComfyUILibraryEntry) -> dict:
    """Convert an entry to a JSON-compatible dict for snapshot comparison.

    Excludes 'pulled' (wizard-time computed) and 'source' (set by the loader,
    differs between curated/fallback). Those are tested separately.
    """
    comparable = json.loads(json.dumps(dataclasses.asdict(e)))
    # The historical catalog snapshot predates the optional per-file
    # readiness override. None means "inherit requiredness from the logical
    # entry" and therefore does not change the pinned artifact metadata.
    for file_row in comparable.get("files", []):
        if file_row.get("provisioning_required") is None:
            file_row.pop("provisioning_required", None)
    return comparable


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


def _minimal_direct_model(**overrides):
    model = {
        "name": "verified-direct",
        "category": "checkpoint",
        "url": (
            "https://huggingface.co/example/model/resolve/"
            + "1" * 40
            + "/model.safetensors"
        ),
        "sha256": "a" * 64,
    }
    model.update(overrides)
    return {"models": [model]}


@pytest.mark.parametrize(
    "overrides",
    [
        {"url": "https://huggingface.co/example/model/resolve/main/model.safetensors"},
        {"sha256": None},
        {"sha256": "a" * 63},
        {"sha256": "A" * 64},
    ],
)
def test_schema_rejects_mutable_or_unverified_direct_artifacts(overrides):
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=_minimal_direct_model(**overrides), schema=schema)


@pytest.mark.parametrize(
    "file_overrides",
    [
        {"url": "https://huggingface.co/example/model/resolve/main/model.safetensors"},
        {"sha256": None},
        {"sha256": "not-a-sha256"},
    ],
)
def test_schema_applies_the_same_trust_contract_to_bundle_files(file_overrides):
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    artifact = {
        "role": "weights",
        "category": "checkpoint",
        "url": (
            "https://huggingface.co/example/model/resolve/"
            + "2" * 40
            + "/model.safetensors"
        ),
        "sha256": "b" * 64,
    }
    artifact.update(file_overrides)
    catalog = {
        "models": [{"name": "verified-bundle", "category": "checkpoint", "files": [artifact]}]
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=catalog, schema=schema)


def test_curated_catalog_has_only_immutable_verified_download_artifacts():
    """Direct and bundle artifacts share one immutable/hash trust boundary."""
    data = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8"))
    artifacts = []
    for model in data["models"]:
        files = model.get("files")
        artifacts.extend(files if files else [model])

    assert len(artifacts) == 20  # 14 direct + 6 bundle declarations
    for artifact in artifacts:
        assert "/resolve/main/" not in artifact["url"]
        assert len(artifact["sha256"]) == 64
        assert artifact["sha256"] == artifact["sha256"].lower()
        assert all(char in "0123456789abcdef" for char in artifact["sha256"])


def test_dead_audioldm_root_artifact_is_not_offered_anywhere():
    """The removed URL never existed in any of the 17 upstream revisions.

    Provenance is recorded in .superpowers/sdd/task-7-report.md; this static
    guard avoids a flaky network dependency while preventing reintroduction.
    """
    dead_name = "audioldm-text-to-audio"
    dead_url = "https://huggingface.co/cvssp/audioldm/resolve/main/pytorch_model.bin"
    curated = _YAML_PATH.read_text(encoding="utf-8")
    fallback = (
        _REPO_ROOT / "bootstrapper/utils/data/comfyui_catalog_fallback.json"
    ).read_text(encoding="utf-8")
    assert dead_name not in curated and dead_url not in curated
    assert dead_name not in fallback and dead_url not in fallback


def test_library_rejects_mutable_curated_catalog_even_without_schema_runner(
    monkeypatch, tmp_path
):
    import utils.comfyui_library as library

    catalog = tmp_path / "models.yaml"
    catalog.write_text(
        """models:
  - name: mutable
    category: checkpoint
    url: https://huggingface.co/example/model/resolve/main/model.safetensors
    sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(library, "_find_comfyui_yaml", lambda: catalog)

    with pytest.raises(RuntimeError, match="immutable 40-hex"):
        library.list_curated()


def test_library_rejects_direct_bundle_target_conflicts(monkeypatch, tmp_path):
    import utils.comfyui_library as library

    revision = "1" * 40
    catalog = tmp_path / "models.yaml"
    catalog.write_text(
        f"""models:
  - name: direct
    category: checkpoint
    filename: shared.safetensors
    url: https://huggingface.co/example/one/resolve/{revision}/one.safetensors
    sha256: {'a' * 64}
  - name: bundle
    category: checkpoint
    files:
      - role: weights
        category: checkpoint
        filename: shared.safetensors
        url: https://huggingface.co/example/two/resolve/{revision}/two.safetensors
        sha256: {'b' * 64}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(library, "_find_comfyui_yaml", lambda: catalog)

    with pytest.raises(RuntimeError, match="Conflicting.*checkpoints/shared"):
        library.list_curated()


def test_complete_curated_catalog_emits_one_verified_row_per_unique_target(tmp_path):
    from utils.comfyui_manifest_generator import ComfyUIManifestGenerator

    tsv_path = tmp_path / "active-models.tsv"
    ComfyUIManifestGenerator({})._write_tsv(list_curated(), tsv_path)
    rows = [line.split("\t") for line in tsv_path.read_text(encoding="utf-8").splitlines()]

    assert len(rows) == 18
    assert all(len(row) == 8 for row in rows)
    assert all(len(row[4]) == 64 and row[4] == row[4].lower() for row in rows)
    assert all(row[6] == "curated" for row in rows)
    assert all(row[7] == "required" for row in rows)
    assert len({(row[5], row[2]) for row in rows}) == len(rows)


def test_yaml_has_expected_entry_count():
    """Curated YAML entry count must match the regenerable snapshot — no magic
    literal, so adding a curated model only updates the snapshot fixture (#815)."""
    data = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8"))
    snapshot = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert len(data["models"]) == snapshot["curated_count"], (
        f"models.yaml has {len(data['models'])} entries; "
        f"snapshot expects {snapshot['curated_count']}"
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
    """Each curated entry's target_dir must be its category default OR an
    explicitly-registered per-entry override.

    The schema supports a per-entry ``target_dir`` override for models whose loader
    reads a different directory than their logical category (e.g. Hunyuan3D-2 is a
    ``mesh_model`` but its dit checkpoint ships to ``checkpoints`` for
    ImageOnlyCheckpointLoader). Overrides are pinned by name here — NOT accepted as
    "any known subdir" — so a mislabeled entry (a ``vae`` accidentally pointed at
    ``loras``) still fails on every non-override entry.
    """
    for e in list_curated():
        default = CATEGORY_TARGET_DIR[e.category]
        expected = KNOWN_TARGET_DIR_OVERRIDES.get(e.name, default)
        assert e.target_dir == expected, (
            f"Entry '{e.name}': target_dir={e.target_dir!r}; expected {expected!r} "
            f"(category default {default!r}"
            + (f"; override → {expected!r}" if e.name in KNOWN_TARGET_DIR_OVERRIDES else "")
            + ")"
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


def test_list_curated_returns_snapshot_count_happy_path():
    """Happy path: list_curated() must return the snapshot's curated_count
    entries when services/comfyui/models.yaml is present and valid — derived
    from the regenerable snapshot, not a magic literal (#815).
    """
    entries = list_curated()
    snapshot = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert len(entries) == snapshot["curated_count"], (
        f"list_curated() returned {len(entries)}; "
        f"snapshot expects {snapshot['curated_count']}"
    )
