from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from .models import (
    RefPostprocessRequest,
    PostprocessParams,
    PostprocessResponse,
    normalization_metadata,
    optimization_metadata,
)
from .runner import GltfTransformError, run_gltf_transform
from .storage import ArtifactStorage, ArtifactTooLargeError, CONTENT_TYPE


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

    @app.middleware("http")
    async def require_api_token(request: Request, call_next):
        if request.url.path == "/health":
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
        return await call_next(request)

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
        file: UploadFile = File(...),
        target_height_m: float | None = Form(default=None),
        target_width_m: float | None = Form(default=None),
        normalize_axis: str = Form(default="height"),
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
            simplify_ratio=simplify_ratio,
            draco=draco,
            meshopt=meshopt,
            ktx2=ktx2,
            collider_decimation=collider_decimation,
        )
        with tempfile.TemporaryDirectory(prefix="asset-worker-upload-") as tmp:
            input_path = Path(tmp) / "input.glb"
            await asyncio.to_thread(_copy_upload_to_path, file.file, input_path)
            return await asyncio.to_thread(_process_path, input_path, params)

    @app.post("/gltf/postprocess/ref", response_model=PostprocessResponse)
    def postprocess_ref(
        request: RefPostprocessRequest,
    ) -> PostprocessResponse:
        _require_allowed_bucket(request.input.bucket)
        storage = ArtifactStorage()
        try:
            data = storage.fetch(request.input.bucket, request.input.key)
        except ArtifactTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        return _process_bytes(data, request.params, storage=storage)

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

    return app


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
    with tempfile.TemporaryDirectory(prefix="asset-worker-") as tmp:
        output_path = Path(tmp) / "output.glb"
        try:
            run_gltf_transform(input_path, output_path, params)
        except GltfTransformError as exc:
            status = 504 if exc.kind == "timeout" else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        output = output_path.read_bytes()

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
