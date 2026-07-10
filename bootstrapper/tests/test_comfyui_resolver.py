"""Tests for bootstrapper.utils.comfyui_resolver.

All tests are pure unit tests — NO network, NO DB, NO running containers.
Synthetic catalog lists are passed via the `catalog=` kwarg so that
``assemble_wizard_catalog()`` (which scrapes HF + civitai) is never called.
Sidecar YAML is written to tmp_path and passed via ``sidecar_path=``.

Test matrix (6 tests matching the brief):
  1. COMFYUI_USER_MODELS="a,b" + catalog [a,b,c] → returns a,b (not c)
  2. Empty COMFYUI_USER_MODELS → returns catalog's essential=True entries
  3. Sidecar always active; sidecar wins name-dedupe over catalog
  4. manifest_dict field mapping (category→type, url→download_url,
     size_gb→file_size_gb) + validates against comfyui-manifest.schema.json
  5. write_manifest round-trip: write → read → validates → same models
  6. Name in COMFYUI_USER_MODELS not in catalog/sidecar → dropped + warning
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from utils.comfyui_library import ComfyUILibraryEntry
from utils.comfyui_resolver import active_comfyui_models, manifest_dict, write_manifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(
    name: str,
    *,
    category: str = "checkpoint",
    url: str = "https://example.com/model.safetensors",
    family: str = "TestFamily",
    size_gb: float = 1.0,
    sha256: str | None = None,
    target_dir: str = "checkpoints",
    min_vram_gb: float | None = None,
    cpu_supported: bool = True,
    requires_custom_node: tuple[str, ...] = (),
    popularity: int = 0,
    source: str = "curated",
    pulled: bool = False,
    essential: bool = False,
    notes: str | None = None,
    filename: str | None = None,
) -> ComfyUILibraryEntry:
    """Build a minimal synthetic ComfyUILibraryEntry for testing."""
    return ComfyUILibraryEntry(
        name=name,
        family=family,
        category=category,
        size_gb=size_gb,
        url=url,
        sha256=sha256,
        target_dir=target_dir,
        min_vram_gb=min_vram_gb,
        cpu_supported=cpu_supported,
        requires_custom_node=requires_custom_node,
        popularity=popularity,
        source=source,
        pulled=pulled,
        essential=essential,
        notes=notes,
        filename=filename,
    )


def _sidecar_yaml(models: list[dict]) -> str:
    """Render a minimal sidecar YAML string."""
    return yaml.dump({"models": models}, default_flow_style=False)


def _schema_path() -> Path:
    """Locate bootstrapper/schemas/comfyui-manifest.schema.json."""
    # bootstrapper/tests/test_comfyui_resolver.py → tests/ → bootstrapper/ → …
    here = Path(__file__).resolve().parent
    return here.parent / "schemas" / "comfyui-manifest.schema.json"


def _validate_manifest(data: dict) -> None:
    """Validate a manifest dict against comfyui-manifest.schema.json.

    jsonschema is a hard runtime dependency (bootstrapper/pyproject.toml), so
    import unconditionally — a missing install should fail loudly rather than
    silently skipping these schema-enforcement tests.
    """
    import jsonschema

    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    jsonschema.validate(instance=data, schema=schema)


def _names(entries: list[ComfyUILibraryEntry]) -> list[str]:
    return [e.name for e in entries]


# ---------------------------------------------------------------------------
# Test 1 — Non-empty COMFYUI_USER_MODELS activates exactly those names
# ---------------------------------------------------------------------------

class TestUserModelsSelection:
    """COMFYUI_USER_MODELS='a,b' + catalog [a,b,c] → active set is {a,b}."""

    def test_active_names_match_csv(self, tmp_path):
        catalog = [_entry("a"), _entry("b"), _entry("c")]
        env = {"COMFYUI_USER_MODELS": "a,b"}
        result = active_comfyui_models(
            env,
            catalog=catalog,
            sidecar_path=str(tmp_path / "nonexistent-sidecar.yaml"),
        )
        assert _names(result) == ["a", "b"]

    def test_c_is_not_in_result(self, tmp_path):
        catalog = [_entry("a"), _entry("b"), _entry("c")]
        env = {"COMFYUI_USER_MODELS": "a,b"}
        result = active_comfyui_models(
            env,
            catalog=catalog,
            sidecar_path=str(tmp_path / "nonexistent-sidecar.yaml"),
        )
        assert "c" not in _names(result)

    def test_order_matches_catalog_order(self, tmp_path):
        """Activated entries appear in catalog order, not CSV order."""
        catalog = [_entry("a"), _entry("b"), _entry("c")]
        env = {"COMFYUI_USER_MODELS": "b,a"}
        result = active_comfyui_models(
            env,
            catalog=catalog,
            sidecar_path=str(tmp_path / "nonexistent-sidecar.yaml"),
        )
        # Both a and b are active; catalog order is a then b.
        assert _names(result) == ["a", "b"]


# ---------------------------------------------------------------------------
# Test 2 — Empty COMFYUI_USER_MODELS → essential entries only
# ---------------------------------------------------------------------------

class TestEmptyUserModels:
    """Empty CSV → activate catalog essential=True entries + sidecar."""

    def test_essential_entries_are_returned(self, tmp_path):
        catalog = [
            _entry("essential-model", essential=True),
            _entry("non-essential-model", essential=False),
        ]
        env = {"COMFYUI_USER_MODELS": ""}
        result = active_comfyui_models(
            env,
            catalog=catalog,
            sidecar_path=str(tmp_path / "nonexistent-sidecar.yaml"),
        )
        assert "essential-model" in _names(result)

    def test_non_essential_entries_are_not_returned(self, tmp_path):
        catalog = [
            _entry("essential-model", essential=True),
            _entry("non-essential-model", essential=False),
        ]
        env = {"COMFYUI_USER_MODELS": ""}
        result = active_comfyui_models(
            env,
            catalog=catalog,
            sidecar_path=str(tmp_path / "nonexistent-sidecar.yaml"),
        )
        assert "non-essential-model" not in _names(result)

    def test_missing_comfyui_user_models_same_as_empty(self, tmp_path):
        """Missing key behaves identically to empty string."""
        catalog = [_entry("e", essential=True), _entry("x", essential=False)]
        result_missing = active_comfyui_models(
            {},
            catalog=catalog,
            sidecar_path=str(tmp_path / "nonexistent-sidecar.yaml"),
        )
        result_empty = active_comfyui_models(
            {"COMFYUI_USER_MODELS": ""},
            catalog=catalog,
            sidecar_path=str(tmp_path / "nonexistent-sidecar.yaml"),
        )
        assert _names(result_missing) == _names(result_empty)

    def test_empty_csv_all_non_essential_returns_empty_list(self, tmp_path):
        """When nothing is essential and CSV is empty, result is [] (+ sidecar)."""
        catalog = [_entry("x", essential=False), _entry("y", essential=False)]
        result = active_comfyui_models(
            {"COMFYUI_USER_MODELS": ""},
            catalog=catalog,
            sidecar_path=str(tmp_path / "nonexistent-sidecar.yaml"),
        )
        assert result == []


# ---------------------------------------------------------------------------
# Test 3 — Sidecar always active + wins name-dedupe
# ---------------------------------------------------------------------------

class TestSidecarBehavior:
    """Sidecar entries are always active; sidecar wins name-dedupe."""

    def _write_sidecar(self, path: Path, models: list[dict]) -> str:
        path.write_text(_sidecar_yaml(models), encoding="utf-8")
        return str(path)

    def test_sidecar_always_active_with_non_empty_csv(self, tmp_path):
        """Sidecar entry 'my-custom' is active even though it's not in COMFYUI_USER_MODELS."""
        sidecar_file = self._write_sidecar(
            tmp_path / "custom-models.yaml",
            [{"name": "my-custom", "category": "lora",
              "url": "https://example.com/my-custom.safetensors"}],
        )
        catalog = [_entry("a"), _entry("b")]
        env = {"COMFYUI_USER_MODELS": "a"}
        result = active_comfyui_models(env, catalog=catalog, sidecar_path=sidecar_file)
        names = _names(result)
        assert "my-custom" in names
        assert "a" in names
        assert "b" not in names

    def test_sidecar_always_active_with_empty_csv(self, tmp_path):
        """Sidecar entry is active even when CSV is empty and it's not essential."""
        sidecar_file = self._write_sidecar(
            tmp_path / "custom-models.yaml",
            [{"name": "always-on", "category": "vae",
              "url": "https://example.com/vae.safetensors"}],
        )
        catalog = [_entry("e", essential=False)]
        env = {"COMFYUI_USER_MODELS": ""}
        result = active_comfyui_models(env, catalog=catalog, sidecar_path=sidecar_file)
        assert "always-on" in _names(result)

    def test_sidecar_wins_name_dedupe_over_catalog(self, tmp_path):
        """When sidecar and catalog share a name, the sidecar version is used."""
        sidecar_file = self._write_sidecar(
            tmp_path / "custom-models.yaml",
            [{"name": "shared-name", "category": "lora",
              "url": "https://sidecar.example.com/shared.safetensors",
              "notes": "sidecar-version"}],
        )
        catalog = [
            _entry("shared-name",
                   url="https://catalog.example.com/shared.safetensors",
                   notes="catalog-version",
                   essential=True),
        ]
        env = {"COMFYUI_USER_MODELS": "shared-name"}
        result = active_comfyui_models(env, catalog=catalog, sidecar_path=sidecar_file)
        names = _names(result)
        assert names.count("shared-name") == 1, "No duplicate for shared name"
        winner = next(e for e in result if e.name == "shared-name")
        assert winner.source == "custom", "Sidecar entry (source='custom') should win"

    def test_sidecar_appended_after_catalog_entries(self, tmp_path):
        """Sidecar entries appear after catalog entries in the result."""
        sidecar_file = self._write_sidecar(
            tmp_path / "custom-models.yaml",
            [{"name": "sidecar-entry", "category": "vae",
              "url": "https://example.com/vae.safetensors"}],
        )
        catalog = [_entry("cat-a"), _entry("cat-b")]
        env = {"COMFYUI_USER_MODELS": "cat-a,cat-b"}
        result = active_comfyui_models(env, catalog=catalog, sidecar_path=sidecar_file)
        names = _names(result)
        assert names.index("sidecar-entry") > names.index("cat-a")
        assert names.index("sidecar-entry") > names.index("cat-b")

    def test_multiple_sidecar_paths_are_union_loaded_from_env(self, tmp_path):
        """COMFYUI_CUSTOM_MODELS_FILE can name multiple sidecars via os.pathsep."""
        sidecar_a = self._write_sidecar(
            tmp_path / "custom-models-a.yaml",
            [{"name": "consumer-a", "category": "vae",
              "url": "https://example.com/a.safetensors"}],
        )
        sidecar_b = self._write_sidecar(
            tmp_path / "custom-models-b.yaml",
            [{"name": "consumer-b", "category": "lora",
              "url": "https://example.com/b.safetensors"}],
        )
        env = {"COMFYUI_CUSTOM_MODELS_FILE": os.pathsep.join([sidecar_a, sidecar_b])}

        result = active_comfyui_models(env, catalog=[])

        assert _names(result) == ["consumer-a", "consumer-b"]


# ---------------------------------------------------------------------------
# Test 4 — manifest_dict field mapping + schema validation
# ---------------------------------------------------------------------------

class TestManifestDict:
    """manifest_dict maps fields correctly and validates against the schema."""

    def test_category_maps_to_type(self):
        entries = [_entry("m1", category="lora")]
        data = manifest_dict(entries)
        row = data["models"][0]
        assert row["type"] == "lora"
        assert "category" not in row

    def test_url_maps_to_download_url(self):
        entries = [_entry("m1", url="https://hf.co/file.safetensors")]
        data = manifest_dict(entries)
        row = data["models"][0]
        assert row["download_url"] == "https://hf.co/file.safetensors"
        assert "url" not in row

    def test_size_gb_maps_to_file_size_gb(self):
        entries = [_entry("m1", size_gb=3.5)]
        data = manifest_dict(entries)
        row = data["models"][0]
        assert row["file_size_gb"] == pytest.approx(3.5)
        assert "size_gb" not in row

    def test_notes_maps_to_description(self):
        entries = [_entry("m1", notes="A test note")]
        data = manifest_dict(entries)
        row = data["models"][0]
        assert row["description"] == "A test note"
        assert "notes" not in row

    def test_active_always_true(self):
        entries = [_entry("m1"), _entry("m2")]
        data = manifest_dict(entries)
        for row in data["models"]:
            assert row["active"] is True

    def test_essential_always_false(self):
        entries = [_entry("m1", essential=True)]
        data = manifest_dict(entries)
        assert data["models"][0]["essential"] is False

    def test_filename_derived_from_url_when_missing(self):
        entries = [_entry("m1", url="https://hf.co/resolve/main/model.safetensors",
                          filename=None)]
        data = manifest_dict(entries)
        assert data["models"][0]["filename"] == "model.safetensors"

    def test_explicit_filename_preferred_over_url(self):
        entries = [_entry("civitai-123", url="https://civitai.com/api/download/models/123?token=abc",
                          filename="civitai-lora.safetensors")]
        data = manifest_dict(entries)
        assert data["models"][0]["filename"] == "civitai-lora.safetensors"

    def test_result_validates_against_schema(self):
        entries = [
            _entry("sd15", category="checkpoint", url="https://hf.co/sd15.safetensors",
                   sha256="abc123", size_gb=3.97, min_vram_gb=4.0, cpu_supported=True,
                   requires_custom_node=(), popularity=95, source="curated", notes="SD1.5"),
            _entry("my-lora", category="lora", url="https://hf.co/lora.safetensors",
                   source="custom"),
        ]
        data = manifest_dict(entries)
        _validate_manifest(data)  # raises jsonschema.ValidationError on failure

    def test_empty_entries_list_validates(self):
        """Empty model list is valid (nothing to download)."""
        _validate_manifest(manifest_dict([]))

    def test_requires_custom_node_is_list(self):
        entries = [_entry("m1", requires_custom_node=("NodeA", "NodeB"))]
        data = manifest_dict(entries)
        val = data["models"][0]["requires_custom_node"]
        assert isinstance(val, list)
        assert val == ["NodeA", "NodeB"]


# ---------------------------------------------------------------------------
# Test 5 — write_manifest round-trip
# ---------------------------------------------------------------------------

class TestWriteManifest:
    """write_manifest → read YAML → validates schema → same models."""

    def test_round_trip_name_and_type(self, tmp_path):
        entries = [
            _entry("sd15", category="checkpoint", url="https://hf.co/sd15.safetensors"),
            _entry("my-vae", category="vae", url="https://hf.co/vae.safetensors"),
        ]
        out_file = str(tmp_path / "manifest.yaml")
        write_manifest(entries, out_file)

        loaded = yaml.safe_load(Path(out_file).read_text(encoding="utf-8"))
        _validate_manifest(loaded)

        names = [row["name"] for row in loaded["models"]]
        assert names == ["sd15", "my-vae"]

    def test_round_trip_all_fields_present(self, tmp_path):
        entry = _entry(
            "test-model",
            category="lora",
            url="https://hf.co/model.safetensors",
            sha256="deadbeef",
            size_gb=2.5,
            family="TestFam",
            target_dir="loras",
            min_vram_gb=6.0,
            cpu_supported=False,
            requires_custom_node=("NodeX",),
            popularity=42,
            source="curated",
            notes="A test model",
        )
        out_file = str(tmp_path / "manifest.yaml")
        write_manifest([entry], out_file)

        loaded = yaml.safe_load(Path(out_file).read_text(encoding="utf-8"))
        row = loaded["models"][0]

        assert row["name"] == "test-model"
        assert row["type"] == "lora"
        assert row["download_url"] == "https://hf.co/model.safetensors"
        assert row["sha256"] == "deadbeef"
        assert row["file_size_gb"] == pytest.approx(2.5)
        assert row["family"] == "TestFam"
        assert row["target_dir"] == "loras"
        assert row["min_vram_gb"] == pytest.approx(6.0)
        assert row["cpu_supported"] is False
        assert row["requires_custom_node"] == ["NodeX"]
        assert row["popularity"] == 42
        assert row["source"] == "curated"
        assert row["description"] == "A test model"
        assert row["active"] is True
        assert row["essential"] is False

    def test_round_trip_validates_schema(self, tmp_path):
        entries = [_entry("m1", url="https://hf.co/m1.safetensors")]
        out_file = str(tmp_path / "manifest.yaml")
        write_manifest(entries, out_file)

        loaded = yaml.safe_load(Path(out_file).read_text(encoding="utf-8"))
        _validate_manifest(loaded)  # no exception = pass

    def test_write_creates_parent_dirs(self, tmp_path):
        entries = [_entry("m1", url="https://hf.co/m1.safetensors")]
        nested = str(tmp_path / "deep" / "nested" / "manifest.yaml")
        write_manifest(entries, nested)
        assert Path(nested).is_file()


# ---------------------------------------------------------------------------
# Test 6 — Unknown name in COMFYUI_USER_MODELS is dropped + warns
# ---------------------------------------------------------------------------

class TestUnknownModelDropped:
    """A name in COMFYUI_USER_MODELS not in catalog or sidecar is dropped."""

    def test_unknown_name_absent_from_result(self, tmp_path, capsys):
        catalog = [_entry("known-model")]
        env = {"COMFYUI_USER_MODELS": "known-model,totally-unknown"}
        result = active_comfyui_models(
            env,
            catalog=catalog,
            sidecar_path=str(tmp_path / "nonexistent-sidecar.yaml"),
        )
        assert "totally-unknown" not in _names(result)
        assert "known-model" in _names(result)

    def test_unknown_name_triggers_warning(self, tmp_path, capsys):
        catalog = [_entry("known-model")]
        env = {"COMFYUI_USER_MODELS": "known-model,ghost-model"}
        active_comfyui_models(
            env,
            catalog=catalog,
            sidecar_path=str(tmp_path / "nonexistent-sidecar.yaml"),
        )
        captured = capsys.readouterr()
        assert "ghost-model" in captured.err

    def test_all_unknown_returns_empty_catalog(self, tmp_path, capsys):
        catalog = [_entry("real")]
        env = {"COMFYUI_USER_MODELS": "ghost1,ghost2"}
        result = active_comfyui_models(
            env,
            catalog=catalog,
            sidecar_path=str(tmp_path / "nonexistent-sidecar.yaml"),
        )
        assert result == []

    def test_unknown_name_in_sidecar_is_NOT_dropped(self, tmp_path):
        """A name in COMFYUI_USER_MODELS that IS in the sidecar is not dropped."""
        sidecar_path = tmp_path / "custom-models.yaml"
        sidecar_path.write_text(
            _sidecar_yaml([{"name": "sidecar-special", "category": "lora",
                            "url": "https://example.com/s.safetensors"}]),
            encoding="utf-8",
        )
        catalog = [_entry("catalog-a")]
        env = {"COMFYUI_USER_MODELS": "catalog-a,sidecar-special"}
        result = active_comfyui_models(
            env,
            catalog=catalog,
            sidecar_path=str(sidecar_path),
        )
        # sidecar-special is active (via sidecar), catalog-a is active (via CSV)
        names = _names(result)
        assert "sidecar-special" in names
        assert "catalog-a" in names


# ---------------------------------------------------------------------------
# Test 7 — Host sidecar fallback (dead /custom-models.yaml container default)
# ---------------------------------------------------------------------------

class TestHostSidecarFallback:
    """When COMFYUI_CUSTOM_MODELS_FILE points at a path absent on the host
    (the shipped /custom-models.yaml is a dead container path), the resolver
    falls back to the repo sidecar services/comfyui/custom-models.yaml."""

    def test_dead_container_default_falls_back_to_repo_sidecar(self, tmp_path, monkeypatch):
        from utils import comfyui_library

        # Build a fake services dir: <tmp>/comfyui/custom-models.yaml
        comfyui_dir = tmp_path / "comfyui"
        comfyui_dir.mkdir()
        (comfyui_dir / "custom-models.yaml").write_text(
            _sidecar_yaml(
                [{"name": "host-custom", "category": "lora",
                  "url": "https://example.com/host-custom.safetensors"}]
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(comfyui_library, "_find_services_dir", lambda: tmp_path)

        # env points at the dead container default; no explicit sidecar_path.
        env = {"COMFYUI_CUSTOM_MODELS_FILE": "/custom-models.yaml",
               "COMFYUI_USER_MODELS": ""}
        result = active_comfyui_models(env, catalog=[_entry("x", essential=False)])
        assert "host-custom" in _names(result)

    def test_unset_env_falls_back_to_repo_sidecar(self, tmp_path, monkeypatch):
        from utils import comfyui_library

        comfyui_dir = tmp_path / "comfyui"
        comfyui_dir.mkdir()
        (comfyui_dir / "custom-models.yaml").write_text(
            _sidecar_yaml(
                [{"name": "host-custom", "category": "lora",
                  "url": "https://example.com/host-custom.safetensors"}]
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(comfyui_library, "_find_services_dir", lambda: tmp_path)

        # COMFYUI_CUSTOM_MODELS_FILE entirely unset → still finds the repo sidecar.
        result = active_comfyui_models({}, catalog=[_entry("x", essential=False)])
        assert "host-custom" in _names(result)

    def test_explicit_sidecar_path_is_not_overridden_by_host_fallback(self, tmp_path, monkeypatch):
        from utils import comfyui_library

        # A repo sidecar exists, but an explicit (nonexistent) sidecar_path must
        # be honored verbatim — no silent host fallback for explicit callers.
        comfyui_dir = tmp_path / "comfyui"
        comfyui_dir.mkdir()
        (comfyui_dir / "custom-models.yaml").write_text(
            _sidecar_yaml(
                [{"name": "host-custom", "category": "lora",
                  "url": "https://example.com/host-custom.safetensors"}]
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(comfyui_library, "_find_services_dir", lambda: tmp_path)

        result = active_comfyui_models(
            {"COMFYUI_USER_MODELS": ""},
            catalog=[_entry("x", essential=False)],
            sidecar_path=str(tmp_path / "explicit-absent.yaml"),
        )
        assert "host-custom" not in _names(result)
