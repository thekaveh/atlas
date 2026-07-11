"""Gateway-side image input hosting + conditioning for image→3D (#340).

Some fal image→3D providers reject inline ``data:`` inputs and require a hosted
URL (Tripo), and several reject tight transparent crops (fal Hunyuan3D v2 raises
``IndexError: Failed to generate 3D mesh from this image``). Rather than
re-discover these quirks in every consumer, the gateway owns them here:

* transparent inputs are composited onto a neutral studio background with
  padding before submission;
* inputs that must be hosted (or that were just conditioned into new bytes for
  a provider that will not accept ``data:``) are uploaded to Atlas storage and
  replaced with the returned public URL.

Pillow is imported lazily (runtime-only dependency) and the storage upload is
injected as a callable, so this module stays inside the backend CI unit-test
venv and out of ``main.py``'s import closure.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

# Neutral studio background + padding applied when compositing transparent
# inputs. fal Hunyuan3D v2 raises IndexError on tight transparent crops
# (DayDreams #408 scar), so the gateway pads segmented sprites onto an opaque
# neutral field before submission. ~35% padding per side, per triage.
NEUTRAL_BACKGROUND: Tuple[int, int, int] = (240, 240, 240)
DEFAULT_PADDING_RATIO: float = 0.35

_DATA_URI_RE = re.compile(
    r"^data:(?P<mime>[^;,]*?)(?P<b64>;base64)?,(?P<data>.*)$", re.DOTALL
)

_EXT_BY_CONTENT_TYPE = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "model/gltf-binary": "glb",
}


class ImageInputError(ValueError):
    """Bad / unhostable client input (maps to HTTP 400)."""


class ImageHostingError(RuntimeError):
    """Atlas storage upload failed or is misconfigured (maps to HTTP 5xx)."""


# Uploader contract: (data, content_type, storage_key) -> hosted public URL.
Uploader = Callable[[bytes, str, str], str]


@dataclass
class PreparedImageInput:
    """Result of preparing an image_to_3d input for a provider."""

    image: str  # final value handed to the provider (http(s) URL or data URI)
    hosted: bool  # True when uploaded to Atlas storage
    hosted_url: Optional[str]
    conditioned: bool  # True when composited onto the neutral background
    content_type: str


def looks_like_url(value: str) -> bool:
    v = (value or "").strip().lower()
    return v.startswith("http://") or v.startswith("https://")


def looks_like_data_uri(value: str) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith("data:")


def parse_data_uri(value: str) -> Optional[Tuple[bytes, str]]:
    """Decode a ``data:`` URI to ``(bytes, content_type)`` or ``None``.

    Raises :class:`ImageInputError` when the URI is a data URI but its payload
    cannot be decoded.
    """

    match = _DATA_URI_RE.match((value or "").strip())
    if not match:
        return None
    content_type = (match.group("mime") or "").strip() or "application/octet-stream"
    raw = match.group("data")
    if match.group("b64"):
        try:
            data = base64.b64decode(raw, validate=True)
        except Exception as exc:  # binascii.Error and friends
            raise ImageInputError("image data URI is not valid base64") from exc
    else:
        from urllib.parse import unquote_to_bytes

        data = unquote_to_bytes(raw)
    if not data:
        raise ImageInputError("image data URI decoded to empty bytes")
    return data, content_type


def _encode_data_uri(data: bytes, content_type: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _content_ext(content_type: str) -> str:
    return _EXT_BY_CONTENT_TYPE.get((content_type or "").strip().lower(), "bin")


# --- lazy Pillow helpers (monkeypatched in tests; runtime uses real PIL) ----


def has_transparency(data: bytes) -> bool:
    """Return True when the image carries a non-opaque alpha channel."""

    from io import BytesIO

    from PIL import Image  # lazy: Pillow is a runtime-only dependency

    with Image.open(BytesIO(data)) as image:
        if image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        ):
            alpha = image.convert("RGBA").getchannel("A")
            return alpha.getextrema()[0] < 255
    return False


def composite_on_neutral_background(
    data: bytes,
    *,
    padding_ratio: float = DEFAULT_PADDING_RATIO,
    background: Tuple[int, int, int] = NEUTRAL_BACKGROUND,
) -> bytes:
    """Crop to the opaque bbox and center it on a padded neutral canvas."""

    from io import BytesIO

    from PIL import Image  # lazy

    with Image.open(BytesIO(data)) as image:
        rgba = image.convert("RGBA")
        bbox = rgba.getchannel("A").getbbox() or (0, 0, rgba.width, rgba.height)
        cropped = rgba.crop(bbox)
        crop_w, crop_h = cropped.size
        side = max(crop_w, crop_h, 1)
        canvas_side = int(round(side * (1.0 + 2.0 * padding_ratio))) or side
        canvas = Image.new("RGB", (canvas_side, canvas_side), background)
        offset = ((canvas_side - crop_w) // 2, (canvas_side - crop_h) // 2)
        canvas.paste(cropped, offset, mask=cropped)
        out = BytesIO()
        canvas.save(out, format="PNG")
        return out.getvalue()


def prepare_image_input(
    image: str,
    *,
    needs_hosted_url: bool,
    accepts_data_uri: bool = True,
    condition_transparent: bool = True,
    uploader: Optional[Uploader] = None,
    key_prefix: str = "media-inputs",
) -> PreparedImageInput:
    """Normalize an image_to_3d input into a provider-ready value.

    * Remote ``http(s)`` URLs pass through untouched (we do not fetch remote
      bytes, so we cannot condition them; a provider that needs a URL is already
      satisfied).
    * ``data:`` inputs are decoded, optionally composited onto the neutral
      background when transparent, then either re-encoded as a data URI or
      uploaded to Atlas storage when the provider requires a hosted URL.
    """

    if not isinstance(image, str) or not image.strip():
        raise ImageInputError(
            "image_to_3d input requires a non-empty 'image' (URL or data URI)"
        )
    image = image.strip()

    if looks_like_url(image):
        return PreparedImageInput(
            image=image,
            hosted=False,
            hosted_url=None,
            conditioned=False,
            content_type="",
        )

    parsed = parse_data_uri(image)
    if parsed is None:
        raise ImageInputError(
            "image_to_3d input must be an http(s) URL or a data URI"
        )
    data, content_type = parsed

    conditioned = False
    if condition_transparent:
        try:
            transparent = has_transparency(data)
        except ImageInputError:
            raise
        except Exception as exc:
            raise ImageInputError(
                f"could not inspect image transparency: {exc}"
            ) from exc
        if transparent:
            try:
                data = composite_on_neutral_background(data)
            except Exception as exc:
                raise ImageInputError(
                    f"could not composite transparent input: {exc}"
                ) from exc
            content_type = "image/png"
            conditioned = True

    if needs_hosted_url or not accepts_data_uri:
        if uploader is None:
            raise ImageHostingError(
                "provider requires a hosted image URL but no storage uploader "
                "is configured"
            )
        digest = hashlib.sha256(data).hexdigest()[:16]
        key = f"{key_prefix}/{digest}.{_content_ext(content_type)}"
        try:
            hosted_url = uploader(data, content_type, key)
        except ImageHostingError:
            raise
        except Exception as exc:
            raise ImageHostingError(
                f"failed to host image input in Atlas storage: {exc}"
            ) from exc
        if not hosted_url:
            raise ImageHostingError("storage uploader returned an empty URL")
        return PreparedImageInput(
            image=hosted_url,
            hosted=True,
            hosted_url=hosted_url,
            conditioned=conditioned,
            content_type=content_type,
        )

    return PreparedImageInput(
        image=_encode_data_uri(data, content_type),
        hosted=False,
        hosted_url=None,
        conditioned=conditioned,
        content_type=content_type,
    )
