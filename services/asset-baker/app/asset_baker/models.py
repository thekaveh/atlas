from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field


BakeMode = Literal["bake", "skip"]

GLB_CONTENT_TYPE = "model/gltf-binary"
PNG_CONTENT_TYPE = "image/png"


class BakeParams(BaseModel):
    """Per-request bake parameters. Unset fields fall back to the container's
    ASSET_BAKER_* env defaults (resolved by :func:`resolve_params`)."""

    target_tris: int | None = Field(default=None, gt=0, le=2_000_000)
    tex_size: int | None = Field(default=None, gt=0, le=8192)
    canonical_size: float | None = Field(default=None, gt=0, le=1000)
    # bake = full HP→LP + texture bake; skip = foliage bypass (normalize + export,
    # no remesh/decimate/bake) so thin leaf shells don't fragment into shards.
    mode: BakeMode = "bake"


class ResolvedParams(BaseModel):
    target_tris: int
    tex_size: int
    canonical_size: float
    brightness_min: float
    mode: BakeMode


def resolve_params(params: BakeParams) -> ResolvedParams:
    """Fill unset request params from the ASSET_BAKER_* env defaults."""
    return ResolvedParams(
        target_tris=params.target_tris or int(os.getenv("ASSET_BAKER_TARGET_TRIS", "39000")),
        tex_size=params.tex_size or int(os.getenv("ASSET_BAKER_TEX_SIZE", "2048")),
        canonical_size=params.canonical_size
        if params.canonical_size is not None
        else float(os.getenv("ASSET_BAKER_CANONICAL_SIZE", "4.0")),
        brightness_min=float(os.getenv("ASSET_BAKER_BRIGHTNESS_MIN", "0.05")),
        mode=params.mode,
    )


class MinioInput(BaseModel):
    bucket: str = Field(min_length=1)
    key: str = Field(min_length=1)


class RefBakeRequest(BaseModel):
    input: MinioInput
    params: BakeParams = Field(default_factory=BakeParams)


class ArtifactRef(BaseModel):
    storage: Literal["local", "minio"]
    key: str
    content_type: str = GLB_CONTENT_TYPE
    bucket: str | None = None
    uri: str | None = None


class TextureArtifact(ArtifactRef):
    role: Literal["basecolor", "normal"]


class BakeSummary(BaseModel):
    mode: BakeMode
    faces_in: int | None = None
    tris_out: int | None = None
    shells_kept: int | None = None
    color_mean: float | None = None
    duration_s: float | None = None


class BakeResponse(BaseModel):
    status: Literal["succeeded"]
    sha256: str
    artifact: ArtifactRef
    textures: list[TextureArtifact]
    summary: BakeSummary
    download_url: str | None
