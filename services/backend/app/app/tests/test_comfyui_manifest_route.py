"""Unit tests for GET /comfyui/db/models — manifest-backed, no DB connection.

These tests verify that the route reads the COMFYUI_MANIFEST_PATH YAML file
(written by the bootstrapper's comfyui_resolver at startup), applies
active_only / essential_only filtering, and returns an empty list (not 500)
when the file is missing.

NOTE: This test file is NOT collected by the bootstrapper's pytest suite
(bootstrapper/tests/) — it lives under services/backend/app/app/tests/ and
runs only when the backend's own dependencies are installed.
"""

from __future__ import annotations

import contextlib
import os
import sys
import textwrap
import tempfile
from pathlib import Path

import pytest


def _stub_required_env(monkeypatch):
    """Stub env vars that main.py needs at import time."""
    for var, default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        if not os.environ.get(var):
            monkeypatch.setenv(var, default)


SAMPLE_MANIFEST = textwrap.dedent("""\
    models:
      - name: Stable Diffusion v1.5
        type: checkpoint
        filename: v1-5-pruned-emaonly.safetensors
        download_url: https://example.com/v1-5.safetensors
        file_size_gb: 4.27
        description: Standard SD 1.5 checkpoint
        active: true
        essential: true
        family: sdxl
        sha256: abc123
        target_dir: checkpoints
        min_vram_gb: 4
        cpu_supported: true
        requires_custom_node: false
        popularity: 5
        source: huggingface
      - name: SDXL Turbo
        type: checkpoint
        filename: sd_xl_turbo_1.0_fp16.safetensors
        download_url: https://example.com/sdxl-turbo.safetensors
        file_size_gb: 6.94
        description: SDXL Turbo fast model
        active: true
        essential: false
        family: sdxl
        sha256: def456
        target_dir: checkpoints
        min_vram_gb: 8
        cpu_supported: false
        requires_custom_node: false
        popularity: 4
        source: huggingface
""")


@pytest.fixture()
def manifest_file(tmp_path: Path) -> Path:
    path = tmp_path / "selected-models.yaml"
    path.write_text(SAMPLE_MANIFEST, encoding="utf-8")
    return path


def test_get_models_returns_manifest_contents(monkeypatch, manifest_file):
    """GET /comfyui/db/models returns all active models from the manifest."""
    _stub_required_env(monkeypatch)
    monkeypatch.setenv("COMFYUI_MANIFEST_PATH", str(manifest_file))

    from fastapi.testclient import TestClient
    import importlib, sys
    # Re-import main so COMFYUI_MANIFEST_PATH env is picked up at request time.
    if "main" in sys.modules:
        main = sys.modules["main"]
    else:
        import main  # type: ignore[import]

    client = TestClient(main.app)
    resp = client.get("/comfyui/db/models?active_only=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["models"]) == 2
    names = {m["name"] for m in body["models"]}
    assert "Stable Diffusion v1.5" in names
    assert "SDXL Turbo" in names


def test_get_models_essential_only(monkeypatch, manifest_file):
    """essential_only=true filters to only models marked essential."""
    _stub_required_env(monkeypatch)
    monkeypatch.setenv("COMFYUI_MANIFEST_PATH", str(manifest_file))

    from fastapi.testclient import TestClient
    import sys
    if "main" in sys.modules:
        main = sys.modules["main"]
    else:
        import main  # type: ignore[import]

    client = TestClient(main.app)
    resp = client.get("/comfyui/db/models?active_only=true&essential_only=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["models"]) == 1
    assert body["models"][0]["name"] == "Stable Diffusion v1.5"
    assert body["models"][0]["essential"] is True


def test_get_models_missing_file_returns_empty_list(monkeypatch, tmp_path):
    """Missing manifest → 200 with empty models list (ComfyUI disabled case)."""
    _stub_required_env(monkeypatch)
    monkeypatch.setenv(
        "COMFYUI_MANIFEST_PATH", str(tmp_path / "nonexistent.yaml")
    )

    from fastapi.testclient import TestClient
    import sys
    if "main" in sys.modules:
        main = sys.modules["main"]
    else:
        import main  # type: ignore[import]

    client = TestClient(main.app)
    resp = client.get("/comfyui/db/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["models"] == []


def test_get_models_no_db_connection(monkeypatch, manifest_file):
    """Confirm GET /comfyui/db/models does not open a DB connection.

    We patch asyncpg.connect to raise an error; the route must still succeed
    because it reads the manifest file, not the database.
    """
    _stub_required_env(monkeypatch)
    monkeypatch.setenv("COMFYUI_MANIFEST_PATH", str(manifest_file))
    # Make DATABASE_URL point somewhere unreachable.
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:x@127.0.0.1:9/nonexistent")

    from fastapi.testclient import TestClient
    import sys
    if "main" in sys.modules:
        main = sys.modules["main"]
    else:
        import main  # type: ignore[import]

    client = TestClient(main.app)
    # If this route touched the DB, it would fail (unreachable host).
    resp = client.get("/comfyui/db/models")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_get_models_corrupt_manifest_returns_empty_list(monkeypatch, tmp_path):
    """Corrupt/malformed YAML in manifest → 200 with empty list, not 500."""
    _stub_required_env(monkeypatch)
    corrupt_file = tmp_path / "corrupt.yaml"
    # Write malformed YAML that yaml.safe_load will reject
    corrupt_file.write_text("models: [unclosed", encoding="utf-8")
    monkeypatch.setenv("COMFYUI_MANIFEST_PATH", str(corrupt_file))

    from fastapi.testclient import TestClient
    import sys
    if "main" in sys.modules:
        main = sys.modules["main"]
    else:
        import main  # type: ignore[import]

    client = TestClient(main.app)
    resp = client.get("/comfyui/db/models")
    # Corrupt manifest should return 200 + empty list, not 500.
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["models"] == []


@contextlib.contextmanager
def _bootstrapper_imports(monkeypatch):
    """Import from bootstrapper/ without leaking its top-level packages.

    Bootstrapper's ``utils``, ``services`` and ``wizard`` packages would
    otherwise stay in sys.modules for the rest of the Backend session and
    shadow any later import of the same names.
    """
    root = Path(__file__).resolve().parents[5] / "bootstrapper"
    loaded = set(sys.modules)
    monkeypatch.syspath_prepend(str(root))
    try:
        yield
    finally:
        for name in set(sys.modules) - loaded:
            origin = getattr(sys.modules[name], "__file__", None)
            if origin and Path(origin).is_relative_to(root):
                del sys.modules[name]


def _publish_custom_numeric_manifest(tmp_path, monkeypatch, change):
    with _bootstrapper_imports(monkeypatch):
        _write_custom_numeric_manifest(tmp_path, monkeypatch, change)


def _write_custom_numeric_manifest(tmp_path, monkeypatch, change):
    import yaml

    # Exercise the host publisher that owns this Backend input without loading
    # a catalog or making any provider/download calls.
    from utils import comfyui_library
    from utils.comfyui_manifest_generator import ComfyUIManifestGenerator

    field, value, bundle = change
    candidate = {"name": "candidate", "category": "lora",
                 "url": "https://example.test/candidate.safetensors"}
    if bundle:
        candidate["files"] = [{"role": "weights", "category": "lora",
                               "url": candidate.pop("url"), field: value}]
    else:
        candidate[field] = value
    healthy = {"name": "healthy", "category": "lora",
               "url": "https://example.test/healthy.safetensors"}
    sidecar = tmp_path / "custom.yaml"
    sidecar.write_text(yaml.safe_dump({"models": [candidate, healthy]}), encoding="utf-8")
    monkeypatch.setattr(comfyui_library, "assemble_wizard_catalog", lambda: [])
    output = tmp_path / "published"
    env = {"COMFYUI_SOURCE": "container", "COMFYUI_CUSTOM_MODELS_FILE": str(sidecar)}
    assert ComfyUIManifestGenerator(env).write(output) is True
    monkeypatch.setenv("COMFYUI_MANIFEST_PATH", str(output / "selected-models.yaml"))


@pytest.mark.parametrize("change", [
    (field, value, False)
    for field in ("size_gb", "min_vram_gb", "min_ram_gb")
    for value in (float("nan"), float("inf"), float("-inf"))
] + [("size_gb", value, True) for value in (float("nan"), float("inf"), float("-inf"))])
def test_published_inventory_retains_healthy_models_after_invalid_numeric_row(
    tmp_path, monkeypatch, change
):
    _stub_required_env(monkeypatch)
    _publish_custom_numeric_manifest(tmp_path, monkeypatch, change)
    root = Path(__file__).resolve().parents[5] / "bootstrapper"
    assert not [name for name, module in list(sys.modules.items())
                if Path(getattr(module, "__file__", None) or "/").is_relative_to(root)]
    from fastapi.testclient import TestClient
    import main

    response = TestClient(main.app, raise_server_exceptions=False).get("/comfyui/db/models")
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert [row["name"] for row in response.json()["models"]] == ["healthy"]
    assert response.json()["models"][0]["file_size_gb"] == 0.0


@pytest.mark.parametrize("change", [
    ("size_gb", "2.5", True), ("size_gb", -1.5, False),
    ("min_vram_gb", None, False), ("min_vram_gb", True, False),
    ("min_ram_gb", "8", False), ("min_ram_gb", False, False),
])
def test_published_inventory_preserves_existing_numeric_metadata(
    tmp_path, monkeypatch, change
):
    _stub_required_env(monkeypatch)
    _publish_custom_numeric_manifest(tmp_path, monkeypatch, change)
    from fastapi.testclient import TestClient
    import main

    response = TestClient(main.app).get("/comfyui/db/models")
    assert response.status_code == 200
    rows = response.json()["models"]
    assert [row["name"] for row in rows] == ["candidate", "healthy"]
    field, value, _bundle = change
    output_key = {"size_gb": "file_size_gb", "min_vram_gb": "min_vram_gb",
                  "min_ram_gb": "min_ram_gb"}[field]
    assert rows[0][output_key] == (None if value is None else float(value))
