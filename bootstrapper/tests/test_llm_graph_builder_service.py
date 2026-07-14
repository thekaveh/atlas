from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig
from services.source_validator import SourceValidator
from services.topology import get_topology, invalidate_cache
from tracks import is_in_track, load_tracks
from utils.kong_config_generator import KongConfigGenerator
from utils.source_override_manager import SourceOverrideManager


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "llm-graph-builder"
MANIFEST = SERVICE_DIR / "service.yml"
COMPOSE = SERVICE_DIR / "compose.yml"
README = SERVICE_DIR / "README.md"
UPSTREAM_REF = "4a412f4688cf4096976045c019edc0a7f6ddcb6b"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_llm_graph_builder_manifest_admission_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "llm-graph-builder"
    assert manifest["category"] == "apps"
    assert manifest["containers"] == [
        "llm-graph-builder-backend",
        "llm-graph-builder-frontend",
    ]
    assert manifest["sources"]["var"] == "LLM_GRAPH_BUILDER_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert {option["id"] for option in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert manifest["depends_on"]["required"] == ["neo4j", "litellm", "kong"]
    assert set(manifest["depends_on"]["optional"]) >= {"minio", "docling"}
    assert manifest["data_flow"]["calls"] == [
        "neo4j",
        "litellm",
        "minio",
        "docling",
    ]
    assert manifest["extra_kong_aliases"] == ["graphbuilder-api.localhost"]

    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["LLM_GRAPH_BUILDER_SOURCE"]["default"] == "disabled"
    assert env_vars["LLM_GRAPH_BUILDER_REF"]["default"] == UPSTREAM_REF
    assert env_vars["LLM_GRAPH_BUILDER_MODEL_ID"]["default"] == "atlas_litellm"
    assert env_vars["LLM_GRAPH_BUILDER_LLM_MODEL"]["default"] == ""
    assert env_vars["LLM_GRAPH_BUILDER_NEO4J_DATABASE"]["default"] == "neo4j"
    assert env_vars["LLM_GRAPH_BUILDER_DIFFBOT_API_KEY"]["default"] == ""
    assert env_vars["LLM_GRAPH_BUILDER_DIFFBOT_API_KEY"]["secret"] is True
    assert env_vars["LLM_GRAPH_BUILDER_GCP_LOG_METRICS_ENABLED"]["default"] is False
    assert env_vars["LLM_GRAPH_BUILDER_GCS_FILE_CACHE"]["default"] is False
    assert env_vars["LLM_GRAPH_BUILDER_GCP_PROJECT_ID"]["default"] == ""
    assert env_vars["LLM_GRAPH_BUILDER_GCS_UPLOAD_BUCKET"]["default"] == ""
    assert env_vars["LLM_GRAPH_BUILDER_GCS_FAILED_BUCKET"]["default"] == ""
    assert env_vars["LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE"]["default"] == ""
    assert "default" not in env_vars["LLM_GRAPH_BUILDER_PORT"]
    for auto_var in (
        "LLM_GRAPH_BUILDER_BACKEND_SCALE",
        "LLM_GRAPH_BUILDER_FRONTEND_SCALE",
        "LLM_GRAPH_BUILDER_ENDPOINT",
        "LLM_GRAPH_BUILDER_BACKEND_ENDPOINT",
        "LLM_GRAPH_BUILDER_LITELLM_MODEL_CONFIG",
    ):
        assert env_vars[auto_var]["auto_managed"] is True

    row = manifest["rows"][0]
    assert row["display_name"] == "Neo4j LLM Graph Builder"
    assert row["source_var"] == "LLM_GRAPH_BUILDER_SOURCE"
    assert row["port_var"] == "LLM_GRAPH_BUILDER_PORT"
    assert row["scale_var"] == "LLM_GRAPH_BUILDER_FRONTEND_SCALE"
    assert row["alias"] == "graphbuilder.localhost"


def test_llm_graph_builder_topology_track_and_env_example_contract() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = [row for row in topology.rows if row.manifest == "llm-graph-builder"]

    assert len(rows) == 1
    assert rows[0].category == "apps"
    assert rows[0].alias == "graphbuilder.localhost"
    assert "graphbuilder.localhost" in topology.aliases
    assert "graphbuilder-api.localhost" in topology.aliases
    assert "LLM_GRAPH_BUILDER_PORT" in topology.port_defaults

    registry = load_tracks()
    for track_key in ("gen-ai-rag", "all"):
        assert is_in_track(
            registry.by_key[track_key],
            "llm-graph-builder",
            always_on=registry.always_on,
        )
    for track_key in ("gen-ai-eng", "gen-ai-creative", "ml-eng", "data-eng"):
        assert not is_in_track(
            registry.by_key[track_key],
            "llm-graph-builder",
            always_on=registry.always_on,
        )

    env_example = (REPO_ROOT / ".env.example").read_text()
    for expected in (
        "LLM_GRAPH_BUILDER_SOURCE=disabled",
        f"LLM_GRAPH_BUILDER_REF={UPSTREAM_REF}",
        "LLM_GRAPH_BUILDER_PORT=",
        "LLM_GRAPH_BUILDER_MODEL_ID=atlas_litellm",
        "LLM_GRAPH_BUILDER_LLM_MODEL=",
        "LLM_GRAPH_BUILDER_NEO4J_DATABASE=neo4j",
        "LLM_GRAPH_BUILDER_DIFFBOT_API_KEY=",
        "LLM_GRAPH_BUILDER_GCP_LOG_METRICS_ENABLED=false",
        "LLM_GRAPH_BUILDER_GCS_FILE_CACHE=false",
        "LLM_GRAPH_BUILDER_GCP_PROJECT_ID=",
        "LLM_GRAPH_BUILDER_GCS_UPLOAD_BUCKET=",
        "LLM_GRAPH_BUILDER_GCS_FAILED_BUCKET=",
        "LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE=",
        "LLM_GRAPH_BUILDER_REACT_APP_SOURCES=local,wiki,web",
        "LLM_GRAPH_BUILDER_BACKEND_SCALE=",
        "LLM_GRAPH_BUILDER_FRONTEND_SCALE=",
        "LLM_GRAPH_BUILDER_ENDPOINT=",
        "LLM_GRAPH_BUILDER_BACKEND_ENDPOINT=",
        "LLM_GRAPH_BUILDER_LITELLM_MODEL_CONFIG=",
    ):
        assert expected in env_example


def test_llm_graph_builder_source_cli_mapping_exists() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))

    assert mgr.source_mapping["llm_graph_builder_source"] == "LLM_GRAPH_BUILDER_SOURCE"
    assert (
        mgr.source_mapping["llm_graph_builder_frontend_source"]
        == "LLM_GRAPH_BUILDER_SOURCE"
    )
    assert mgr.collect_overrides(llm_graph_builder_source="container") == {
        "LLM_GRAPH_BUILDER_SOURCE": "container",
    }


def test_llm_graph_builder_scale_endpoint_and_dependency_gate() -> None:
    sc = ServiceConfig(config_parser=MagicMock())
    sc.config_parser.parse_env_file.return_value = {
        "LITELLM_DEFAULT_MODEL": "ollama/llama3.1",
        "LLM_GRAPH_BUILDER_LLM_MODEL": "",
    }

    sc.service_sources = {
        "LLM_GRAPH_BUILDER_SOURCE": "disabled",
        "NEO4J_GRAPH_DB_SOURCE": "disabled",
    }
    assert sc._generate_llm_graph_builder_config() == {
        "LLM_GRAPH_BUILDER_BACKEND_SCALE": "0",
        "LLM_GRAPH_BUILDER_FRONTEND_SCALE": "0",
        "LLM_GRAPH_BUILDER_ENDPOINT": "",
        "LLM_GRAPH_BUILDER_BACKEND_ENDPOINT": "",
        "LLM_GRAPH_BUILDER_LITELLM_MODEL_CONFIG": "",
    }

    sc.service_sources = {
        "LLM_GRAPH_BUILDER_SOURCE": "container",
        "NEO4J_GRAPH_DB_SOURCE": "disabled",
    }
    with pytest.raises(ValueError, match="requires Neo4j"):
        sc._generate_llm_graph_builder_config()

    sc.service_sources = {
        "LLM_GRAPH_BUILDER_SOURCE": "container",
        "NEO4J_GRAPH_DB_SOURCE": "localhost",
    }
    with pytest.raises(ValueError, match="in-stack Neo4j"):
        sc._generate_llm_graph_builder_config()

    sc.service_sources = {
        "LLM_GRAPH_BUILDER_SOURCE": "container",
        "NEO4J_GRAPH_DB_SOURCE": "container",
    }
    assert sc._generate_llm_graph_builder_config() == {
        "LLM_GRAPH_BUILDER_BACKEND_SCALE": "1",
        "LLM_GRAPH_BUILDER_FRONTEND_SCALE": "1",
        "LLM_GRAPH_BUILDER_ENDPOINT": "http://llm-graph-builder-frontend:8080",
        "LLM_GRAPH_BUILDER_BACKEND_ENDPOINT": "http://llm-graph-builder-backend:8000",
        "LLM_GRAPH_BUILDER_LITELLM_MODEL_CONFIG": (
            "ollama/llama3.1,http://litellm:4000/v1,${LITELLM_MASTER_KEY}"
        ),
    }


def test_llm_graph_builder_compose_contract() -> None:
    compose = _compose()["services"]
    backend = compose["llm-graph-builder-backend"]
    frontend = compose["llm-graph-builder-frontend"]

    assert backend["build"]["context"] == "https://github.com/neo4j-labs/llm-graph-builder.git#${LLM_GRAPH_BUILDER_REF}:backend"
    assert frontend["build"]["context"] == "https://github.com/neo4j-labs/llm-graph-builder.git#${LLM_GRAPH_BUILDER_REF}:frontend"
    assert backend["deploy"]["replicas"] == "${LLM_GRAPH_BUILDER_BACKEND_SCALE:-0}"
    assert frontend["deploy"]["replicas"] == "${LLM_GRAPH_BUILDER_FRONTEND_SCALE:-0}"
    assert "ports" not in backend
    assert frontend["ports"] == ["${HOST_BIND_IP:-}${LLM_GRAPH_BUILDER_PORT}:8080"]
    assert frontend["build"]["args"]["VITE_BACKEND_API_URL"] == (
        "http://graphbuilder-api.localhost:${KONG_HTTP_PORT:-63000}"
    )
    assert frontend["build"]["args"]["VITE_LLM_MODELS"] == "${LLM_GRAPH_BUILDER_MODEL_ID:-atlas_litellm}"
    assert backend["environment"]["NEO4J_URI"] == "${NEO4J_URI}"
    assert backend["environment"]["NEO4J_USERNAME"] == "${GRAPH_DB_USER:-neo4j}"
    assert backend["environment"]["NEO4J_PASSWORD"] == "${GRAPH_DB_PASSWORD}"
    assert backend["environment"]["NEO4J_DATABASE"] == "${LLM_GRAPH_BUILDER_NEO4J_DATABASE:-neo4j}"
    assert backend["environment"]["LLM_MODEL_CONFIG_ATLAS_LITELLM"] == "${LLM_GRAPH_BUILDER_LITELLM_MODEL_CONFIG}"
    assert backend["environment"]["DIFFBOT_API_KEY"] == (
        "${LLM_GRAPH_BUILDER_DIFFBOT_API_KEY:-${DIFFBOT_API_KEY:-}}"
    )
    assert backend["environment"]["GCP_LOG_METRICS_ENABLED"] == "${LLM_GRAPH_BUILDER_GCP_LOG_METRICS_ENABLED:-false}"
    assert backend["environment"]["GCS_FILE_CACHE"] == "${LLM_GRAPH_BUILDER_GCS_FILE_CACHE:-false}"
    project_fallback = (
        "${LLM_GRAPH_BUILDER_GCP_PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-}}"
    )
    assert backend["environment"]["PROJECT_ID"] == project_fallback
    assert backend["environment"]["GOOGLE_CLOUD_PROJECT"] == project_fallback
    assert backend["environment"]["BUCKET_UPLOAD_FILE"] == "${LLM_GRAPH_BUILDER_GCS_UPLOAD_BUCKET:-}"
    assert backend["environment"]["BUCKET_FAILED_FILE"] == "${LLM_GRAPH_BUILDER_GCS_FAILED_BUCKET:-}"
    credential_mount = backend["volumes"][0]
    assert credential_mount["source"] == "${LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE:-./config/disabled-gcp-credentials.json}"
    assert credential_mount["target"] == "/run/secrets/atlas-llm-graph-builder-gcp.json"
    assert credential_mount["read_only"] is True
    assert backend["depends_on"]["neo4j-graph-db"]["condition"] == "service_healthy"
    assert backend["depends_on"]["litellm"]["condition"] == "service_healthy"
    assert frontend["depends_on"]["llm-graph-builder-backend"]["condition"] == "service_healthy"


def test_llm_graph_builder_gcp_features_require_complete_config(
    env_with_overrides, tmp_path: Path
) -> None:
    missing_env = env_with_overrides(
        {
            "LLM_GRAPH_BUILDER_SOURCE": "container",
            "NEO4J_GRAPH_DB_SOURCE": "container",
            "LLM_GRAPH_BUILDER_GCS_FILE_CACHE": "true",
            "LLM_GRAPH_BUILDER_GCP_PROJECT_ID": "",
            "LLM_GRAPH_BUILDER_GCS_UPLOAD_BUCKET": "",
            "LLM_GRAPH_BUILDER_GCS_FAILED_BUCKET": "",
            "LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE": "",
        }
    )
    parser = ConfigParser(str(REPO_ROOT))
    parser.env_file_path = missing_env
    validator = SourceValidator(parser)
    assert validator.validate_all_sources() is False
    errors = "\n".join(validator.get_validation_errors())
    for required in (
        "LLM_GRAPH_BUILDER_GCP_PROJECT_ID",
        "LLM_GRAPH_BUILDER_GCS_UPLOAD_BUCKET",
        "LLM_GRAPH_BUILDER_GCS_FAILED_BUCKET",
        "LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE",
    ):
        assert required in errors

    credentials = tmp_path / "gcp.json"
    credentials.write_text(
        '{"type":"service_account","client_email":"atlas@example.invalid",'
        '"private_key":"not-a-real-key","token_uri":"https://oauth2.googleapis.com/token"}\n',
        encoding="utf-8",
    )
    configured_env = env_with_overrides(
        {
            "LLM_GRAPH_BUILDER_SOURCE": "container",
            "NEO4J_GRAPH_DB_SOURCE": "container",
            "LLM_GRAPH_BUILDER_GCS_FILE_CACHE": "true",
            "LLM_GRAPH_BUILDER_GCP_PROJECT_ID": "atlas-project",
            "LLM_GRAPH_BUILDER_GCS_UPLOAD_BUCKET": "uploads",
            "LLM_GRAPH_BUILDER_GCS_FAILED_BUCKET": "failed",
            "LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE": str(credentials),
        }
    )
    parser.env_file_path = configured_env
    validator = SourceValidator(parser)
    assert validator.validate_all_sources() is True

    legacy_project_env = env_with_overrides(
        {
            "LLM_GRAPH_BUILDER_SOURCE": "container",
            "NEO4J_GRAPH_DB_SOURCE": "container",
            "LLM_GRAPH_BUILDER_GCP_LOG_METRICS_ENABLED": "true",
            "LLM_GRAPH_BUILDER_GCP_PROJECT_ID": "",
            "GOOGLE_CLOUD_PROJECT": "legacy-atlas-project",
            "LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE": str(credentials),
        }
    )
    parser.env_file_path = legacy_project_env
    validator = SourceValidator(parser)
    assert validator.validate_all_sources() is True

    relative_env = env_with_overrides(
        {
            "LLM_GRAPH_BUILDER_SOURCE": "container",
            "NEO4J_GRAPH_DB_SOURCE": "container",
            "LLM_GRAPH_BUILDER_GCP_LOG_METRICS_ENABLED": "true",
            "LLM_GRAPH_BUILDER_GCP_PROJECT_ID": "atlas-project",
            "LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE": "credentials/gcp.json",
        }
    )
    parser.env_file_path = relative_env
    validator = SourceValidator(parser)
    assert validator.validate_all_sources() is False
    assert "must be an absolute host path" in "\n".join(
        validator.get_validation_errors()
    )


def test_llm_graph_builder_gcp_rejects_ambiguous_or_inactive_credentials(
    env_with_overrides, tmp_path: Path
) -> None:
    credentials = tmp_path / "gcp.json"
    credentials.write_text(
        '{"type":"authorized_user","client_id":"id","client_secret":"secret",'
        '"refresh_token":"token"}\n',
        encoding="utf-8",
    )
    parser = ConfigParser(str(REPO_ROOT))

    inactive_env = env_with_overrides(
        {
            "LLM_GRAPH_BUILDER_SOURCE": "container",
            "NEO4J_GRAPH_DB_SOURCE": "container",
            "LLM_GRAPH_BUILDER_GCP_LOG_METRICS_ENABLED": "false",
            "LLM_GRAPH_BUILDER_GCS_FILE_CACHE": "false",
            "LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE": str(credentials),
        }
    )
    parser.env_file_path = inactive_env
    validator = SourceValidator(parser)
    assert validator.validate_all_sources() is False
    assert "is set while both" in "\n".join(validator.get_validation_errors())

    ambiguous_env = env_with_overrides(
        {
            "LLM_GRAPH_BUILDER_SOURCE": "container",
            "NEO4J_GRAPH_DB_SOURCE": "container",
            "LLM_GRAPH_BUILDER_GCP_LOG_METRICS_ENABLED": "on",
        }
    )
    parser.env_file_path = ambiguous_env
    validator = SourceValidator(parser)
    assert validator.validate_all_sources() is False
    assert "true/false, 1/0, or yes/no" in "\n".join(
        validator.get_validation_errors()
    )


def test_llm_graph_builder_gcp_rejects_malformed_adc(
    env_with_overrides, tmp_path: Path
) -> None:
    credentials = tmp_path / "gcp.json"
    credentials.write_text("{}\n", encoding="utf-8")
    env_path = env_with_overrides(
        {
            "LLM_GRAPH_BUILDER_SOURCE": "container",
            "NEO4J_GRAPH_DB_SOURCE": "container",
            "LLM_GRAPH_BUILDER_GCP_LOG_METRICS_ENABLED": "true",
            "LLM_GRAPH_BUILDER_GCP_PROJECT_ID": "atlas-project",
            "LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE": str(credentials),
        }
    )
    parser = ConfigParser(str(REPO_ROOT))
    parser.env_file_path = env_path
    validator = SourceValidator(parser)
    assert validator.validate_all_sources() is False
    assert "structurally complete Google ADC JSON" in "\n".join(
        validator.get_validation_errors()
    )


def test_llm_graph_builder_kong_routes_only_when_container(tmp_path: Path) -> None:
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

    enabled_hosts = _hosts("LLM_GRAPH_BUILDER_SOURCE=container\n")
    disabled_hosts = _hosts("LLM_GRAPH_BUILDER_SOURCE=disabled\n")

    assert enabled_hosts["graphbuilder.localhost"]["name"] == "llm-graph-builder"
    assert enabled_hosts["graphbuilder.localhost"]["url"] == "http://llm-graph-builder-frontend:8080/"
    assert enabled_hosts["graphbuilder-api.localhost"]["name"] == "llm-graph-builder-api"
    assert enabled_hosts["graphbuilder-api.localhost"]["url"] == "http://llm-graph-builder-backend:8000/"
    for host in ("graphbuilder.localhost", "graphbuilder-api.localhost"):
        service = enabled_hosts[host]
        assert service["routes"][0]["preserve_host"] is True
        assert {plugin["name"] for plugin in service["plugins"]} >= {
            "basic-auth",
            "acl",
            "cors",
        }
    assert "graphbuilder.localhost" not in disabled_hosts
    assert "graphbuilder-api.localhost" not in disabled_hosts


def test_llm_graph_builder_docs_describe_setup_and_guardrails() -> None:
    readme = README.read_text()

    for expected in (
        "LLM_GRAPH_BUILDER_SOURCE=disabled",
        "graphbuilder.localhost",
        "graphbuilder-api.localhost",
        "gen-ai-rag",
        "apps",
        "Neo4j 5.23",
        "LiteLLM",
        "MinIO",
        "Docling",
        "document-to-graph",
        "LLM_GRAPH_BUILDER_NEO4J_DATABASE",
        "LLM_GRAPH_BUILDER_GCS_FILE_CACHE",
        "LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE",
        "namespace",
        "rollback",
    ):
        assert expected in readme
