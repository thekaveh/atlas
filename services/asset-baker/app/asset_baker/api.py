from __future__ import annotations

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
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import ValidationError

from .models import (
    ArtifactRef,
    BakeParams,
    BakeResponse,
    BakeSummary,
    GLB_CONTENT_TYPE,
    PNG_CONTENT_TYPE,
    RefBakeRequest,
    TextureArtifact,
    resolve_params,
)
from .runner import BakeError, run_bake
from .storage import ArtifactStorage, ArtifactTooLargeError


logger = logging.getLogger(__name__)


def create_app(*, api_token: str | None = None) -> FastAPI:
    app = FastAPI(
        title="Atlas Asset Baker",
        description="Blender headless HP→LP bake worker (voxel-remesh → decimate → "
        "Smart-UV → bake color+normal) for Atlas creative/3D pipelines.",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    expected_token = (
        api_token if api_token is not None else os.getenv("ASSET_BAKER_API_TOKEN", "")
    )

    @app.middleware("http")
    async def require_api_token_and_admit(request: Request, call_next):
        if request.url.path in {"/health", "/metrics"}:
            return await call_next(request)
        if not expected_token:
            return JSONResponse(
                status_code=503,
                content={"detail": "Asset Baker authentication is not configured"},
            )
        scheme, _, credential = request.headers.get("authorization", "").partition(" ")
        # Compare utf-8 bytes: secrets.compare_digest raises TypeError on a
        # non-ASCII str, which would surface as a 500 instead of this 401.
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            credential.encode("utf-8"), expected_token.encode("utf-8")
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid Asset Baker bearer token"},
            )
        admitted = False
        if request.method == "POST" and request.url.path in {
            "/assets/bake",
            "/assets/bake/ref",
        }:
            admitted = app.state.bake_semaphore.acquire(blocking=False)
            if not admitted:
                logger.info("asset_bake_rejected reason=busy")
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Bake worker is busy; retry later"},
                )
        try:
            return await call_next(request)
        finally:
            if admitted:
                app.state.bake_semaphore.release()

    # Bounded worker: a single Cycles bake saturates CPU, so concurrent bakes are
    # net-negative. Reject (429) rather than queue unboundedly when saturated.
    # Held on app.state so it is the one semaphore under test (patching the global
    # threading.Semaphore would also swap the event-loop threadpool's own).
    concurrency = max(1, int(os.getenv("ASSET_BAKER_CONCURRENCY", "1")))
    app.state.bake_semaphore = threading.Semaphore(concurrency)

    @app.get("/health")
    def health() -> JSONResponse:
        binary = os.getenv("ASSET_BAKER_BLENDER_BIN", "blender")
        ready = shutil.which(binary) is not None
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ok" if ready else "unavailable", "blender": ready},
        )

    # Sync `def` so Starlette offloads the whole (blocking, up-to-600s) bake to
    # the threadpool instead of freezing the event loop / liveness probes.
    @app.post("/assets/bake", response_model=BakeResponse)
    def bake_upload(
        request: Request,
        file: UploadFile = File(...),
        target_tris: int | None = Form(default=None),
        tex_size: int | None = Form(default=None),
        canonical_size: float | None = Form(default=None),
        mode: str = Form(default="bake"),
    ) -> BakeResponse:
        _validate_glb_name(file.filename or "")
        _enforce_content_length(request)  # reject obviously-oversize before buffering
        # Built inside the handler, so FastAPI doesn't validate these Form fields
        # for us — a bad value (e.g. mode outside the Literal, target_tris<=0)
        # would raise ValidationError → 500. Map it to 422 like the body-validated
        # /ref twin (client error, not server fault).
        try:
            params = BakeParams(
                target_tris=target_tris,
                tex_size=tex_size,
                canonical_size=canonical_size,
                mode=mode,
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=jsonable_encoder(exc.errors())) from exc
        file.file.seek(0)
        with tempfile.TemporaryDirectory(prefix="asset-baker-upload-") as tmp:
            input_path = Path(tmp) / "input.glb"
            _copy_upload_to_path(file.file, input_path)
            return _process_path(input_path, params)

    @app.post("/assets/bake/ref", response_model=BakeResponse)
    def bake_ref(
        request: RefBakeRequest,
    ) -> BakeResponse:
        _require_allowed_bucket(request.input.bucket)
        storage = ArtifactStorage()
        try:
            data = storage.fetch(request.input.bucket, request.input.key)
        except ArtifactTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        return _process_bytes(data, request.params, storage=storage)

    @app.get("/assets/artifacts/{sha256}.{ext}")
    def get_local_artifact(
        sha256: str,
        ext: str,
    ):
        if not _is_sha256(sha256) or ext not in ("glb", "png"):
            raise HTTPException(status_code=400, detail="Invalid artifact reference")
        path = ArtifactStorage().local_path(sha256, ext)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Artifact not found")
        media = GLB_CONTENT_TYPE if ext == "glb" else PNG_CONTENT_TYPE
        return FileResponse(path, media_type=media, filename=f"{sha256}.{ext}")

    Instrumentator(
        excluded_handlers=["/metrics", "/health"],
        should_group_status_codes=True,
    ).instrument(app).expose(app, endpoint="/metrics")
    return app


def _process_bytes(
    data: bytes,
    params: BakeParams,
    *,
    storage: ArtifactStorage | None = None,
) -> BakeResponse:
    _enforce_size(data)
    with tempfile.TemporaryDirectory(prefix="asset-baker-") as tmp:
        input_path = Path(tmp) / "input.glb"
        input_path.write_bytes(data)
        return _process_path(input_path, params, storage=storage)


def _process_path(
    input_path: Path,
    params: BakeParams,
    *,
    storage: ArtifactStorage | None = None,
) -> BakeResponse:
    _enforce_input_size(input_path.stat().st_size)
    resolved = resolve_params(params)
    storage = storage or ArtifactStorage()

    started_at = time.monotonic()
    logger.info("asset_bake_started")
    with tempfile.TemporaryDirectory(prefix="asset-baker-") as tmp:
        out_dir = Path(tmp) / "out"
        try:
            artifacts = run_bake(input_path, out_dir, resolved)
        except BakeError as exc:
            logger.warning("asset_bake_failed kind=%s", exc.kind)
            status = 504 if exc.kind == "timeout" else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        glb_bytes = artifacts.glb_path.read_bytes()
        texture_bytes = [
            (role, path.read_bytes())
            for role, path in (
                ("basecolor", artifacts.basecolor_path),
                ("normal", artifacts.normal_path),
            )
            if path is not None
        ]
        record = artifacts.summary
    logger.info(
        "asset_bake_completed duration_seconds=%.3f",
        time.monotonic() - started_at,
    )

    sha = hashlib.sha256(glb_bytes).hexdigest()
    glb_artifact = storage.store(
        glb_bytes, sha256=sha, suffix="glb", content_type=GLB_CONTENT_TYPE
    )
    textures: list[TextureArtifact] = []
    for role, payload in texture_bytes:
        tex_sha = hashlib.sha256(payload).hexdigest()
        info = storage.store(payload, sha256=tex_sha, suffix="png", content_type=PNG_CONTENT_TYPE)
        textures.append(TextureArtifact(role=role, **info))

    download_url = f"/assets/artifacts/{sha}.glb" if glb_artifact["storage"] == "local" else None
    summary = BakeSummary(
        mode=resolved.mode,
        faces_in=record.get("faces_in"),
        tris_out=record.get("tris_out"),
        shells_kept=record.get("shells_kept"),
        color_mean=record.get("color_mean"),
        duration_s=record.get("duration_s"),
    )
    return BakeResponse(
        status="succeeded",
        sha256=sha,
        artifact=ArtifactRef(**glb_artifact),
        textures=textures,
        summary=summary,
        download_url=download_url,
    )


def _enforce_size(data: bytes) -> None:
    _enforce_input_size(len(data))


def _require_allowed_bucket(bucket: str) -> None:
    configured = os.getenv("ASSET_BAKER_ALLOWED_INPUT_BUCKETS", "raw-assets")
    allowed = {value for value in configured.replace(",", " ").split() if value}
    if bucket not in allowed:
        raise HTTPException(status_code=403, detail="Input bucket is not allowed")


def _max_upload_bytes() -> int:
    max_mb = float(os.getenv("ASSET_BAKER_MAX_UPLOAD_MB", "200"))
    return max(1, int(max_mb * 1024 * 1024))


def _enforce_input_size(size: int) -> None:
    if size == 0:
        raise HTTPException(status_code=400, detail="GLB input is empty")
    max_bytes = _max_upload_bytes()
    if size > max_bytes:
        raise HTTPException(
            status_code=413, detail=f"GLB exceeds {max_bytes} byte limit"
        )


def _copy_upload_to_path(source, path: Path) -> None:
    max_bytes = _max_upload_bytes()
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


def _enforce_content_length(request: Request) -> None:
    """Reject an obviously-oversize upload from its Content-Length header before
    the body is buffered into memory. `_enforce_size` remains the authoritative
    post-read check; this only bounds peak memory for hostile large uploads.
    A 2 MiB slack covers multipart boundary/header overhead."""
    raw = request.headers.get("content-length")
    if raw and raw.isdigit():
        max_mb = float(os.getenv("ASSET_BAKER_MAX_UPLOAD_MB", "200"))
        if int(raw) > (max_mb + 2) * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"Upload exceeds {max_mb:.0f} MiB limit")


def _validate_glb_name(filename: str) -> None:
    if not filename.lower().endswith(".glb"):
        raise HTTPException(status_code=400, detail="Input must be a GLB file")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())
