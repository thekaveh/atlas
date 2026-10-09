from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import types
from unittest.mock import MagicMock

import pytest
import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig
from services.topology import get_topology, invalidate_cache
from tracks import is_in_track, load_tracks
from utils.key_generator import KeyGenerator
from utils.source_override_manager import SourceOverrideManager

from tests.test_mlflow_gateway_guard import _load_guard


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "mlflow"
MANIFEST = SERVICE_DIR / "service.yml"
COMPOSE = SERVICE_DIR / "compose.yml"
README = SERVICE_DIR / "README.md"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_mlflow_manifest_admission_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "mlflow"
    assert manifest["category"] == "apps"
    assert manifest["containers"] == ["mlflow-init", "mlflow"]
    assert manifest["sources"]["var"] == "MLFLOW_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert {option["id"] for option in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert manifest["depends_on"]["required"] == ["supabase", "minio"]
    assert manifest["depends_on"].get("optional", []) == ["jupyterhub"]
    assert manifest["data_flow"]["calls"] == ["supabase", "minio"]

    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["MLFLOW_SOURCE"]["default"] == "disabled"
    assert env_vars["MLFLOW_SCALE"]["auto_managed"] is True
    assert env_vars["MLFLOW_INIT_SCALE"]["auto_managed"] is True
    assert env_vars["MLFLOW_DB_PASSWORD"]["secret"] is True
    assert "default" not in env_vars["MLFLOW_PORT"]

    row = manifest["rows"][0]
    assert row["display_name"] == "MLflow"
    assert row["source_var"] == "MLFLOW_SOURCE"
    assert row["port_var"] == "MLFLOW_PORT"
    assert row["scale_var"] == "MLFLOW_SCALE"
    assert row["alias"] == "mlflow.localhost"


def test_mlflow_topology_alias_and_env_example_contract() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = [row for row in topology.rows if row.manifest == "mlflow"]

    assert len(rows) == 1
    assert rows[0].category == "apps"
    assert rows[0].alias == "mlflow.localhost"
    assert "mlflow.localhost" in topology.aliases
    assert "MLFLOW_PORT" in topology.port_defaults

    env_example = (REPO_ROOT / ".env.example").read_text()
    for expected in (
        "MLFLOW_SOURCE=disabled",
        "MLFLOW_IMAGE=ghcr.io/mlflow/mlflow:v3.16.1",
        "MLFLOW_PORT=",
        "MLFLOW_ENDPOINT=",
        "MLFLOW_SCALE=",
        "MLFLOW_INIT_SCALE=",
        "MLFLOW_DB_NAME=mlflow",
        "MLFLOW_DB_USER=mlflow",
        "MLFLOW_DB_PASSWORD=",
        "MINIO_BUCKET_MLFLOW=mlflow",
    ):
        assert expected in env_example


def test_mlflow_track_membership_is_ml_eng_only() -> None:
    registry = load_tracks()

    assert is_in_track(
        registry.by_key["ml-eng"],
        "mlflow",
        always_on=registry.always_on,
    )
    assert is_in_track(
        registry.by_key["all"],
        "mlflow",
        always_on=registry.always_on,
    )
    for track_key in ("gen-ai-rag", "gen-ai-eng", "gen-ai-creative", "data-eng"):
        assert not is_in_track(
            registry.by_key[track_key],
            "mlflow",
            always_on=registry.always_on,
        )


def test_mlflow_source_cli_mapping_exists() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))

    assert mgr.source_mapping["mlflow_source"] == "MLFLOW_SOURCE"
    assert mgr.collect_overrides(mlflow_source="container") == {
        "MLFLOW_SOURCE": "container",
    }


def test_mlflow_scale_generation_and_minio_gate() -> None:
    sc = ServiceConfig(config_parser=MagicMock())

    sc.service_sources = {"MLFLOW_SOURCE": "disabled", "MINIO_SOURCE": "disabled"}
    assert sc._generate_mlflow_config() == {
        "MLFLOW_INIT_SCALE": "0",
        "MLFLOW_SCALE": "0",
        "MLFLOW_ENDPOINT": "",
        "MLFLOW_TRACKING_URI": "",
    }

    sc.service_sources = {"MLFLOW_SOURCE": "container", "MINIO_SOURCE": "container"}
    assert sc._generate_mlflow_config() == {
        "MLFLOW_INIT_SCALE": "1",
        "MLFLOW_SCALE": "1",
        "MLFLOW_ENDPOINT": "http://mlflow:5000",
        "MLFLOW_TRACKING_URI": "http://mlflow:5000",
    }

    sc.service_sources = {"MLFLOW_SOURCE": "container", "MINIO_SOURCE": "disabled"}
    with pytest.raises(ValueError, match="MLflow requires MinIO"):
        sc._generate_mlflow_config()


def test_mlflow_compose_contract() -> None:
    compose = _compose()["services"]
    init = compose["mlflow-init"]
    service = compose["mlflow"]

    assert init["build"]["context"] == "./init"
    assert init["depends_on"]["supabase-db-init"]["condition"] == "service_completed_successfully"
    assert init["depends_on"]["minio-init"]["condition"] == "service_completed_successfully"
    assert init["environment"]["MLFLOW_DB_NAME"] == "${MLFLOW_DB_NAME:-mlflow}"
    assert "MINIO_ROOT_USER" not in init["environment"]

    assert service["image"] == "${PROJECT_NAME}-mlflow:local"
    assert service["build"] == {
        "context": ".",
        "dockerfile": "build/Dockerfile",
        "args": {"BASE_IMAGE": "${MLFLOW_IMAGE:-ghcr.io/mlflow/mlflow:v3.16.1}"},
    }
    assert service["ports"] == ["${HOST_BIND_IP-127.0.0.1:}${MLFLOW_PORT}:5000"]
    assert service["depends_on"]["mlflow-init"]["condition"] == "service_completed_successfully"
    assert service["environment"]["MLFLOW_S3_ENDPOINT_URL"] == "http://minio:9000"
    assert service["environment"]["AWS_ACCESS_KEY_ID"] == "${MINIO_MLFLOW_ACCESS_KEY}"
    assert service["environment"]["AWS_SECRET_ACCESS_KEY"] == "${MINIO_MLFLOW_SECRET_KEY}"
    assert service["command"] == ["python", "atlas_server.py"]
    assert service["working_dir"] == "/opt/atlas"
    assert "volumes" not in service
    environment = service["environment"]
    database_uri = "postgresql://${MLFLOW_DB_USER_URI:?MLFLOW_DB_USER_URI is required}:${MLFLOW_DB_PASSWORD_URI:?MLFLOW_DB_PASSWORD_URI is required}@supabase-db:5432/${MLFLOW_DB_NAME_URI:?MLFLOW_DB_NAME_URI is required}"
    assert environment["_MLFLOW_SERVER_FILE_STORE"] == database_uri
    assert environment["_MLFLOW_SERVER_REGISTRY_STORE"] == database_uri
    assert environment["_MLFLOW_SERVER_ARTIFACT_ROOT"] == "mlflow-artifacts:/"
    assert environment["_MLFLOW_SERVER_ARTIFACT_DESTINATION"] == "s3://${MINIO_BUCKET_MLFLOW:-mlflow}"
    assert environment["_MLFLOW_SERVER_SERVE_ARTIFACTS"] == "true"
    assert "localhost:5000" in environment["MLFLOW_SERVER_ALLOWED_HOSTS"].split(",")
    # The Kong origin must be allowed or every UI write through Kong is a 403.
    origins = environment["MLFLOW_SERVER_CORS_ALLOWED_ORIGINS"].split(",")
    assert "http://mlflow.localhost:${KONG_HTTP_PORT}" in origins
    assert "http://localhost:${MLFLOW_PORT}" in origins


def test_minio_provisions_mlflow_bucket_and_scoped_credentials() -> None:
    minio_manifest = yaml.safe_load(
        (REPO_ROOT / "services" / "minio" / "service.yml").read_text()
    )
    env_vars = {entry["name"]: entry for entry in minio_manifest["env"]}

    assert env_vars["MINIO_BUCKET_MLFLOW"]["default"] == "mlflow"
    assert env_vars["MINIO_MLFLOW_ACCESS_KEY"]["secret"] is True
    assert env_vars["MINIO_MLFLOW_SECRET_KEY"]["secret"] is True

    minio_compose = yaml.safe_load(
        (REPO_ROOT / "services" / "minio" / "compose.yml").read_text()
    )
    minio_init_env = minio_compose["services"]["minio-init"]["environment"]
    assert minio_init_env["MINIO_BUCKET_MLFLOW"] == "${MINIO_BUCKET_MLFLOW}"
    assert minio_init_env["MINIO_MLFLOW_ACCESS_KEY"] == "${MINIO_MLFLOW_ACCESS_KEY}"
    assert minio_init_env["MINIO_MLFLOW_SECRET_KEY"] == "${MINIO_MLFLOW_SECRET_KEY}"

    script = (
        REPO_ROOT / "services" / "minio" / "init" / "scripts" / "init-minio.sh"
    ).read_text()
    assert "mlflow:MINIO_BUCKET_MLFLOW:MINIO_MLFLOW_ACCESS_KEY:MINIO_MLFLOW_SECRET_KEY" in script


def test_key_generator_creates_mlflow_credentials(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PROJECT_NAME=atlas-test\n"
        "MLFLOW_DB_PASSWORD=\n"
        "MINIO_MLFLOW_ACCESS_KEY=\n"
        "MINIO_MLFLOW_SECRET_KEY=\n"
    )

    results = KeyGenerator(str(tmp_path)).generate_missing_keys()
    generated = ConfigParser(str(tmp_path)).parse_env_file()

    assert results["MLFLOW_DB_PASSWORD"] is True
    assert results["MINIO_MLFLOW_ACCESS_KEY"] is True
    assert results["MINIO_MLFLOW_SECRET_KEY"] is True
    assert generated["MLFLOW_DB_PASSWORD"]
    assert generated["MINIO_MLFLOW_ACCESS_KEY"]
    assert generated["MINIO_MLFLOW_SECRET_KEY"]


def test_jupyterhub_receives_mlflow_tracking_uri_and_client() -> None:
    manifest = yaml.safe_load(
        (REPO_ROOT / "services" / "jupyterhub" / "service.yml").read_text()
    )
    compose = yaml.safe_load(
        (REPO_ROOT / "services" / "jupyterhub" / "compose.yml").read_text()
    )
    adaptation = manifest["runtime_adaptive"]["jupyterhub"]

    assert "mlflow" in adaptation["adapts_to"]
    assert adaptation["environment_adaptation"]["MLFLOW_TRACKING_URI"] == "${MLFLOW_TRACKING_URI}"
    assert (
        compose["services"]["jupyterhub"]["environment"]["MLFLOW_TRACKING_URI"]
        == "${MLFLOW_TRACKING_URI:-}"
    )
    assert "mlflow" in (REPO_ROOT / "services" / "jupyterhub" / "build" / "requirements.txt").read_text()


def test_mlflow_kong_route_only_when_container() -> None:
    from utils.kong_config_generator import KongConfigGenerator

    def _config(env: dict[str, str]) -> dict:
        cp = ConfigParser(str(REPO_ROOT))
        gen = KongConfigGenerator(cp)
        gen.load_environment_variables = lambda: setattr(gen, "env_vars", env)
        return gen.generate_kong_config()

    enabled = _config({"MLFLOW_SOURCE": "container"})
    disabled = _config({"MLFLOW_SOURCE": "disabled"})

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
    assert enabled_hosts["mlflow.localhost"]["name"] == "mlflow"
    assert enabled_hosts["mlflow.localhost"]["url"] == "http://mlflow:5000/"
    assert enabled_hosts["mlflow.localhost"]["routes"][0]["preserve_host"] is True
    assert {plugin["name"] for plugin in enabled_hosts["mlflow.localhost"]["plugins"]} >= {
        "basic-auth",
        "acl",
        "cors",
    }
    assert "mlflow.localhost" not in disabled_hosts


def test_mlflow_docs_describe_scope_and_notebook_smoke() -> None:
    readme = README.read_text()

    assert "MLFLOW_SOURCE=disabled" in readme
    assert "mlflow.localhost" in readme
    assert "MLFLOW_TRACKING_URI" in readme
    assert "MinIO-backed artifact" in readme
    assert "model promotion automations are out of scope" in readme
    assert "mlflow.start_run" in readme


_OUTDATED = (
    "Detected out-of-date database schema (found version 6f8d9c3b2a1e, but "
    "expected b7e2c1a4d9f3). Take a backup of your database, then run "
    "'mlflow db upgrade <database_uri>'"
)


def _serve_against_stores(
    monkeypatch: pytest.MonkeyPatch,
    errors: list[str],
    *,
    registry: str = "postgresql://db/mlflow",
    upgrade_fails: bool = False,
) -> list[tuple[str, object]]:
    """Run ``_serve`` with store initialization raising ``errors`` in order."""

    async def upstream(scope, receive, send):
        pass

    module = _load_guard(monkeypatch, upstream)
    mlflow_exception = sys.modules["mlflow.exceptions"].MlflowException
    constants = types.ModuleType("mlflow.server.constants")
    constants.BACKEND_STORE_URI_ENV_VAR = "_MLFLOW_SERVER_FILE_STORE"
    constants.REGISTRY_STORE_URI_ENV_VAR = "_MLFLOW_SERVER_REGISTRY_STORE"
    constants.ARTIFACT_ROOT_ENV_VAR = "_MLFLOW_SERVER_ARTIFACT_ROOT"
    handlers = types.ModuleType("mlflow.server.handlers")
    events: list[tuple[str, object]] = []
    pending = list(errors)

    def initialize_backend_stores(*stores: str) -> None:
        events.append(("stores", stores))
        if pending:
            raise mlflow_exception(pending.pop(0))

    def run(argv: list[str], *, check: bool) -> None:
        events.append(("upgrade", (argv, check)))
        if upgrade_fails:
            raise subprocess.CalledProcessError(1, argv)

    handlers.initialize_backend_stores = initialize_backend_stores
    monkeypatch.setitem(sys.modules, "mlflow.server.constants", constants)
    monkeypatch.setitem(sys.modules, "mlflow.server.handlers", handlers)
    monkeypatch.setenv("_MLFLOW_SERVER_FILE_STORE", "postgresql://db/mlflow")
    monkeypatch.setenv("_MLFLOW_SERVER_REGISTRY_STORE", registry)
    monkeypatch.setenv("_MLFLOW_SERVER_ARTIFACT_ROOT", "mlflow-artifacts:/")
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.os, "execvp", lambda *_args: events.append(("exec", None)))
    module._serve()
    return events


@pytest.mark.parametrize(
    ("registry", "upgraded"),
    (
        ("postgresql://db/mlflow", ["postgresql://db/mlflow"]),
        ("postgresql://db/registry", ["postgresql://db/mlflow", "postgresql://db/registry"]),
    ),
)
def test_mlflow_serve_upgrades_a_schema_an_earlier_pin_created(
    monkeypatch: pytest.MonkeyPatch, registry: str, upgraded: list[str]
) -> None:
    """A 3.15.1 database is migrated once per distinct store, then served (#1287)."""
    events = _serve_against_stores(monkeypatch, [_OUTDATED], registry=registry)

    stores = ("postgresql://db/mlflow", registry, "mlflow-artifacts:/")
    assert events == [
        ("stores", stores),
        *[
            ("upgrade", ([sys.executable, "-m", "mlflow", "db", "upgrade", uri], True))
            for uri in upgraded
        ],
        ("stores", stores),
        ("exec", None),
    ]


def test_mlflow_serve_never_upgrades_for_other_store_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(Exception, match="connection refused"):
        _serve_against_stores(monkeypatch, ["connection refused"])


def test_mlflow_serve_does_not_launch_when_the_schema_upgrade_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        _serve_against_stores(monkeypatch, [_OUTDATED], upgrade_fails=True)


@pytest.mark.parametrize(
    "case",
    (
        ("POST", "/api/2.0/mlflow/experiments/create", b"http://localhost:8080", False),
        ("OPTIONS", "/api/2.0/mlflow/experiments/delete", b"http://127.0.0.1:3000", False),
        ("GET", "/ajax-api/2.0/mlflow/experiments/search", b"http://localhost:8080", False),
        ("POST", "/graphql", b"null", False),
        ("POST", "/api/2.0/mlflow/experiments/create", b"http://localhost:5000", True),
        ("POST", "/api/2.0/mlflow/experiments/create", None, True),
        ("GET", "/static-files/app.js", b"http://localhost:8080", True),
    ),
)
def test_origin_guard_holds_mlflow_to_the_exact_allowed_origins(
    monkeypatch: pytest.MonkeyPatch, case: tuple
) -> None:
    """MLflow admits any localhost origin on any port; the direct port has no
    login (2026-10-08 run, cycle 73)."""
    import asyncio

    method, path, origin, reaches = case

    seen: list[str] = []
    sent: list[dict] = []

    async def upstream(scope, receive, send):
        seen.append(scope["path"])

    module = _load_guard(monkeypatch, upstream)
    monkeypatch.setenv(
        "MLFLOW_SERVER_CORS_ALLOWED_ORIGINS",
        "http://mlflow.localhost:63002, http://localhost:5000",
    )

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    headers = [] if origin is None else [(b"origin", origin)]
    asyncio.run(module.app({"type": "http", "method": method, "path": path, "headers": headers}, receive, send))

    assert (seen == [path]) is reaches
    if not reaches:
        assert sent[0]["status"] == 403
