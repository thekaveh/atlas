from __future__ import annotations

from pathlib import Path

import yaml

from core.config_parser import ConfigParser
from tracks import is_in_track, load_tracks
from utils.source_override_manager import SourceOverrideManager
from tests.three_surface_test_utils import surface_text


ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"
MANIFEST = SERVICES / "asset-worker" / "service.yml"
README = SERVICES / "asset-worker" / "README.md"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_asset_worker_manifest_contract() -> None:
    manifest = _manifest()
    env = {entry["name"]: entry for entry in manifest["env"]}

    assert manifest["name"] == "asset-worker"
    assert manifest["label"] == "Asset Worker (glTF post-processing)"
    assert manifest["category"] == "media"
    assert manifest["docs"] == "services/asset-worker/README.md"
    assert manifest["containers"] == ["asset-worker"]
    assert manifest["sources"] == {
        "var": "ASSET_WORKER_SOURCE",
        "default": "disabled",
        "options": [
            {"id": "container", "label": "Container"},
            {"id": "disabled", "label": "Disabled"},
        ],
    }
    assert manifest["depends_on"]["required"] == ["minio"]
    assert set(manifest["depends_on"]["optional"]) >= {"backend", "comfyui", "fal", "blender-mcp"}
    assert manifest["data_flow"]["calls"] == ["minio"]
    assert manifest["rows"][0]["alias"] == "asset-worker.localhost"
    assert env["ASSET_WORKER_SOURCE"]["default"] == "disabled"
    assert "ASSET_WORKER_PORT" in env
    assert env["ASSET_WORKER_ENDPOINT"]["auto_managed"] is True
    assert env["ASSET_WORKER_MINIO_BUCKET"]["default"] == "asset-worker"
    assert env["ASSET_WORKER_GLTF_TRANSFORM_VERSION"]["default"] == "4.4.1"


def test_asset_worker_track_membership_and_cli_mapping() -> None:
    registry = load_tracks()
    manager = SourceOverrideManager(ConfigParser(str(ROOT)))
    start_py = (ROOT / "bootstrapper" / "start.py").read_text(encoding="utf-8")

    assert is_in_track(
        registry.by_key["gen-ai-creative"],
        "asset-worker",
        always_on=registry.always_on,
    )
    assert not is_in_track(
        registry.by_key["gen-ai-rag"],
        "asset-worker",
        always_on=registry.always_on,
    )
    assert manager.source_mapping["asset_worker_source"] == "ASSET_WORKER_SOURCE"
    assert manager.collect_overrides(asset_worker_source="container") == {
        "ASSET_WORKER_SOURCE": "container",
    }
    assert "@click.option('--asset-worker-source'" in start_py
    assert "'asset_worker_source': asset_worker_source" in start_py


def test_asset_worker_env_docs_and_site_surfaces() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    service_page = surface_text("services/asset-worker/README.md", "site")
    source_values = surface_text("docs/reference/source-values.md", "site")
    ports_routes = surface_text("docs/reference/ports-routes.md", "site")
    wiki_service = surface_text("services/asset-worker/README.md", "wiki")

    assert "ASSET_WORKER_SOURCE=disabled" in env_example
    assert "ASSET_WORKER_MINIO_BUCKET=asset-worker" in env_example
    assert "POST /gltf/postprocess" in service_page
    assert "ASSET_WORKER_SOURCE" in source_values
    assert "asset-worker.localhost" in ports_routes
    assert "POST /gltf/postprocess" in wiki_service


def test_asset_worker_readme_documents_api_and_postprocess_contract() -> None:
    text = README.read_text(encoding="utf-8")

    for required in [
        "## 1. Overview",
        "## 2. Access",
        "## 3. Configuration",
        "## 4. API Contract",
        "## 5. Architecture & Wiring",
        "## 6. Dependencies & Integrations",
        "POST /gltf/postprocess",
        "POST /gltf/postprocess/ref",
        "up_axis",
        "base-at-y=0",
        "content-addressed",
        "Draco",
        "Meshopt",
        "KTX2",
        "collider decimation",
        "ASSET_WORKER_SOURCE=disabled",
        "before request-body parsing or object-store fetch",
    ]:
        assert required in text
