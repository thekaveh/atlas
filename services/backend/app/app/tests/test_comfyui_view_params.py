"""#801: `/comfyui/image/{filename}` forwards `subfolder` + `folder_type`
straight to ComfyUI's `/view`. Guard against an out-of-set folder_type and a
path-traversal subfolder (both must 400, not reach the internal ComfyUI).

Tests the validation helper directly (like test_content_disposition.py) — no
live stack, no TestClient/auth.
"""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException


@pytest.fixture(scope="module")
def validate():
    # main.py validates these env vars at import time; provide stubs like the
    # content-disposition test does so the module imports without a live stack.
    for var, default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        os.environ.setdefault(var, default)
    import main

    return main._validate_comfy_view_params


@pytest.mark.parametrize("folder_type", ["etc", "..", "", "OUTPUT", "models", "/output"])
def test_rejects_out_of_set_folder_type(validate, folder_type):
    with pytest.raises(HTTPException) as exc:
        validate("", folder_type)
    assert exc.value.status_code == 400
    assert "folder_type" in exc.value.detail


@pytest.mark.parametrize(
    "subfolder",
    ["..", "../etc", "a/../b", "/abs", "/", "sub\x00", "a\x00b"],
)
def test_rejects_traversal_subfolder(validate, subfolder):
    with pytest.raises(HTTPException) as exc:
        validate(subfolder, "output")
    assert exc.value.status_code == 400
    assert "subfolder" in exc.value.detail


@pytest.mark.parametrize("folder_type", ["output", "input", "temp"])
def test_accepts_valid_folder_types(validate, folder_type):
    # No exception for the allowlisted folder types with a clean subfolder.
    validate("", folder_type)
    validate("nested/dir", folder_type)


def test_accepts_empty_and_relative_subfolder(validate):
    validate("", "output")
    validate("a/b/c", "input")
    validate("run_2026", "temp")


# End-to-end route assertions: the guard runs before ComfyUI is contacted, so a
# rejected request returns HTTP 400 without a live stack. conftest's autouse
# fixture disables backend auth for route tests.
@pytest.fixture(scope="module")
def route_client():
    for var, default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        os.environ.setdefault(var, default)
    from fastapi.testclient import TestClient
    import main

    return TestClient(main.app)


def test_route_returns_400_for_bad_folder_type(route_client):
    r = route_client.get("/comfyui/image/test.png", params={"folder_type": "etc"})
    assert r.status_code == 400


def test_route_returns_400_for_traversal_subfolder(route_client):
    r = route_client.get("/comfyui/image/test.png", params={"subfolder": "../etc"})
    assert r.status_code == 400


def test_extract_artifacts_keeps_names_out_of_the_url():
    """#1379: the URL carries only the operation id and an index; the file
    name and subfolder stay in the stored artifact, so `&`, `#` or a space in
    them cannot make the URL name a different file."""
    from urllib.parse import unquote, urlsplit

    from comfyui_media_client import ComfyUIMediaClient

    entry = {"outputs": {"9": {"images": [
        {"filename": "a b#1.png", "subfolder": "x&y", "type": "output"},
    ]}}}
    artifact = ComfyUIMediaClient._extract_artifacts(entry, operation_id="p&1 #2")[0]
    parts = urlsplit(artifact["url"])
    assert unquote(parts.path) == "/media/operations/p&1 #2/artifacts/0"
    assert parts.query == "" and parts.fragment == ""
    assert (artifact["filename"], artifact["subfolder"]) == ("a b#1.png", "x&y")
