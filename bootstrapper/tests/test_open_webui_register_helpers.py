from __future__ import annotations

import importlib
import importlib.util
import sys
import time
import types
from pathlib import Path

import pytest
import requests


SCRIPTS = Path(__file__).resolve().parents[2] / "services/open-webui/init/scripts"
WEBUI = "http://open-webui"


class _DatabaseError(Exception):
    pass


@pytest.fixture
def stubbed_imports(monkeypatch):
    # register_helpers imports jwt and psycopg2, and neither is in the
    # bootstrapper venv. Stub them as test_open_webui_registration_failures does.
    monkeypatch.setenv("WEBUI_URL", WEBUI)
    monkeypatch.setenv("WEBUI_SECRET_KEY", "secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://atlas")
    monkeypatch.setitem(
        sys.modules,
        "jwt",
        types.SimpleNamespace(encode=lambda *_args, **_kwargs: "token"),
    )
    monkeypatch.setitem(
        sys.modules,
        "psycopg2",
        types.SimpleNamespace(Error=_DatabaseError, connect=lambda *_a, **_k: None),
    )
    # The scripts import register_helpers from their own directory, which is
    # sys.path[0] inside the container.
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.delitem(sys.modules, "register_helpers", raising=False)
    yield
    sys.modules.pop("register_helpers", None)


@pytest.fixture
def helpers(stubbed_imports):
    return importlib.import_module("register_helpers")


@pytest.fixture
def load_script(stubbed_imports):
    def _load(filename: str):
        module_name = f"wh_{filename.replace('-', '_').replace('.', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / filename)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    return _load


class _Resp:
    def __init__(self, status_code: int, error: Exception | None = None):
        self.status_code = status_code
        self._error = error

    def raise_for_status(self):
        if self._error is not None:
            raise self._error

    def json(self):
        return {"ok": True}


def test_parse_docstring_header_reads_only_requested_fields(helpers):
    content = '"""\ntitle: Weather\ndescription: Forecasts\ntype: action\n"""\nprint(1)\n'

    assert helpers.parse_docstring_header(content, ("title", "description")) == {
        "title": "Weather",
        "description": "Forecasts",
    }
    assert helpers.parse_docstring_header(content, ("title", "description", "type")) == {
        "title": "Weather",
        "description": "Forecasts",
        "type": "action",
    }


def test_parse_docstring_header_without_header_returns_empty(helpers):
    assert helpers.parse_docstring_header("print(1)\n", ("title",)) == {}


@pytest.mark.parametrize(
    ("status", "expected"),
    [(200, True), (401, True), (403, True), (404, False), (500, False)],
)
def test_api_reachable_status_codes(helpers, monkeypatch, status, expected):
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return _Resp(status)

    monkeypatch.setattr(requests, "get", fake_get)

    assert helpers.api_reachable(WEBUI, "/api/v1/tools/") is expected
    assert seen == {"url": f"{WEBUI}/api/v1/tools/", "timeout": 5}


@pytest.mark.parametrize(
    "error",
    [requests.exceptions.ConnectionError, requests.exceptions.Timeout],
)
def test_api_reachable_treats_connection_errors_as_not_ready(helpers, monkeypatch, error):
    def fake_get(url, **kwargs):
        raise error("not yet")

    monkeypatch.setattr(requests, "get", fake_get)

    assert helpers.api_reachable(WEBUI, "/api/v1/functions/") is False


def test_api_reachable_propagates_unexpected_errors(helpers, monkeypatch):
    def fake_get(url, **kwargs):
        raise ValueError("unexpected")

    monkeypatch.setattr(requests, "get", fake_get)

    with pytest.raises(ValueError):
        helpers.api_reachable(WEBUI, "/api/v1/tools/")


@pytest.mark.parametrize(
    "case",
    [("tools", 200, True), ("functions", 404, False)],
)
def test_artifact_exists_uses_resource_path(helpers, monkeypatch, case):
    resource, status, expected = case
    seen = {}

    def fake_get(url, headers, timeout):
        seen.update(url=url, headers=headers, timeout=timeout)
        return _Resp(status)

    monkeypatch.setattr(requests, "get", fake_get)

    assert helpers.artifact_exists(WEBUI, resource, "my_id", {"h": "1"}) is expected
    assert seen == {
        "url": f"{WEBUI}/api/v1/{resource}/id/my_id",
        "headers": {"h": "1"},
        "timeout": 10,
    }


def test_create_artifact_without_extra_sends_no_type(helpers, monkeypatch):
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, json=json, timeout=timeout)
        return _Resp(200)

    monkeypatch.setattr(requests, "post", fake_post)

    helpers.create_artifact(WEBUI, "tools", "t1", "Tool One", "code", "desc", {"h": "1"})

    assert seen["url"] == f"{WEBUI}/api/v1/tools/create"
    assert list(seen["json"]) == ["id", "name", "content", "meta"]
    assert seen["json"]["meta"] == {"description": "desc"}
    assert seen["timeout"] == 30


def test_create_artifact_extra_fields_sit_before_meta(helpers, monkeypatch):
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, json=json)
        return _Resp(200)

    monkeypatch.setattr(requests, "post", fake_post)

    helpers.create_artifact(WEBUI, "functions", "f1", "F", "code", "d", {}, type="filter")

    assert seen["url"] == f"{WEBUI}/api/v1/functions/create"
    assert list(seen["json"]) == ["id", "name", "content", "type", "meta"]
    assert seen["json"]["type"] == "filter"


def test_update_artifact_targets_id_path_and_propagates_http_errors(helpers, monkeypatch):
    seen = {}
    error = requests.exceptions.HTTPError("HTTP 500")

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, timeout=timeout)
        return _Resp(500, error=error)

    monkeypatch.setattr(requests, "post", fake_post)

    with pytest.raises(requests.exceptions.HTTPError):
        helpers.update_artifact(WEBUI, "functions", "f1", "F", "code", "d", {}, type="pipe")
    assert seen == {"url": f"{WEBUI}/api/v1/functions/id/f1/update", "timeout": 30}


class _Cursor:
    def __init__(self, row=None, error: Exception | None = None):
        self.row = row
        self.error = error
        self.sql = None
        self.closed = False

    def execute(self, sql):
        if self.error is not None:
            raise self.error
        self.sql = sql

    def fetchone(self):
        return self.row

    def close(self):
        self.closed = True


class _Conn:
    def __init__(self, cursor: _Cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def test_fetch_admin_row_returns_row_and_closes_handles(helpers, monkeypatch):
    cursor = _Cursor(row=("admin-id",))
    conn = _Conn(cursor)
    seen = {}

    def fake_connect(dsn, connect_timeout):
        seen.update(dsn=dsn, connect_timeout=connect_timeout)
        return conn

    monkeypatch.setattr(helpers.psycopg2, "connect", fake_connect)

    assert helpers.fetch_admin_row("postgresql://db") == ("admin-id",)
    assert seen == {"dsn": "postgresql://db", "connect_timeout": 5}
    assert "role = 'admin'" in cursor.sql
    assert cursor.closed and conn.closed


def test_fetch_admin_row_closes_handles_then_reraises_query_errors(helpers, monkeypatch):
    cursor = _Cursor(error=_DatabaseError("boom"))
    conn = _Conn(cursor)
    monkeypatch.setattr(helpers.psycopg2, "connect", lambda *_a, **_k: conn)

    with pytest.raises(_DatabaseError):
        helpers.fetch_admin_row("postgresql://db")
    assert cursor.closed and conn.closed


def test_make_token_signs_with_given_secret_and_one_hour_expiry(helpers, monkeypatch):
    seen = {}

    def fake_encode(payload, key, algorithm):
        seen.update(payload=payload, key=key, algorithm=algorithm)
        return "signed"

    monkeypatch.setattr(helpers.jwt, "encode", fake_encode)

    assert helpers.make_token("admin-1", "s3cret") == "signed"
    assert seen["key"] == "s3cret"
    assert seen["algorithm"] == "HS256"
    assert seen["payload"]["id"] == "admin-1"
    assert abs(seen["payload"]["exp"] - (time.time() + 3600)) < 5


def test_script_wrappers_keep_per_artifact_payload_shapes(load_script, monkeypatch):
    tools = load_script("register-tools.py")
    functions = load_script("register-functions.py")
    posts = []

    def fake_post(url, json, headers, timeout):
        posts.append((url, json, timeout))
        return _Resp(200)

    monkeypatch.setattr(requests, "post", fake_post)

    tools.create_tool("my_tool", "My Tool", "code", "desc", {})
    functions.create_function("my_fn", "My Fn", "code", "desc", "filter", {})

    assert posts == [
        (
            f"{WEBUI}/api/v1/tools/create",
            {"id": "my_tool", "name": "My Tool", "content": "code", "meta": {"description": "desc"}},
            30,
        ),
        (
            f"{WEBUI}/api/v1/functions/create",
            {
                "id": "my_fn",
                "name": "My Fn",
                "content": "code",
                "type": "filter",
                "meta": {"description": "desc"},
            },
            30,
        ),
    ]


def test_script_metadata_parsers_keep_their_own_fields(load_script):
    tools = load_script("register-tools.py")
    functions = load_script("register-functions.py")
    header = '"""\ntitle: Pipe\ndescription: Demo\ntype: pipe\n"""\n'

    assert tools.parse_tool_metadata(header) == {"title": "Pipe", "description": "Demo"}
    assert functions.parse_function_metadata(header) == {
        "title": "Pipe",
        "description": "Demo",
        "type": "pipe",
    }
