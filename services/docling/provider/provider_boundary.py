"""Shared authentication, admission, and deadline policy for model providers."""

import asyncio
import hmac
import logging
import os
import threading
from dataclasses import dataclass
from typing import Callable, Collection, FrozenSet, Literal, Tuple, TypeVar

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, Response


FATAL_TIMEOUT_EXIT_CODE = 70
T = TypeVar("T")
logger = logging.getLogger(__name__)


class ProviderDeadlineExceeded(RuntimeError):
    """A native provider operation exceeded its killable process deadline."""


@dataclass(frozen=True)
class BoundarySettings:
    service_name: str
    token: str
    auth_mode: Literal["required", "disabled"]
    capacity: int
    expensive_paths: FrozenSet[str]
    cors_origins: Tuple[str, ...]


def _positive_int(raw_value: str, variable: str) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{variable} must be a positive integer") from exc
    if value < 1:
        raise ValueError(f"{variable} must be a positive integer")
    return value


def parse_positive_int(variable: str, *, default: int) -> int:
    """Read one positive integer setting during provider initialization."""
    return _positive_int(os.getenv(variable, str(default)), variable)


def load_boundary_settings(
    prefix: str, expensive_paths: Collection[str]
) -> BoundarySettings:
    """Read and validate one provider's boundary-related environment."""
    auth_variable = f"{prefix}_AUTH_MODE"
    auth_mode = os.getenv(auth_variable, "required").strip().lower()
    if auth_mode not in {"required", "disabled"}:
        raise ValueError(f"{auth_variable} must be 'required' or 'disabled'")

    concurrency_variable = f"{prefix}_CONCURRENCY"
    capacity = _positive_int(
        os.getenv(concurrency_variable, "1"), concurrency_variable
    )

    cors_variable = f"{prefix}_CORS_ORIGINS"
    raw_origins = os.getenv(cors_variable, "")
    cors_origins = tuple(
        dict.fromkeys(origin.strip() for origin in raw_origins.split(",") if origin.strip())
    )
    if auth_mode == "required" and "*" in cors_origins:
        raise ValueError(
            f"{cors_variable} cannot contain '*' when authentication is required"
        )

    return BoundarySettings(
        service_name=prefix.title(),
        token=os.getenv(f"{prefix}_API_TOKEN", ""),
        auth_mode=auth_mode,
        capacity=capacity,
        expensive_paths=frozenset(expensive_paths),
        cors_origins=cors_origins,
    )


class _ProviderBoundaryMiddleware:
    def __init__(self, app, *, settings: BoundarySettings):
        self.app = app
        self.settings = settings
        self._permits = threading.BoundedSemaphore(settings.capacity)

    @staticmethod
    def _authorization_header(scope) -> bytes:
        for name, value in scope.get("headers", ()):
            if name.lower() == b"authorization":
                return value
        return b""

    def _authorized(self, scope) -> bool:
        expected = b"Bearer " + self.settings.token.encode("utf-8")
        supplied = self._authorization_header(scope)
        return hmac.compare_digest(supplied, expected)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path != "/health" and self.settings.auth_mode == "required":
            if not self.settings.token:
                response = JSONResponse(
                    {"detail": "Provider authentication is unavailable"},
                    status_code=503,
                )
                await response(scope, receive, send)
                return
            if not self._authorized(scope):
                response = JSONResponse(
                    {"detail": "Authentication required"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return

        needs_permit = (
            scope.get("method", "").upper() == "POST"
            and path in self.settings.expensive_paths
        )
        admitted = False
        if needs_permit:
            admitted = self._permits.acquire(blocking=False)
            if not admitted:
                response = JSONResponse(
                    {"detail": "Provider capacity is full"},
                    status_code=429,
                    headers={"Retry-After": "1"},
                )
                await response(scope, receive, send)
                return

        try:
            await self.app(scope, receive, send)
        finally:
            if admitted:
                self._permits.release()


def install_provider_boundary(
    app: FastAPI, settings: BoundarySettings
) -> None:
    """Install CORS and the outer provider boundary on the application."""
    app.add_middleware(_ProviderBoundaryMiddleware, settings=settings)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["Authorization", "Content-Type"],
        )


def parse_timeout_seconds(prefix: str, *, default: int = 900) -> int:
    variable = f"{prefix}_INFERENCE_TIMEOUT_SECONDS"
    raw_value = os.getenv(variable, str(default))
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{variable} must be an integer from 1 through 3600") from exc
    if not 1 <= value <= 3600:
        raise ValueError(f"{variable} must be an integer from 1 through 3600")
    return value


async def run_with_deadline(
    prefix: str,
    operation: Callable[[], T],
    terminate_on_cancel_timeout: Callable[[int], None] = os._exit,
) -> T:
    """Run native work without detaching it when the HTTP task is cancelled."""
    timeout = parse_timeout_seconds(prefix)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    native_task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.wait_for(asyncio.shield(native_task), timeout=timeout)
    except asyncio.CancelledError as cancelled:
        remaining = max(0.0, deadline - loop.time())
        try:
            await asyncio.wait_for(asyncio.shield(native_task), timeout=remaining)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            logger.error(
                "Cancelled provider request exceeded native deadline "
                "(provider=%s)",
                prefix,
            )
            terminate_on_cancel_timeout(FATAL_TIMEOUT_EXIT_CODE)
            raise ProviderDeadlineExceeded from exc
        except Exception:
            pass
        raise cancelled
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise ProviderDeadlineExceeded from exc


def fatal_timeout_response(
    prefix: str, terminate: Callable[[int], None] = os._exit
) -> Response:
    """Return a generic 504, then terminate after its bytes have been sent."""
    logger.error("Provider inference deadline exceeded (provider=%s)", prefix)
    return JSONResponse(
        {"detail": "Provider operation timed out"},
        status_code=504,
        background=BackgroundTask(terminate, FATAL_TIMEOUT_EXIT_CODE),
    )
