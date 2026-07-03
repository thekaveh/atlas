from __future__ import annotations

from pathlib import Path
import runpy
from unittest.mock import MagicMock

import pytest
import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig
from services.topology import get_topology, invalidate_cache
from tracks import is_in_track, load_tracks
from utils.key_generator import KeyGenerator
from utils.source_override_manager import SourceOverrideManager


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "crawl4ai"
MANIFEST = SERVICE_DIR / "service.yml"
COMPOSE = SERVICE_DIR / "compose.yml"
README = SERVICE_DIR / "README.md"
LDR_PATCH = (
    REPO_ROOT
    / "services"
    / "local-deep-researcher"
    / "build"
    / "scripts"
    / "patch-crawl4ai-fetch.py"
)


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_crawl4ai_manifest_admission_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "crawl4ai"
    assert manifest["category"] == "media"
    assert manifest["containers"] == ["crawl4ai"]
    assert manifest["sources"]["var"] == "CRAWL4AI_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert {option["id"] for option in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert manifest["images"] == [
        {
            "var": "CRAWL4AI_IMAGE",
            "default": "unclecode/crawl4ai:0.9.0",
            "container": "crawl4ai",
        }
    ]
    assert manifest["depends_on"]["required"] == []
    assert set(manifest["depends_on"].get("optional", [])) >= {
        "local-deep-researcher",
        "n8n",
    }
    assert manifest["data_flow"]["calls"] == []

    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["CRAWL4AI_SOURCE"]["default"] == "disabled"
    assert env_vars["CRAWL4AI_SCALE"]["auto_managed"] is True
    assert env_vars["CRAWL4AI_ENDPOINT"]["auto_managed"] is True
    assert env_vars["CRAWL4AI_API_TOKEN"]["secret"] is True
    assert env_vars["CRAWL4AI_TIMEOUT_SECONDS"]["default"] == 30
    assert env_vars["CRAWL4AI_MAX_CHARS"]["default"] == 60000
    assert "default" not in env_vars["CRAWL4AI_PORT"]

    row = manifest["rows"][0]
    assert row["display_name"] == "Crawl4AI"
    assert row["source_var"] == "CRAWL4AI_SOURCE"
    assert row["port_var"] == "CRAWL4AI_PORT"
    assert row["scale_var"] == "CRAWL4AI_SCALE"
    assert row["alias"] == "crawl4ai.localhost"


def test_crawl4ai_topology_alias_and_env_example_contract() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = [row for row in topology.rows if row.manifest == "crawl4ai"]

    assert len(rows) == 1
    assert rows[0].category == "media"
    assert rows[0].alias == "crawl4ai.localhost"
    assert "crawl4ai.localhost" in topology.aliases
    assert "CRAWL4AI_PORT" in topology.port_defaults

    env_example = (REPO_ROOT / ".env.example").read_text()
    for expected in (
        "CRAWL4AI_SOURCE=disabled",
        "CRAWL4AI_IMAGE=unclecode/crawl4ai:0.9.0",
        "CRAWL4AI_PORT=",
        "CRAWL4AI_ENDPOINT=",
        "CRAWL4AI_API_TOKEN=",
        "CRAWL4AI_SCALE=",
        "CRAWL4AI_TIMEOUT_SECONDS=30",
        "CRAWL4AI_MAX_CHARS=60000",
        "LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE=disabled",
    ):
        assert expected in env_example


def test_crawl4ai_track_membership_is_rag_only() -> None:
    registry = load_tracks()

    for track_key in ("gen-ai-rag", "all"):
        assert is_in_track(
            registry.by_key[track_key],
            "crawl4ai",
            always_on=registry.always_on,
        )

    for track_key in ("gen-ai-eng", "gen-ai-creative", "ml-eng", "data-eng"):
        assert not is_in_track(
            registry.by_key[track_key],
            "crawl4ai",
            always_on=registry.always_on,
        )


def test_crawl4ai_source_cli_mapping_exists() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))

    assert mgr.source_mapping["crawl4ai_source"] == "CRAWL4AI_SOURCE"
    assert mgr.collect_overrides(crawl4ai_source="container") == {
        "CRAWL4AI_SOURCE": "container",
    }


def test_crawl4ai_scale_generation_and_ldr_mode_gate() -> None:
    sc = ServiceConfig(config_parser=MagicMock())

    sc.service_sources = {"CRAWL4AI_SOURCE": "disabled"}
    assert sc._generate_crawl4ai_config() == {
        "CRAWL4AI_SCALE": "0",
        "CRAWL4AI_ENDPOINT": "",
    }

    sc.service_sources = {"CRAWL4AI_SOURCE": "container"}
    assert sc._generate_crawl4ai_config() == {
        "CRAWL4AI_SCALE": "1",
        "CRAWL4AI_ENDPOINT": "http://crawl4ai:11235",
    }

    sc.config_parser.parse_env_file.return_value = {
        "LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE": "disabled",
        "CRAWL4AI_SOURCE": "disabled",
    }
    sc.service_sources = {"CRAWL4AI_SOURCE": "disabled"}
    assert sc._generate_local_deep_researcher_extraction_config() == {
        "FETCH_FULL_PAGE": "false",
        "LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE": "disabled",
        "CRAWL4AI_ENDPOINT": "",
    }

    sc.config_parser.parse_env_file.return_value = {
        "LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE": "builtin",
        "CRAWL4AI_SOURCE": "disabled",
    }
    assert sc._generate_local_deep_researcher_extraction_config() == {
        "FETCH_FULL_PAGE": "true",
        "LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE": "builtin",
        "CRAWL4AI_ENDPOINT": "",
    }

    sc.config_parser.parse_env_file.return_value = {
        "LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE": "crawl4ai",
        "CRAWL4AI_SOURCE": "container",
    }
    sc.service_sources = {"CRAWL4AI_SOURCE": "container"}
    assert sc._generate_local_deep_researcher_extraction_config() == {
        "FETCH_FULL_PAGE": "true",
        "LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE": "crawl4ai",
        "CRAWL4AI_ENDPOINT": "http://crawl4ai:11235",
    }

    sc.config_parser.parse_env_file.return_value = {
        "LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE": "crawl4ai",
        "CRAWL4AI_SOURCE": "disabled",
    }
    sc.service_sources = {"CRAWL4AI_SOURCE": "disabled"}
    with pytest.raises(ValueError, match="Local Deep Researcher Crawl4AI"):
        sc._generate_local_deep_researcher_extraction_config()

    sc.config_parser.parse_env_file.return_value = {
        "LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE": "surprise",
    }
    with pytest.raises(ValueError, match="LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE"):
        sc._generate_local_deep_researcher_extraction_config()


def test_crawl4ai_compose_contract() -> None:
    service = _compose()["services"]["crawl4ai"]

    assert service["image"] == "${CRAWL4AI_IMAGE:-unclecode/crawl4ai:0.9.0}"
    assert service["ports"] == ["${HOST_BIND_IP:-}${CRAWL4AI_PORT}:11235"]
    assert service["deploy"]["replicas"] == "${CRAWL4AI_SCALE:-0}"
    assert service["environment"]["CRAWL4AI_API_TOKEN"] == "${CRAWL4AI_API_TOKEN}"
    assert service["environment"]["CRAWL4AI_ALLOW_INTERNAL_URLS"] == "${CRAWL4AI_ALLOW_INTERNAL_URLS:-false}"
    assert service["shm_size"] == "1gb"
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["pids_limit"] == 512
    assert service["read_only"] is True
    assert set(service["tmpfs"]) >= {
        "/tmp",
        "/home/appuser/.cache",
        "/var/lib/redis",
        "/var/lib/crawl4ai/outputs:mode=0700",
    }
    assert "curl -fsS http://localhost:11235/health" in service["healthcheck"]["test"]


def test_key_generator_creates_crawl4ai_api_token(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("PROJECT_NAME=atlas-test\nCRAWL4AI_API_TOKEN=\n")

    results = KeyGenerator(str(tmp_path)).generate_missing_keys()
    generated = ConfigParser(str(tmp_path)).parse_env_file()

    assert results["CRAWL4AI_API_TOKEN"] is True
    assert generated["CRAWL4AI_API_TOKEN"]
    assert len(generated["CRAWL4AI_API_TOKEN"]) >= 32


def test_local_deep_researcher_receives_crawl4ai_runtime_env_and_patch() -> None:
    manifest = yaml.safe_load(
        (REPO_ROOT / "services" / "local-deep-researcher" / "service.yml").read_text()
    )
    compose = yaml.safe_load(
        (REPO_ROOT / "services" / "local-deep-researcher" / "compose.yml").read_text()
    )
    env_vars = {entry["name"]: entry for entry in manifest["env"]}

    assert env_vars["LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE"]["default"] == "disabled"
    assert "crawl4ai" in manifest["depends_on"]["optional"]
    assert "crawl4ai" in manifest["data_flow"]["calls"]

    service_env = compose["services"]["local-deep-researcher"]["environment"]
    assert service_env["FETCH_FULL_PAGE"] == "${FETCH_FULL_PAGE:-false}"
    assert service_env["LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE"] == (
        "${LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE:-disabled}"
    )
    assert service_env["CRAWL4AI_ENDPOINT"] == "${CRAWL4AI_ENDPOINT:-}"
    assert service_env["CRAWL4AI_API_TOKEN"] == "${CRAWL4AI_API_TOKEN:-}"
    assert service_env["CRAWL4AI_TIMEOUT_SECONDS"] == "${CRAWL4AI_TIMEOUT_SECONDS:-30}"
    assert service_env["CRAWL4AI_MAX_CHARS"] == "${CRAWL4AI_MAX_CHARS:-60000}"

    patch = LDR_PATCH.read_text()
    assert "def crawl4ai_fetch_raw_content" in patch
    assert "Authorization" in patch
    assert "/crawl" in patch

    entrypoint = (
        REPO_ROOT
        / "services"
        / "local-deep-researcher"
        / "build"
        / "scripts"
        / "docker-entrypoint.sh"
    ).read_text()
    assert "patch-crawl4ai-fetch.py" in entrypoint


def test_local_deep_researcher_crawl4ai_patch_parses_v090_sync_response() -> None:
    script_globals = runpy.run_path(str(LDR_PATCH))
    replacement = script_globals["_crawl4ai_fetch_replacement"]()

    namespace = {
        "os": MagicMock(),
        "httpx": MagicMock(),
        "Optional": str | None,
    }
    namespace["os"].getenv.side_effect = lambda name, default="": {
        "CRAWL4AI_ENDPOINT": "http://crawl4ai:11235",
        "CRAWL4AI_API_TOKEN": "secret-token",
        "CRAWL4AI_TIMEOUT_SECONDS": "30",
        "CRAWL4AI_MAX_CHARS": "12",
    }.get(name, default)

    response = MagicMock()
    response.json.return_value = {
        "results": [
            {
                "success": True,
                "markdown": "hello rendered markdown",
            }
        ]
    }
    client = MagicMock()
    client.post.return_value = response
    namespace["httpx"].Client.return_value.__enter__.return_value = client

    exec(replacement, namespace)

    assert namespace["fetch_raw_content"]("https://example.com") == "hello render"
    client.post.assert_called_once_with(
        "http://crawl4ai:11235/crawl",
        json={"urls": ["https://example.com"], "priority": 10},
        headers={"Authorization": "Bearer secret-token"},
    )
    response.raise_for_status.assert_called_once()


def test_n8n_receives_crawl4ai_runtime_env() -> None:
    manifest = yaml.safe_load((REPO_ROOT / "services" / "n8n" / "service.yml").read_text())
    compose = yaml.safe_load((REPO_ROOT / "services" / "n8n" / "compose.yml").read_text())

    adaptation = manifest["runtime_adaptive"]["n8n"]
    assert "crawl4ai" in adaptation["adapts_to"]
    assert adaptation["environment_adaptation"]["CRAWL4AI_ENDPOINT"] == "${CRAWL4AI_ENDPOINT}"
    assert adaptation["environment_adaptation"]["CRAWL4AI_API_TOKEN"] == "${CRAWL4AI_API_TOKEN}"
    assert "crawl4ai" in manifest["data_flow"]["calls"]

    for service_name in ("n8n", "n8n-worker"):
        env = compose["services"][service_name]["environment"]
        assert env["CRAWL4AI_ENDPOINT"] == "${CRAWL4AI_ENDPOINT:-}"
        assert env["CRAWL4AI_API_TOKEN"] == "${CRAWL4AI_API_TOKEN:-}"


def test_crawl4ai_kong_route_only_when_container() -> None:
    from utils.kong_config_generator import KongConfigGenerator

    def _config(env: dict[str, str]) -> dict:
        cp = ConfigParser(str(REPO_ROOT))
        gen = KongConfigGenerator(cp)
        gen.load_environment_variables = lambda: setattr(gen, "env_vars", env)
        return gen.generate_kong_config()

    enabled = _config({"CRAWL4AI_SOURCE": "container"})
    disabled = _config({"CRAWL4AI_SOURCE": "disabled"})

    enabled_hosts = {
        host: service
        for service in enabled["services"]
        for route in service.get("routes", [])
        for host in route.get("hosts") or []
    }
    disabled_hosts = {
        host: service
        for service in disabled["services"]
        for route in service.get("routes", [])
        for host in route.get("hosts") or []
    }
    assert enabled_hosts["crawl4ai.localhost"]["name"] == "crawl4ai"
    assert enabled_hosts["crawl4ai.localhost"]["url"] == "http://crawl4ai:11235/"
    assert enabled_hosts["crawl4ai.localhost"]["routes"][0]["preserve_host"] is True
    assert {plugin["name"] for plugin in enabled_hosts["crawl4ai.localhost"]["plugins"]} >= {
        "basic-auth",
        "acl",
        "cors",
    }
    assert "crawl4ai.localhost" not in disabled_hosts


def test_crawl4ai_docs_describe_security_mcp_n8n_and_deferrals() -> None:
    docs = README.read_text()
    candidate = (REPO_ROOT / "docs" / "research" / "candidates" / "crawl4ai.md").read_text()

    for expected in (
        "CRAWL4AI_API_TOKEN",
        "crawl4ai.localhost",
        "Local Deep Researcher",
        "n8n",
        "/mcp/sse",
        "/mcp/ws",
        "Authorization: Bearer",
        "not registered into the curated MCP package",
    ):
        assert expected in docs

    assert "unclecode/crawl4ai:0.9.0" in candidate
    assert "secure-by-default" in candidate
