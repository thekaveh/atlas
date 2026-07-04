from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from core.config_parser import ConfigParser
from services.manifests import load_manifests, option_in_profile
from tracks import is_in_track, load_tracks
from utils.source_override_manager import SourceOverrideManager


ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"
README = SERVICES / "blender-mcp" / "README.md"
SCRIPT = ROOT / "scripts" / "gltf-transform-postprocess.sh"


def _manifest() -> dict:
    return yaml.safe_load((SERVICES / "blender-mcp" / "service.yml").read_text())


def test_blender_mcp_manifest_is_disabled_virtual_localhost_profile() -> None:
    manifest = _manifest()

    assert manifest["virtual"] is True
    assert manifest["containers"] == []
    assert manifest["category"] == "media"
    assert manifest["docs"] == "services/blender-mcp/README.md"
    assert manifest["sources"]["var"] == "BLENDER_MCP_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert manifest["sources"]["options"] == [
        {
            "id": "localhost",
            "label": "Localhost (host Blender MCP add-on + server)",
            "profiles": ["default"],
        },
        {"id": "disabled", "label": "Disabled"},
    ]
    assert manifest["depends_on"] == {"required": [], "optional": []}
    assert manifest["exports"] == []
    assert manifest["data_flow"]["calls"] == []
    assert "rows" in manifest
    assert manifest["rows"][0].get("alias") is None


def test_blender_mcp_env_contract_and_prod_profile_gate() -> None:
    manifests = load_manifests(SERVICES)
    env = {entry["name"]: entry for entry in _manifest()["env"]}

    assert env["BLENDER_MCP_SOURCE"]["default"] == "disabled"
    assert env["BLENDER_MCP_HOST"]["default"] == "localhost"
    assert env["BLENDER_MCP_LOCALHOST_PORT"]["default"] == 9876
    assert env["BLENDER_MCP_ENDPOINT"]["auto_managed"] is True
    assert option_in_profile(manifests, "blender-mcp", "localhost", "default") is True
    assert option_in_profile(manifests, "blender-mcp", "localhost", "prod") is False
    assert option_in_profile(manifests, "blender-mcp", "disabled", "prod") is True


def test_blender_mcp_track_membership_and_cli_mapping() -> None:
    registry = load_tracks()
    mgr = SourceOverrideManager(ConfigParser(str(ROOT)))
    start_py = (ROOT / "bootstrapper" / "start.py").read_text(encoding="utf-8")

    assert is_in_track(
        registry.by_key["gen-ai-creative"],
        "blender-mcp",
        always_on=registry.always_on,
    ) is True
    assert is_in_track(
        registry.by_key["gen-ai-rag"],
        "blender-mcp",
        always_on=registry.always_on,
    ) is False
    assert mgr.source_mapping["blender_mcp_source"] == "BLENDER_MCP_SOURCE"
    assert mgr.collect_overrides(blender_mcp_source="localhost") == {
        "BLENDER_MCP_SOURCE": "localhost",
    }
    assert "@click.option('--blender-mcp-source'" in start_py
    assert "'blender_mcp_source': blender_mcp_source" in start_py


def test_blender_mcp_does_not_get_a_kong_route_or_compose_fragment() -> None:
    from utils.kong_config_generator import KongConfigGenerator

    cp = ConfigParser(str(ROOT))
    generator = KongConfigGenerator(cp)

    def _stub_load() -> None:
        generator.env_vars = cp.parse_env_file()
        generator.env_vars.update({
            "BLENDER_MCP_SOURCE": "localhost",
            "BLENDER_MCP_PORT": "9876",
        })

    generator.load_environment_variables = _stub_load  # type: ignore[method-assign]
    config = generator.generate_kong_config()
    by_host = {
        host: svc["name"]
        for svc in config["services"]
        for route in svc.get("routes", [])
        for host in (route.get("hosts") or [])
    }

    assert "blender-mcp.localhost" not in by_host
    assert "blender.localhost" not in by_host
    assert not any(svc["name"] == "blender-mcp" for svc in config["services"])
    assert not (SERVICES / "blender-mcp" / "compose.yml").exists()


def test_env_example_and_docs_site_include_blender_mcp_without_kong_alias() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    service_page = ROOT / "docs" / "site" / "services" / "blender-mcp.md"
    source_values = ROOT / "docs" / "site" / "reference" / "source-values.md"
    ports_routes = ROOT / "docs" / "site" / "reference" / "ports-routes.md"

    assert "BLENDER_MCP_SOURCE=disabled" in env_example
    assert "BLENDER_MCP_HOST=localhost" in env_example
    assert "BLENDER_MCP_LOCALHOST_PORT=9876" in env_example
    assert service_page.exists()
    assert "services/blender-mcp/README.md" in service_page.read_text(encoding="utf-8")
    assert "BLENDER_MCP_SOURCE" in source_values.read_text(encoding="utf-8")
    assert "blender-mcp.localhost" not in ports_routes.read_text(encoding="utf-8")


def test_blender_mcp_readme_documents_security_and_setup_contract() -> None:
    text = README.read_text(encoding="utf-8")

    for required in [
        "## 1. Overview",
        "## 2. Access",
        "## 3. Configuration",
        "## 4. Architecture & Wiring",
        "## 5. Dependencies & Integrations",
        "## 6. Security & Guardrails",
        "BLENDER_MCP_SOURCE=disabled",
        "BLENDER_MCP_SOURCE=localhost",
        "BLENDER_MCP_LOCALHOST_PORT",
        "9876",
        "No Kong route",
        "host-installed Blender",
        "execute generated Python code",
        "glTF-Transform",
        "gltf-transform-postprocess.sh",
    ]:
        assert required in text


def test_gltf_transform_postprocess_script_contract() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    mode = SCRIPT.stat().st_mode

    assert mode & os.X_OK
    assert "@gltf-transform/cli@4.4.1" in text
    assert "gltf-transform inspect" in text
    assert "gltf-transform validate" in text
    assert "gltf-transform optimize" in text
    assert "--compress meshopt" in text
    assert "--texture-compress webp" in text
    assert "docker run" in text
    assert re.search(r"Usage: .*gltf-transform-postprocess\.sh", text)
