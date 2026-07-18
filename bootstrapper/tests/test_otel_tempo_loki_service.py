from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig
from services.topology import get_topology, invalidate_cache
from tracks import is_in_track, load_tracks
from utils.source_override_manager import SourceOverrideManager


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES = REPO_ROOT / "services"


def _manifest(name: str) -> dict:
    return yaml.safe_load((SERVICES / name / "service.yml").read_text())


def _compose(name: str) -> dict:
    return yaml.safe_load((SERVICES / name / "compose.yml").read_text())


def test_observability_tracing_manifests_are_disabled_by_default() -> None:
    expected = {
        "otel-collector": {
            "source": "OTEL_COLLECTOR_SOURCE",
            "containers": ["otel-collector"],
            "scales": ["OTEL_COLLECTOR_SCALE"],
            "endpoint": "OTEL_COLLECTOR_ENDPOINT",
            "calls": ["tempo"],
        },
        "tempo": {
            "source": "TEMPO_SOURCE",
            "containers": ["tempo"],
            "scales": ["TEMPO_SCALE"],
            "endpoint": "TEMPO_ENDPOINT",
            "calls": [],
        },
        "loki": {
            "source": "LOKI_SOURCE",
            "containers": ["loki"],
            "scales": ["LOKI_SCALE"],
            "endpoint": "LOKI_ENDPOINT",
            "calls": [],
        },
    }

    for name, contract in expected.items():
        manifest = _manifest(name)
        assert manifest["name"] == name
        assert manifest["category"] == "infra"
        assert manifest["containers"] == contract["containers"]
        assert manifest["sources"]["var"] == contract["source"]
        assert manifest["sources"]["default"] == "disabled"
        assert {option["id"] for option in manifest["sources"]["options"]} == {
            "container",
            "disabled",
        }
        env_vars = {entry["name"]: entry for entry in manifest["env"]}
        assert env_vars[contract["source"]]["default"] == "disabled"
        assert env_vars[contract["endpoint"]]["auto_managed"] is True
        for scale_var in contract["scales"]:
            assert env_vars[scale_var]["auto_managed"] is True
        row = manifest["rows"][0]
        assert row["source_var"] == contract["source"]
        assert "alias" not in row
        assert "port_var" not in row
        assert manifest["data_flow"]["calls"] == contract["calls"]


def test_observability_tracing_does_not_consume_infra_host_port_slots() -> None:
    invalidate_cache()
    topology = get_topology(SERVICES)
    rows = {row.manifest: row for row in topology.rows}

    for name in ("otel-collector", "tempo", "loki"):
        assert rows[name].category == "infra"
        assert rows[name].port_var is None
        assert rows[name].alias is None

    env_example = (REPO_ROOT / ".env.example").read_text()
    for expected in (
        "OTEL_COLLECTOR_SOURCE=disabled",
        "OTEL_COLLECTOR_ENDPOINT=",
        "OTEL_COLLECTOR_OTLP_GRPC_ENDPOINT=",
        "OTEL_COLLECTOR_OTLP_HTTP_ENDPOINT=",
        "TEMPO_SOURCE=disabled",
        "TEMPO_ENDPOINT=",
        "LOKI_SOURCE=disabled",
        "LOKI_ENDPOINT=",
    ):
        assert expected in env_example
    for forbidden in (
        "OTEL_COLLECTOR_PORT=",
        "OTEL_COLLECTOR_OTLP_HTTP_PORT=",
        "OTEL_COLLECTOR_OTLP_GRPC_PORT=",
        "TEMPO_PORT=",
        "LOKI_PORT=",
    ):
        assert forbidden not in env_example


def test_observability_tracing_track_membership_excludes_data_eng() -> None:
    registry = load_tracks()

    for service in ("otel-collector", "tempo", "loki"):
        for track_key in (
            "gen-ai-rag",
            "gen-ai-eng",
            "gen-ai-creative",
            "ml-eng",
            "all",
        ):
            assert is_in_track(
                registry.by_key[track_key],
                service,
                always_on=registry.always_on,
            )

        assert not is_in_track(
            registry.by_key["data-eng"],
            service,
            always_on=registry.always_on,
        )


def test_observability_tracing_source_cli_mapping_exists() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))

    assert mgr.source_mapping["otel_collector_source"] == "OTEL_COLLECTOR_SOURCE"
    assert mgr.source_mapping["tempo_source"] == "TEMPO_SOURCE"
    assert mgr.source_mapping["loki_source"] == "LOKI_SOURCE"
    assert mgr.collect_overrides(
        otel_collector_source="container",
        tempo_source="container",
        loki_source="disabled",
    ) == {
        "OTEL_COLLECTOR_SOURCE": "container",
        "TEMPO_SOURCE": "container",
        "LOKI_SOURCE": "disabled",
    }


def test_observability_tracing_scale_generation_and_dependency_gate() -> None:
    sc = ServiceConfig(config_parser=MagicMock())

    sc.service_sources = {
        "OTEL_COLLECTOR_SOURCE": "disabled",
        "TEMPO_SOURCE": "disabled",
        "LOKI_SOURCE": "disabled",
    }
    assert sc._generate_otel_tempo_loki_config() == {
        "OTEL_COLLECTOR_SCALE": "0",
        "OTEL_COLLECTOR_ENDPOINT": "",
        "OTEL_COLLECTOR_OTLP_HTTP_ENDPOINT": "",
        "OTEL_COLLECTOR_OTLP_GRPC_ENDPOINT": "",
        "TEMPO_SCALE": "0",
        "TEMPO_ENDPOINT": "",
        "LOKI_SCALE": "0",
        "LOKI_ENDPOINT": "",
        "ATLAS_OTEL_ENABLED": "false",
    }

    sc.service_sources = {
        "OTEL_COLLECTOR_SOURCE": "container",
        "TEMPO_SOURCE": "container",
        "LOKI_SOURCE": "container",
    }
    assert sc._generate_otel_tempo_loki_config() == {
        "OTEL_COLLECTOR_SCALE": "1",
        "OTEL_COLLECTOR_ENDPOINT": "http://otel-collector:4318",
        "OTEL_COLLECTOR_OTLP_HTTP_ENDPOINT": "http://otel-collector:4318",
        "OTEL_COLLECTOR_OTLP_GRPC_ENDPOINT": "http://otel-collector:4317",
        "TEMPO_SCALE": "1",
        "TEMPO_ENDPOINT": "http://tempo:3200",
        "LOKI_SCALE": "1",
        "LOKI_ENDPOINT": "http://loki:3100",
        "ATLAS_OTEL_ENABLED": "true",
    }

    sc.service_sources = {
        "OTEL_COLLECTOR_SOURCE": "container",
        "TEMPO_SOURCE": "disabled",
        "LOKI_SOURCE": "disabled",
    }
    with pytest.raises(ValueError, match="OTel Collector requires Tempo"):
        sc._generate_otel_tempo_loki_config()


def test_observability_tracing_compose_contract() -> None:
    otel = _compose("otel-collector")["services"]["otel-collector"]
    tempo = _compose("tempo")["services"]["tempo"]
    loki = _compose("loki")["services"]["loki"]

    assert otel["image"] == "${OTEL_COLLECTOR_IMAGE:-otel/opentelemetry-collector-contrib:0.154.0}"
    assert otel["deploy"]["replicas"] == "${OTEL_COLLECTOR_SCALE:-0}"
    assert "ports" not in otel
    assert otel["volumes"] == ["./config/config.yaml:/etc/otelcol/config.yaml:ro"]
    assert otel["command"] == ["--config=/etc/otelcol/config.yaml"]
    assert otel["depends_on"]["tempo"]["condition"] == "service_healthy"
    healthcheck = otel["healthcheck"]
    assert healthcheck["test"] == [
        "CMD",
        "/otelcol-contrib",
        "validate",
        "--config=/etc/otelcol/config.yaml",
    ]

    assert tempo["image"] == "${TEMPO_IMAGE:-grafana/tempo:3.0.0}"
    assert tempo["deploy"]["replicas"] == "${TEMPO_SCALE:-0}"
    assert "ports" not in tempo
    assert tempo["command"] == ["-config.file=/etc/tempo/tempo.yaml", "-config.expand-env=true"]
    assert "tempo-data:/var/tempo" in tempo["volumes"]

    assert loki["image"] == "${LOKI_IMAGE:-grafana/loki:3.7.0}"
    assert loki["deploy"]["replicas"] == "${LOKI_SCALE:-0}"
    assert "ports" not in loki
    assert loki["command"] == ["-config.file=/etc/loki/loki.yaml", "-config.expand-env=true"]
    assert "loki-data:/loki" in loki["volumes"]


def test_litellm_and_backend_receive_otel_env_only_from_atlas_vars() -> None:
    litellm = yaml.safe_load((SERVICES / "litellm" / "compose.yml").read_text())
    litellm_env = litellm["services"]["litellm"]["environment"]
    assert litellm_env["LITELLM_OTEL_V2"] == "${ATLAS_OTEL_ENABLED:-false}"
    assert litellm_env["OTEL_EXPORTER"] == "otlp_http"
    assert litellm_env["OTEL_ENDPOINT"] == "${OTEL_COLLECTOR_OTLP_HTTP_ENDPOINT:-}"
    assert litellm_env["OTEL_SERVICE_NAME"] == "litellm"

    backend = yaml.safe_load((SERVICES / "backend" / "compose.yml").read_text())
    backend_env = backend["services"]["backend"]["environment"]
    assert backend_env["ATLAS_OTEL_ENABLED"] == "${ATLAS_OTEL_ENABLED:-false}"
    assert backend_env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "${OTEL_COLLECTOR_OTLP_HTTP_ENDPOINT:-}"
    assert backend_env["OTEL_SERVICE_NAME"] == "backend"


def test_grafana_provisions_tempo_and_loki_datasources() -> None:
    datasource = yaml.safe_load(
        (
            SERVICES
            / "grafana"
            / "config"
            / "provisioning"
            / "datasources"
            / "tempo-loki.yml"
        ).read_text()
    )
    datasources = {entry["name"]: entry for entry in datasource["datasources"]}

    assert datasources["Tempo"]["uid"] == "Tempo"
    assert datasources["Tempo"]["type"] == "tempo"
    assert datasources["Tempo"]["url"] == "${TEMPO_ENDPOINT}"
    assert datasources["Loki"]["uid"] == "Loki"
    assert datasources["Loki"]["type"] == "loki"
    assert datasources["Loki"]["url"] == "${LOKI_ENDPOINT}"

    # The datasource urls above interpolate from the container environment at
    # provisioning time, so grafana's compose must actually forward the vars —
    # regression guard for when TEMPO_ENDPOINT/LOKI_ENDPOINT were absent and the
    # Tempo/Loki datasources provisioned with an empty url (unqueryable).
    grafana_env = _compose("grafana")["services"]["grafana"]["environment"]
    for var in ("PROMETHEUS_ENDPOINT", "TEMPO_ENDPOINT", "LOKI_ENDPOINT"):
        assert var in grafana_env, f"grafana compose must forward {var}"


def test_observability_tracing_docs_state_internal_only_and_grafana_surface() -> None:
    for name in ("otel-collector", "tempo", "loki"):
        readme = (SERVICES / name / "README.md").read_text()
        for expected in (
            "disabled by default",
            "Grafana",
            "internal-only",
            "no Kong route",
            "local development",
        ):
            assert expected in readme
