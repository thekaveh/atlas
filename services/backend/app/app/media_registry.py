"""Curated image→3D model registry for the FAL media gateway (#340).

This module is intentionally dependency-free (stdlib only) so it stays inside
the backend CI unit-test venv and never drags heavy media libraries into
``main.py``'s import closure.

Each entry pins a *canonical vendor endpoint id* and records commercial-use /
license status as data (rather than re-discovering it per consumer), plus a
per-run cost estimate and the input-hosting quirks the gateway must own:

* ``needs_hosted_url`` — provider rejects data-URI inputs (Tripo), so the
  gateway must upload the bytes to Atlas storage and pass a URL instead.
* ``accepts_data_uri`` — provider tolerates inline ``data:`` inputs.

Response-key normalization is shared: fal image→3D endpoints return the GLB
under a different key per model, so :data:`GLB_RESPONSE_KEYS` ports the probe
order from DayDreams' ``extractGlbUrl`` (``matrix-gen.ts``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# Probe order for the normalized GLB URL across fal image→3D endpoints, ported
# from DayDreams' extractGlbUrl. Each key may hold either a bare URL string or
# an object with a ``url`` field.
GLB_RESPONSE_KEYS: Tuple[str, ...] = (
    "model_glb",
    "model_mesh",
    "model",
    "mesh",
    "pbr_model",
    "base_model",
)

# Keys that, when present, carry a rendered preview image of the mesh.
PREVIEW_RESPONSE_KEYS: Tuple[str, ...] = (
    "preview_image",
    "rendered_image",
    "thumbnail",
    "preview",
)

# Keys that may carry packed texture atlases (string, object, or list).
TEXTURE_RESPONSE_KEYS: Tuple[str, ...] = (
    "textures",
    "texture",
    "albedo",
)


@dataclass(frozen=True)
class ImageTo3DModel:
    """Registry entry for a single image→3D endpoint."""

    model_id: str  # canonical fal endpoint id
    label: str
    family: str  # hunyuan3d | trellis | tripo | rodin | pixal3d
    license: str
    license_notes: str
    commercial_use: str  # "yes" | "gated" | "conditional"
    # Indicative per-run cost in USD. Estimated from fal's published pricing;
    # verify against the live fal catalog before billing decisions.
    estimated_cost_usd: Optional[float]
    needs_hosted_url: bool
    accepts_data_uri: bool
    endpoint_verified: bool = True
    glb_keys: Tuple[str, ...] = GLB_RESPONSE_KEYS
    preview_keys: Tuple[str, ...] = PREVIEW_RESPONSE_KEYS
    texture_keys: Tuple[str, ...] = TEXTURE_RESPONSE_KEYS
    aliases: Tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""


_MODELS: Tuple[ImageTo3DModel, ...] = (
    ImageTo3DModel(
        model_id="fal-ai/trellis",
        label="TRELLIS (image-to-3D)",
        family="trellis",
        license="MIT",
        license_notes=(
            "TRELLIS-2 weights are MIT-licensed — the cleanest commercial "
            "terms of the set."
        ),
        commercial_use="yes",
        estimated_cost_usd=0.05,
        needs_hosted_url=False,
        accepts_data_uri=True,
        aliases=("trellis", "trellis-2", "fal-ai/trellis/image-to-3d"),
    ),
    ImageTo3DModel(
        model_id="fal-ai/hunyuan3d/v2",
        label="Hunyuan3D v2 (image-to-3D)",
        family="hunyuan3d",
        license="tencent-hunyuan-community",
        license_notes=(
            "Commercial use OK when consumed via fal's hosted endpoint. "
            "Self-hosted weights are Tencent-gated with EU/UK/KR territorial "
            "exclusions."
        ),
        commercial_use="yes",
        estimated_cost_usd=0.10,
        needs_hosted_url=False,
        accepts_data_uri=True,
        aliases=("hunyuan3d", "hunyuan3d-v2", "fal-ai/hunyuan3d-v2"),
        notes=(
            "Rejects tight transparent crops (IndexError). The gateway "
            "composites transparent inputs onto a neutral background first."
        ),
    ),
    ImageTo3DModel(
        model_id="tripo3d/tripo/v2.5/image-to-3d",
        label="Tripo v2.5 (image-to-3D)",
        family="tripo",
        license="tripo-commercial-gated",
        license_notes=(
            "Commercial use gated to Tripo Pro/Enterprise plans; the marketing "
            "FAQ contradicts the binding ToS. 'Prism' is 3D AI Studio's rebrand "
            "of Tripo H3.1 — this registry records canonical vendor ids only."
        ),
        commercial_use="gated",
        estimated_cost_usd=0.20,
        needs_hosted_url=True,  # Tripo rejects data-URI inputs — must host a URL
        accepts_data_uri=False,
        aliases=(
            "tripo",
            "tripo3d",
            "tripo-v2.5",
            "prism",
            "fal-ai/tripo3d",
            "fal-ai/tripo3d/tripo/v2.5/image-to-3d",
        ),
    ),
    ImageTo3DModel(
        model_id="fal-ai/hyper3d/rodin",
        label="Rodin (Hyper3D, image-to-3D)",
        family="rodin",
        license="hyper3d-provider-terms",
        license_notes="Governed by Hyper3D / Rodin provider terms via fal.",
        commercial_use="conditional",
        estimated_cost_usd=0.40,
        needs_hosted_url=False,
        accepts_data_uri=True,
        aliases=("rodin", "hyper3d", "hyper3d-rodin", "fal-ai/rodin"),
    ),
    ImageTo3DModel(
        model_id="fal-ai/pixal3d/image-to-3d",
        label="Pixal3D (image-to-3D)",
        family="pixal3d",
        license="pixal3d-provider-terms",
        license_notes="Governed by Pixal3D provider terms via fal.",
        commercial_use="conditional",
        estimated_cost_usd=None,
        needs_hosted_url=False,
        accepts_data_uri=True,
        endpoint_verified=False,
        aliases=("pixal3d", "pixel3d"),
        notes=(
            "Endpoint id and pricing unverified against the live fal catalog. "
            "Confirm before production use; response-key normalization already "
            "covers its GLB output."
        ),
    ),
)


_BY_ID: Dict[str, ImageTo3DModel] = {}
for _m in _MODELS:
    _BY_ID[_m.model_id.lower()] = _m
    for _alias in _m.aliases:
        _BY_ID.setdefault(_alias.lower(), _m)


# Default endpoint when a caller omits ``model`` for modality=image_to_3d.
# TRELLIS is chosen for its clean MIT license.
_DEFAULT_MODEL_ID = "fal-ai/trellis"


def all_models() -> Tuple[ImageTo3DModel, ...]:
    """Return the full curated registry."""

    return _MODELS


def lookup(model_id: Optional[str]) -> Optional[ImageTo3DModel]:
    """Resolve a canonical id or alias to its entry (case-insensitive)."""

    if not model_id:
        return None
    return _BY_ID.get(model_id.strip().lower())


def known_ids() -> Tuple[str, ...]:
    """Return production-verified canonical endpoint ids."""

    return tuple(m.model_id for m in _MODELS if m.endpoint_verified)


def default_model_id() -> str:
    """Return the default image→3D endpoint id.

    Honors ``FAL_IMAGE_TO_3D_MODEL`` when set so operators can pin a house
    default without code changes.
    """

    override = (os.getenv("FAL_IMAGE_TO_3D_MODEL") or "").strip()
    if override:
        return override
    return _DEFAULT_MODEL_ID
