from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig
from services.topology import get_topology, invalidate_cache
from tracks import is_in_track, load_tracks
from utils.kong_config_generator import KongConfigGenerator
from utils.source_override_manager import SourceOverrideManager


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "verba"
MANIFEST = SERVICE_DIR / "service.yml"
COMPOSE = SERVICE_DIR / "compose.yml"
README = SERVICE_DIR / "README.md"
IMAGE = (
    "semitechnologies/verba@"
    "sha256:0947d289ebff2c9814941c8d4282ee994dc79598e76162ae82e6efda4682b0b7"
)


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_verba_manifest_admission_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "verba"
    assert manifest["category"] == "apps"
    assert manifest["containers"] == ["verba"]
    assert manifest["sources"]["var"] == "VERBA_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert {option["id"] for option in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert manifest["depends_on"]["required"] == ["weaviate", "litellm", "kong"]
    assert manifest["depends_on"].get("optional", []) == [
        "docling",
        "open-webui",
        "jupyterhub",
    ]
    assert manifest["data_flow"]["calls"] == ["weaviate", "litellm"]

    images = {entry["var"]: entry for entry in manifest["images"]}
    assert images["VERBA_IMAGE"]["default"] == IMAGE

    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["VERBA_SOURCE"]["default"] == "disabled"
    assert "default" not in env_vars["VERBA_PORT"]
    assert env_vars["VERBA_OPENAI_MODEL"]["default"] == ""
    assert env_vars["VERBA_OPENAI_EMBED_MODEL"]["default"] == ""
    assert env_vars["VERBA_DEFAULT_DEPLOYMENT"]["default"] == "Docker"
    for auto_var in ("VERBA_ENDPOINT", "VERBA_SCALE", "VERBA_WEAVIATE_URL"):
        assert env_vars[auto_var]["auto_managed"] is True

    row = manifest["rows"][0]
    assert row["display_name"] == "Verba"
    assert row["source_var"] == "VERBA_SOURCE"
    assert row["port_var"] == "VERBA_PORT"
    assert row["scale_var"] == "VERBA_SCALE"
    assert row["alias"] == "verba.localhost"


def test_verba_topology_track_and_env_example_contract() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = [row for row in topology.rows if row.manifest == "verba"]

    assert len(rows) == 1
    assert rows[0].category == "apps"
    assert rows[0].alias == "verba.localhost"
    assert "verba.localhost" in topology.aliases
    assert "VERBA_PORT" in topology.port_defaults

    registry = load_tracks()
    for track_key in ("gen-ai-rag", "all"):
        assert is_in_track(
            registry.by_key[track_key],
            "verba",
            always_on=registry.always_on,
        )
    for track_key in ("gen-ai-eng", "gen-ai-creative", "ml-eng", "data-eng"):
        assert not is_in_track(
            registry.by_key[track_key],
            "verba",
            always_on=registry.always_on,
        )

    env_example = (REPO_ROOT / ".env.example").read_text()
    for expected in (
        "VERBA_SOURCE=disabled",
        f"VERBA_IMAGE={IMAGE}",
        "VERBA_PORT=",
        "VERBA_ENDPOINT=",
        "VERBA_SCALE=",
        "VERBA_WEAVIATE_URL=",
        "VERBA_OPENAI_MODEL=",
        "VERBA_OPENAI_EMBED_MODEL=",
        "VERBA_DEFAULT_DEPLOYMENT=Docker",
    ):
        assert expected in env_example


def test_verba_source_cli_mapping_exists() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))

    assert mgr.source_mapping["verba_source"] == "VERBA_SOURCE"
    assert mgr.collect_overrides(verba_source="container") == {
        "VERBA_SOURCE": "container",
    }


def test_verba_scale_endpoint_and_weaviate_gate() -> None:
    sc = ServiceConfig(config_parser=MagicMock())

    sc.service_sources = {"VERBA_SOURCE": "disabled", "WEAVIATE_SOURCE": "disabled"}
    assert sc._generate_verba_config() == {
        "VERBA_SCALE": "0",
        "VERBA_ENDPOINT": "",
        "VERBA_WEAVIATE_URL": "",
    }

    sc.service_sources = {"VERBA_SOURCE": "container", "WEAVIATE_SOURCE": "container"}
    assert sc._generate_verba_config() == {
        "VERBA_SCALE": "1",
        "VERBA_ENDPOINT": "http://verba:8000",
        "VERBA_WEAVIATE_URL": "http://weaviate:8080",
    }

    sc.config_parser.parse_env_file.return_value = {"WEAVIATE_LOCALHOST_PORT": "18080"}
    sc.service_sources = {"VERBA_SOURCE": "container", "WEAVIATE_SOURCE": "localhost"}
    assert sc._generate_verba_config()["VERBA_WEAVIATE_URL"] == (
        f"http://{sc.localhost_host}:18080"
    )

    sc.service_sources = {"VERBA_SOURCE": "container", "WEAVIATE_SOURCE": "disabled"}
    with pytest.raises(ValueError, match="Verba requires Weaviate"):
        sc._generate_verba_config()


def test_verba_compose_contract() -> None:
    service = _compose()["services"]["verba"]

    assert service["image"] == f"${{VERBA_IMAGE:-{IMAGE}}}"
    assert service["deploy"]["replicas"] == "${VERBA_SCALE:-0}"
    assert service["depends_on"]["weaviate"]["condition"] == "service_healthy"
    assert service["depends_on"]["litellm"]["condition"] == "service_started"
    assert service["ports"] == ["${HOST_BIND_IP:-}${VERBA_PORT}:8000"]
    assert service["volumes"] == ["verba-data:/data"]

    env = service["environment"]
    assert env["WEAVIATE_URL_VERBA"] == "${VERBA_WEAVIATE_URL:-http://weaviate:8080}"
    assert env["OPENAI_API_KEY"] == "${LITELLM_MASTER_KEY}"
    assert env["OPENAI_BASE_URL"] == "http://litellm:4000/v1"
    assert env["OPENAI_EMBED_API_KEY"] == "${LITELLM_MASTER_KEY}"
    assert env["OPENAI_EMBED_BASE_URL"] == "http://litellm:4000/v1"
    assert env["OPENAI_CUSTOM_EMBED"] == "true"
    assert env["OPENAI_MODEL"] == "${VERBA_OPENAI_MODEL:-}"
    assert env["OPENAI_EMBED_MODEL"] == "${VERBA_OPENAI_EMBED_MODEL:-}"
    assert env["DEFAULT_DEPLOYMENT"] == "${VERBA_DEFAULT_DEPLOYMENT:-Docker}"
    assert env["UNSTRUCTURED_API_URL"] == ""
    assert env["UNSTRUCTURED_API_KEY"] == ""


def test_verba_kong_route_only_when_container(tmp_path: Path) -> None:
    def _hosts(env_text: str) -> dict[str, dict]:
        env_path = tmp_path / ".env"
        env_path.write_text(
            env_text
            + "\nDASHBOARD_USERNAME=u\nDASHBOARD_PASSWORD=p\nKONG_HTTP_PORT=64000\n",
            encoding="utf-8",
        )
        gen = KongConfigGenerator(ConfigParser(str(tmp_path)))
        config = gen.generate_kong_config()
        return {
            host: service
            for service in config["services"]
            for route in service.get("routes", [])
            for host in route.get("hosts") or []
        }

    enabled_hosts = _hosts("VERBA_SOURCE=container\n")
    disabled_hosts = _hosts("VERBA_SOURCE=disabled\n")

    assert enabled_hosts["verba.localhost"]["name"] == "verba"
    assert enabled_hosts["verba.localhost"]["url"] == "http://verba:8000/"
    assert enabled_hosts["verba.localhost"]["routes"][0]["preserve_host"] is True
    assert {plugin["name"] for plugin in enabled_hosts["verba.localhost"]["plugins"]} >= {
        "basic-auth",
        "acl",
        "cors",
    }
    assert "verba.localhost" not in disabled_hosts


def test_verba_docs_describe_archived_status_integrations_and_sample_path() -> None:
    readme = README.read_text()

    for expected in (
        "VERBA_SOURCE=disabled",
        "verba.localhost",
        "Track: `gen-ai-rag`",
        "Category: `apps`",
        "archived",
        "discontinued",
        "semitechnologies/verba",
        "Weaviate",
        "LiteLLM",
        "Docling",
        "namespaced",
        "VERBA_Document",
        "sample ingest/query",
        "single-user",
    ):
        assert expected in readme
