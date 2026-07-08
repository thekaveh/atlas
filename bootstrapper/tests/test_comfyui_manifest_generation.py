"""
Tests for C3: bootstrapper/utils/comfyui_manifest_generator.py.

All tests are pure unit tests — NO network, NO DB, NO running containers.
Synthetic catalog entries are passed so ``assemble_wizard_catalog()``
(live HF/civitai scrape) is never called.

Test matrix (4 tests per brief):
  1. Generator writes valid manifest YAML + correct TSV columns + '' for null sha256.
  2. When COMFYUI_SOURCE=disabled the generator skips without writing files.
  3. download_models.sh does NOT reference public.comfyui_models (grep guard).
  4. Manifest round-trips: active set matches COMFYUI_USER_MODELS + sidecar selection.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

# Add bootstrapper/ to sys.path so utils.* imports resolve.
import sys
_BOOTSTRAPPER = Path(__file__).resolve().parent.parent
if str(_BOOTSTRAPPER) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAPPER))

from utils.comfyui_library import ComfyUILibraryEntry
from utils.comfyui_manifest_generator import ComfyUIManifestGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(
    name: str,
    *,
    category: str = "checkpoint",
    url: str = "https://huggingface.co/example/model.safetensors",
    sha256: str | None = None,
    essential: bool = False,
    filename: str | None = None,
) -> ComfyUILibraryEntry:
    return ComfyUILibraryEntry(
        name=name,
        family="TestFamily",
        category=category,
        size_gb=1.0,
        url=url,
        sha256=sha256,
        target_dir=category + "s",
        min_vram_gb=None,
        cpu_supported=True,
        requires_custom_node=(),
        popularity=0,
        source="curated",
        pulled=False,
        essential=essential,
        notes=None,
        filename=filename,
    )


def _schema_path() -> Path:
    here = Path(__file__).resolve().parent
    return here.parent / "schemas" / "comfyui-manifest.schema.json"


def _validate_manifest(data: dict) -> None:
    # jsonschema is a hard runtime dependency (bootstrapper/pyproject.toml);
    # import unconditionally so a missing install fails loudly rather than
    # silently skipping schema enforcement.
    import jsonschema
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    jsonschema.validate(instance=data, schema=schema)


# ---------------------------------------------------------------------------
# Test 1: generator writes valid YAML + correct TSV for an active set
# ---------------------------------------------------------------------------

class TestGeneratorWritesFiles:
    """C3 T1 — files are written with correct content when comfyui is enabled."""

    def test_yaml_manifest_valid(self, tmp_path, monkeypatch):
        """YAML manifest validates against comfyui-manifest.schema.json."""
        catalog = [
            _entry("ModelA", sha256="abc123"),
            _entry("ModelB", category="vae"),
        ]
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": "ModelA,ModelB"}

        # Patch active_comfyui_models to use our synthetic catalog.
        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(
            resolver,
            "active_comfyui_models",
            lambda e, **kw: [m for m in catalog if m.name in {"ModelA", "ModelB"}],
        )

        gen = ComfyUIManifestGenerator(env)
        assert gen.write(tmp_path) is True

        yaml_path = tmp_path / "selected-models.yaml"
        assert yaml_path.exists(), "selected-models.yaml not written"
        data = yaml.safe_load(yaml_path.read_text())
        _validate_manifest(data)
        assert len(data["models"]) == 2
        names = {m["name"] for m in data["models"]}
        assert names == {"ModelA", "ModelB"}

    def test_tsv_columns_and_null_sha256(self, tmp_path, monkeypatch):
        """TSV has 5 tab-separated columns; null sha256 → empty string."""
        catalog = [
            _entry("ModelA", sha256="deadbeef"),
            _entry("ModelB", sha256=None),   # null sha256 → ''
        ]
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": "ModelA,ModelB"}

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(
            resolver,
            "active_comfyui_models",
            lambda e, **kw: catalog,
        )

        gen = ComfyUIManifestGenerator(env)
        gen.write(tmp_path)

        tsv_path = tmp_path / "active-models.tsv"
        assert tsv_path.exists(), "active-models.tsv not written"
        lines = [l for l in tsv_path.read_text().splitlines() if l]
        assert len(lines) == 2, f"Expected 2 rows, got: {lines}"

        # Each row must have exactly 5 tab-separated columns.
        for line in lines:
            cols = line.split("\t")
            assert len(cols) == 5, f"Expected 5 columns, got {len(cols)}: {line!r}"

        # name / type / filename / download_url / sha256
        row_a = dict(zip(["name","type","filename","download_url","sha256"],
                         lines[0].split("\t")))
        row_b = dict(zip(["name","type","filename","download_url","sha256"],
                         lines[1].split("\t")))

        assert row_a["name"] == "ModelA"
        assert row_a["sha256"] == "deadbeef"
        assert row_b["name"] == "ModelB"
        assert row_b["sha256"] == "", "null sha256 must be empty string in TSV"

    def test_tsv_url_matches_entry(self, tmp_path, monkeypatch):
        """TSV download_url column matches the entry's URL."""
        url = "https://huggingface.co/some/model.safetensors"
        catalog = [_entry("ModelC", url=url)]
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": "ModelC"}

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(
            resolver, "active_comfyui_models", lambda e, **kw: catalog
        )

        ComfyUIManifestGenerator(env).write(tmp_path)
        tsv = (tmp_path / "active-models.tsv").read_text().strip()
        cols = tsv.split("\t")
        assert cols[3] == url

    @pytest.mark.parametrize(
        "field,value",
        [
            ("name", "Bad\tName"),
            ("name", "Bad\nName"),
            ("filename", "../escape.safetensors"),
            ("filename", "nested/escape.safetensors"),
            ("filename", "nested\\escape.safetensors"),
            ("download_url", "https://example.test/model\nother"),
            ("sha256", "abc\tdef"),
        ],
    )
    def test_tsv_rejects_unsafe_fields(self, tmp_path, monkeypatch, field, value):
        """TSV fields must not shift columns or write outside the model dir."""
        kwargs = {}
        name = "SafeName"
        sha256 = "deadbeef"
        url = "https://huggingface.co/example/model.safetensors"
        if field == "name":
            name = value
        elif field == "filename":
            kwargs["filename"] = value
        elif field == "download_url":
            url = value
        elif field == "sha256":
            sha256 = value
        catalog = [_entry(name, url=url, sha256=sha256, **kwargs)]
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": name}

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(resolver, "active_comfyui_models", lambda e, **kw: catalog)

        with pytest.raises(ValueError):
            ComfyUIManifestGenerator(env).write(tmp_path)

    def test_empty_catalog_produces_empty_tsv(self, tmp_path, monkeypatch):
        """Empty active set → empty TSV (zero bytes → download_models.sh exits 0)."""
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": ""}

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(resolver, "active_comfyui_models", lambda e, **kw: [])

        ComfyUIManifestGenerator(env).write(tmp_path)
        tsv_path = tmp_path / "active-models.tsv"
        assert tsv_path.exists()
        assert tsv_path.stat().st_size == 0, "Empty active set must produce empty TSV"


# ---------------------------------------------------------------------------
# Test 2: generator skips when COMFYUI_SOURCE=disabled
# ---------------------------------------------------------------------------

class TestGeneratorSkipsWhenDisabled:
    """C3 T2 — no files written when ComfyUI is disabled."""

    def test_disabled_returns_true_no_files(self, tmp_path):
        env = {"COMFYUI_SOURCE": "disabled"}
        gen = ComfyUIManifestGenerator(env)
        assert gen.is_enabled() is False
        result = gen.write(tmp_path)
        assert result is True
        assert not (tmp_path / "selected-models.yaml").exists()
        assert not (tmp_path / "active-models.tsv").exists()

    def test_missing_source_key_treated_as_disabled(self, tmp_path):
        """Missing COMFYUI_SOURCE defaults to disabled."""
        env: dict[str, str] = {}
        gen = ComfyUIManifestGenerator(env)
        assert gen.is_enabled() is False
        result = gen.write(tmp_path)
        assert result is True
        assert not (tmp_path / "selected-models.yaml").exists()


# ---------------------------------------------------------------------------
# Test 3: download_models.sh does NOT reference public.comfyui_models
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_download_models_sh_no_public_comfyui_models():
    """download_models.sh must not query public.comfyui_models (C3 regression guard)."""
    text = (REPO_ROOT / "services/comfyui/init/scripts/download_models.sh").read_text()
    assert "public.comfyui_models" not in text, (
        "download_models.sh still references public.comfyui_models — "
        "model list must come from $MANIFEST_TSV (bootstrapper-generated TSV), not the DB."
    )


def test_download_models_sh_no_psql_call():
    """download_models.sh must not invoke psql as a command (no DB dependency after C3).

    Comments mentioning 'psql' (historical context) are acceptable; executable
    psql invocations are not.  We check that no non-comment line contains the
    word psql.
    """
    text = (REPO_ROOT / "services/comfyui/init/scripts/download_models.sh").read_text()
    code_lines = [
        line for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for line in code_lines:
        assert "psql" not in line, (
            f"download_models.sh has a psql call on a non-comment line: {line!r} — "
            "it should read $MANIFEST_TSV instead."
        )


def test_download_models_sh_reads_manifest_tsv():
    """download_models.sh must reference MANIFEST_TSV (the bootstrapper-generated file)."""
    text = (REPO_ROOT / "services/comfyui/init/scripts/download_models.sh").read_text()
    assert "MANIFEST_TSV" in text, (
        "download_models.sh does not reference MANIFEST_TSV — "
        "it should read the bootstrapper manifest, not the DB."
    )


def test_comfyui_init_compose_no_pg_env():
    """comfyui-init in compose.yml must not inject PGHOST/PGPASSWORD (no DB)."""
    text = (REPO_ROOT / "services/comfyui/compose.yml").read_text()
    # Extract only the comfyui-init block (roughly between comfyui-init: and comfyui:).
    start = text.find("  comfyui-init:")
    end = text.find("  comfyui:", start)
    block = text[start:end]
    assert "PGHOST" not in block, "comfyui-init compose block still has PGHOST"
    assert "PGPASSWORD" not in block, "comfyui-init compose block still has PGPASSWORD"


def test_comfyui_init_compose_has_manifest_mount():
    """comfyui-init in compose.yml must bind-mount volumes/comfyui."""
    text = (REPO_ROOT / "services/comfyui/compose.yml").read_text()
    start = text.find("  comfyui-init:")
    end = text.find("  comfyui:", start)
    block = text[start:end]
    assert "volumes/comfyui" in block, (
        "comfyui-init compose block missing volumes/comfyui bind-mount"
    )
    assert "COMFYUI_MANIFEST_TSV" in block, (
        "comfyui-init compose block missing COMFYUI_MANIFEST_TSV env var"
    )


# ---------------------------------------------------------------------------
# Test 4: manifest round-trips through the resolver
# ---------------------------------------------------------------------------

class TestManifestRoundTrip:
    """C3 T4 — active set matches COMFYUI_USER_MODELS + sidecar selection."""

    def test_user_models_csv_selects_subset(self, tmp_path, monkeypatch):
        """Only the named models appear in the manifest."""
        catalog = [
            _entry("Alpha"),
            _entry("Beta"),
            _entry("Gamma"),
        ]
        env = {
            "COMFYUI_SOURCE": "container",
            "COMFYUI_USER_MODELS": "Alpha,Gamma",
        }

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(
            resolver,
            "active_comfyui_models",
            lambda e, **kw: [m for m in catalog if m.name in {"Alpha", "Gamma"}],
        )

        ComfyUIManifestGenerator(env).write(tmp_path)
        data = yaml.safe_load((tmp_path / "selected-models.yaml").read_text())
        names = {m["name"] for m in data["models"]}
        assert names == {"Alpha", "Gamma"}, f"Unexpected names: {names}"

    def test_tsv_rows_match_yaml_rows(self, tmp_path, monkeypatch):
        """TSV row count equals YAML model count."""
        catalog = [_entry("M1"), _entry("M2"), _entry("M3")]
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": "M1,M2,M3"}

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(
            resolver, "active_comfyui_models", lambda e, **kw: catalog
        )

        ComfyUIManifestGenerator(env).write(tmp_path)
        yaml_rows = yaml.safe_load(
            (tmp_path / "selected-models.yaml").read_text()
        )["models"]
        tsv_lines = [
            l for l in (tmp_path / "active-models.tsv").read_text().splitlines() if l
        ]
        assert len(yaml_rows) == len(tsv_lines), (
            f"YAML has {len(yaml_rows)} rows but TSV has {len(tsv_lines)} rows"
        )

    def test_tsv_names_match_yaml_names(self, tmp_path, monkeypatch):
        """TSV name column matches YAML name field for every row."""
        catalog = [_entry("X1"), _entry("X2")]
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": "X1,X2"}

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(
            resolver, "active_comfyui_models", lambda e, **kw: catalog
        )

        ComfyUIManifestGenerator(env).write(tmp_path)
        yaml_names = {
            m["name"]
            for m in yaml.safe_load(
                (tmp_path / "selected-models.yaml").read_text()
            )["models"]
        }
        tsv_names = {
            l.split("\t")[0]
            for l in (tmp_path / "active-models.tsv").read_text().splitlines()
            if l
        }
        assert yaml_names == tsv_names
