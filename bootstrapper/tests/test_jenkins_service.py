from __future__ import annotations

from pathlib import Path
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
SERVICE_DIR = REPO_ROOT / "services" / "jenkins"
MANIFEST = SERVICE_DIR / "service.yml"
COMPOSE = SERVICE_DIR / "compose.yml"
DOCKERFILE = SERVICE_DIR / "build" / "Dockerfile"
PLUGINS = SERVICE_DIR / "build" / "plugins.txt"
JENKINS_YAML = SERVICE_DIR / "casc" / "jenkins.yaml"
README = SERVICE_DIR / "README.md"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_jenkins_manifest_admission_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "jenkins"
    assert manifest["category"] == "apps"
    assert manifest["containers"] == ["jenkins"]
    assert manifest["sources"]["var"] == "JENKINS_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert {option["id"] for option in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert manifest["depends_on"]["required"] == ["minio"]
    assert manifest["depends_on"].get("optional", []) == ["airflow", "spark"]
    # #1273: no Jenkins job ships, so the MinIO edge is optional.
    assert manifest["data_flow"]["calls"] == [{
        "target": "minio",
        "status": "optional",
        "condition": "an operator-authored Jenkins job; none ships with Atlas",
        "evidence": "services/jenkins/README.md",
    }]

    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["JENKINS_SOURCE"]["default"] == "disabled"
    assert env_vars["JENKINS_SCALE"]["auto_managed"] is True
    assert env_vars["JENKINS_ADMIN_PASSWORD"]["secret"] is True
    assert "default" not in env_vars["JENKINS_PORT"]

    row = manifest["rows"][0]
    assert row["display_name"] == "Jenkins"
    assert row["source_var"] == "JENKINS_SOURCE"
    assert row["port_var"] == "JENKINS_PORT"
    assert row["scale_var"] == "JENKINS_SCALE"
    assert row["alias"] == "jenkins.localhost"


def test_jenkins_topology_alias_and_env_example_contract() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = [row for row in topology.rows if row.manifest == "jenkins"]

    assert len(rows) == 1
    assert rows[0].category == "apps"
    assert rows[0].alias == "jenkins.localhost"
    assert "jenkins.localhost" in topology.aliases
    assert "JENKINS_PORT" in topology.port_defaults

    env_example = (REPO_ROOT / ".env.example").read_text()
    for expected in (
        "JENKINS_SOURCE=disabled",
        "JENKINS_IMAGE=jenkins/jenkins:lts-jdk21",
        "JENKINS_PORT=",
        "JENKINS_SCALE=",
        "JENKINS_ADMIN_USER=admin",
        "JENKINS_ADMIN_PASSWORD=",
    ):
        assert expected in env_example


def test_jenkins_track_membership_is_data_eng_only() -> None:
    registry = load_tracks()

    assert is_in_track(
        registry.by_key["data-eng"],
        "jenkins",
        always_on=registry.always_on,
    )
    assert is_in_track(
        registry.by_key["all"],
        "jenkins",
        always_on=registry.always_on,
    )
    for track_key in ("gen-ai-rag", "gen-ai-eng", "gen-ai-creative", "ml-eng"):
        assert not is_in_track(
            registry.by_key[track_key],
            "jenkins",
            always_on=registry.always_on,
        )


def test_jenkins_source_cli_mapping_exists() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))

    assert mgr.source_mapping["jenkins_source"] == "JENKINS_SOURCE"
    assert mgr.collect_overrides(jenkins_source="container") == {
        "JENKINS_SOURCE": "container",
    }


def test_jenkins_scale_generation_and_minio_gate() -> None:
    sc = ServiceConfig(config_parser=MagicMock())

    sc.service_sources = {"JENKINS_SOURCE": "disabled", "MINIO_SOURCE": "disabled"}
    assert sc._generate_jenkins_config() == {"JENKINS_SCALE": "0"}

    sc.service_sources = {"JENKINS_SOURCE": "container", "MINIO_SOURCE": "container"}
    assert sc._generate_jenkins_config() == {"JENKINS_SCALE": "1"}

    sc.service_sources = {"JENKINS_SOURCE": "container", "MINIO_SOURCE": "disabled"}
    with pytest.raises(ValueError, match="Jenkins requires MinIO"):
        sc._generate_jenkins_config()


def test_jenkins_key_generation_covers_admin_password(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("PROJECT_NAME=atlas-test\nJENKINS_ADMIN_PASSWORD=\n")

    results = KeyGenerator(str(tmp_path)).generate_missing_keys()
    generated = ConfigParser(str(tmp_path)).parse_env_file()

    assert results["JENKINS_ADMIN_PASSWORD"] is True
    assert generated["JENKINS_ADMIN_PASSWORD"]


def test_jenkins_compose_contract() -> None:
    service = _compose()["services"]["jenkins"]

    assert service["build"]["context"] == "./build"
    assert service["build"]["args"]["BASE_IMAGE"] == "${JENKINS_IMAGE:-jenkins/jenkins:lts-jdk21@sha256:c1e4c349365f6d16d88595b2c5f7e8ff39b8ae1d061f62420bac193b4b9616d0}"
    assert service["image"] == "${PROJECT_NAME}-jenkins:local"
    assert service["ports"] == ["${HOST_BIND_IP-127.0.0.1:}${JENKINS_PORT}:8080"]
    assert service["volumes"] == [
        "jenkins-home:/var/jenkins_home",
        "./casc:/var/jenkins_home/casc:ro",
    ]
    assert service["environment"]["CASC_JENKINS_CONFIG"] == "/var/jenkins_home/casc/jenkins.yaml"
    assert service["environment"]["JENKINS_ADMIN_USER"] == "${JENKINS_ADMIN_USER:-admin}"
    assert service["environment"]["JENKINS_ADMIN_PASSWORD"] == "${JENKINS_ADMIN_PASSWORD}"
    assert service["environment"]["KONG_HTTP_PORT"] == "${KONG_HTTP_PORT}"
    assert service["environment"]["MINIO_ENDPOINT"] == "http://minio:9000"
    assert service["environment"]["MINIO_BUCKET_ICEBERG_JARS"] == "${MINIO_BUCKET_ICEBERG_JARS:-jars}"
    assert service["depends_on"]["minio-init"]["condition"] == "service_completed_successfully"
    assert "privileged" not in service
    assert "/var/run/docker.sock" not in "\n".join(service.get("volumes", []))


def test_jenkins_dockerfile_and_plugins_are_minimal_and_pinned() -> None:
    dockerfile = DOCKERFILE.read_text()
    plugins = PLUGINS.read_text()

    assert "ARG BASE_IMAGE=jenkins/jenkins:lts-jdk21" in dockerfile
    assert "FROM ${BASE_IMAGE}" in dockerfile
    assert "USER root" in dockerfile
    assert "maven" in dockerfile
    # #1288: mc comes from the maintained pgsty fork's digest-pinned image,
    # the same one minio-init runs, never from the abandoned official release.
    assert (
        "ARG MC_IMAGE=pgsty/mc:RELEASE.2026-09-16T00-00-00Z@sha256:"
        "cfc83108c3abb371f8fb84d99c1fdc88f8c237e022409b0081fb7c0a3be634dd"
    ) in dockerfile
    assert "FROM ${MC_IMAGE} AS mc" in dockerfile
    assert "COPY --from=mc /usr/bin/mc /usr/local/bin/mc" in dockerfile
    assert "github.com/minio/mc" not in dockerfile
    assert "MINIO_MC_" not in dockerfile
    assert "dl.min.io" not in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert "jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt" in dockerfile
    assert "jenkins.install.UpgradeWizard.state" in dockerfile
    assert "USER jenkins" in dockerfile
    assert "docker" not in dockerfile.lower()

    for plugin in ("configuration-as-code", "workflow-aggregator", "git"):
        assert f"{plugin}:" in plugins
    assert "blueocean" not in plugins


def test_jenkins_jcasc_sets_admin_and_no_project_jobs() -> None:
    casc = yaml.safe_load(JENKINS_YAML.read_text())
    raw = JENKINS_YAML.read_text()

    assert casc["jenkins"]["securityRealm"]["local"]["allowsSignup"] is False
    users = casc["jenkins"]["securityRealm"]["local"]["users"]
    assert users == [
        {
            "id": "${JENKINS_ADMIN_USER:-admin}",
            "password": "${JENKINS_ADMIN_PASSWORD}",
        }
    ]
    assert "loggedInUsersCanDoAnything" in casc["jenkins"]["authorizationStrategy"]
    assert casc["unclassified"]["location"]["url"] == "http://jenkins.localhost:${KONG_HTTP_PORT:-63000}/"
    assert "remotingSecurity" not in casc["jenkins"]
    assert "data-eng-lab" not in raw
    assert "github.com" not in raw.lower()
    assert "jobs:" not in raw


def test_jenkins_docs_describe_scope_auth_and_artifact_path() -> None:
    readme = README.read_text()

    assert "JENKINS_SOURCE=disabled" in readme
    assert "jenkins.localhost" in readme
    assert "JENKINS_ADMIN_PASSWORD" in readme
    assert "Atlas provides the Jenkins server" in readme
    assert "data-eng-lab job definitions" not in readme
    assert "mc cp target/*.jar" in readme
    assert "s3a://jars/<app>/<version>/app.jar" in readme
