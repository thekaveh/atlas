from __future__ import annotations

import base64
from io import BytesIO

import pytest

import media_input
from media_input import (
    ImageHostingError,
    ImageInputError,
    parse_data_uri,
    prepare_image_input,
    validate_media_input_config,
)

# A 1x1 transparent PNG (not actually decoded — Pillow is monkeypatched in the
# conditioning tests so these bytes never hit a real decoder).
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
_DATA_URI = "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode("ascii")


def _valid_png_bytes() -> bytes:
    from PIL import Image

    out = BytesIO()
    Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(out, format="PNG")
    return out.getvalue()


def test_parse_data_uri_decodes_base64():
    data, content_type = parse_data_uri(_DATA_URI)
    assert data == _PNG_BYTES
    assert content_type == "image/png"


def test_parse_data_uri_rejects_bad_base64():
    with pytest.raises(ImageInputError):
        parse_data_uri("data:image/png;base64,@@@not-base64@@@")


def test_parse_data_uri_returns_none_for_non_data_uri():
    assert parse_data_uri("https://cdn.example/a.png") is None


def test_strict_image_data_uri_decodes_supported_base64():
    data, content_type = media_input.parse_strict_image_data_uri(
        _DATA_URI, len(_PNG_BYTES)
    )

    assert data == _PNG_BYTES
    assert content_type == "image/png"


def test_strict_image_data_uri_requires_base64():
    with pytest.raises(ImageInputError, match="base64"):
        media_input.parse_strict_image_data_uri("data:image/png,raw", 1024)


def test_strict_image_data_uri_rejects_unsupported_mime():
    payload = base64.b64encode(_PNG_BYTES).decode("ascii")

    with pytest.raises(ImageInputError, match="PNG, JPEG, or WebP"):
        media_input.parse_strict_image_data_uri(
            f"data:image/gif;base64,{payload}", 1024
        )


def test_strict_image_data_uri_rejects_oversize_before_decode(monkeypatch):
    monkeypatch.setattr(
        media_input.base64,
        "b64decode",
        lambda *_args, **_kwargs: pytest.fail("oversize payload must not decode"),
    )
    payload = base64.b64encode(b"four").decode("ascii")

    with pytest.raises(ImageInputError, match="configured byte limit"):
        media_input.parse_strict_image_data_uri(
            f"data:image/png;base64,{payload}", 3
        )


def test_strict_image_data_uri_rejects_empty_payload():
    with pytest.raises(ImageInputError, match="empty bytes"):
        media_input.parse_strict_image_data_uri("data:image/png;base64,", 1024)


@pytest.mark.parametrize(
    ("image_format", "expected_content_type", "expected_extension"),
    [
        ("PNG", "image/png", "png"),
        ("JPEG", "image/jpeg", "jpg"),
        ("WEBP", "image/webp", "webp"),
    ],
)
def test_validate_raster_image_returns_verified_format(
    image_format, expected_content_type, expected_extension
):
    from PIL import Image

    out = BytesIO()
    Image.new("RGB", (1, 1), "white").save(out, format=image_format)
    data = out.getvalue()
    content_type, extension = media_input.validate_raster_image(
        data, expected_content_type, len(data)
    )

    assert (content_type, extension) == (
        expected_content_type,
        expected_extension,
    )


def test_validate_raster_image_rejects_mime_decoder_mismatch():
    valid_png = _valid_png_bytes()
    with pytest.raises(ImageInputError, match="does not match"):
        media_input.validate_raster_image(
            valid_png, "image/jpeg", len(valid_png)
        )


def test_validate_raster_image_rejects_malformed_bytes():
    with pytest.raises(ImageInputError, match="valid PNG, JPEG, or WebP"):
        media_input.validate_raster_image(b"not-an-image", "image/png", 1024)


def test_validate_raster_image_normalizes_corrupt_png_error():
    with pytest.raises(ImageInputError, match="valid PNG, JPEG, or WebP"):
        media_input.validate_raster_image(_PNG_BYTES, "image/png", 1024)


def test_validate_raster_image_enforces_pixel_limit(monkeypatch):
    from PIL import Image

    out = BytesIO()
    Image.new("RGB", (11, 10), "white").save(out, format="PNG")
    monkeypatch.setenv("MEDIA_INPUT_MAX_PIXELS", "100")

    with pytest.raises(ImageInputError, match="MEDIA_INPUT_MAX_PIXELS"):
        media_input.validate_raster_image(
            out.getvalue(), "image/png", len(out.getvalue())
        )


@pytest.mark.parametrize(
    ("image_format", "content_type"),
    [("WEBP", "image/webp"), ("PNG", "image/png")],
)
def test_validate_raster_image_rejects_animated_inputs(
    image_format, content_type
):
    from PIL import Image

    out = BytesIO()
    first = Image.new("RGB", (11, 10), "white")
    second = Image.new("RGB", (11, 10), "black")
    first.save(
        out,
        format=image_format,
        save_all=True,
        append_images=[second],
        duration=100,
    )

    with pytest.raises(ImageInputError, match="single-frame"):
        media_input.validate_raster_image(
            out.getvalue(), content_type, len(out.getvalue())
        )


def test_oversize_base64_is_rejected_before_decode(monkeypatch):
    monkeypatch.setenv("MEDIA_INPUT_MAX_BYTES", "3")
    monkeypatch.setattr(
        media_input.base64,
        "b64decode",
        lambda *_args, **_kwargs: pytest.fail("oversize payload must not decode"),
    )
    payload = base64.b64encode(b"four").decode("ascii")
    with pytest.raises(ImageInputError, match="MEDIA_INPUT_MAX_BYTES"):
        parse_data_uri(f"data:image/png;base64,{payload}")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MEDIA_INPUT_MAX_BYTES", "0"),
        ("MEDIA_INPUT_MAX_BYTES", "many"),
        ("MEDIA_INPUT_MAX_PIXELS", "-1"),
        ("MEDIA_INPUT_MAX_PIXELS", "1.5"),
    ],
)
def test_media_input_config_rejects_invalid_limits(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ImageInputError, match=name):
        validate_media_input_config()


def test_image_pixel_limit_is_checked_before_conversion(monkeypatch):
    from PIL import Image

    out = BytesIO()
    Image.new("RGBA", (11, 10), (0, 0, 0, 0)).save(out, format="PNG")
    monkeypatch.setenv("MEDIA_INPUT_MAX_PIXELS", "100")

    with pytest.raises(ImageInputError, match="MEDIA_INPUT_MAX_PIXELS"):
        media_input.has_transparency(out.getvalue())


def test_conditioned_canvas_must_fit_pixel_limit(monkeypatch):
    from PIL import Image

    out = BytesIO()
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(out, format="PNG")
    monkeypatch.setenv("MEDIA_INPUT_MAX_PIXELS", "100")

    with pytest.raises(ImageInputError, match="conditioned image"):
        media_input.composite_on_neutral_background(out.getvalue())


def test_url_input_passes_through_untouched(monkeypatch):
    # A remote URL must not trigger Pillow or an upload.
    monkeypatch.setattr(
        media_input, "has_transparency", lambda data: pytest.fail("no PIL for URLs")
    )
    prepared = prepare_image_input(
        "https://cdn.example/sprite.png",
        needs_hosted_url=True,  # already a URL — no hosting needed
        accepts_data_uri=False,
        uploader=lambda *a: pytest.fail("URL inputs must not be re-hosted"),
    )
    assert prepared.image == "https://cdn.example/sprite.png"
    assert prepared.hosted is False
    assert prepared.conditioned is False


def test_opaque_datauri_for_data_uri_provider_returns_data_uri(monkeypatch):
    monkeypatch.setattr(media_input, "has_transparency", lambda data: False)
    prepared = prepare_image_input(
        _DATA_URI,
        needs_hosted_url=False,
        accepts_data_uri=True,
        uploader=lambda *a: pytest.fail("data-URI provider must not host"),
    )
    assert prepared.image.startswith("data:image/png;base64,")
    assert prepared.hosted is False
    assert prepared.conditioned is False


def test_opaque_datauri_hosted_when_provider_needs_url(monkeypatch):
    monkeypatch.setattr(media_input, "has_transparency", lambda data: False)
    captured = {}

    def fake_uploader(data, content_type, key):
        captured["data"] = data
        captured["content_type"] = content_type
        captured["key"] = key
        return "https://storage.example/media-inputs/hosted.png"

    prepared = prepare_image_input(
        _DATA_URI,
        needs_hosted_url=True,
        accepts_data_uri=False,
        uploader=fake_uploader,
    )
    assert prepared.hosted is True
    assert prepared.image == "https://storage.example/media-inputs/hosted.png"
    assert captured["data"] == _PNG_BYTES
    assert captured["key"].startswith("media-inputs/")


def test_transparent_input_conditioned_then_hosted(monkeypatch):
    monkeypatch.setattr(media_input, "has_transparency", lambda data: True)
    monkeypatch.setattr(
        media_input,
        "composite_on_neutral_background",
        lambda data, **kw: b"CONDITIONED-PNG-BYTES",
    )
    captured = {}

    def fake_uploader(data, content_type, key):
        captured["data"] = data
        captured["content_type"] = content_type
        return "https://storage.example/media-inputs/conditioned.png"

    prepared = prepare_image_input(
        _DATA_URI,
        needs_hosted_url=True,
        accepts_data_uri=False,
        uploader=fake_uploader,
    )
    assert prepared.conditioned is True
    assert prepared.hosted is True
    assert captured["data"] == b"CONDITIONED-PNG-BYTES"
    assert captured["content_type"] == "image/png"


def test_transparent_input_conditioned_reencoded_for_data_uri_provider(monkeypatch):
    monkeypatch.setattr(media_input, "has_transparency", lambda data: True)
    monkeypatch.setattr(
        media_input,
        "composite_on_neutral_background",
        lambda data, **kw: b"CONDITIONED",
    )
    prepared = prepare_image_input(
        _DATA_URI,
        needs_hosted_url=False,
        accepts_data_uri=True,
        uploader=lambda *a: pytest.fail("data-URI provider must not host"),
    )
    assert prepared.conditioned is True
    assert prepared.hosted is False
    # Re-encoded conditioned bytes as a data URI.
    assert prepared.image.startswith("data:image/png;base64,")
    payload = prepared.image.split(",", 1)[1]
    assert base64.b64decode(payload) == b"CONDITIONED"


def test_storage_failure_raises_hosting_error(monkeypatch):
    monkeypatch.setattr(media_input, "has_transparency", lambda data: False)

    def boom(data, content_type, key):
        raise RuntimeError("minio down")

    with pytest.raises(ImageHostingError):
        prepare_image_input(
            _DATA_URI,
            needs_hosted_url=True,
            accepts_data_uri=False,
            uploader=boom,
        )


def test_missing_uploader_when_hosting_required_raises_hosting_error(monkeypatch):
    monkeypatch.setattr(media_input, "has_transparency", lambda data: False)
    with pytest.raises(ImageHostingError):
        prepare_image_input(
            _DATA_URI,
            needs_hosted_url=True,
            accepts_data_uri=False,
            uploader=None,
        )


def test_empty_image_rejected():
    with pytest.raises(ImageInputError):
        prepare_image_input("", needs_hosted_url=False)


def test_non_uri_non_url_rejected():
    with pytest.raises(ImageInputError):
        prepare_image_input("just some text", needs_hosted_url=False)
