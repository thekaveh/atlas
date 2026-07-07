"""
Tests for the YAML model catalog files and the llm_catalog.py loader.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BOOTSTRAPPER = REPO_ROOT / "bootstrapper"

sys.path.insert(0, str(BOOTSTRAPPER))


def _load_schema():
    schema_path = BOOTSTRAPPER / "schemas" / "models.schema.json"
    return json.loads(schema_path.read_text())


def _load_yaml(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text())


def test_yaml_files_validate_against_schema():
    """Both YAML catalog files must validate against models.schema.json."""
    # jsonschema is a hard runtime dependency (bootstrapper/pyproject.toml);
    # import unconditionally so a missing install fails loudly rather than
    # silently skipping schema enforcement.
    import jsonschema

    schema = _load_schema()
    ollama_yaml = _load_yaml(REPO_ROOT / "services" / "ollama" / "models.yaml")
    litellm_yaml = _load_yaml(REPO_ROOT / "services" / "litellm" / "models.yaml")

    jsonschema.validate(ollama_yaml, schema)
    jsonschema.validate(litellm_yaml, schema)


def test_loader_reproduces_catalog_snapshot():
    """Loader output must match the characterization snapshot captured before the rewrite."""
    snapshot_path = BOOTSTRAPPER / "tests" / "fixtures" / "llm_catalog_snapshot.json"
    if not snapshot_path.exists():
        pytest.skip("snapshot fixture missing")

    snap = json.loads(snapshot_path.read_text())

    from utils import llm_catalog as c
    loaded = c.all_catalog_entries()

    def cap(e):
        return sorted(k for k in ("content", "embeddings", "vision") if getattr(e, k) > 0)

    loaded_set = {
        (e.provider, e.name, e.description, tuple(sorted(e.badges)), e.default_active, tuple(cap(e)))
        for e in loaded
    }
    snap_set = {
        (s["provider"], s["name"], s["description"], tuple(sorted(s["badges"])), s["default_active"], tuple(s["capabilities"]))
        for s in snap
    }

    missing_from_loader = snap_set - loaded_set
    extra_in_loader = loaded_set - snap_set

    assert not missing_from_loader, f"Entries in snapshot but not loader:\n" + "\n".join(str(x) for x in sorted(missing_from_loader))
    assert not extra_in_loader, f"Extra entries in loader not in snapshot:\n" + "\n".join(str(x) for x in sorted(extra_in_loader))


def test_public_functions_intact():
    """All public functions return non-empty typed CatalogEntry lists."""
    from utils import llm_catalog as c

    # Module-level catalog globals
    assert isinstance(c.CLOUD_CATALOG, list) and len(c.CLOUD_CATALOG) > 0
    assert isinstance(c.OLLAMA_DEFAULT_CATALOG, list) and len(c.OLLAMA_DEFAULT_CATALOG) > 0

    # ollama_entries
    ollama = c.ollama_entries()
    assert len(ollama) > 0, "ollama_entries() must be non-empty"
    assert all(hasattr(e, "provider") for e in ollama)
    assert all(e.provider == "ollama" for e in ollama)

    # cloud_entries for openai
    openai = c.cloud_entries("openai")
    assert len(openai) > 0, "cloud_entries('openai') must be non-empty"
    assert all(e.provider == "openai" for e in openai)

    # all_catalog_entries
    all_entries = c.all_catalog_entries()
    assert len(all_entries) > 0
    assert all(hasattr(e, "name") for e in all_entries)
    assert all(hasattr(e, "provider") for e in all_entries)
    assert all(hasattr(e, "content") for e in all_entries)
    assert all(hasattr(e, "structured_content") for e in all_entries)
    assert all(hasattr(e, "vision") for e in all_entries)
    assert all(hasattr(e, "embeddings") for e in all_entries)

    # default_active_names
    ollama_defaults = c.default_active_names("ollama")
    assert len(ollama_defaults) > 0, "default_active_names('ollama') must be non-empty"
    assert all(isinstance(n, str) for n in ollama_defaults)

    openai_defaults = c.default_active_names("openai")
    assert len(openai_defaults) > 0


def test_embedding_entries_declare_dim():
    """Embedding catalog entries carry the `dim:` from YAML onto CatalogEntry,
    and exactly one curated embedding model matches the backend's 768-dim
    requirement so the picker can auto-select it."""
    from utils import llm_catalog as c
    from utils.model_resolver import MEMORY_FACTS_EMBEDDING_DIM

    by_name = {e.name: e for e in c.all_catalog_entries()}
    assert by_name["nomic-embed-text"].dim == MEMORY_FACTS_EMBEDDING_DIM  # 768
    assert by_name["qwen3-embedding:0.6b"].dim == 1536
    assert by_name["text-embedding-3-large"].dim == 3072
    assert by_name["text-embedding-3-small"].dim == 1536

    # Non-embedding (content/vision) entries declare no dim.
    assert by_name["qwen3.6:latest"].dim is None

    # At least one curated embedding model satisfies the required dim.
    embed_dims = [e.dim for e in c.all_catalog_entries() if e.embeddings > 0]
    assert MEMORY_FACTS_EMBEDDING_DIM in embed_dims


def test_find_yaml_resolves_flat_container_layout(tmp_path):
    """litellm-init mounts the catalogs as flat ``<dir>/<service>-models.yaml``
    under ``/atlas-models`` (the #154 container layout), NOT the repo's nested
    ``<service>/models.yaml``. ``_find_yaml`` must resolve that flat form — the
    loose-import subprocess test exercises the full load; this pins the path
    resolution directly."""
    from utils import llm_catalog as c

    # nested form present → primary path wins
    nested = tmp_path / "nested"
    (nested / "ollama").mkdir(parents=True)
    primary = nested / "ollama" / "models.yaml"
    primary.write_text("content: [{name: x}]\n", encoding="utf-8")
    assert c._find_yaml(nested, "ollama") == primary

    # only the flat form present (the /atlas-models case) → fallback resolves it
    flat = tmp_path / "atlas-models"
    flat.mkdir()
    flat_ollama = flat / "ollama-models.yaml"
    flat_litellm = flat / "litellm-models.yaml"
    flat_ollama.write_text("content: [{name: x}]\n", encoding="utf-8")
    flat_litellm.write_text("openai: {content: [{name: y}]}\n", encoding="utf-8")
    assert c._find_yaml(flat, "ollama") == flat_ollama
    assert c._find_yaml(flat, "litellm") == flat_litellm
