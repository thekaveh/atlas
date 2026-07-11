from __future__ import annotations

import base64

import pytest

import media_input
from media_input import (
    ImageHostingError,
    ImageInputError,
    parse_data_uri,
    prepare_image_input,
)

# A 1x1 transparent PNG (not actually decoded — Pillow is monkeypatched in the
# conditioning tests so these bytes never hit a real decoder).
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
_DATA_URI = "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode("ascii")


def test_parse_data_uri_decodes_base64():
    data, content_type = parse_data_uri(_DATA_URI)
    assert data == _PNG_BYTES
    assert content_type == "image/png"


def test_parse_data_uri_rejects_bad_base64():
    with pytest.raises(ImageInputError):
        parse_data_uri("data:image/png;base64,@@@not-base64@@@")


def test_parse_data_uri_returns_none_for_non_data_uri():
    assert parse_data_uri("https://cdn.example/a.png") is None


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
