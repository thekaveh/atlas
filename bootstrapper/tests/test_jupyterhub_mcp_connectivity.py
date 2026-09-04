"""JupyterHub's source-aware curated MCP wiring contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig


ROOT = Path(__file__).resolve().parents[2]
JUPYTER_MANIFEST = ROOT / "services" / "jupyterhub" / "service.yml"
JUPYTER_COMPOSE = ROOT / "services" / "jupyterhub" / "compose.yml"
MCP_MANIFEST = ROOT / "services" / "mcp-servers" / "service.yml"
MCP_RUNTIME = (
    ROOT / "services" / "mcp-servers" / "runtime" / "atlas_mcp_server.py"
)
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"
INTERNAL_MCP_URL = "http://mcp-servers:8000/mcp"


def _generated_environment(env_path: Path) -> dict[str, str]:
    parser = ConfigParser(str(ROOT))
    parser.env_file_path = env_path
    config = ServiceConfig(config_parser=parser)
    config.localhost_host = "localhost"
    return config.generate_service_environment()


@pytest.mark.parametrize(
    ("source", "expected"),
    (("container", INTERNAL_MCP_URL), ("disabled", "")),
)
def test_mcp_notebook_endpoint_is_generated_from_the_supported_source_modes(
    env_with_overrides, source: str, expected: str
) -> None:
    env = _generated_environment(
        env_with_overrides(
            {
                "MCP_SERVERS_SOURCE": source,
                "MCP_SERVERS_URL": "http://stale.invalid/mcp",
                "NEO4J_GRAPH_DB_SOURCE": "container",
                "SEARXNG_SOURCE": "container",
            }
        )
    )

    assert env["MCP_SERVERS_URL"] == expected


def test_jupyterhub_compose_injects_only_the_generated_mcp_endpoint() -> None:
    compose = yaml.safe_load(JUPYTER_COMPOSE.read_text(encoding="utf-8"))

    assert compose["services"]["jupyterhub"]["environment"][
        "MCP_SERVERS_URL"
    ] == "${MCP_SERVERS_URL:-}"


def test_internal_url_matches_mcp_service_dns_port_path_and_transport() -> None:
    compose = yaml.safe_load(
        (ROOT / "services" / "mcp-servers" / "compose.yml").read_text(
            encoding="utf-8"
        )
    )
    service = compose["services"]["mcp-servers"]
    assert service["networks"] == ["backend-network"]
    assert service["ports"] == ["127.0.0.1:${MCP_SERVERS_PORT}:8000"]

    spec = importlib.util.spec_from_file_location(
        "atlas_mcp_server_jupyter_contract", MCP_RUNTIME
    )
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    captured: dict[str, object] = {}

    class _Server:
        def run(self, **kwargs) -> None:
            captured.update(kwargs)

    runtime.run_server(_Server())
    assert captured == {
        "transport": "http",
        "host": "0.0.0.0",
        "port": 8000,
        "path": "/mcp",
        "stateless_http": True,
        "json_response": True,
        "host_origin_protection": True,
        "allowed_hosts": ["mcp-servers", "mcp.localhost"],
    }
    assert INTERNAL_MCP_URL == "http://mcp-servers:8000/mcp"


def test_mcp_source_contract_does_not_invent_an_unsupported_localhost_mode() -> None:
    manifest = yaml.safe_load(MCP_MANIFEST.read_text(encoding="utf-8"))
    jupyter = yaml.safe_load(JUPYTER_MANIFEST.read_text(encoding="utf-8"))

    assert [option["id"] for option in manifest["sources"]["options"]] == [
        "container",
        "disabled",
    ]
    env_vars = {row["name"]: row for row in jupyter["env"]}
    assert env_vars["MCP_SERVERS_URL"]["auto_managed"] is True
    assert "default" not in env_vars["MCP_SERVERS_URL"]
    assert jupyter["runtime_adaptive"]["jupyterhub"]["environment_adaptation"][
        "MCP_SERVERS_URL"
    ] == INTERNAL_MCP_URL


def test_jupyterhub_manifest_reports_the_exercised_mcp_capability_truthfully() -> None:
    manifest = yaml.safe_load(JUPYTER_MANIFEST.read_text(encoding="utf-8"))
    capability = next(
        row
        for row in manifest["capabilities"]
        if row["name"] == "Curated MCP notebook endpoint"
    )

    assert (capability["status"], capability["verification"]) == (
        "supported",
        "tested",
    )
    assert "only when MCP_SERVERS_SOURCE=container" in capability["note"]
    assert "empty when disabled" in capability["note"]
    assert "bypasses Kong authentication" in capability["note"]


def test_changelog_does_not_claim_the_obsolete_first_tool_invocation() -> None:
    changelog = CHANGELOG.read_text(encoding="utf-8")

    assert "bounded `searxng_web_search` smoke" in changelog
    assert "filling required string arguments from the tool's own" not in changelog
