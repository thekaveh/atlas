from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import ValidationError

from .models import (
    RefPostprocessRequest,
    PostprocessParams,
    PostprocessResponse,
    normalization_metadata,
    optimization_metadata,
)
from .normalizer import _glb_json_chunk, external_resource_uris
from .runner import GltfTransformError, run_gltf_transform
from .storage import ArtifactStorage, ArtifactTooLargeError, CONTENT_TYPE


from .upload_body import UploadBody, UploadRejected


logger = logging.getLogger(__name__)


def _validation_detail(exc):
    return jsonable_encoder(
        exc.errors(),
        custom_encoder={float: lambda value: value if math.isfinite(value) else str(value)},
    )


async def _request_validation_error(_request, exc):
    return JSONResponse(status_code=422, content={"detail": _validation_detail(exc)})


async def _join_request_task(task: asyncio.Task):
    """Keep admitted sync work owned through repeated caller cancellation."""
    cancelled: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancelled = exc
    if cancelled is not None:
        try:
            task.result()
        except BaseException:
            pass
        raise cancelled
    return task.result()


def _storage_error_status(exc: Exception):
    """404/403 for a missing or forbidden input object (botocore ClientError),
    instead of a bare 500; None for anything else (same mapping as asset-baker)."""
    response = getattr(exc, "response", None)
    error = response.get("Error", {}) if isinstance(response, dict) else {}
    code = str(error.get("Code", "")) if isinstance(error, dict) else ""
    if code in ("NoSuchKey", "NoSuchBucket", "404", "NotFound"):
        return 404
    if code in ("AccessDenied", "403", "Forbidden"):
        return 403
    return None


class _RequestAdmission:
    def __init__(self, app, owner, expected_token):
        self.app = app
        self.owner = owner
        self.expected_token = expected_token

    def _authenticate(self, request):
        if not self.expected_token:
            return JSONResponse(
                status_code=503, content={"detail": "Asset Worker authentication is not configured"}
            )
        scheme, _, credential = request.headers.get("authorization", "").partition(" ")
        # Byte comparison also handles non-ASCII credentials deliberately.
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            credential.encode("utf-8"), self.expected_token.encode("utf-8")
        ):
            return JSONResponse(
                status_code=401, content={"detail": "Invalid Asset Worker bearer token"}
            )
        return None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        if request.url.path in {"/health", "/metrics"}:
            return await self.app(scope, receive, send)
        rejection = self._authenticate(request)
        if rejection is not None:
            return await rejection(scope, receive, send)
        if request.method == "POST" and request.url.path in {
            "/gltf/postprocess", "/gltf/postprocess/ref",
        }:
            return await self._mutation(request, receive, send)
        await _join_request_task(asyncio.create_task(self.app(scope, receive, send)))

    async def _mutation(self, request, receive, send):
        scope = request.scope
        semaphore = self.owner.state.transform_semaphore
        if not semaphore.acquire(blocking=False):
            logger.info("asset_transform_rejected reason=busy")
            rejection = JSONResponse(
                status_code=429, content={"detail": "Asset worker is busy; retry later"}
            )
            return await rejection(scope, receive, send)
        try:
            rejection = self._declared_rejection(request)
            if rejection is not None:
                return await rejection(scope, receive, send)
            if request.url.path == "/gltf/postprocess":
                return await self._upload(scope, receive, send)
            await _join_request_task(asyncio.create_task(self.app(scope, receive, send)))
        finally:
            semaphore.release()

    def _declared_rejection(self, request):
        if _declared_body_too_large(request):
            return JSONResponse(
                status_code=413, content={"detail": f"GLB exceeds {_max_input_bytes()} byte limit"}
            )

    async def _upload(self, scope, receive, send):
        try:
            timeout = _upload_timeout_seconds()
            with UploadBody(
                _max_input_bytes() + _MULTIPART_OVERHEAD_BYTES, timeout, time.monotonic
            ) as body:
                await body.receive(receive)
                work = asyncio.create_task(self.app(scope, body.replay, send))
                await _join_request_task(work)
        except UploadRejected as exc:
            # The ingress ownership scope exits before a rejection is sent.
            rejection = JSONResponse(status_code=exc.status, content={"detail": exc.detail})
            await rejection(scope, receive, send)


def _upload_timeout_seconds():
    name = "ASSET_WORKER_UPLOAD_TIMEOUT_SECONDS"
    try:
        value = float(os.getenv(name, "300"))
        if not math.isfinite(value) or value <= 0:
            raise ValueError
    except ValueError as exc:
        raise UploadRejected(500, f"{name} must be a finite positive number") from exc
    return value


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

    app.add_exception_handler(RequestValidationError, _request_validation_error)
    app.add_middleware(_RequestAdmission, owner=app, expected_token=expected_token)

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
        # Built inside the handler, so FastAPI doesn't validate these Form fields
        # for us — a bad value would raise ValidationError → 500. Map it to 422
        # like the body-validated /ref twin does (client error, not server fault).
        try:
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
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc
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
        except Exception as exc:
            status = _storage_error_status(exc)
            if status is None:
                raise
            raise HTTPException(
                status_code=status,
                detail=f"Input object {payload.input.bucket}/{payload.input.key} "
                + ("was not found" if status == 404 else "is not readable"),
            ) from exc
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


def _require_self_contained_glb(data: bytes) -> None:
    """Fail closed: a .gltf JSON file, an undecodable GLB or one with external
    URIs is rejected, because gltf-transform picks GLB vs JSON by content and
    resolves non-data: URIs against the filesystem (or network)."""
    if _glb_json_chunk(data) is None:
        raise HTTPException(status_code=400, detail="Input must be a binary glTF 2.0 GLB")
    if external_resource_uris(data):
        raise HTTPException(
            status_code=400,
            detail="GLB must be self-contained: external buffer/image URIs are not allowed",
        )


def _process_path(
    input_path: Path,
    params: PostprocessParams,
    *,
    storage: ArtifactStorage | None = None,
) -> PostprocessResponse:
    _enforce_input_size(input_path.stat().st_size)
    _require_self_contained_glb(input_path.read_bytes())
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


# Multipart framing and form fields on top of the GLB itself.
_MULTIPART_OVERHEAD_BYTES = 1024 * 1024


def _declared_body_too_large(request: Request) -> bool:
    """Reject oversized declared bodies before bounded upload reception."""
    try:
        declared = int(request.headers.get("content-length", ""))
    except ValueError:
        return False
    return declared > _max_input_bytes() + _MULTIPART_OVERHEAD_BYTES


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
