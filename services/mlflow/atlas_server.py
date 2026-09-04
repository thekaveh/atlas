"""Atlas MLflow entrypoint with the unfixed AI Gateway surface disabled."""

from __future__ import annotations

import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from mlflow.version import VERSION
from packaging.version import Version


Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]

_NOT_FOUND = b'{"detail":"Not Found"}'

if Version(VERSION) != Version("3.15.1"):
    raise RuntimeError(
        "Atlas requires exactly MLflow 3.15.1: the gateway denylist and server "
        "integration must be reviewed before accepting another release"
    )


def _without_static_prefix(path: str) -> str:
    """Undo MLflow's ``_add_static_prefix`` before classifying a route."""
    prefix = os.environ.get("_MLFLOW_STATIC_PREFIX", "").rstrip("/")
    if prefix and (path == prefix or path.startswith(f"{prefix}/")):
        return path[len(prefix) :] or "/"
    return path


def _is_gateway_path(path: str) -> bool:
    """Match every native, REST, and AJAX MLflow AI Gateway route family."""
    path = _without_static_prefix(path)
    if path == "/gateway" or path.startswith("/gateway/"):
        return True

    for api_prefix in ("/api/", "/ajax-api/"):
        if not path.startswith(api_prefix):
            continue
        _, separator, route = path[len(api_prefix) :].partition("/mlflow/")
        if not separator:
            continue
        if route == "gateway" or route.startswith(("gateway/", "gateway-")):
            return True
    return False


class GatewayDisabled:
    """Fail closed around CVE-2026-71211 while preserving tracking APIs."""

    def __init__(self, upstream: Callable[[Scope, Receive, Send], Awaitable[None]]):
        self._upstream = upstream

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if _is_gateway_path(scope.get("path", "")):
            if scope.get("type") == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            if scope.get("type") == "http":
                await send(
                    {
                        "type": "http.response.start",
                        "status": 404,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"cache-control", b"no-store"),
                            (b"content-length", str(len(_NOT_FOUND)).encode("ascii")),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": _NOT_FOUND})
                return
        await self._upstream(scope, receive, send)


def _guarded_app() -> GatewayDisabled:
    from mlflow.server.fastapi_app import app as upstream_app

    return GatewayDisabled(upstream_app)


def _serve() -> None:
    """Preserve MLflow CLI's fail-fast store initialization, then exec ASGI."""
    from mlflow.server.constants import (
        ARTIFACT_ROOT_ENV_VAR,
        BACKEND_STORE_URI_ENV_VAR,
        REGISTRY_STORE_URI_ENV_VAR,
    )
    from mlflow.server.handlers import initialize_backend_stores

    backend_store = os.environ[BACKEND_STORE_URI_ENV_VAR]
    registry_store = os.environ.get(REGISTRY_STORE_URI_ENV_VAR, backend_store)
    artifact_root = os.environ[ARTIFACT_ROOT_ENV_VAR]
    initialize_backend_stores(backend_store, registry_store, artifact_root)
    os.execvp(
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
    )


if __name__ == "__main__":
    _serve()
else:
    app = _guarded_app()
