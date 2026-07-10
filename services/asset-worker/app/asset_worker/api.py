from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .models import (
    RefPostprocessRequest,
    PostprocessParams,
    PostprocessResponse,
    normalization_metadata,
    optimization_metadata,
)
from .runner import run_gltf_transform
from .storage import ArtifactStorage, CONTENT_TYPE


def create_app() -> FastAPI:
    app = FastAPI(
        title="Atlas Asset Worker",
        description="glTF post-processing worker for Atlas creative and 3D pipelines.",
        version="0.1.0",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
        return _process_bytes(await file.read(), params)

    @app.post("/gltf/postprocess/ref", response_model=PostprocessResponse)
    def postprocess_ref(request: RefPostprocessRequest) -> PostprocessResponse:
        storage = ArtifactStorage()
        data = storage.fetch(request.input.bucket, request.input.key)
        return _process_bytes(data, request.params, storage=storage)

    @app.get("/gltf/artifacts/{sha256}.glb")
    def get_local_artifact(sha256: str):
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
    if not data:
        raise HTTPException(status_code=400, detail="GLB input is empty")
    storage = storage or ArtifactStorage()
    with tempfile.TemporaryDirectory(prefix="asset-worker-") as tmp:
        tmpdir = Path(tmp)
        input_path = tmpdir / "input.glb"
        output_path = tmpdir / "output.glb"
        input_path.write_bytes(data)
        run_gltf_transform(input_path, output_path, params)
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


def _validate_glb_name(filename: str) -> None:
    if not filename.lower().endswith(".glb"):
        raise HTTPException(status_code=400, detail="Input must be a GLB file")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())
