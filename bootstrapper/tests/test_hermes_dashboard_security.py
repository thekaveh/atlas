"""Configured-gateway dashboard policy, using the real parser and Compose."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
import subprocess

import pytest
import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig
from services.source_validator import SourceValidator
from utils.atomic_write import render_env_assignment
from utils.kong_config_generator import KongConfigGenerator

ROOT = Path(__file__).resolve().parents[2]
_requires_compose = pytest.mark.skipif(shutil.which("docker") is None, reason="docker not on PATH")


def _parser(directory, monkeypatch, values):
    active = directory / "selected.env"
    active.write_text("".join(k + "=" + render_env_assignment(k, v) + "\n" for k, v in values.items()))
    monkeypatch.setenv("ATLAS_ENV_FILE", str(active))
    return ConfigParser(root_dir=str(ROOT))


@pytest.mark.parametrize("case", [
    ({}, "true"),
    ({"HERMES_DASHBOARD_ENABLED": ""}, "true"),
    ({"HERMES_DASHBOARD_ENABLED": "false"}, "false"),
    ({"HERMES_DASHBOARD_ENABLED": "TRUE"}, "TRUE"),
    ({"HERMES_DASHBOARD_ENABLED": "1"}, "1"),
    ({"HERMES_DASHBOARD_ENABLED": "yes"}, "yes"),
    ({"HERMES_DASHBOARD_ENABLED": "on"}, "on"),
    ({"KONG_API_GATEWAY_SOURCE": "container"}, "true"),
    ({"KONG_API_GATEWAY_SOURCE": "disabled"}, "false"),
    ({"KONG_API_GATEWAY_SOURCE": "localhost"}, "false"),
    ({"KONG_API_GATEWAY_SOURCE": ""}, "false"),
    ({"KONG_API_GATEWAY_SOURCE": "unsupported"}, "false"),
    ({"KONG_API_GATEWAY_SOURCE": "disabled", "HERMES_DASHBOARD_EFFECTIVE": "true"}, "false"),
    ({"HERMES_SOURCE": "disabled", "HERMES_DASHBOARD_EFFECTIVE": "true"}, "false"),
    ({"HERMES_SOURCE": "localhost", "HERMES_LOCALHOST_PORT": "18642"}, "false"),
], ids=["default", "blank-request", "explicit-disable", "uppercase", "numeric", "yes", "on",
        "container-kong", "disabled-kong", "localhost-kong", "blank-kong", "unsupported-kong",
        "stale-effective", "disabled-hermes", "localhost-hermes"])
def test_hermes_effective_dashboard_uses_active_launch_sources(case, tmp_path, monkeypatch):
    values, expected = case
    parser = _parser(tmp_path, monkeypatch, values)
    before = parser.env_file_path.read_bytes()
    service = ServiceConfig(parser)
    assert service.load_config()
    actual = service._generate_hermes_config()
    assert actual.get("HERMES_DASHBOARD_EFFECTIVE") == expected, actual
    assert "HERMES_DASHBOARD_ENABLED" not in actual
    assert parser.env_file_path.read_bytes() == before
    source = values.get("HERMES_SOURCE", "container")
    assert actual["HERMES_SCALE"] == ("1" if source == "container" else "0")
    endpoint = {"container": "http://hermes:8642", "disabled": "",
                "localhost": f"http://{service.localhost_host}:18642"}[source]
    assert actual["HERMES_ENDPOINT"] == endpoint
    (tmp_path / "runtime.json").write_text(json.dumps(actual, indent=2))
    _assert_persisted_request_and_effective(service, actual, expected)


def _assert_persisted_request_and_effective(service, computed, expected):
    parser = service.config_parser
    before = parser.parse_env_file()
    assert service.update_env_file(computed, create_backup=False)
    after = parser.parse_env_file()
    assert after["HERMES_DASHBOARD_EFFECTIVE"] == expected
    requested = "HERMES_DASHBOARD_ENABLED"
    assert (requested in after, after.get(requested)) == (requested in before, before.get(requested))
    assert after["HERMES_ENDPOINT"] == computed["HERMES_ENDPOINT"]
    assert after["HERMES_SCALE"] == computed["HERMES_SCALE"]


def _compose(directory, values):
    parser = ConfigParser(root_dir=str(directory))
    parser.env_file_path = ROOT / ".env.example"
    env = parser.parse_env_file()
    env.update(values)
    env = {k: v for k, v in env.items() if v is not None}
    active = directory / "compose.env"
    active.write_text("".join(k + "=" + render_env_assignment(k, v) + "\n" for k, v in env.items()))
    argv = ["docker", "compose", "--env-file", str(active), "-p", "atlas", "-f",
            str(ROOT / "docker-compose.yml"), "config"]
    result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=30)
    (directory / "compose.stdout.yml").write_text(result.stdout)
    (directory / "compose.stderr.txt").write_text(result.stderr)
    (directory / "compose-result.json").write_text(json.dumps(
        {"argv": argv, "returncode": result.returncode}, indent=2))
    assert result.returncode == 0, result.stderr
    return yaml.safe_load(result.stdout)["services"]["hermes"]


@pytest.mark.parametrize("bind", ["127.0.0.1:", "0.0.0.0:", "[::1]:", ""])
@_requires_compose
def test_hermes_dashboard_has_no_host_publish(bind, tmp_path):
    service = _compose(tmp_path, {"HOST_BIND_IP": bind, "HERMES_DASHBOARD_PORT": "65073"})
    assert [p["target"] for p in service["ports"]] == [8642], service["ports"]
    assert service["ports"][0]["published"] == "63072"
    assert service["ports"][0].get("host_ip", "") == bind.rstrip(":").strip("[]")


@pytest.mark.parametrize("client", ["", "agent:owned-test-client"])
@_requires_compose
def test_hermes_oauth_client_passes_through_without_provisioning(client, tmp_path):
    service = _compose(tmp_path, {"HERMES_DASHBOARD_OAUTH_CLIENT_ID": client,
                                  "HERMES_DASHBOARD_INSECURE": "false"})
    assert service["environment"].get("HERMES_DASHBOARD_OAUTH_CLIENT_ID") == client
    assert service["environment"]["HERMES_DASHBOARD_INSECURE"] == "false"


@pytest.mark.parametrize("effective,expected", [(None, "false"), ("", "false"), ("true", "true"), ("false", "false")])
@_requires_compose
def test_hermes_compose_uses_effective_flag_with_safe_raw_fallback(effective, expected, tmp_path):
    values = {"HERMES_DASHBOARD_ENABLED": "true", "HERMES_DASHBOARD_EFFECTIVE": effective}
    service = _compose(tmp_path, values)
    assert service["environment"]["HERMES_DASHBOARD"] == expected


@_requires_compose
def test_hermes_api_auth_and_persistence_controls_stay_intact(tmp_path):
    service = _compose(tmp_path, {"HERMES_API_KEY": "owned-api-key", "HERMES_SCALE": "1"})
    env = service["environment"]
    assert (env["API_SERVER_KEY"], env["API_SERVER_ENABLED"], env["API_SERVER_HOST"]) == (
        "owned-api-key", "true", "0.0.0.0")
    assert env["HERMES_DASHBOARD_INSECURE"] == "true"
    assert service["image"] == "nousresearch/hermes-agent:v2026.6.19"
    assert service["command"] == ["gateway", "run"]
    _assert_hermes_process_and_storage(service)


def _assert_hermes_process_and_storage(service):
    env = service["environment"]
    assert service["deploy"] == {"replicas": 1, "resources": {"limits": {"cpus": 2.0, "memory": "4294967296"}}}
    assert service["volumes"] == [{"type": "volume", "source": "hermes-data", "target": "/opt/data", "volume": {}}]
    assert "user" not in service
    assert (env["HERMES_UID"], env["HERMES_GID"]) == ("10000", "10000")
    assert service["networks"] == {"backend-network": None}
    assert service["logging"] == {"driver": "json-file", "options": {"max-file": "3", "max-size": "10m"}}
    assert service["healthcheck"]["test"][0] == "CMD-SHELL"
    assert '"$$API_SERVER_KEY"' in service["healthcheck"]["test"][1]
    assert "http://127.0.0.1:8642/v1/models" in service["healthcheck"]["test"][1]


def _route(directory, monkeypatch, values):
    generator = KongConfigGenerator(_parser(directory, monkeypatch, values))
    generator.load_environment_variables()
    probes = []
    monkeypatch.setattr(generator, "check_localhost_service", lambda *args: probes.append(args))
    route = generator.generate_hermes_service()
    (directory / "route.json").write_text(json.dumps({"route": route, "probes": probes}, indent=2))
    return route, probes


@pytest.mark.parametrize("kong", ["disabled", "localhost", "", "unsupported"])
def test_hermes_route_omits_unconfigured_gateway_before_localhost_probe(kong, tmp_path, monkeypatch):
    route, probes = _route(tmp_path, monkeypatch, {"KONG_API_GATEWAY_SOURCE": kong, "HERMES_SOURCE": "localhost"})
    assert route is None
    assert probes == []


@pytest.mark.parametrize("source", ["container", "localhost", "disabled"])
def test_hermes_route_keeps_auth_and_api_separation(source, tmp_path, monkeypatch):
    route, probes = _route(tmp_path, monkeypatch, {"HERMES_SOURCE": source})
    if source == "disabled":
        assert route is None and probes == []
        return
    assert route["plugins"] == [{"name": "cors"}, {"name": "basic-auth"},
                                {"name": "acl", "config": {"allow": ["dashboard_user"]}}]
    assert route["routes"] == [{"name": "hermes-dashboard-all", "strip_path": False,
                                "preserve_host": True, "hosts": ["hermes.localhost"]}]
    assert route["url"] == ("http://hermes:9119/" if source == "container" else "http://host.docker.internal:9119/")
    assert probes == ([] if source == "container" else [("localhost", 9119, "Hermes Dashboard")])


def test_hermes_route_respects_explicit_dashboard_disable(tmp_path, monkeypatch):
    route, probes = _route(tmp_path, monkeypatch, {"HERMES_SOURCE": "container", "HERMES_DASHBOARD_ENABLED": "false"})
    assert route is None and probes == []


@pytest.mark.parametrize("requested,enabled", [(None, True), ("", True), ("false", False)],
                         ids=["missing-request", "empty-request", "disabled-request"])
def test_hermes_route_uses_requested_default_consistently(requested, enabled, tmp_path, monkeypatch):
    values = {"HERMES_SOURCE": "container"}
    if requested is not None:
        values["HERMES_DASHBOARD_ENABLED"] = requested
    route, probes = _route(tmp_path, monkeypatch, values)
    assert bool(route) is enabled
    assert probes == []
    if enabled:
        assert route["plugins"] == [{"name": "cors"}, {"name": "basic-auth"},
                                    {"name": "acl", "config": {"allow": ["dashboard_user"]}}]


@pytest.mark.parametrize("kong", [None, "container", " container", "container ", " container "],
                         ids=["missing-kong", "plain-kong", "leading-padding", "trailing-padding", "both-padding"])
def test_hermes_route_matches_validated_configured_container(kong, tmp_path, monkeypatch):
    values = {"HERMES_SOURCE": "container", "HERMES_DASHBOARD_ENABLED": "true"}
    if kong is not None:
        values["KONG_API_GATEWAY_SOURCE"] = kong
    parser = _parser(tmp_path, monkeypatch, values)
    assert parser.parse_env_file().get("KONG_API_GATEWAY_SOURCE") == kong
    normalized = parser.parse_service_sources().get("KONG_API_GATEWAY_SOURCE", "container")
    assert normalized == "container"
    validator = SourceValidator(parser)
    assert validator.load_yaml_config()
    assert validator.validate_source_value("KONG_API_GATEWAY_SOURCE", normalized)
    service = ServiceConfig(parser)
    assert service.load_config()
    assert service._generate_hermes_config()["HERMES_DASHBOARD_EFFECTIVE"] == "true"
    route, probes = _route(tmp_path, monkeypatch, values)
    assert route == {"name": "hermes-dashboard", "url": "http://hermes:9119/",
                     "routes": [{"name": "hermes-dashboard-all", "strip_path": False,
                                 "preserve_host": True, "hosts": ["hermes.localhost"]}],
                     "plugins": [{"name": "cors"}, {"name": "basic-auth"},
                                 {"name": "acl", "config": {"allow": ["dashboard_user"]}}]}
    assert probes == []


@pytest.mark.parametrize("case", [("", "true"), ("   ", "true"), ("disabled", "true"),
                                  ("localhost", "true"), ("unsupported", "true"), ("container", "false")],
                         ids=["blank-kong", "whitespace-kong", "disabled-kong", "localhost-kong",
                              "unsupported-kong", "explicit-dashboard-disable"])
def test_hermes_route_and_effective_flag_remain_unavailable(case, tmp_path, monkeypatch):
    kong, requested = case
    values = {"HERMES_SOURCE": "container", "KONG_API_GATEWAY_SOURCE": kong,
              "HERMES_DASHBOARD_ENABLED": requested}
    parser = _parser(tmp_path, monkeypatch, values)
    assert parser.parse_service_sources()["KONG_API_GATEWAY_SOURCE"] == kong.strip()
    service = ServiceConfig(parser)
    assert service.load_config()
    assert service._generate_hermes_config()["HERMES_DASHBOARD_EFFECTIVE"] == "false"
    route, probes = _route(tmp_path, monkeypatch, values)
    assert route is None and probes == []
    route, probes = _route(tmp_path, monkeypatch, values | {"HERMES_SOURCE": "localhost"})
    assert route is None and probes == []
