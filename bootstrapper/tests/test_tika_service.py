from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig
from services.topology import get_topology, invalidate_cache
from tracks import is_in_track, load_tracks
from utils.kong_config_generator import KongConfigGenerator
from utils.source_override_manager import SourceOverrideManager


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "tika"
MANIFEST = SERVICE_DIR / "service.yml"
COMPOSE = SERVICE_DIR / "compose.yml"
README = SERVICE_DIR / "README.md"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_tika_manifest_admission_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "tika"
    assert manifest["category"] == "media"
    assert manifest["containers"] == ["tika"]
    assert manifest["sources"]["var"] == "TIKA_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert {option["id"] for option in manifest["sources"]["options"]} == {
        "container",
        "tika-localhost",
        "disabled",
    }
    assert manifest["images"] == [
        {
            "var": "TIKA_IMAGE",
            "default": "apache/tika:3.3.1.0",
            "container": "tika",
        }
    ]
    assert manifest["depends_on"]["required"] == []
    assert set(manifest["depends_on"].get("optional", [])) >= {"backend", "n8n"}
    assert manifest["data_flow"]["calls"] == []

    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["TIKA_SOURCE"]["default"] == "disabled"
    assert "default" not in env_vars["TIKA_PORT"]
    assert env_vars["TIKA_LOCALHOST_PORT"]["default"] == "9998"
    assert env_vars["TIKA_SCALE"]["auto_managed"] is True
    assert env_vars["TIKA_ENDPOINT"]["auto_managed"] is True
    assert env_vars["TIKA_MAX_FILE_SIZE"]["default"] == 52428800
    assert env_vars["TIKA_TIMEOUT_SECONDS"]["default"] == 30

    row = manifest["rows"][0]
    assert row["display_name"] == "Apache Tika"
    assert row["source_var"] == "TIKA_SOURCE"
    assert row["port_var"] == "TIKA_PORT"
    assert row["scale_var"] == "TIKA_SCALE"
    assert row["alias"] == "tika.localhost"
    assert row["localhost_endpoint_var"] == "TIKA_ENDPOINT"
    assert row["localhost_port_var"] == "TIKA_LOCALHOST_PORT"


def test_tika_topology_track_and_env_example_contract() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = [row for row in topology.rows if row.manifest == "tika"]

    assert len(rows) == 1
    assert rows[0].category == "media"
    assert rows[0].alias == "tika.localhost"
    assert "tika.localhost" in topology.aliases
    assert "TIKA_PORT" in topology.port_defaults

    registry = load_tracks()
    for track_key in ("gen-ai-rag", "gen-ai-eng", "all"):
        assert is_in_track(
            registry.by_key[track_key],
            "tika",
            always_on=registry.always_on,
        )
    for track_key in ("gen-ai-creative", "ml-eng", "data-eng"):
        assert not is_in_track(
            registry.by_key[track_key],
            "tika",
            always_on=registry.always_on,
        )

    env_example = (REPO_ROOT / ".env.example").read_text()
    for expected in (
        "TIKA_SOURCE=disabled",
        "TIKA_IMAGE=apache/tika:3.3.1.0",
        "TIKA_PORT=",
        "TIKA_LOCALHOST_PORT=9998",
        "TIKA_SCALE=",
        "TIKA_ENDPOINT=",
        "TIKA_MAX_FILE_SIZE=52428800",
        "TIKA_TIMEOUT_SECONDS=30",
    ):
        assert expected in env_example


def test_tika_source_cli_mapping_exists() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))

    assert mgr.source_mapping["tika_source"] == "TIKA_SOURCE"
    assert mgr.collect_overrides(tika_source="container") == {
        "TIKA_SOURCE": "container",
    }


def test_tika_scale_and_endpoint_generation() -> None:
    sc = ServiceConfig(config_parser=MagicMock())
    sc.localhost_host = "host.docker.internal"

    sc.service_sources = {"TIKA_SOURCE": "disabled"}
    sc.config_parser.parse_env_file.return_value = {"TIKA_LOCALHOST_PORT": "9998"}
    assert sc._generate_tika_config() == {
        "TIKA_SCALE": "0",
        "TIKA_ENDPOINT": "",
    }

    sc.service_sources = {"TIKA_SOURCE": "container"}
    assert sc._generate_tika_config() == {
        "TIKA_SCALE": "1",
        "TIKA_ENDPOINT": "http://tika:9998",
    }

    sc.service_sources = {"TIKA_SOURCE": "tika-localhost"}
    sc.config_parser.parse_env_file.return_value = {"TIKA_LOCALHOST_PORT": "7777"}
    assert sc._generate_tika_config() == {
        "TIKA_SCALE": "0",
        "TIKA_ENDPOINT": "http://host.docker.internal:7777",
    }


def test_tika_compose_contract() -> None:
    service = _compose()["services"]["tika"]

    assert service["image"] == "${TIKA_IMAGE:-apache/tika:3.3.1.0}"
    assert service["deploy"]["replicas"] == "${TIKA_SCALE:-0}"
    assert service["ports"] == ["${HOST_BIND_IP:-}${TIKA_PORT}:9998"]
    assert service["environment"]["JAVA_TOOL_OPTIONS"] == "${TIKA_JAVA_TOOL_OPTIONS:--Xmx768m}"
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["pids_limit"] == 512
    assert set(service["tmpfs"]) >= {"/tmp:mode=1777"}
    # apache/tika:3.3.1.0 has bash but no curl/wget/nc, so readiness is a bash
    # /dev/tcp connect to the listen port (a curl probe could never succeed).
    assert service["healthcheck"]["test"] == [
        "CMD", "bash", "-c", "exec 3<>/dev/tcp/localhost/9998",
    ]


def test_tika_kong_routes_container_localhost_and_disabled(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TIKA_SOURCE=container\n"
        "TIKA_LOCALHOST_PORT=7777\n"
        "DASHBOARD_USERNAME=u\n"
        "DASHBOARD_PASSWORD=p\n",
        encoding="utf-8",
    )
    gen = KongConfigGenerator(ConfigParser(str(tmp_path)))
    config = gen.generate_kong_config()
    by_host = {
        host: service["url"]
        for service in config["services"]
        for route in service.get("routes", [])
        for host in route.get("hosts") or []
    }
    assert by_host["tika.localhost"] == "http://tika:9998/"

    env_path.write_text(
        "TIKA_SOURCE=tika-localhost\n"
        "TIKA_LOCALHOST_PORT=7777\n"
        "DASHBOARD_USERNAME=u\n"
        "DASHBOARD_PASSWORD=p\n",
        encoding="utf-8",
    )
    gen = KongConfigGenerator(ConfigParser(str(tmp_path)))
    config = gen.generate_kong_config()
    by_host = {
        host: service["url"]
        for service in config["services"]
        for route in service.get("routes", [])
        for host in route.get("hosts") or []
    }
    assert by_host["tika.localhost"] == "http://host.docker.internal:7777/"

    env_path.write_text(
        "TIKA_SOURCE=disabled\n"
        "DASHBOARD_USERNAME=u\n"
        "DASHBOARD_PASSWORD=p\n",
        encoding="utf-8",
    )
    gen = KongConfigGenerator(ConfigParser(str(tmp_path)))
    config = gen.generate_kong_config()
    by_host = {
        host
        for service in config["services"]
        for route in service.get("routes", [])
        for host in route.get("hosts") or []
    }
    assert "tika.localhost" not in by_host


def test_tika_docs_describe_fallback_scope_and_guardrails() -> None:
    readme = README.read_text()

    for expected in (
        "TIKA_SOURCE=disabled",
        "Docling-first",
        "EML",
        "MSG",
        "RTF",
        "ODT",
        "ZIP",
        "TIKA_MAX_FILE_SIZE",
        "TIKA_TIMEOUT_SECONDS",
        "tika.localhost",
        "Apache Tika 3.3.1",
    ):
        assert expected in readme
