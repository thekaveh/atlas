"""Executable contracts for the Docling/Parakeet provider boundary."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import threading
from pathlib import Path
from types import ModuleType

import pytest
from fastapi import FastAPI, Request, Response
from httpx2 import ASGITransport, AsyncClient


ROOT = Path(__file__).resolve().parents[2]
BOUNDARY_PATH = ROOT / "services" / "docling" / "provider" / "provider_boundary.py"


def _load_boundary() -> ModuleType:
    spec = importlib.util.spec_from_file_location("docling_provider_boundary", BOUNDARY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {BOUNDARY_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request(app: FastAPI, method: str, path: str, **kwargs):
    async def run():
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://provider.test",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def _make_app(boundary: ModuleType, settings, *, fail: bool = False) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.get("/")
    async def root():
        return {"status": "ok"}

    @app.post("/work")
    async def work(request: Request):
        await request.body()
        if fail:
            raise RuntimeError("private downstream failure")
        return {"status": "ok"}

    boundary.install_provider_boundary(app, settings)
    return app


def _settings(boundary: ModuleType, **overrides):
    values = {
        "service_name": "Docling",
        "token": "secret-token",
        "auth_mode": "required",
        "capacity": 1,
        "expensive_paths": frozenset({"/work"}),
        "cors_origins": (),
    }
    values.update(overrides)
    return boundary.BoundarySettings(**values)


def test_health_is_public_but_every_other_route_requires_bearer():
    boundary = _load_boundary()
    app = _make_app(boundary, _settings(boundary))

    assert _request(app, "GET", "/health").status_code == 200
    missing = _request(app, "GET", "/")
    wrong = _request(app, "GET", "/", headers={"Authorization": "Bearer wrong"})
    non_ascii = _request(
        app,
        "GET",
        "/",
        headers={b"authorization": "Bearer s\N{SNOWMAN}".encode("utf-8")},
    )
    valid = _request(
        app,
        "GET",
        "/",
        headers={"Authorization": "Bearer secret-token"},
    )

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert wrong.status_code == 401
    assert non_ascii.status_code == 401
    assert valid.status_code == 200
    assert "wrong" not in wrong.text


def test_required_mode_with_empty_token_fails_closed_before_body_parsing():
    boundary = _load_boundary()
    app = _make_app(boundary, _settings(boundary, token=""))

    response = _request(app, "POST", "/work", content=b"unread")

    assert response.status_code == 503
    assert response.json() == {"detail": "Provider authentication is unavailable"}


def test_disabled_mode_is_an_explicit_authentication_rollback():
    boundary = _load_boundary()
    app = _make_app(boundary, _settings(boundary, token="", auth_mode="disabled"))

    assert _request(app, "GET", "/").status_code == 200
    assert _request(app, "POST", "/work", content=b"read").status_code == 200


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"DOCLING_AUTH_MODE": "optional"}, "DOCLING_AUTH_MODE"),
        ({"DOCLING_CONCURRENCY": "0"}, "DOCLING_CONCURRENCY"),
        ({"DOCLING_CONCURRENCY": "many"}, "DOCLING_CONCURRENCY"),
        (
            {
                "DOCLING_AUTH_MODE": "required",
                "DOCLING_CORS_ORIGINS": "*",
            },
            "DOCLING_CORS_ORIGINS",
        ),
    ],
)
def test_boundary_configuration_is_validated(monkeypatch, env, message):
    boundary = _load_boundary()
    monkeypatch.setenv("DOCLING_API_TOKEN", "secret-token")
    monkeypatch.setenv("DOCLING_AUTH_MODE", "required")
    monkeypatch.setenv("DOCLING_CONCURRENCY", "1")
    monkeypatch.setenv("DOCLING_CORS_ORIGINS", "")
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        boundary.load_boundary_settings("DOCLING", {"/work"})


def test_explicit_cors_origins_are_normalized(monkeypatch):
    boundary = _load_boundary()
    monkeypatch.setenv("DOCLING_API_TOKEN", "secret-token")
    monkeypatch.setenv("DOCLING_AUTH_MODE", "required")
    monkeypatch.setenv("DOCLING_CONCURRENCY", "2")
    monkeypatch.setenv(
        "DOCLING_CORS_ORIGINS",
        " https://one.example,https://two.example,https://one.example ",
    )

    settings = boundary.load_boundary_settings("DOCLING", {"/work"})

    assert settings.capacity == 2
    assert settings.cors_origins == (
        "https://one.example",
        "https://two.example",
    )


def test_configured_cors_preflight_is_answered_without_weakening_route_auth():
    boundary = _load_boundary()
    app = _make_app(
        boundary,
        _settings(boundary, cors_origins=("https://trusted.example",)),
    )

    preflight = _request(
        app,
        "OPTIONS",
        "/work",
        headers={
            "Origin": "https://trusted.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    unauthenticated_post = _request(
        app,
        "POST",
        "/work",
        headers={"Origin": "https://trusted.example"},
        content=b"still protected",
    )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://trusted.example"
    assert unauthenticated_post.status_code == 401


@pytest.mark.parametrize("value", ["0", "3601", "1.5", "inf", "", "nine"])
def test_timeout_must_be_a_finite_integer_in_the_supported_range(monkeypatch, value):
    boundary = _load_boundary()
    monkeypatch.setenv("DOCLING_INFERENCE_TIMEOUT_SECONDS", value)

    with pytest.raises(ValueError, match="DOCLING_INFERENCE_TIMEOUT_SECONDS"):
        boundary.parse_timeout_seconds("DOCLING")


def test_timeout_default_and_bounds(monkeypatch):
    boundary = _load_boundary()
    monkeypatch.delenv("DOCLING_INFERENCE_TIMEOUT_SECONDS", raising=False)
    assert boundary.parse_timeout_seconds("DOCLING") == 900
    for value in ("1", "3600"):
        monkeypatch.setenv("DOCLING_INFERENCE_TIMEOUT_SECONDS", value)
        assert boundary.parse_timeout_seconds("DOCLING") == int(value)


@pytest.mark.parametrize("variable", ["DOCLING_MAX_FILE_SIZE", "PARAKEET_MAX_UPLOAD_BYTES"])
@pytest.mark.parametrize("value", ["invalid", "0", "-1"])
def test_provider_upload_limit_must_be_a_positive_integer(
    monkeypatch, variable, value
):
    boundary = _load_boundary()
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError, match=variable):
        boundary.parse_positive_int(variable, default=1024)


def test_provider_upload_limit_default(monkeypatch):
    boundary = _load_boundary()
    monkeypatch.delenv("DOCLING_MAX_FILE_SIZE", raising=False)
    assert boundary.parse_positive_int("DOCLING_MAX_FILE_SIZE", default=1024) == 1024


@pytest.mark.parametrize("value", ["0", "invalid", "3601", "9" * 5000])
def test_provider_upload_timeout_must_be_within_supported_range(
    monkeypatch, value
):
    boundary = _load_boundary()
    variable = "DOCLING_UPLOAD_TIMEOUT_SECONDS"
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError, match=variable):
        boundary.parse_positive_int(variable, default=120, maximum=3600)


def test_full_capacity_returns_429_without_reading_second_request_body():
    boundary = _load_boundary()
    entered = asyncio.Event()
    release = asyncio.Event()
    app = FastAPI()

    @app.post("/work")
    async def work(request: Request):
        entered.set()
        await release.wait()
        await request.body()
        return {"status": "ok"}

    boundary.install_provider_boundary(app, _settings(boundary))

    async def scenario():
        first_messages = iter(
            [
                {
                    "type": "http.request",
                    "body": b"first",
                    "more_body": False,
                }
            ]
        )

        async def first_receive():
            return next(first_messages)

        first_sent = []

        async def first_send(message):
            first_sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/work",
            "raw_path": b"/work",
            "query_string": b"",
            "headers": [(b"authorization", b"Bearer secret-token")],
            "client": ("127.0.0.1", 1),
            "server": ("provider.test", 80),
            "root_path": "",
        }
        first = asyncio.create_task(app(scope, first_receive, first_send))
        await entered.wait()

        body_was_read = False

        async def forbidden_receive():
            nonlocal body_was_read
            body_was_read = True
            raise AssertionError("capacity rejection read the request body")

        second_sent = []

        async def second_send(message):
            second_sent.append(message)

        await app(scope, forbidden_receive, second_send)
        release.set()
        await first
        return body_was_read, second_sent

    body_was_read, messages = asyncio.run(scenario())
    start = next(message for message in messages if message["type"] == "http.response.start")
    headers = dict(start["headers"])
    assert start["status"] == 429
    assert headers[b"retry-after"] == b"1"
    assert body_was_read is False


def test_permit_is_released_after_success_and_downstream_exception():
    boundary = _load_boundary()
    success_app = _make_app(boundary, _settings(boundary))
    failure_app = _make_app(boundary, _settings(boundary), fail=True)
    headers = {"Authorization": "Bearer secret-token"}

    assert _request(success_app, "POST", "/work", content=b"one", headers=headers).status_code == 200
    assert _request(success_app, "POST", "/work", content=b"two", headers=headers).status_code == 200
    assert _request(failure_app, "POST", "/work", content=b"one", headers=headers).status_code == 500
    assert _request(failure_app, "POST", "/work", content=b"two", headers=headers).status_code == 500


def test_permit_is_released_after_downstream_validation_response():
    boundary = _load_boundary()
    calls = 0
    app = FastAPI()

    @app.post("/work")
    async def work(request: Request):
        nonlocal calls
        calls += 1
        await request.body()
        if calls == 1:
            return Response(status_code=422)
        return {"status": "ok"}

    boundary.install_provider_boundary(app, _settings(boundary))
    headers = {"Authorization": "Bearer secret-token"}

    assert _request(app, "POST", "/work", content=b"bad", headers=headers).status_code == 422
    assert _request(app, "POST", "/work", content=b"good", headers=headers).status_code == 200


def test_permit_is_released_when_downstream_request_is_cancelled():
    boundary = _load_boundary()
    entered = asyncio.Event()
    calls = 0
    app = FastAPI()

    @app.post("/work")
    async def work():
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.Event().wait()
        return {"status": "ok"}

    boundary.install_provider_boundary(app, _settings(boundary))

    async def scenario():
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/work",
            "raw_path": b"/work",
            "query_string": b"",
            "headers": [(b"authorization", b"Bearer secret-token")],
            "client": ("127.0.0.1", 1),
            "server": ("provider.test", 80),
            "root_path": "",
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        sent = []

        async def send(message):
            sent.append(message)

        first = asyncio.create_task(app(scope, receive, send))
        await entered.wait()
        first.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await first

        sent.clear()
        await app(scope, receive, send)
        return sent

    messages = asyncio.run(scenario())
    start = next(message for message in messages if message["type"] == "http.response.start")
    assert start["status"] == 200


def test_deadline_wrapper_raises_a_specific_generic_exception(monkeypatch):
    boundary = _load_boundary()

    async def force_timeout(awaitable, timeout):
        awaitable.cancel()
        raise asyncio.TimeoutError

    monkeypatch.setattr(boundary.asyncio, "wait_for", force_timeout)
    monkeypatch.setattr(boundary, "parse_timeout_seconds", lambda prefix: 7)

    with pytest.raises(boundary.ProviderDeadlineExceeded):
        asyncio.run(boundary.run_with_deadline("DOCLING", lambda: "never returned"))


def test_deadline_wrapper_defers_cancellation_until_native_work_settles(monkeypatch):
    boundary = _load_boundary()
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(boundary, "parse_timeout_seconds", lambda prefix: 30)

    def native_work():
        started.set()
        release.wait(timeout=5)
        return "settled"

    async def scenario():
        task = asyncio.create_task(boundary.run_with_deadline("DOCLING", native_work))
        deadline = asyncio.get_running_loop().time() + 5.0
        while not started.is_set():
            assert asyncio.get_running_loop().time() < deadline, (
                "native work never signalled started"
            )
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.02)
        cancelled_before_native_settled = task.done()
        release.set()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return cancelled_before_native_settled

    assert asyncio.run(scenario()) is False


def test_cancelled_native_timeout_uses_direct_process_terminator(monkeypatch):
    boundary = _load_boundary()
    started = threading.Event()
    release = threading.Event()
    exits: list[int] = []
    real_wait_for = asyncio.wait_for
    wait_calls = 0
    monkeypatch.setattr(boundary, "parse_timeout_seconds", lambda prefix: 30)

    async def controlled_wait_for(awaitable, timeout):
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            return await real_wait_for(awaitable, timeout)
        awaitable.cancel()
        raise asyncio.TimeoutError

    monkeypatch.setattr(boundary.asyncio, "wait_for", controlled_wait_for)

    def native_work():
        started.set()
        release.wait(timeout=5)

    async def scenario():
        task = asyncio.create_task(
            boundary.run_with_deadline(
                "DOCLING",
                native_work,
                terminate_on_cancel_timeout=lambda code: exits.append(code),
            )
        )
        deadline = asyncio.get_running_loop().time() + 5.0
        while not started.is_set():
            assert asyncio.get_running_loop().time() < deadline, (
                "native work never signalled started"
            )
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(boundary.ProviderDeadlineExceeded):
            await task
        release.set()

    asyncio.run(scenario())
    assert exits == [boundary.FATAL_TIMEOUT_EXIT_CODE]


def test_fatal_timeout_response_terminates_only_after_response_is_sent():
    boundary = _load_boundary()
    exits: list[int] = []
    response = boundary.fatal_timeout_response(
        "DOCLING", terminate=lambda code: exits.append(code)
    )

    assert response.status_code == 504
    assert exits == []

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/work",
        "raw_path": b"/work",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("provider.test", 80),
        "root_path": "",
    }
    asyncio.run(response(scope, receive, send))

    assert exits == [boundary.FATAL_TIMEOUT_EXIT_CODE]
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    assert b"timed out" in body.lower()
    assert b"DOCLING" not in body
