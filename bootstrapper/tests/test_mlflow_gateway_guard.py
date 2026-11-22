from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = ROOT / "services/mlflow/atlas_server.py"
SMOKE_PATH = ROOT / "scripts/smoke_mlflow_gateway_guard.py"


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location("atlas_test_mlflow_smoke", SMOKE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_guard(
    monkeypatch: pytest.MonkeyPatch,
    upstream: Any,
    *,
    version: str = "3.17.2",
    static_prefix: str | None = None,
):
    mlflow = types.ModuleType("mlflow")
    server = types.ModuleType("mlflow.server")
    fastapi_app = types.ModuleType("mlflow.server.fastapi_app")
    version_module = types.ModuleType("mlflow.version")
    exceptions = types.ModuleType("mlflow.exceptions")
    exceptions.MlflowException = type("MlflowException", (Exception,), {})
    fastapi_app.app = upstream
    version_module.VERSION = version
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.server", server)
    monkeypatch.setitem(sys.modules, "mlflow.server.fastapi_app", fastapi_app)
    monkeypatch.setitem(sys.modules, "mlflow.version", version_module)
    monkeypatch.setitem(sys.modules, "mlflow.exceptions", exceptions)
    if static_prefix is None:
        monkeypatch.delenv("_MLFLOW_STATIC_PREFIX", raising=False)
    else:
        monkeypatch.setenv("_MLFLOW_STATIC_PREFIX", static_prefix)

    spec = importlib.util.spec_from_file_location("atlas_test_mlflow_server", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "version", ("3.14.0", "3.15.1", "3.16.0", "3.16.1", "3.16.2", "3.17.1", "4.0.0")
)
def test_gateway_guard_refuses_every_unreviewed_server_version(
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    async def upstream(scope, receive, send):  # pragma: no cover - import must fail
        pass

    with pytest.raises(RuntimeError, match="exactly MLflow 3.17.2"):
        _load_guard(monkeypatch, upstream, version=version)


def test_mlflow_serve_initializes_stores_before_exact_uvicorn_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def upstream(scope, receive, send):
        pass

    module = _load_guard(monkeypatch, upstream)
    constants = types.ModuleType("mlflow.server.constants")
    constants.BACKEND_STORE_URI_ENV_VAR = "_MLFLOW_SERVER_FILE_STORE"
    constants.REGISTRY_STORE_URI_ENV_VAR = "_MLFLOW_SERVER_REGISTRY_STORE"
    constants.ARTIFACT_ROOT_ENV_VAR = "_MLFLOW_SERVER_ARTIFACT_ROOT"
    handlers = types.ModuleType("mlflow.server.handlers")
    events: list[tuple[str, object]] = []

    def initialize_backend_stores(backend: str, registry: str, artifact: str) -> None:
        events.append(("stores", (backend, registry, artifact)))

    handlers.initialize_backend_stores = initialize_backend_stores
    monkeypatch.setitem(sys.modules, "mlflow.server.constants", constants)
    monkeypatch.setitem(sys.modules, "mlflow.server.handlers", handlers)
    monkeypatch.setenv("_MLFLOW_SERVER_FILE_STORE", "sqlite:////tmp/tracking.db")
    monkeypatch.setenv("_MLFLOW_SERVER_REGISTRY_STORE", "sqlite:////tmp/registry.db")
    monkeypatch.setenv("_MLFLOW_SERVER_ARTIFACT_ROOT", "mlflow-artifacts:/")
    monkeypatch.setattr(
        module.os,
        "execvp",
        lambda executable, argv: events.append(("exec", (executable, argv))),
    )

    module._serve()

    assert events[0] == (
        "stores",
        ("sqlite:////tmp/tracking.db", "sqlite:////tmp/registry.db", "mlflow-artifacts:/"),
    )
    assert events[1] == (
        "exec",
        (
            sys.executable,
            [
                sys.executable,
                "-m",
                "uvicorn",
                "--host",
                "0.0.0.0",
                "--port",
                "5000",
                "--workers",
                "4",
                "atlas_server:app",
            ],
        ),
    )


def test_mlflow_serve_does_not_launch_when_store_initialization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def upstream(scope, receive, send):
        pass

    module = _load_guard(monkeypatch, upstream)
    constants = types.ModuleType("mlflow.server.constants")
    constants.BACKEND_STORE_URI_ENV_VAR = "_MLFLOW_SERVER_FILE_STORE"
    constants.REGISTRY_STORE_URI_ENV_VAR = "_MLFLOW_SERVER_REGISTRY_STORE"
    constants.ARTIFACT_ROOT_ENV_VAR = "_MLFLOW_SERVER_ARTIFACT_ROOT"
    handlers = types.ModuleType("mlflow.server.handlers")

    def fail_initialization(*_args) -> None:
        raise RuntimeError("invalid store")

    handlers.initialize_backend_stores = fail_initialization
    monkeypatch.setitem(sys.modules, "mlflow.server.constants", constants)
    monkeypatch.setitem(sys.modules, "mlflow.server.handlers", handlers)
    monkeypatch.setenv("_MLFLOW_SERVER_FILE_STORE", "invalid://store")
    monkeypatch.setenv("_MLFLOW_SERVER_ARTIFACT_ROOT", "mlflow-artifacts:/")
    monkeypatch.setattr(
        module.os,
        "execvp",
        lambda *_args: pytest.fail("Uvicorn launched after store initialization failed"),
    )

    with pytest.raises(RuntimeError, match="invalid store"):
        module._serve()


@pytest.mark.parametrize(
    "path",
    (
        "/gateway",
        "/gateway/",
        "/gateway/proxy/atlas/chat/completions",
        "/api/3.0/mlflow/gateway/secrets/create",
        "/ajax-api/3.0/mlflow/gateway/secrets/create",
        "/ajax-api/3.0/mlflow/gateway/supported-providers",
        "/ajax-api/2.0/mlflow/gateway-proxy",
        "/api/2.0/mlflow/gateway-proxy",
        "/ajax-api/3.0/mlflow/assistant/providers",
        "/ajax-api/3.0/mlflow/assistant/providers/openai/models",
        "/ajax-api/3.0/mlflow/assistant/sessions/s1/stream",
        "/api/3.0/mlflow/assistant/message",
        "/endpoints",
        "/servers/demo/endpoints",
        "/servers/demo/endpoints/e1",
    ),
)
def test_gateway_guard_rejects_every_mlflow_gateway_route_family(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    async def upstream(scope, receive, send):  # pragma: no cover - must not run
        raise AssertionError(f"blocked request reached MLflow: {scope['path']}")

    module = _load_guard(monkeypatch, upstream)
    sent: list[dict[str, Any]] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    import asyncio

    asyncio.run(
        module.app(
            {"type": "http", "method": "POST", "path": path, "headers": []},
            receive,
            send,
        )
    )

    assert sent == [
        {
            "type": "http.response.start",
            "status": 404,
            "headers": [
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
                (b"content-length", b"22"),
            ],
        },
        {"type": "http.response.body", "body": b'{"detail":"Not Found"}'},
    ]


@pytest.mark.parametrize(
    "path",
    (
        "/tenant/gateway/proxy/atlas/chat/completions",
        "/tenant/api/3.0/mlflow/gateway/secrets/create",
        "/tenant/ajax-api/3.0/mlflow/gateway/secrets/create",
        "/tenant/ajax-api/2.0/mlflow/gateway-proxy",
    ),
)
def test_gateway_guard_rejects_routes_beneath_mlflow_static_prefix(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    async def upstream(scope, receive, send):  # pragma: no cover - must not run
        raise AssertionError(f"blocked request reached MLflow: {scope['path']}")

    module = _load_guard(monkeypatch, upstream, static_prefix="/tenant")
    sent: list[dict[str, Any]] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    import asyncio

    asyncio.run(
        module.app(
            {"type": "http", "method": "POST", "path": path, "headers": []},
            receive,
            send,
        )
    )

    assert sent[0]["status"] == 404
    assert sent[1]["body"] == b'{"detail":"Not Found"}'


@pytest.mark.parametrize(
    "path",
    (
        "/health",
        "/api/2.0/mlflow/runs/create",
        "/ajax-api/3.0/mlflow/experiments/search",
        "/api/2.0/mlflow-artifacts/artifacts/run/file",
        "/mlflow/gatewayish",
    ),
)
def test_gateway_guard_preserves_tracking_and_artifact_routes(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    seen: list[str] = []

    async def upstream(scope, receive, send):
        seen.append(scope["path"])

    module = _load_guard(monkeypatch, upstream)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):  # pragma: no cover - upstream owns the response
        pass

    import asyncio

    asyncio.run(
        module.app(
            {"type": "http", "method": "GET", "path": path, "headers": []},
            receive,
            send,
        )
    )

    assert seen == [path]


@pytest.mark.parametrize(
    "path",
    (
        "/tenant/health",
        "/tenant/api/2.0/mlflow/runs/create",
        "/tenant/ajax-api/3.0/mlflow/experiments/search",
        "/tenantish/api/3.0/mlflow/gateway/secrets/create",
    ),
)
def test_gateway_guard_preserves_non_gateway_routes_with_static_prefix(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    seen: list[str] = []

    async def upstream(scope, receive, send):
        seen.append(scope["path"])

    module = _load_guard(monkeypatch, upstream, static_prefix="/tenant")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):  # pragma: no cover - upstream owns the response
        pass

    import asyncio

    asyncio.run(
        module.app(
            {"type": "http", "method": "GET", "path": path, "headers": []},
            receive,
            send,
        )
    )

    assert seen == [path]


def _mlflow_service_config() -> tuple[dict[str, Any], dict[str, Any]]:
    compose = yaml.safe_load((ROOT / "services/mlflow/compose.yml").read_text())
    manifest = yaml.safe_load((ROOT / "services/mlflow/service.yml").read_text())
    return compose["services"]["mlflow"], manifest


def test_mlflow_compose_runs_the_guarded_patched_server() -> None:
    service, manifest = _mlflow_service_config()

    assert (
        service["image"],
        service["build"],
        manifest["images"][0]["default"],
        service["command"],
        "volumes" in service,
        service["working_dir"],
        "mlflow server" in " ".join(service["command"]),
    ) == (
        "${PROJECT_NAME}-mlflow:local",
        {
            "context": ".",
            "dockerfile": "build/Dockerfile",
            "args": {
                "BASE_IMAGE": "${MLFLOW_IMAGE:-ghcr.io/mlflow/mlflow:v3.17.2}"
            },
        },
        "ghcr.io/mlflow/mlflow:v3.17.2",
        ["python", "atlas_server.py"],
        False,
        "/opt/atlas",
        False,
    )


def test_mlflow_manifest_documents_the_exact_reviewed_guard_version() -> None:
    _, manifest = _mlflow_service_config()
    capability = next(
        item
        for item in manifest["capabilities"]
        if item["name"] == "MLflow AI Gateway SSRF containment"
    )

    assert "exactly reviewed MLflow 3.17.2" in capability["note"]
    assert "CVE-2026-71211" in capability["note"]
    assert "CVE-2026-116692" in capability["note"]


def test_mlflow_runtime_image_installs_postgres_and_s3_drivers() -> None:
    dockerfile = (ROOT / "services/mlflow/build/Dockerfile").read_text()
    requirements = (ROOT / "services/mlflow/build/requirements.txt").read_text()
    lock = (ROOT / "services/mlflow/build/requirements-locked.txt").read_text()

    assert (
        "boto3==1.43.56" in requirements,
        "psycopg2-binary==2.9.12" in requirements,
        "botocore==1.43.56" in lock,
        "pip check" in dockerfile,
        "S3ArtifactRepository" in dockerfile,
        'create_engine("postgresql+psycopg2://' in dockerfile,
    ) == (True, True, True, True, True, True)


def test_mlflow_compose_preserves_the_cli_storage_contract() -> None:
    service, _ = _mlflow_service_config()
    environment = service["environment"]

    assert (
        environment["_MLFLOW_SERVER_FILE_STORE"].startswith("postgresql+psycopg2://"),
        environment["_MLFLOW_SERVER_REGISTRY_STORE"].startswith("postgresql+psycopg2://"),
        environment["_MLFLOW_SERVER_ARTIFACT_ROOT"],
        environment["_MLFLOW_SERVER_ARTIFACT_DESTINATION"].startswith("s3://"),
        environment["_MLFLOW_SERVER_SERVE_ARTIFACTS"],
        "localhost:5000" in environment["MLFLOW_SERVER_ALLOWED_HOSTS"].split(","),
        "http://localhost:5000/health" in service["healthcheck"]["test"][1],
    ) == (True, True, "mlflow-artifacts:/", True, "true", True, True)


def test_mlflow_server_advisory_is_fixed_by_the_pin_not_excepted() -> None:
    """3.16.1 fixes CVE-2026-71211 (#1287) and 3.17.2 fixes the scanner findings (#1948)."""
    exceptions = yaml.safe_load((ROOT / ".trivyignore.yaml").read_text())["vulnerabilities"]

    assert not [
        entry
        for entry in exceptions
        if entry["id"] == "CVE-2026-71211"
        or any(purl.startswith("pkg:pypi/mlflow@") for purl in entry.get("purls", []))
    ]
    dockerfile = (ROOT / "services/mlflow/build/Dockerfile").read_text()
    assert "ARG BASE_IMAGE=ghcr.io/mlflow/mlflow:v3.17.2\n" in dockerfile
    assert "class GatewayDisabled" in GUARD_PATH.read_text()
