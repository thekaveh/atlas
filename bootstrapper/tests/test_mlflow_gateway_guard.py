from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
from datetime import date
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
    version: str = "3.15.1",
    static_prefix: str | None = None,
):
    mlflow = types.ModuleType("mlflow")
    server = types.ModuleType("mlflow.server")
    fastapi_app = types.ModuleType("mlflow.server.fastapi_app")
    version_module = types.ModuleType("mlflow.version")
    fastapi_app.app = upstream
    version_module.VERSION = version
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.server", server)
    monkeypatch.setitem(sys.modules, "mlflow.server.fastapi_app", fastapi_app)
    monkeypatch.setitem(sys.modules, "mlflow.version", version_module)
    if static_prefix is None:
        monkeypatch.delenv("_MLFLOW_STATIC_PREFIX", raising=False)
    else:
        monkeypatch.setenv("_MLFLOW_STATIC_PREFIX", static_prefix)

    spec = importlib.util.spec_from_file_location("atlas_test_mlflow_server", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version", ("3.14.0", "3.15.0", "3.15.2", "4.0.0"))
def test_gateway_guard_refuses_every_unreviewed_server_version(
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    async def upstream(scope, receive, send):  # pragma: no cover - import must fail
        pass

    with pytest.raises(RuntimeError, match="exactly MLflow 3.15.1"):
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
                "BASE_IMAGE": "${MLFLOW_IMAGE:-ghcr.io/mlflow/mlflow:v3.15.1}"
            },
        },
        "ghcr.io/mlflow/mlflow:v3.15.1",
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

    assert "exactly reviewed MLflow 3.15.1" in capability["note"]


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
        'create_engine("postgresql://' in dockerfile,
    ) == (True, True, True, True, True, True)


def test_mlflow_compose_preserves_the_cli_storage_contract() -> None:
    service, _ = _mlflow_service_config()
    environment = service["environment"]

    assert (
        environment["_MLFLOW_SERVER_FILE_STORE"].startswith("postgresql://"),
        environment["_MLFLOW_SERVER_REGISTRY_STORE"].startswith("postgresql://"),
        environment["_MLFLOW_SERVER_ARTIFACT_ROOT"],
        environment["_MLFLOW_SERVER_ARTIFACT_DESTINATION"].startswith("s3://"),
        environment["_MLFLOW_SERVER_SERVE_ARTIFACTS"],
        "localhost:5000" in environment["MLFLOW_SERVER_ALLOWED_HOSTS"].split(","),
        "http://localhost:5000/health" in service["healthcheck"]["test"][1],
    ) == (True, True, "mlflow-artifacts:/", True, "true", True, True)


def test_mlflow_server_advisory_exception_is_exact_bounded_and_mitigated() -> None:
    exceptions = yaml.safe_load((ROOT / ".trivyignore.yaml").read_text())["vulnerabilities"]
    matches = [entry for entry in exceptions if entry["id"] == "CVE-2026-71211"]

    assert len(matches) == 1
    exception = matches[0]
    assert exception["purls"] == ["pkg:pypi/mlflow@3.15.1"]
    assert exception["expired_at"] == date(2026, 9, 30)
    statement = exception["statement"]
    for evidence in (
        "no fixed release",
        "AI Gateway",
        "404",
        "atlas_server.py",
        "tracking",
        "CVE-2026-64849",
        "Jupyter",
    ):
        assert evidence in statement


def test_mlflow_image_smoke_bounds_every_docker_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(smoke.subprocess, "run", hang)

    with pytest.raises(RuntimeError, match="timed out after"):
        smoke._run("docker", "port", "atlas-smoke", "5000/tcp")


@pytest.mark.parametrize("error", (FileNotFoundError("missing"), PermissionError("denied")))
def test_mlflow_image_smoke_normalizes_docker_launch_errors(
    monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    smoke = _load_smoke_module()
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
    )

    with pytest.raises(RuntimeError, match="Docker command could not start") as caught:
        smoke._run("docker", "port", "atlas-smoke", "5000/tcp")

    assert caught.value.__cause__ is error


def test_mlflow_image_smoke_cleanup_preserves_primary_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    smoke = _load_smoke_module()
    monkeypatch.setattr(
        smoke,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("hung cleanup")),
    )

    smoke._cleanup_container("atlas-smoke", suppress_error=True)

    assert "hung cleanup" in capsys.readouterr().err


def test_mlflow_image_smoke_cleanup_failure_is_not_hidden_without_primary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    monkeypatch.setattr(
        smoke,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("hung cleanup")),
    )

    with pytest.raises(RuntimeError, match="hung cleanup"):
        smoke._cleanup_container("atlas-smoke", suppress_error=False)


@pytest.mark.parametrize("error", (FileNotFoundError("missing"), PermissionError("denied")))
def test_mlflow_image_smoke_cleanup_suppresses_ordinary_error_during_primary_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: OSError,
) -> None:
    smoke = _load_smoke_module()
    monkeypatch.setattr(
        smoke, "_run", lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
    )

    smoke._cleanup_container("atlas-smoke", suppress_error=True)

    assert str(error) in capsys.readouterr().err


@pytest.mark.parametrize("error", (FileNotFoundError("missing"), PermissionError("denied")))
def test_mlflow_image_smoke_cleanup_propagates_ordinary_error_without_primary_failure(
    monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    smoke = _load_smoke_module()
    monkeypatch.setattr(
        smoke, "_run", lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
    )

    with pytest.raises(type(error), match=str(error)):
        smoke._cleanup_container("atlas-smoke", suppress_error=False)


def test_mlflow_driver_probe_timeout_cleans_its_verified_container_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    events: list[tuple[str, ...]] = []
    container_id = "a" * 64
    owner_token = "1" * 32
    removed = [False]

    def fake_run(*args, **_kwargs):
        events.append(args)
        if args[:2] == ("docker", "create"):
            return subprocess.CompletedProcess(args, 0, f"{container_id}\n", "")
        if args[:2] == ("docker", "inspect"):
            return subprocess.CompletedProcess(
                args, 0, f"{container_id} {owner_token}\n", ""
            )
        if args[:2] == ("docker", "wait"):
            raise RuntimeError("driver probe timed out")
        if args[:3] == ("docker", "ps", "--all"):
            output = "" if removed[0] else f"{container_id}\n"
            return subprocess.CompletedProcess(args, 0, output, "")
        if args[:3] == ("docker", "rm", "--force"):
            removed[0] = True
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(smoke, "_run", fake_run)

    with pytest.raises(RuntimeError, match="driver probe timed out"):
        smoke._assert_runtime_drivers(
            "atlas-mlflow:test", "atlas-driver-probe", owner_token
        )

    launch = next(args for args in events if args[:2] == ("docker", "create"))
    assert launch[launch.index("--name") + 1] == "atlas-driver-probe"
    assert launch[launch.index("--label") + 1] == (
        f"{smoke.OWNER_LABEL}={owner_token}"
    )
    assert ("docker", "rm", "--force", container_id) in events


@pytest.mark.parametrize("surface", ("driver", "server"))
def test_mlflow_name_collision_never_removes_an_unowned_container(
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
) -> None:
    smoke = _load_smoke_module()
    events: list[tuple[str, ...]] = []
    owner_token = "2" * 32
    name = f"preexisting-{surface}"

    def fake_run(*args, **_kwargs):
        events.append(args)
        if args[:2] == ("docker", "create"):
            raise subprocess.CalledProcessError(
                125,
                args,
                stderr=(
                    'Conflict. The container name "/occupied" is already in use '
                    "by another container."
                ),
            )
        if args[:3] == ("docker", "ps", "--all"):
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(smoke, "_run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        if surface == "driver":
            smoke._assert_runtime_drivers("atlas-mlflow:test", name, owner_token)
        else:
            smoke._start_container("atlas-mlflow:test", name, owner_token)

    assert (
        [args for args in events if args[:3] == ("docker", "rm", "--force")],
        all(name not in args for args in events if args[:2] == ("docker", "rm")),
        owner_token in smoke._INFLIGHT_OWNER_TOKENS,
        owner_token in smoke._OBSERVED_OWNER_TOKENS,
    ) == ([], True, False, False)


def test_mlflow_ambiguous_create_failure_recovers_only_by_owner_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    events: list[tuple[str, ...]] = []
    owner_token = "4" * 32
    container_id = "c" * 64
    removed = [False]

    def fake_run(*args, **_kwargs):
        events.append(args)
        if args[:2] == ("docker", "create"):
            raise RuntimeError("create response timed out")
        if args[:3] == ("docker", "ps", "--all"):
            output = "" if removed[0] else f"{container_id}\n"
            return subprocess.CompletedProcess(args, 0, output, "")
        if args[:3] == ("docker", "rm", "--force"):
            removed[0] = True
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(smoke, "_run", fake_run)

    with pytest.raises(RuntimeError, match="create response timed out"):
        smoke._start_container("atlas-mlflow:test", "random-name", owner_token)

    discovery = next(args for args in events if args[:3] == ("docker", "ps", "--all"))
    assert discovery[discovery.index("--filter") + 1] == (
        f"label={smoke.OWNER_LABEL}={owner_token}"
    )
    assert ("docker", "rm", "--force", container_id) in events


@pytest.mark.parametrize(
    "create_error",
    (
        RuntimeError("ambiguous create timeout"),
        subprocess.CalledProcessError(
            125, ("docker", "create"), stderr="daemon response lost"
        ),
    ),
    ids=("client-timeout", "daemon-create-error"),
)
def test_mlflow_ambiguous_create_reconciles_delayed_container_visibility(
    monkeypatch: pytest.MonkeyPatch,
    create_error: BaseException,
) -> None:
    smoke = _load_smoke_module()
    owner_token = "6" * 32
    container_id = "d" * 64
    discoveries = iter(("", f"{container_id}\n", ""))
    removed: list[str] = []
    now = [0.0]

    def fake_run(*args, **_kwargs):
        if args[:2] == ("docker", "create"):
            raise create_error
        if args[:3] == ("docker", "ps", "--all"):
            return subprocess.CompletedProcess(args, 0, next(discoveries), "")
        if args[:3] == ("docker", "rm", "--force"):
            removed.append(args[3])
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(smoke, "_run", fake_run)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(smoke.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))

    with pytest.raises(type(create_error)):
        smoke._start_container("atlas-mlflow:test", "random-name", owner_token)

    assert (
        removed,
        owner_token in smoke._INFLIGHT_OWNER_TOKENS,
        owner_token in smoke._OBSERVED_OWNER_TOKENS,
    ) == (
        [container_id],
        False,
        False,
    )


def test_mlflow_ambiguous_no_show_retains_token_for_outer_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    owner_token = "a" * 32
    container_id = "f" * 64
    visible = [False]
    removed: list[str] = []
    now = [0.0]

    def fake_run(*args, **_kwargs):
        if args[:2] == ("docker", "create"):
            raise RuntimeError("ambiguous create timeout")
        if args[:3] == ("docker", "ps", "--all"):
            output = f"{container_id}\n" if visible[0] and not removed else ""
            return subprocess.CompletedProcess(args, 0, output, "")
        if args[:3] == ("docker", "rm", "--force"):
            removed.append(args[3])
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(smoke, "_run", fake_run)
    monkeypatch.setattr(smoke, "CLEANUP_RECONCILE_SECONDS", 0.5)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        smoke.time,
        "sleep",
        lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    with pytest.raises(RuntimeError, match="ambiguous create timeout"):
        smoke._start_container("atlas-mlflow:test", "random-name", owner_token)

    assert (owner_token in smoke._INFLIGHT_OWNER_TOKENS, removed) == (True, [])

    visible[0] = True
    smoke._cleanup_all_owned(suppress_error=False)

    assert (
        removed,
        owner_token in smoke._INFLIGHT_OWNER_TOKENS,
        bool(smoke._OWNED_CONTAINERS),
    ) == ([container_id], False, False)


def test_mlflow_post_create_inspect_failure_waits_for_label_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    owner_token = "b" * 32
    container_id = "a" * 64
    discoveries = iter(("", f"{container_id}\n", ""))
    removed: list[str] = []
    now = [0.0]

    def fake_run(*args, **_kwargs):
        if args[:2] == ("docker", "create"):
            return subprocess.CompletedProcess(args, 0, f"{container_id}\n", "")
        if args[:2] == ("docker", "inspect"):
            raise subprocess.CalledProcessError(1, args, stderr="daemon restarting")
        if args[:3] == ("docker", "ps", "--all"):
            return subprocess.CompletedProcess(args, 0, next(discoveries), "")
        if args[:3] == ("docker", "rm", "--force"):
            removed.append(args[3])
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(smoke, "_run", fake_run)
    monkeypatch.setattr(smoke, "CLEANUP_RECONCILE_SECONDS", 0.5)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        smoke.time,
        "sleep",
        lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    with pytest.raises(subprocess.CalledProcessError):
        smoke._start_container("atlas-mlflow:test", "random-name", owner_token)

    assert (
        removed,
        owner_token in smoke._INFLIGHT_OWNER_TOKENS,
        bool(smoke._OWNED_CONTAINERS),
    ) == ([container_id], False, False)


def test_mlflow_failed_owner_cleanup_retains_retry_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    owner_token = "7" * 32
    container_id = "e" * 64
    now = [0.0]
    removal_attempts = [0]
    removed = [False]

    def fake_run(*args, **_kwargs):
        if args[:3] == ("docker", "ps", "--all"):
            output = "" if removed[0] else f"{container_id}\n"
            return subprocess.CompletedProcess(args, 0, output, "")
        if args[:3] == ("docker", "rm", "--force"):
            removal_attempts[0] += 1
            if removal_attempts[0] == 5:
                removed[0] = True
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 1, "", "daemon restarting")
        raise AssertionError(args)

    monkeypatch.setattr(smoke, "_run", fake_run)
    monkeypatch.setattr(smoke, "CLEANUP_RECONCILE_SECONDS", 0.25)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(smoke.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))

    smoke._INFLIGHT_OWNER_TOKENS.add(owner_token)
    smoke._cleanup_owner_token(
        owner_token, suppress_error=True, wait_for_late_container=True
    )

    assert (
        owner_token in smoke._INFLIGHT_OWNER_TOKENS,
        container_id in smoke._OWNED_CONTAINERS,
        removal_attempts,
    ) == (True, True, [2])

    smoke._cleanup_all_owned(suppress_error=False)

    assert (
        owner_token in smoke._INFLIGHT_OWNER_TOKENS,
        bool(smoke._OWNED_CONTAINERS),
        owner_token in smoke._OBSERVED_OWNER_TOKENS,
        removal_attempts,
    ) == (False, False, False, [5])


def test_mlflow_interrupted_owner_discovery_retains_retry_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    owner_token = "8" * 32
    smoke._INFLIGHT_OWNER_TOKENS.add(owner_token)
    monkeypatch.setattr(
        smoke,
        "_container_ids_for_owner",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    with pytest.raises(KeyboardInterrupt):
        smoke._cleanup_owner_token(
            owner_token,
            suppress_error=True,
            wait_for_late_container=True,
        )

    assert owner_token in smoke._INFLIGHT_OWNER_TOKENS


def test_mlflow_driver_preverification_interrupt_retries_late_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    owner_token = "9" * 32
    cleanup_modes: list[bool] = []

    def interrupt_before_verification(*_args) -> str:
        smoke._INFLIGHT_OWNER_TOKENS.add(owner_token)
        raise KeyboardInterrupt

    def cleanup(
        token: str, *, suppress_error: bool, wait_for_late_container: bool
    ) -> None:
        assert token == owner_token
        assert suppress_error is True
        cleanup_modes.append(wait_for_late_container)
        smoke._INFLIGHT_OWNER_TOKENS.discard(token)

    monkeypatch.setattr(smoke, "_create_and_start_container", interrupt_before_verification)
    monkeypatch.setattr(smoke, "_cleanup_owner_token", cleanup)

    with pytest.raises(KeyboardInterrupt):
        smoke._assert_runtime_drivers("atlas-mlflow:test", "driver-name", owner_token)

    assert cleanup_modes == [True]


def test_mlflow_health_request_has_a_total_slow_response_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    release = smoke.threading.Event()

    def slow_response(*_args, **_kwargs):
        release.wait(1)
        return 200, b"", {}

    monkeypatch.setattr(smoke, "_request", slow_response)
    started = smoke.time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="HTTP request exceeded"):
            smoke._request_with_total_timeout("http://localhost", "/health", 0.01)
    finally:
        release.set()

    assert smoke.time.monotonic() - started < 0.5


def test_mlflow_readiness_loop_honors_one_overall_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    now = [0.0]
    attempts: list[float] = []

    def unavailable(*_args, **_kwargs):
        attempts.append(_kwargs.get("timeout_seconds", _args[-1]))
        raise ConnectionRefusedError("starting")

    monkeypatch.setattr(smoke, "_request_with_total_timeout", unavailable)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: now.__setitem__(0, 61.0))

    assert smoke._wait_for_health("http://localhost") is False
    assert attempts == [smoke.READINESS_REQUEST_TIMEOUT_SECONDS]


def test_mlflow_readiness_does_not_retry_a_slow_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    attempts: list[None] = []

    def slow(*_args, **_kwargs):
        attempts.append(None)
        raise TimeoutError("slow response")

    monkeypatch.setattr(smoke, "_request_with_total_timeout", slow)

    with pytest.raises(TimeoutError, match="slow response"):
        smoke._wait_for_health("http://localhost")

    assert attempts == [None]


def test_mlflow_termination_signal_cleans_every_owned_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    cleaned: list[str] = []
    smoke._OWNED_CONTAINERS.update(("driver-probe", "server-probe"))

    def cleanup(
        name: str, *, suppress_error: bool, timeout_seconds: float = 30
    ) -> None:
        assert suppress_error is False
        assert timeout_seconds == smoke.CLEANUP_TIMEOUT_SECONDS
        cleaned.append(name)
        smoke._OWNED_CONTAINERS.discard(name)

    monkeypatch.setattr(smoke, "_cleanup_container", cleanup)

    with pytest.raises(SystemExit) as caught:
        smoke._handle_termination(smoke.signal.SIGTERM, None)

    assert (caught.value.code, set(cleaned)) == (
        128 + smoke.signal.SIGTERM,
        {"driver-probe", "server-probe"},
    )


def test_mlflow_termination_signal_recovers_an_inflight_labeled_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    owner_token = "5" * 32
    recovered: list[tuple[str, bool]] = []
    smoke._INFLIGHT_OWNER_TOKENS.add(owner_token)

    def cleanup(
        token: str, *, suppress_error: bool, wait_for_late_container: bool
    ) -> None:
        recovered.append((token, suppress_error))
        assert wait_for_late_container is True
        smoke._INFLIGHT_OWNER_TOKENS.discard(token)

    monkeypatch.setattr(smoke, "_cleanup_owner_token", cleanup)

    with pytest.raises(SystemExit):
        smoke._handle_termination(smoke.signal.SIGTERM, None)

    assert recovered == [(owner_token, False)]


def test_mlflow_repeated_termination_does_not_interrupt_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    events: list[str] = []

    def cleanup(*, suppress_error: bool) -> None:
        assert suppress_error is True
        events.append("cleanup-start")
        smoke._handle_termination(smoke.signal.SIGTERM, None)
        events.append("cleanup-finished")

    monkeypatch.setattr(smoke, "_cleanup_all_owned", cleanup)

    with pytest.raises(SystemExit) as caught:
        smoke._handle_termination(smoke.signal.SIGTERM, None)

    assert caught.value.code == 128 + smoke.signal.SIGTERM
    assert events == ["cleanup-start", "cleanup-finished"]


def test_mlflow_repeated_signal_during_outer_cleanup_preserves_first_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    cleanup_calls = [0]

    def cleanup(*, suppress_error: bool) -> None:
        assert suppress_error is True
        cleanup_calls[0] += 1
        if cleanup_calls[0] == 2:
            smoke._handle_termination(smoke.signal.SIGINT, None)

    monkeypatch.setattr(smoke, "_cleanup_all_owned", cleanup)

    with pytest.raises(SystemExit) as caught:
        try:
            smoke._handle_termination(smoke.signal.SIGTERM, None)
        finally:
            smoke._cleanup_all_owned(suppress_error=True)

    assert (caught.value.code, cleanup_calls) == (
        128 + smoke.signal.SIGTERM,
        [2],
    )


def test_mlflow_installs_hup_and_term_cleanup_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    installed: dict[int, object] = {}
    monkeypatch.setattr(
        smoke.signal, "signal", lambda signum, handler: installed.update({signum: handler})
    )

    smoke._install_signal_handlers()

    assert (
        installed[smoke.signal.SIGHUP],
        installed[smoke.signal.SIGINT],
        installed[smoke.signal.SIGTERM],
    ) == (
        smoke._handle_termination,
        smoke._handle_termination,
        smoke._handle_termination,
    )


def test_mlflow_image_smoke_launch_interrupt_still_attempts_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    cleanup: list[str] = []
    container_id = "b" * 64
    owner_token = "3" * 32
    removed = [False]
    monkeypatch.setattr(smoke, "_image_argument", lambda: "atlas-mlflow:test")
    monkeypatch.setattr(smoke, "_assert_pinned_base", lambda: None)
    monkeypatch.setattr(
        smoke, "_assert_runtime_drivers", lambda _image, _name, _token: None
    )
    monkeypatch.setattr(smoke, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr(smoke.secrets, "token_hex", lambda _size: owner_token)

    def interrupt_launch(*args, **kwargs):
        if args[:2] == ("docker", "create"):
            return subprocess.CompletedProcess(args, 0, f"{container_id}\n", "")
        if args[:2] == ("docker", "inspect"):
            return subprocess.CompletedProcess(
                args, 0, f"{container_id} {owner_token}\n", ""
            )
        if args[:2] == ("docker", "start"):
            return subprocess.CompletedProcess(args, 0, f"{container_id}\n", "")
        if args[:2] == ("docker", "port"):
            raise KeyboardInterrupt
        if args[:3] == ("docker", "ps", "--all"):
            output = "" if removed[0] else f"{container_id}\n"
            return subprocess.CompletedProcess(args, 0, output, "")
        if args[:3] == ("docker", "rm", "--force"):
            removed[0] = True
            cleanup.append(args[3])
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(smoke, "_run", interrupt_launch)

    with pytest.raises(KeyboardInterrupt):
        smoke.main()

    assert cleanup == [container_id]


def test_jupyter_mlflow_exception_scans_every_shipped_execution_surface() -> None:
    build = ROOT / "services/jupyterhub/build"
    tracked = subprocess.run(
        ["git", "ls-files", "services/jupyterhub/build"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    paths = [ROOT / "services/jupyterhub/compose.yml", *(ROOT / path for path in tracked)]

    forbidden = (
        "mlflow.server",
        "creategatewaysecret",
        "create_gateway_secret",
        "create_gateway(",
        "auth_config",
        "/gateway/proxy/",
        "/mlflow/gateway/",
        "mlflow ui",
        "mlflow server",
    )
    violations: list[str] = []
    for path in paths:
        content = path.read_bytes().decode("utf-8", errors="replace")
        content = "\n".join(
            line for line in content.splitlines() if not line.lstrip().startswith("#")
        ).lower()
        for token in forbidden:
            if token in content:
                violations.append(f"{path.relative_to(ROOT)}: {token}")

    assert not violations, "Jupyter's reviewed client-only exception became reachable:\n" + "\n".join(
        violations
    )
