"""Exercise Atlas's MLflow guard against the exact shipped container image."""

from __future__ import annotations

import argparse
import json
import queue
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
STATIC_PREFIX = "/atlas-smoke"
COMMAND_TIMEOUT_SECONDS = 300
CLEANUP_TIMEOUT_SECONDS = 30
CLEANUP_RECONCILE_SECONDS = 30
CLEANUP_POLL_SECONDS = 0.25
READINESS_TIMEOUT_SECONDS = 60
READINESS_REQUEST_TIMEOUT_SECONDS = 2
HTTP_REQUEST_TIMEOUT_SECONDS = 30
OWNER_LABEL = "com.atlas.mlflow-smoke-owner"
_OWNED_CONTAINERS: set[str] = set()
_INFLIGHT_OWNER_TOKENS: set[str] = set()
_OBSERVED_OWNER_TOKENS: set[str] = set()
_CONTAINER_ID_RE = re.compile(r"[0-9a-f]{12,64}")
_TERMINATING = False
SMOKE_SERVER_ENV = (
    "_MLFLOW_SERVER_FILE_STORE=sqlite:////tmp/mlflow.db",
    "_MLFLOW_SERVER_REGISTRY_STORE=sqlite:////tmp/mlflow.db",
    "_MLFLOW_SERVER_ARTIFACT_ROOT=mlflow-artifacts:/",
    "_MLFLOW_SERVER_ARTIFACT_DESTINATION=file:///tmp/artifacts",
    "_MLFLOW_SERVER_SERVE_ARTIFACTS=true",
    "MLFLOW_SERVER_ALLOWED_HOSTS=localhost:5000",
    f"_MLFLOW_STATIC_PREFIX={STATIC_PREFIX}",
)


def _run(
    *args: str,
    check: bool = True,
    timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Docker command timed out after {timeout_seconds}s: {' '.join(args[:3])}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Docker command could not start ({type(exc).__name__}): "
            f"{' '.join(args[:3])}"
        ) from exc


def _cleanup_container(
    container_id: str,
    *,
    suppress_error: bool,
    timeout_seconds: float = CLEANUP_TIMEOUT_SECONDS,
) -> None:
    try:
        result = _run(
            "docker",
            "rm",
            "--force",
            container_id,
            check=False,
            timeout_seconds=timeout_seconds,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or f"exit {result.returncode}"
            raise RuntimeError(f"failed to remove MLflow smoke container: {detail}")
        _OWNED_CONTAINERS.discard(container_id)
    except Exception as exc:  # noqa: BLE001 — cleanup must not mask a primary failure
        if not suppress_error:
            raise
        print(f"MLflow smoke cleanup warning: {exc}", file=sys.stderr)


def _container_ids_for_owner(
    owner_token: str, timeout_seconds: float = CLEANUP_TIMEOUT_SECONDS
) -> tuple[str, ...]:
    result = _run(
        "docker",
        "ps",
        "--all",
        "--quiet",
        "--filter",
        f"label={OWNER_LABEL}={owner_token}",
        check=False,
        timeout_seconds=timeout_seconds,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"failed to find owned MLflow smoke container: {detail}")
    container_ids = tuple(
        line.strip() for line in result.stdout.splitlines() if line.strip()
    )
    if any(
        _CONTAINER_ID_RE.fullmatch(container_id) is None
        for container_id in container_ids
    ):
        raise RuntimeError("Docker returned an invalid MLflow smoke container ID")
    return container_ids


def _cleanup_timeout(deadline: float) -> float:
    return max(0.1, min(CLEANUP_TIMEOUT_SECONDS, deadline - time.monotonic()))


def _reconcile_owner_token(owner_token: str, wait_for_late_container: bool) -> None:
    deadline = time.monotonic() + CLEANUP_RECONCILE_SECONDS
    saw_container = False
    last_error: Exception | None = None
    while True:
        try:
            container_ids = _container_ids_for_owner(
                owner_token, _cleanup_timeout(deadline)
            )
            if not container_ids and (saw_container or not wait_for_late_container):
                _INFLIGHT_OWNER_TOKENS.discard(owner_token)
                _OBSERVED_OWNER_TOKENS.discard(owner_token)
                return
            if container_ids:
                _OBSERVED_OWNER_TOKENS.add(owner_token)
            for container_id in container_ids:
                saw_container = True
                _OWNED_CONTAINERS.add(container_id)
                _cleanup_container(
                    container_id,
                    suppress_error=False,
                    timeout_seconds=_cleanup_timeout(deadline),
                )
            last_error = None
        except Exception as exc:  # noqa: BLE001 — retain authority and retry
            last_error = exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(CLEANUP_POLL_SECONDS, remaining))
    if last_error is not None:
        raise RuntimeError("failed to reconcile owned MLflow smoke container") from last_error
    if saw_container:
        raise RuntimeError("could not confirm removal of owned MLflow smoke container")
    raise RuntimeError(
        "could not establish absence of an owned MLflow smoke container; "
        "retaining owner token for a later cleanup attempt"
    )


def _cleanup_owner_token(
    owner_token: str,
    *,
    suppress_error: bool,
    wait_for_late_container: bool,
) -> None:
    try:
        _reconcile_owner_token(owner_token, wait_for_late_container)
    except Exception as exc:  # noqa: BLE001 — cleanup must not mask a primary failure
        if not suppress_error:
            raise
        print(f"MLflow smoke cleanup warning: {exc}", file=sys.stderr)


def _confirm_observed_owner_cleanup(failures: list[Exception]) -> None:
    for owner_token in tuple(
        _INFLIGHT_OWNER_TOKENS & _OBSERVED_OWNER_TOKENS
    ):
        try:
            _cleanup_owner_token(
                owner_token,
                suppress_error=False,
                wait_for_late_container=False,
            )
        except Exception as exc:  # noqa: BLE001 — finish every cleanup attempt
            failures.append(exc)


def _cleanup_all_owned(*, suppress_error: bool) -> None:
    failures: list[Exception] = []
    for owner_token in tuple(_INFLIGHT_OWNER_TOKENS):
        try:
            _cleanup_owner_token(
                owner_token,
                suppress_error=False,
                wait_for_late_container=True,
            )
        except Exception as exc:  # noqa: BLE001 — finish every cleanup attempt
            failures.append(exc)
    for container_id in tuple(_OWNED_CONTAINERS):
        try:
            _cleanup_container(container_id, suppress_error=False)
        except Exception as exc:  # noqa: BLE001 — finish every cleanup attempt
            failures.append(exc)
    _confirm_observed_owner_cleanup(failures)
    if not _OWNED_CONTAINERS and not _INFLIGHT_OWNER_TOKENS:
        return
    failure = RuntimeError(
        "MLflow smoke cleanup left owned resources after bounded retries: "
        f"containers={sorted(_OWNED_CONTAINERS)!r}, "
        f"owner_tokens={sorted(_INFLIGHT_OWNER_TOKENS)!r}"
    )
    if not suppress_error:
        raise failure from (failures[-1] if failures else None)
    print(f"MLflow smoke cleanup warning: {failure}", file=sys.stderr)


def _handle_termination(signum: int, _frame: object) -> None:
    global _TERMINATING
    if _TERMINATING:
        return
    _TERMINATING = True
    _cleanup_all_owned(suppress_error=True)
    raise SystemExit(128 + signum)


def _install_signal_handlers() -> None:
    for signal_name in ("SIGHUP", "SIGINT", "SIGTERM"):
        termination_signal = getattr(signal, signal_name, None)
        if termination_signal is not None:
            signal.signal(termination_signal, _handle_termination)


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Host": "localhost:5000"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items())


def _json_request(
    base_url: str, path: str, payload: dict[str, object], method: str = "POST"
) -> tuple[int, bytes, dict[str, str]]:
    return _request_with_total_timeout(
        base_url,
        path,
        HTTP_REQUEST_TIMEOUT_SECONDS,
        {"method": method, "data": json.dumps(payload).encode()},
    )


def _request_with_total_timeout(
    base_url: str,
    path: str,
    timeout_seconds: float,
    request_kwargs: dict[str, object] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    """Bound connect and slow-response time without retaining a worker process."""
    outcome: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def invoke() -> None:
        try:
            outcome.put((True, _request(base_url, path, **(request_kwargs or {}))))
        except Exception as exc:  # noqa: BLE001 — re-raised on the caller thread
            outcome.put((False, exc))

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        raise TimeoutError(f"HTTP request exceeded {timeout_seconds:.1f}s")
    succeeded, value = outcome.get_nowait()
    if not succeeded:
        raise value  # type: ignore[misc]
    return value  # type: ignore[return-value]


def _wait_for_health(base_url: str) -> bool:
    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    path = f"{STATIC_PREFIX}/health"
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            timeout = min(READINESS_REQUEST_TIMEOUT_SECONDS, remaining)
            if _request_with_total_timeout(base_url, path, timeout)[0] == 200:
                return True
        except TimeoutError:
            raise
        except OSError:
            pass
        time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
    return False


def _assert_pinned_base() -> str:
    manifest = yaml.safe_load((ROOT / "services/mlflow/service.yml").read_text())
    image = manifest["images"][0]["default"]
    compose = (ROOT / "services/mlflow/compose.yml").read_text()
    if image not in compose:
        raise RuntimeError(f"MLflow image {image!r} is not synchronized with Compose")
    return image


def _verify_owned_container(container_id: str, owner_token: str) -> str:
    if _CONTAINER_ID_RE.fullmatch(container_id) is None:
        raise RuntimeError("Docker create returned an invalid MLflow smoke container ID")
    result = _run(
        "docker",
        "inspect",
        "--format",
        f'{{{{.Id}}}} {{{{index .Config.Labels "{OWNER_LABEL}"}}}}',
        container_id,
    )
    parts = result.stdout.strip().split()
    if (
        len(parts) != 2
        or parts[1] != owner_token
        or not parts[0].startswith(container_id)
    ):
        raise RuntimeError("Docker did not verify ownership of the MLflow smoke container")
    if _CONTAINER_ID_RE.fullmatch(parts[0]) is None:
        raise RuntimeError("Docker inspect returned an invalid MLflow smoke container ID")
    return parts[0]


def _is_definite_name_conflict(exc: BaseException) -> bool:
    if not isinstance(exc, subprocess.CalledProcessError) or not isinstance(
        exc.stderr, str
    ):
        return False
    detail = exc.stderr.casefold()
    return "container name" in detail and "already in use" in detail


def _create_owned_container(
    name: str, owner_token: str, docker_args: tuple[str, ...]
) -> str:
    _INFLIGHT_OWNER_TOKENS.add(owner_token)
    created = False
    try:
        result = _run(
            "docker",
            "create",
            "--name",
            name,
            "--label",
            f"{OWNER_LABEL}={owner_token}",
            *docker_args,
        )
        created = True
        container_id = _verify_owned_container(result.stdout.strip(), owner_token)
        _OBSERVED_OWNER_TOKENS.add(owner_token)
        _OWNED_CONTAINERS.add(container_id)
        return container_id
    except BaseException as exc:
        _cleanup_owner_token(
            owner_token,
            suppress_error=True,
            wait_for_late_container=(
                created or not _is_definite_name_conflict(exc)
            ),
        )
        raise


def _create_and_start_container(
    name: str, owner_token: str, docker_args: tuple[str, ...]
) -> str:
    container_id = _create_owned_container(name, owner_token, docker_args)
    try:
        _run("docker", "start", container_id)
        return container_id
    except BaseException:
        _cleanup_owner_token(
            owner_token,
            suppress_error=True,
            wait_for_late_container=False,
        )
        raise


def _assert_runtime_drivers(image: str, name: str, owner_token: str) -> None:
    probe = (
        "import boto3, psycopg2; "
        "from mlflow.store.artifact.s3_artifact_repo import S3ArtifactRepository; "
        "from sqlalchemy import create_engine; "
        "engine=create_engine('postgresql://atlas:atlas@127.0.0.1/atlas'); "
        "assert engine.dialect.dbapi.__name__ == 'psycopg2'; "
        "assert boto3.__version__ == '1.43.56'; "
        "assert psycopg2.__version__.startswith('2.9.12'); "
        "assert S3ArtifactRepository"
    )
    container_id: str | None = None
    try:
        container_id = _create_and_start_container(
            name,
            owner_token,
            (
                "--pull=never",
                image,
                "python",
                "-c",
                probe,
            ),
        )
        result = _run("docker", "wait", container_id)
        if result.stdout.strip() != "0":
            logs = _run("docker", "logs", container_id, check=False)
            detail = (logs.stderr or logs.stdout).strip()[-2000:]
            raise RuntimeError(f"MLflow runtime driver probe failed: {detail}")
    finally:
        if owner_token in _INFLIGHT_OWNER_TOKENS:
            _cleanup_owner_token(
                owner_token,
                suppress_error=sys.exc_info()[0] is not None,
                wait_for_late_container=container_id is None,
            )


def _assert_blocked(base_url: str, path: str) -> None:
    status, body, headers = _json_request(base_url, path, {})
    if status != 404 or body != b'{"detail":"Not Found"}':
        raise AssertionError(f"gateway route escaped guard: {path} -> {status} {body!r}")
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    if normalized_headers.get("cache-control") != "no-store":
        raise AssertionError(f"gateway 404 did not come from Atlas guard: {path} -> {headers!r}")


def _assert_tracking_registry_and_artifacts(base_url: str) -> None:
    status, _, _ = _request_with_total_timeout(
        base_url,
        f"{STATIC_PREFIX}/api/2.0/mlflow/experiments/get-by-name?experiment_name=Default",
        HTTP_REQUEST_TIMEOUT_SECONDS,
    )
    if status != 200:
        raise AssertionError(f"tracking API is not reachable: HTTP {status}")

    model_name = "atlas-guard-smoke-model"
    status, response, _ = _json_request(
        base_url,
        f"{STATIC_PREFIX}/api/2.0/mlflow/registered-models/create",
        {"name": model_name},
    )
    if status != 200:
        raise AssertionError(f"registered-model creation failed: HTTP {status} {response!r}")
    status, response, _ = _request_with_total_timeout(
        base_url,
        f"{STATIC_PREFIX}/api/2.0/mlflow/registered-models/get?name={model_name}",
        HTTP_REQUEST_TIMEOUT_SECONDS,
    )
    if status != 200 or json.loads(response)["registered_model"]["name"] != model_name:
        raise AssertionError(f"registered-model read failed: HTTP {status} {response!r}")

    status, response, _ = _json_request(
        base_url,
        f"{STATIC_PREFIX}/api/2.0/mlflow/runs/create",
        {"experiment_id": "0", "start_time": int(time.time() * 1000), "tags": []},
    )
    if status != 200:
        raise AssertionError(f"run creation failed: HTTP {status} {response!r}")
    run_id = json.loads(response)["run"]["info"]["run_id"]
    artifact_path = f"0/{run_id}/artifacts/atlas-guard-smoke.txt"
    artifact_api = f"{STATIC_PREFIX}/api/2.0/mlflow-artifacts/artifacts/{artifact_path}"
    status, response, _ = _request_with_total_timeout(
        base_url,
        artifact_api,
        HTTP_REQUEST_TIMEOUT_SECONDS,
        {"method": "PUT", "data": b"atlas guard smoke\n"},
    )
    if status not in (200, 201):
        raise AssertionError(f"artifact upload failed: HTTP {status} {response!r}")
    status, response, _ = _request_with_total_timeout(
        base_url, artifact_api, HTTP_REQUEST_TIMEOUT_SECONDS
    )
    if status != 200 or response != b"atlas guard smoke\n":
        raise AssertionError(f"artifact download failed: HTTP {status} {response!r}")


def _image_argument() -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, help="locally built Atlas MLflow image")
    return parser.parse_args().image


def _start_container(image: str, name: str, owner_token: str) -> str:
    environment: list[str] = []
    for value in SMOKE_SERVER_ENV:
        environment.extend(("--env", value))
    return _create_and_start_container(
        name,
        owner_token,
        (
            "--pull=never",
            "--publish",
            "127.0.0.1::5000",
            *environment,
            image,
            "python",
            "atlas_server.py",
        ),
    )


def main() -> int:
    image = _image_argument()
    _assert_pinned_base()
    _install_signal_handlers()
    try:
        driver_token = secrets.token_hex(16)
        _assert_runtime_drivers(
            image, f"atlas-mlflow-driver-smoke-{driver_token}", driver_token
        )
        owner_token = secrets.token_hex(16)
        name = f"atlas-mlflow-guard-smoke-{owner_token}"
        container_id = _start_container(image, name, owner_token)
        endpoint = _run("docker", "port", container_id, "5000/tcp").stdout.strip()
        base_url = f"http://127.0.0.1:{endpoint.rsplit(':', 1)[1]}"
        if not _wait_for_health(base_url):
            logs = _run("docker", "logs", container_id, check=False).stdout
            raise RuntimeError(f"guarded MLflow server did not become healthy:\n{logs}")

        for path in (
            f"{STATIC_PREFIX}/gateway/proxy/atlas/chat/completions",
            f"{STATIC_PREFIX}/api/3.0/mlflow/gateway/secrets/create",
            f"{STATIC_PREFIX}/ajax-api/3.0/mlflow/gateway/secrets/create",
            f"{STATIC_PREFIX}/ajax-api/2.0/mlflow/gateway-proxy",
        ):
            _assert_blocked(base_url, path)
        _assert_tracking_registry_and_artifacts(base_url)
        print(f"MLflow gateway guard smoke passed for {image}")
        return 0
    finally:
        _cleanup_all_owned(suppress_error=sys.exc_info()[0] is not None)


if __name__ == "__main__":
    raise SystemExit(main())
