from __future__ import annotations

from pathlib import Path

import yaml

from core.config_parser import ConfigParser
from services.topology import get_topology, invalidate_cache
from tracks import is_in_track, load_tracks, synthesize_track_source_args
from utils.source_override_manager import SourceOverrideManager
from wizard.service_discovery import ServiceDiscovery


REPO_ROOT = Path(__file__).resolve().parents[2]

LAKEHOUSE_SERVICES = {
    "spark",
    "airflow",
    "jupyterhub",
    "zeppelin",
    "minio",
    "iceberg-rest",
}

DATA_ENG_IMPLEMENTED_SERVICES = LAKEHOUSE_SERVICES | {"jenkins"}


def _manifest(service: str) -> dict:
    return yaml.safe_load((REPO_ROOT / "services" / service / "service.yml").read_text())


def test_data_eng_track_includes_lakehouse_builder_but_not_future_backlog_services() -> None:
    registry = load_tracks()
    data_eng = registry.by_key["data-eng"]

    for service in DATA_ENG_IMPLEMENTED_SERVICES:
        assert is_in_track(data_eng, service, always_on=registry.always_on), service

    for deferred_service in ("trino", "redpanda"):
        assert not is_in_track(
            data_eng,
            deferred_service,
            always_on=registry.always_on,
        ), deferred_service


def test_data_eng_track_no_tui_synthesis_keeps_lakehouse_and_disables_off_track() -> None:
    source_args = {
        "spark_source": None,
        "airflow_source": None,
        "jupyterhub_source": None,
        "zeppelin_source": None,
        "minio_source": None,
        "iceberg_rest_source": None,
        "comfyui_source": None,
        "n8n_source": None,
    }

    synthesize_track_source_args(
        source_args,
        track_key="data-eng",
        registry=load_tracks(),
        force_disable=True,
    )

    for cli_key in (
        "spark_source",
        "airflow_source",
        "jupyterhub_source",
        "zeppelin_source",
        "minio_source",
        "iceberg_rest_source",
    ):
        assert source_args[cli_key] is None, cli_key
    assert source_args["comfyui_source"] == "disabled"
    assert source_args["n8n_source"] == "disabled"


def test_data_eng_lakehouse_services_have_cli_flags_and_wizard_copy() -> None:
    mapping = SourceOverrideManager(ConfigParser(str(REPO_ROOT))).source_mapping
    discovered = {svc.key: svc for svc in ServiceDiscovery(ConfigParser(str(REPO_ROOT))).discover()}

    expected = {
        "spark_master_source": ("spark-master", "container", "lakehouse-ready"),
        "airflow_webserver_source": ("airflow-webserver", "container", "SparkSubmit"),
        "jupyterhub_source": ("jupyterhub", "container", "lakehouse"),
        "zeppelin_source": ("zeppelin", "container", "Spark-first"),
        "minio_source": ("minio", "container", "object storage"),
        "iceberg_rest_source": ("iceberg-rest", "container", "Iceberg"),
    }

    for cli_key, (wizard_key, source_value, label_fragment) in expected.items():
        assert cli_key in mapping, cli_key
        assert wizard_key in discovered, wizard_key
        service = discovered[wizard_key]
        assert source_value in service.options
        assert label_fragment in service.option_labels[source_value]


def test_data_eng_lakehouse_categories_and_ports_use_topology() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = {row.manifest: row for row in topology.rows}

    expected_categories = {
        "spark": "data",
        "airflow": "agents",
        "jupyterhub": "apps",
        "zeppelin": "apps",
        "minio": "data",
        "iceberg-rest": "data",
    }

    for service, category in expected_categories.items():
        manifest = _manifest(service)
        assert manifest["category"] == category
        assert rows[service].category == category
        port_var = manifest["rows"][0].get("port_var")
        if port_var:
            assert port_var in topology.port_defaults


def test_strategy_report_current_snapshot_mentions_iceberg_rest_in_data_eng() -> None:
    report = (REPO_ROOT / "docs" / "strategy" / "atlas-vnext-strategy-report.md").read_text()

    assert (
        "`data-eng`: `spark`, `airflow`, `jupyterhub`, `zeppelin`, "
        "`minio`, `iceberg-rest`, `weaviate`, `neo4j`"
    ) in report
