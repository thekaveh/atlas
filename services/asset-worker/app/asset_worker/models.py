from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


NormalizeAxis = Literal["height", "width"]
# Orientation handling (#524). glTF is +Y-up by spec, so the default is to
# TRUST the incoming orientation and only scale/center/ground: reorientation
# is opt-in, never a silent guess.
UpAxis = Literal["keep", "auto", "x", "y", "z"]


class PostprocessParams(BaseModel):
    target_height_m: float | None = Field(default=None, gt=0)
    target_width_m: float | None = Field(default=None, gt=0)
    normalize_axis: NormalizeAxis = "height"
    up_axis: UpAxis = "keep"
    simplify_ratio: float | None = Field(default=None, gt=0, le=1)
    draco: bool = False
    meshopt: bool = True
    ktx2: bool = False
    collider_decimation: float | None = Field(default=None, gt=0, le=1)

    @model_validator(mode="after")
    def _require_matching_target(self) -> "PostprocessParams":
        if self.normalize_axis == "height" and self.target_width_m is not None and self.target_height_m is None:
            raise ValueError("target_height_m is required when normalize_axis=height")
        if self.normalize_axis == "width" and self.target_height_m is not None and self.target_width_m is None:
            raise ValueError("target_width_m is required when normalize_axis=width")
        return self

    @property
    def effective_simplify_ratio(self) -> float | None:
        if self.simplify_ratio is not None:
            return self.simplify_ratio
        return self.collider_decimation


class MinioInput(BaseModel):
    bucket: str = Field(min_length=1)
    key: str = Field(min_length=1)


class RefPostprocessRequest(BaseModel):
    input: MinioInput
    params: PostprocessParams = Field(default_factory=PostprocessParams)


class ArtifactRef(BaseModel):
    storage: Literal["local", "minio"]
    key: str
    content_type: str = "model/gltf-binary"
    bucket: str | None = None
    uri: str | None = None


class PostprocessResponse(BaseModel):
    status: Literal["succeeded"]
    sha256: str
    artifact: ArtifactRef
    download_url: str | None
    normalization: dict[str, float | int | str | None]
    optimization: dict[str, float | bool | None]


def normalization_metadata(params: PostprocessParams) -> dict[str, float | int | str | None]:
    # Report the algorithm actually run (#524) — not a fixed label. `keep`
    # (default) performs no reorientation; `auto` runs the minimum-AABB-volume
    # pitch/roll search; x|y|z is an explicit axis remap.
    if params.up_axis == "keep":
        method = "keep"
    elif params.up_axis == "auto":
        method = "min-aabb-volume"
    else:
        method = f"axis:{params.up_axis}"
    metadata: dict[str, float | int | str | None] = {
        "method": method,
        "up_axis": params.up_axis,
        "base_y": 0,
        "normalize_axis": params.normalize_axis,
    }
    if params.target_height_m is not None:
        metadata["target_height_m"] = params.target_height_m
    if params.target_width_m is not None:
        metadata["target_width_m"] = params.target_width_m
    return metadata


def optimization_metadata(params: PostprocessParams) -> dict[str, float | bool | None]:
    return {
        "simplify_ratio": params.simplify_ratio,
        "draco": params.draco,
        "meshopt": params.meshopt,
        "ktx2": params.ktx2,
        "collider_decimation": params.collider_decimation,
    }
