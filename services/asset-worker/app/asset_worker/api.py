from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from .models import (
    RefPostprocessRequest,
    PostprocessParams,
    PostprocessResponse,
    normalization_metadata,
    optimization_metadata,
)
from .runner import GltfTransformError, run_gltf_transform
from .storage import ArtifactStorage, ArtifactTooLargeError, CONTENT_TYPE


logger = logging.getLogger(__name__)


def create_app(*, api_token: str | None = None) -> FastAPI:
    app = FastAPI(
        title="Atlas Asset Worker",
        description="glTF post-processing worker for Atlas creative and 3D pipelines.",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    expected_token = (
        api_token if api_token is not None else os.getenv("ASSET_WORKER_API_TOKEN", "")
    )
    concurrency = max(1, int(os.getenv("ASSET_WORKER_CONCURRENCY", "1")))
    app.state.transform_semaphore = threading.Semaphore(concurrency)

    @app.middleware("http")
    async def require_api_token_and_admit(request: Request, call_next):
        if request.url.path in {"/health", "/metrics"}:
            return await call_next(request)
        if not expected_token:
            return JSONResponse(
                status_code=503,
                content={"detail": "Asset Worker authentication is not configured"},
            )
        scheme, _, credential = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(credential, expected_token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid Asset Worker bearer token"},
            )
        admitted = False
        if request.method == "POST" and request.url.path in {
            "/gltf/postprocess",
            "/gltf/postprocess/ref",
        }:
            if not app.state.transform_semaphore.acquire(blocking=False):
                logger.info("asset_transform_rejected reason=busy")
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Asset worker is busy; retry later"},
                )
            admitted = True
        try:
            return await call_next(request)
        except asyncio.CancelledError:
            active_work = getattr(request.state, "asset_work_task", None)
            if active_work is not None and not active_work.done():
                try:
                    await asyncio.shield(active_work)
                except (Exception, asyncio.CancelledError):
                    pass
            raise
        finally:
            if admitted:
                app.state.transform_semaphore.release()

    @app.get("/health")
    def health() -> JSONResponse:
        binary = os.getenv("ASSET_WORKER_GLTF_TRANSFORM_BIN", "gltf-transform")
        ready = shutil.which(binary) is not None
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ok" if ready else "unavailable", "gltf_transform": ready},
        )

    @app.post("/gltf/postprocess", response_model=PostprocessResponse)
    async def postprocess_upload(
        request: Request,
        file: UploadFile = File(...),
        target_height_m: float | None = Form(default=None),
        target_width_m: float | None = Form(default=None),
        normalize_axis: str = Form(default="height"),
        up_axis: str = Form(default="keep"),
        simplify_ratio: float | None = Form(default=None),
        draco: bool = Form(default=False),
        meshopt: bool = Form(default=True),
        ktx2: bool = Form(default=False),
        collider_decimation: float | None = Form(default=None),
    ) -> PostprocessResponse:
        _validate_glb_name(file.filename or "")
        params = PostprocessParams(
            target_height_m=target_height_m,
            target_width_m=target_width_m,
            normalize_axis=normalize_axis,
            up_axis=up_axis,
            simplify_ratio=simplify_ratio,
            draco=draco,
            meshopt=meshopt,
            ktx2=ktx2,
            collider_decimation=collider_decimation,
        )
        return await _run_blocking_request(request, _process_upload, file.file, params)

    @app.post("/gltf/postprocess/ref", response_model=PostprocessResponse)
    async def postprocess_ref(
        payload: RefPostprocessRequest,
        request: Request,
    ) -> PostprocessResponse:
        _require_allowed_bucket(payload.input.bucket)
        return await _run_blocking_request(request, _process_reference, payload)

    def _process_reference(payload: RefPostprocessRequest) -> PostprocessResponse:
        storage = ArtifactStorage()
        try:
            data = storage.fetch(payload.input.bucket, payload.input.key)
        except ArtifactTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        return _process_bytes(
            data,
            payload.params,
            storage=storage,
        )

    @app.get("/gltf/artifacts/{sha256}.glb")
    def get_local_artifact(
        sha256: str,
    ):
        if not _is_sha256(sha256):
            raise HTTPException(status_code=400, detail="Invalid sha256")
        path = ArtifactStorage().local_path(sha256)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Artifact not found")
        return FileResponse(path, media_type=CONTENT_TYPE, filename=f"{sha256}.glb")

    Instrumentator(
        excluded_handlers=["/metrics", "/health"],
        should_group_status_codes=True,
    ).instrument(app).expose(app, endpoint="/metrics")
    return app


async def _run_blocking_request(request: Request, function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    request.state.asset_work_task = task
    return await asyncio.shield(task)


def _process_upload(source, params: PostprocessParams) -> PostprocessResponse:
    with tempfile.TemporaryDirectory(prefix="asset-worker-upload-") as tmp:
        input_path = Path(tmp) / "input.glb"
        _copy_upload_to_path(source, input_path)
        return _process_path(input_path, params)


def _process_bytes(
    data: bytes,
    params: PostprocessParams,
    *,
    storage: ArtifactStorage | None = None,
) -> PostprocessResponse:
    with tempfile.TemporaryDirectory(prefix="asset-worker-") as tmp:
        tmpdir = Path(tmp)
        input_path = tmpdir / "input.glb"
        input_path.write_bytes(data)
        return _process_path(input_path, params, storage=storage)


def _process_path(
    input_path: Path,
    params: PostprocessParams,
    *,
    storage: ArtifactStorage | None = None,
) -> PostprocessResponse:
    _enforce_input_size(input_path.stat().st_size)
    storage = storage or ArtifactStorage()
    started_at = time.monotonic()
    logger.info("asset_transform_started")
    with tempfile.TemporaryDirectory(prefix="asset-worker-") as tmp:
        output_path = Path(tmp) / "output.glb"
        try:
            run_gltf_transform(input_path, output_path, params)
        except GltfTransformError as exc:
            logger.warning("asset_transform_failed kind=%s", exc.kind)
            status = 504 if exc.kind == "timeout" else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        output = output_path.read_bytes()
    logger.info(
        "asset_transform_completed duration_seconds=%.3f",
        time.monotonic() - started_at,
    )

    sha = hashlib.sha256(output).hexdigest()
    artifact = storage.store(output, sha256=sha)
    download_url = f"/gltf/artifacts/{sha}.glb" if artifact["storage"] == "local" else None
    return PostprocessResponse(
        status="succeeded",
        sha256=sha,
        artifact=artifact,
        download_url=download_url,
        normalization=normalization_metadata(params),
        optimization=optimization_metadata(params),
    )


def _copy_upload_to_path(source, path: Path) -> None:
    max_bytes = _max_input_bytes()
    total = 0
    with path.open("wb") as stream:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"GLB exceeds {max_bytes} byte limit",
                )
            stream.write(chunk)
    _enforce_input_size(total)


def _max_input_bytes() -> int:
    max_mb = float(os.getenv("ASSET_WORKER_MAX_UPLOAD_MB", "200"))
    return max(1, int(max_mb * 1024 * 1024))


def _enforce_input_size(size: int) -> None:
    if size == 0:
        raise HTTPException(status_code=400, detail="GLB input is empty")
    max_bytes = _max_input_bytes()
    if size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"GLB exceeds {max_bytes} byte limit",
        )


def _validate_glb_name(filename: str) -> None:
    if not filename.lower().endswith(".glb"):
        raise HTTPException(status_code=400, detail="Input must be a GLB file")


def _require_allowed_bucket(bucket: str) -> None:
    configured = os.getenv("ASSET_WORKER_ALLOWED_INPUT_BUCKETS", "raw-assets")
    allowed = {value for value in configured.replace(",", " ").split() if value}
    if bucket not in allowed:
        raise HTTPException(status_code=403, detail="Input bucket is not allowed")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())
