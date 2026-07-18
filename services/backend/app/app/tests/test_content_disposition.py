"""Unit tests for the ComfyUI image Content-Disposition header helper.

Guards the `/comfyui/image/{filename}` path against a 500 when the filename
carries characters the latin-1 header codec cannot encode (RFC 5987).
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def disposition():
    # main.py validates these env vars at import time; provide stubs like the
    # fastapi_client fixture does so the module imports without a live stack.
    for var, default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        os.environ.setdefault(var, default)
    import main

    return main._inline_content_disposition


def test_ascii_filename_uses_plain_parameter_only(disposition):
    value = disposition("image.png")
    assert value == 'inline; filename="image.png"'
    assert "filename*" not in value


def test_non_latin1_filename_is_header_safe_and_rfc5987_encoded(disposition):
    # A CJK name is not latin-1 encodable; the header codec would raise 500.
    value = disposition("日本語.png")
    # The whole header value must be latin-1 encodable (what Starlette emits).
    value.encode("latin-1")
    # Full name preserved via the RFC 5987 filename*= form (percent-encoded).
    assert "filename*=UTF-8''" in value
    assert "%E6%97%A5" in value  # 日 percent-encoded
    # ASCII fallback carries no raw non-ASCII bytes.
    assert "日" not in value


def test_latin1_accented_filename_is_header_safe(disposition):
    value = disposition("café.png")
    value.encode("latin-1")
    assert "filename*=UTF-8''caf%C3%A9.png" in value


def test_crlf_and_quotes_are_stripped(disposition):
    value = disposition('a\r\nb".png')
    assert "\r" not in value and "\n" not in value
    # The injected quote is removed so it cannot break the header structure.
    assert value == 'inline; filename="ab.png"'
