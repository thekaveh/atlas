"""Exact async subset of the Docling Serve API used by LightRAG v1.5.4."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from bounded_upload import EmptyUploadError, UploadTooLargeError, spool_upload

from .jobs import JobRegistry
from .upstream import DoclingUpstream, UpstreamConversionError


SUBMIT_PATH = "/v1/convert/file/async"


class _EphemeralFileResponse(FileResponse):
    """Finish a claimed result lease after one complete-body transmission."""

    def __init__(
        self, path: Path, *, finish, timeout_seconds: float, **kwargs
    ) -> None:
        self._finish = finish
        self._timeout_seconds = timeout_seconds
        super().__init__(path, **kwargs)
        if "accept-ranges" in self.headers:
            del self.headers["accept-ranges"]

    async def __call__(self, scope, receive, send) -> None:
        # This endpoint is intentionally one-shot. Ignore Range so malformed,
        # unsatisfiable, and partial requests all receive the complete archive.
        scope = {
            **scope,
            "headers": [
                (name, value)
                for name, value in scope.get("headers", [])
                if name.lower() != b"range"
            ],
        }
        try:
            await asyncio.wait_for(
                super().__call__(scope, receive, send),
                timeout=self._timeout_seconds,
            )
        finally:
            await self._finish()


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _safe_suffix(filename: str | None) -> str:
    suffix = Path(filename or "upload").suffix.lower()
    if 1 < len(suffix) <= 12 and suffix[1:].isalnum():
        return suffix
    return ".bin"


class _AdmissionMiddleware:
    def __init__(self, app, *, registry: JobRegistry):
        self.app = app
        self.registry = registry

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method", "").upper() != "POST"
            or scope.get("path") != SUBMIT_PATH
        ):
            await self.app(scope, receive, send)
            return

        reservation = await self.registry.reserve()
        if reservation is None:
            response = JSONResponse(
                {"detail": "Adapter capacity is full"},
                status_code=429,
                headers={"Retry-After": "1"},
            )
            await response(scope, receive, send)
            return

        scope.setdefault("state", {})["adapter_reservation"] = reservation
        try:
            await self.app(scope, receive, send)
        finally:
            await self.registry.release_reservation(reservation)


def create_app(
    *,
    upstream=None,
    spool_root: Path | None = None,
    max_jobs: int | None = None,
    result_ttl_seconds: int | None = None,
    upload_max_bytes: int | None = None,
    job_timeout_seconds: int | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    root = spool_root or Path(os.getenv("DOCLING_ADAPTER_SPOOL_ROOT", "/tmp/docling-adapter"))
    configured_max_jobs = max_jobs or _positive_int("DOCLING_ADAPTER_MAX_JOBS", 2)
    maximum_upload = upload_max_bytes or _positive_int(
        "DOCLING_MAX_FILE_SIZE", 52_428_800
    )
    maximum_result = _positive_int(
        "DOCLING_ADAPTER_MAX_RESULT_BYTES", 104_857_600
    )
    root.mkdir(parents=True, exist_ok=True)
    per_slot_storage = max(
        2 * maximum_upload,
        maximum_upload + maximum_result,
    )
    required_storage = configured_max_jobs * per_slot_storage
    required_storage += 64 * 1024 * 1024
    if shutil.disk_usage(root).free < required_storage:
        raise ValueError(
            "Docling adapter temporary storage is smaller than the configured "
            "concurrent upload and result budget"
        )
    registry = JobRegistry(
        root=root,
        max_jobs=configured_max_jobs,
        result_ttl_seconds=result_ttl_seconds
        or _positive_int("DOCLING_ADAPTER_RESULT_TTL_SECONDS", 900),
        clock=clock,
    )
    timeout = job_timeout_seconds or _positive_int(
        "DOCLING_INFERENCE_TIMEOUT_SECONDS", 900
    )
    download_timeout = _positive_int(
        "DOCLING_ADAPTER_DOWNLOAD_TIMEOUT_SECONDS", 300
    )
    provider = upstream or DoclingUpstream(
        endpoint=os.getenv(
            "DOCLING_ADAPTER_UPSTREAM_ENDPOINT",
            "http://docling-gpu:8000/internal/lightrag/bundle",
        ),
        token=os.getenv("DOCLING_API_TOKEN", ""),
        result_root=root,
        max_result_bytes=maximum_result,
        max_capacity_retries=_positive_int(
            "DOCLING_ADAPTER_UPSTREAM_MAX_ATTEMPTS", 3
        )
        - 1,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async def sweep_expired_results() -> None:
            interval = min(60, registry.result_ttl_seconds)
            while True:
                await asyncio.sleep(interval)
                await registry.cleanup_expired()

        sweeper = asyncio.create_task(sweep_expired_results())
        try:
            yield
        finally:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
            await registry.close()

    app = FastAPI(
        title="Docling LightRAG Adapter",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.post(SUBMIT_PATH, status_code=202)
    async def submit(request: Request, files: UploadFile = File(...)):
        reservation = getattr(request.state, "adapter_reservation", None)
        if reservation is None:
            return JSONResponse({"detail": "Adapter admission failed"}, status_code=503)
        try:
            upload_path = await spool_upload(
                files,
                max_bytes=maximum_upload,
                suffix=_safe_suffix(files.filename),
                directory=root,
            )
        except UploadTooLargeError:
            return JSONResponse({"detail": "Upload is too large"}, status_code=413)
        except EmptyUploadError:
            return JSONResponse({"detail": "Upload is empty"}, status_code=400)
        finally:
            await files.close()

        upload_name = Path(files.filename or "upload.bin").name or "upload.bin"

        async def convert(path: Path, name: str) -> bytes | Path:
            return await provider.convert(path, name, timeout)

        try:
            task_id = await registry.start(
                reservation,
                upload_path=upload_path,
                upload_name=upload_name,
                worker=convert,
            )
        except BaseException:
            upload_path.unlink(missing_ok=True)
            raise
        return {"task_id": task_id}

    @app.get("/v1/status/poll/{task_id}")
    async def poll(task_id: str, wait: int = 0):
        del wait
        status = await registry.status(task_id)
        if status is None:
            return JSONResponse({"detail": "Task not found"}, status_code=404)
        return {"task_id": task_id, "task_status": status}

    @app.get("/v1/result/{task_id}")
    async def result(task_id: str):
        status = await registry.status(task_id)
        if status is None:
            return JSONResponse({"detail": "Task not found"}, status_code=404)
        if status != "success":
            return JSONResponse({"detail": "Task result is not ready"}, status_code=409)
        result_path = await registry.claim_result(task_id)
        if result_path is None:
            return JSONResponse({"detail": "Task not found"}, status_code=404)
        return _EphemeralFileResponse(
            result_path,
            media_type="application/zip",
            finish=lambda: registry.finish_result(task_id),
            timeout_seconds=download_timeout,
        )

    app.add_middleware(_AdmissionMiddleware, registry=registry)
    app.state.job_registry = registry
    return app


app = create_app()
