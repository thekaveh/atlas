from __future__ import annotations

from pathlib import Path

import yaml

from core.config_parser import ConfigParser
from tracks import is_in_track, load_tracks
from utils.source_override_manager import SourceOverrideManager
from tests.three_surface_test_utils import surface_text


ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"
MANIFEST = SERVICES / "asset-baker" / "service.yml"
README = SERVICES / "asset-baker" / "README.md"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_asset_baker_manifest_contract() -> None:
    manifest = _manifest()
    env = {entry["name"]: entry for entry in manifest["env"]}

    assert manifest["name"] == "asset-baker"
    assert manifest["label"] == "Asset Baker (Blender HP→LP bake)"
    assert manifest["category"] == "media"
    assert manifest["docs"] == "services/asset-baker/README.md"
    assert manifest["containers"] == ["asset-baker"]
    assert manifest["sources"] == {
        "var": "ASSET_BAKER_SOURCE",
        "default": "disabled",
        "options": [
            {"id": "container-cpu", "label": "Container (Cycles CPU)"},
            {"id": "disabled", "label": "Disabled"},
        ],
    }
    assert manifest["depends_on"]["required"] == ["minio"]
    assert set(manifest["depends_on"]["optional"]) >= {"backend", "comfyui", "fal", "blender-mcp", "asset-worker"}
    assert manifest["data_flow"]["calls"] == ["minio"]
    assert manifest["rows"][0]["alias"] == "asset-baker.localhost"
    assert env["ASSET_BAKER_SOURCE"]["default"] == "disabled"
    assert "ASSET_BAKER_PORT" in env
    assert env["ASSET_BAKER_ENDPOINT"]["auto_managed"] is True
    assert env["ASSET_BAKER_MINIO_BUCKET"]["default"] == "asset-baker"
    assert env["ASSET_BAKER_BLENDER_VERSION"]["default"] == "4.3.2"
    # Bounded-worker + QA-gate knobs the triage requires as explicit config.
    assert env["ASSET_BAKER_BRIGHTNESS_MIN"]["default"] == "0.05"
    assert env["ASSET_BAKER_TIMEOUT_SECONDS"]["default"] == "600"
    assert env["ASSET_BAKER_CONCURRENCY"]["default"] == "1"


def test_asset_baker_runtime_sc_variants() -> None:
    manifest = _manifest()
    variants = manifest["runtime_sc"]["asset-baker"]
    assert variants["container-cpu"]["scale"] == 1
    assert variants["container-cpu"]["environment"]["ASSET_BAKER_ENDPOINT"] == "http://asset-baker:8096"
    assert variants["disabled"]["scale"] == 0
    assert variants["disabled"]["environment"]["ASSET_BAKER_ENDPOINT"] == ""


def test_asset_baker_track_membership_and_cli_mapping() -> None:
    registry = load_tracks()
    manager = SourceOverrideManager(ConfigParser(str(ROOT)))
    start_py = (ROOT / "bootstrapper" / "start.py").read_text(encoding="utf-8")

    assert is_in_track(
        registry.by_key["gen-ai-creative"],
        "asset-baker",
        always_on=registry.always_on,
    )
    assert not is_in_track(
        registry.by_key["gen-ai-rag"],
        "asset-baker",
        always_on=registry.always_on,
    )
    assert manager.source_mapping["asset_baker_source"] == "ASSET_BAKER_SOURCE"
    assert manager.collect_overrides(asset_baker_source="container-cpu") == {
        "ASSET_BAKER_SOURCE": "container-cpu",
    }
    assert "@click.option('--asset-baker-source'" in start_py
    assert "'asset_baker_source': asset_baker_source" in start_py


def test_asset_baker_env_docs_and_site_surfaces() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    service_page = surface_text("services/asset-baker/README.md", "site")
    source_values = surface_text("docs/reference/source-values.md", "site")
    ports_routes = surface_text("docs/reference/ports-routes.md", "site")
    wiki_service = surface_text("services/asset-baker/README.md", "wiki")

    assert "ASSET_BAKER_SOURCE=disabled" in env_example
    assert "ASSET_BAKER_MINIO_BUCKET=asset-baker" in env_example
    assert "POST /assets/bake" in service_page
    assert "ASSET_BAKER_SOURCE" in source_values
    assert "asset-baker.localhost" in ports_routes
    assert "POST /assets/bake" in wiki_service


def test_asset_baker_readme_documents_api_and_bake_contract() -> None:
    text = README.read_text(encoding="utf-8")

    for required in [
        "## 1. Overview",
        "## 2. Access",
        "## 3. Configuration",
        "## 4. API Contract",
        "## 5. Architecture & Wiring",
        "## 6. Dependencies & Integrations",
        "POST /assets/bake",
        "POST /assets/bake/ref",
        "voxel-remesh",
        "Smart-UV",
        "Metallic neutralization",
        "mean-brightness",
        "foliage bypass",
        "content-address",
        "ASSET_BAKER_SOURCE=disabled",
    ]:
        assert required in text, required
