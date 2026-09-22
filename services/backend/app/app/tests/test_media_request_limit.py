from __future__ import annotations

import asyncio
import errno
import importlib
import json
import sys
import time
from uuid import uuid4

import jwt
import pytest

import media_request_limit
from backend_identity import authenticate_backend_scope
from media_request_limit import (
    MediaRequestLimitMiddleware,
    media_request_max_bytes_from_env,
)


def _scope(
    *,
    content_length: int | str | None = None,
    bearer_token: str | None = None,
    method: str = "POST",
    path: str = "/media/generate",
) -> dict:
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    if bearer_token is not None:
        headers.append((b"authorization", f"Bearer {bearer_token}".encode("ascii")))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "client": ("test", 1),
        "server": ("test", 80),
    }


def _middleware(app, *, max_bytes: int) -> MediaRequestLimitMiddleware:
    return MediaRequestLimitMiddleware(
        app,
        max_bytes=max_bytes,
        authenticate=authenticate_backend_scope,
    )


def _track_spools(monkeypatch):
    created = []
    original = media_request_limit.SpooledTemporaryFile

    def tracking_spool(*args, **kwargs):
        spool = original(*args, **kwargs)
        created.append(spool)
        return spool

    monkeypatch.setattr(media_request_limit, "SpooledTemporaryFile", tracking_spool)
    return created


def _fresh_main_app(monkeypatch, *, otel_enabled: bool):
    for name, value in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
        ("BACKEND_CORS_ORIGINS", "*"),
        ("MEDIA_REQUEST_MAX_BYTES", "4"),
    ):
        monkeypatch.setenv(name, value)
    sys.modules.pop("main", None)

    if otel_enabled:
        monkeypatch.setenv("ATLAS_OTEL_ENABLED", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel.invalid")
        import observability
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        monkeypatch.setattr(
            observability,
            "_create_tracer_provider",
            lambda _service_name: object(),
        )
        monkeypatch.setattr(
            CeleryInstrumentor,
            "instrument",
            lambda self, **_kwargs: None,
        )
    else:
        monkeypatch.setenv("ATLAS_OTEL_ENABLED", "false")

    return importlib.import_module("main")


@pytest.mark.parametrize("value", ["0", "-1", "many", "1.5"])
def test_media_request_config_rejects_invalid_limit(monkeypatch, value):
    monkeypatch.setenv("MEDIA_REQUEST_MAX_BYTES", value)
    with pytest.raises(ValueError, match="MEDIA_REQUEST_MAX_BYTES"):
        media_request_max_bytes_from_env()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/media/generate"),
        ("POST", "/media/operations/example"),
    ],
)
def test_non_target_method_and_path_bypass_auth_and_buffering(
    monkeypatch,
    method,
    path,
):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    received = []

    async def receive():
        return {"type": "http.request", "body": b"raw", "more_body": False}

    async def app(scope, downstream_receive, send):
        assert downstream_receive is receive
        received.append(await downstream_receive())

    async def send(_message):
        return None

    middleware = _middleware(app, max_bytes=1)
    asyncio.run(
        middleware(
            _scope(method=method, path=path),
            receive,
            send,
        )
    )

    assert received == [
        {"type": "http.request", "body": b"raw", "more_body": False}
    ]


@pytest.mark.parametrize("bearer_token", [None, "invalid-token"])
@pytest.mark.parametrize("content_length", ["not-an-integer", 5])
def test_unauthorized_media_request_precedes_content_length_and_body(
    monkeypatch,
    bearer_token,
    content_length,
):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")
    monkeypatch.setenv(
        "SUPABASE_JWT_SECRET",
        "atlas-test-supabase-jwt-secret-32-bytes",
    )
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    middleware = _middleware(app, max_bytes=4)
    sent = []

    async def receive():
        raise AssertionError("unauthorized media body must not be read")

    async def send(message):
        sent.append(message)

    asyncio.run(
        middleware(
            _scope(
                content_length=content_length,
                bearer_token=bearer_token,
            ),
            receive,
            send,
        )
    )

    assert called is False
    assert sent[0]["status"] == 401
    assert dict(sent[0]["headers"])[b"www-authenticate"] == b"Bearer"
    expected_detail = (
        "Valid backend bearer authentication is required"
        if bearer_token is None
        else "Invalid or expired backend bearer token"
    )
    assert json.loads(sent[1]["body"]) == {"detail": expected_detail}


def test_invalid_auth_configuration_is_503_before_body_read(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "unexpected")
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    middleware = _middleware(app, max_bytes=4)
    sent = []

    async def receive():
        raise AssertionError("misconfigured auth media body must not be read")

    async def send(message):
        sent.append(message)

    asyncio.run(
        middleware(
            _scope(content_length="not-an-integer"),
            receive,
            send,
        )
    )

    assert called is False
    assert sent[0]["status"] == 503
    assert json.loads(sent[1]["body"]) == {
        "detail": "BACKEND_IDENTITY_AUTH must be required or disabled"
    }


@pytest.mark.parametrize(
    ("content_length", "detail"),
    [
        ("not-an-integer", "Content-Length must be an integer"),
        (-1, "Content-Length must not be negative"),
    ],
)
def test_authenticated_request_rejects_invalid_content_length_before_receive(
    monkeypatch,
    content_length,
    detail,
):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")
    called = False
    sent = []

    async def receive():
        raise AssertionError("invalid declared length must not read the body")

    async def app(scope, downstream_receive, send):
        nonlocal called
        called = True

    async def send(message):
        sent.append(message)

    middleware = _middleware(app, max_bytes=4)
    asyncio.run(
        middleware(
            _scope(
                content_length=content_length,
                bearer_token="internal-secret",
            ),
            receive,
            send,
        )
    )

    assert called is False
    assert sent[0]["status"] == 400
    assert json.loads(sent[1]["body"]) == {"detail": detail}


@pytest.mark.parametrize("otel_enabled", [False, True])
def test_full_stack_auth_rejection_precedes_content_length_and_receive(
    monkeypatch,
    otel_enabled,
):
    main = _fresh_main_app(monkeypatch, otel_enabled=otel_enabled)
    auth_cases = (
        (
            "missing",
            "required",
            None,
            401,
            "Valid backend bearer authentication is required",
        ),
        (
            "invalid",
            "required",
            "invalid-token",
            401,
            "Invalid or expired backend bearer token",
        ),
        (
            "scoped",
            "required",
            "n8n-secret",
            403,
            "N8N credentials are not authorized for this backend route",
        ),
        (
            "misconfigured",
            "unexpected",
            None,
            503,
            "BACKEND_IDENTITY_AUTH must be required or disabled",
        ),
    )
    content_lengths = ("not-an-integer", -1, 5)

    try:
        for case, mode, token, expected_status, expected_detail in auth_cases:
            monkeypatch.setenv("BACKEND_IDENTITY_AUTH", mode)
            monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")
            monkeypatch.setenv("BACKEND_N8N_API_TOKEN", "n8n-secret")
            monkeypatch.setenv(
                "SUPABASE_JWT_SECRET",
                "atlas-test-supabase-jwt-secret-32-bytes",
            )
            for content_length in content_lengths:
                received = 0
                sent = []

                async def receive():
                    nonlocal received
                    received += 1
                    raise AssertionError(
                        f"{case} auth must reject before ASGI receive"
                    )

                async def send(message):
                    sent.append(message)

                scope = _scope(
                    content_length=content_length,
                    bearer_token=token,
                )
                scope["headers"].append((b"origin", b"https://client.example"))
                asyncio.run(main.app(scope, receive, send))

                assert received == 0, (case, content_length)
                assert sent[0]["status"] == expected_status, (case, content_length)
                headers = dict(sent[0]["headers"])
                assert headers[b"access-control-allow-origin"] == b"*"
                if expected_status == 401:
                    assert headers[b"www-authenticate"] == b"Bearer"
                else:
                    assert b"www-authenticate" not in headers
                assert json.loads(sent[1]["body"]) == {
                    "detail": expected_detail
                }

        middleware_names = [
            middleware.cls.__name__ for middleware in main.app.user_middleware
        ]
        assert middleware_names[:3] == [
            "CORSMiddleware",
            "MediaRequestLimitMiddleware",
            "PrometheusInstrumentatorMiddleware",
        ]
        if otel_enabled:
            stack = main.app.middleware_stack
            stack_names = []
            while stack is not None:
                stack_names.append(type(stack).__name__)
                stack = getattr(stack, "app", None)
            assert "OpenTelemetryMiddleware" in stack_names
    finally:
        if otel_enabled:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.uninstrument_app(main.app)
        sys.modules.pop("main", None)


def test_media_request_rejects_declared_oversize_body_before_app(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    middleware = _middleware(app, max_bytes=4)
    sent = []

    async def receive():
        raise AssertionError("declared oversize body must not be read")

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(_scope(content_length=5), receive, send))

    assert called is False
    assert sent[0]["status"] == 413


def test_media_request_rejects_stream_that_crosses_limit(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    called = False
    messages = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"de", "more_body": False},
        ]
    )

    async def app(scope, receive, send):
        nonlocal called
        called = True

    async def receive():
        return next(messages)

    middleware = _middleware(app, max_bytes=4)
    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(_scope(), receive, send))

    assert called is False
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["detail"].startswith("Media request body exceeds")


def test_auth_disabled_replays_bounded_body_to_app(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    messages = iter(
        [
            {"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.request", "body": b"cd", "more_body": False},
        ]
    )
    received = []

    async def receive():
        return next(messages)

    async def app(scope, replay, send):
        received.append(await replay())

    async def send(_message):
        return None

    middleware = _middleware(app, max_bytes=4)
    asyncio.run(middleware(_scope(), receive, send))

    assert received == [
        {"type": "http.request", "body": b"abcd", "more_body": False}
    ]


def test_replay_delegates_to_original_receive_after_terminal_body(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    messages = iter(
        [
            {"type": "http.request", "body": b"abcd", "more_body": False},
            {"type": "http.disconnect"},
        ]
    )
    received = []

    async def receive():
        return next(messages)

    async def app(scope, replay, send):
        received.append(await replay())
        received.append(await replay())

    async def send(_message):
        return None

    middleware = _middleware(app, max_bytes=4)
    asyncio.run(middleware(_scope(), receive, send))

    assert received == [
        {"type": "http.request", "body": b"abcd", "more_body": False},
        {"type": "http.disconnect"},
    ]


def test_empty_frames_and_exact_replay_boundaries_preserve_receive_sequence(
    monkeypatch,
):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    first = b"a" * 65_536
    second = b"b" * 65_536
    messages = iter(
        [
            {"type": "http.request", "body": b"", "more_body": True},
            {"type": "http.request", "body": first, "more_body": True},
            {"type": "http.request", "body": b"", "more_body": True},
            {"type": "http.request", "body": second, "more_body": False},
            {"type": "http.disconnect"},
        ]
    )
    received = []

    async def receive():
        try:
            return next(messages)
        except StopIteration:
            return {"type": "http.disconnect"}

    async def app(scope, replay, send):
        for _ in range(4):
            received.append(await replay())

    async def send(_message):
        return None

    middleware = _middleware(app, max_bytes=131_072)
    asyncio.run(middleware(_scope(), receive, send))

    assert received == [
        {"type": "http.request", "body": first, "more_body": True},
        {"type": "http.request", "body": second, "more_body": False},
        {"type": "http.disconnect"},
        {"type": "http.disconnect"},
    ]


def test_internal_token_chunked_body_is_replayed_in_bounded_chunks(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")
    payload = (b"a" * 70_000) + (b"b" * 61_073)
    messages = iter(
        [
            {"type": "http.request", "body": payload[:70_000], "more_body": True},
            {"type": "http.request", "body": payload[70_000:], "more_body": False},
        ]
    )
    replayed = []

    async def receive():
        return next(messages)

    async def app(scope, replay, send):
        while True:
            message = await replay()
            replayed.append(message)
            if not message.get("more_body", False):
                break

    async def send(_message):
        return None

    middleware = _middleware(app, max_bytes=131_073)
    asyncio.run(
        middleware(
            _scope(bearer_token="internal-secret"),
            receive,
            send,
        )
    )

    assert b"".join(message["body"] for message in replayed) == payload
    assert len(replayed) > 1
    assert all(message["more_body"] for message in replayed[:-1])
    assert replayed[-1]["more_body"] is False
    assert max(len(message["body"]) for message in replayed) <= 65_536


def test_declared_length_at_limit_is_accepted(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")
    delivered = []

    async def receive():
        return {"type": "http.request", "body": b"abcd", "more_body": False}

    async def app(scope, replay, send):
        delivered.append((await replay())["body"])

    async def send(_message):
        return None

    middleware = _middleware(app, max_bytes=4)
    asyncio.run(
        middleware(
            _scope(content_length=4, bearer_token="internal-secret"),
            receive,
            send,
        )
    )

    assert delivered == [b"abcd"]


def test_supabase_user_token_reaches_media_app(monkeypatch):
    secret = "atlas-test-supabase-jwt-secret-32-bytes"
    subject = str(uuid4())
    token = jwt.encode(
        {
            "sub": subject,
            "role": "authenticated",
            "aud": "authenticated",
            "exp": int(time.time()) + 60,
        },
        secret,
        algorithm="HS256",
    )
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    delivered = []

    async def receive():
        return {"type": "http.request", "body": b"x", "more_body": False}

    async def app(scope, replay, send):
        delivered.append((await replay())["body"])

    async def send(_message):
        return None

    middleware = _middleware(app, max_bytes=1)
    asyncio.run(middleware(_scope(bearer_token=token), receive, send))

    assert delivered == [b"x"]


def test_concurrent_requests_keep_exact_limit_and_rejection_isolated(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    delivered = {}

    async def app(scope, replay, send):
        messages = []
        while True:
            message = await replay()
            messages.append(message["body"])
            if not message.get("more_body", False):
                break
        delivered[scope["client"][0]] = b"".join(messages)

    middleware = _middleware(app, max_bytes=4)

    async def invoke(label, chunks):
        messages = iter(chunks)
        sent = []

        async def receive():
            await asyncio.sleep(0)
            return next(messages)

        async def send(message):
            sent.append(message)

        scope = _scope()
        scope["client"] = (label, 1)
        await middleware(scope, receive, send)
        return sent

    async def run_concurrently():
        return await asyncio.gather(
            invoke(
                "exact",
                [
                    {"type": "http.request", "body": b"ab", "more_body": True},
                    {"type": "http.request", "body": b"cd", "more_body": False},
                ],
            ),
            invoke(
                "oversize",
                [
                    {"type": "http.request", "body": b"xyz", "more_body": True},
                    {"type": "http.request", "body": b"12", "more_body": False},
                ],
            ),
        )

    exact_sent, oversize_sent = asyncio.run(run_concurrently())

    assert exact_sent == []
    assert oversize_sent[0]["status"] == 413
    assert delivered == {"exact": b"abcd"}


def test_disconnect_closes_request_spool_without_calling_app(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    spools = _track_spools(monkeypatch)
    called = False

    async def receive():
        return {"type": "http.disconnect"}

    async def app(scope, replay, send):
        nonlocal called
        called = True

    async def send(_message):
        return None

    middleware = _middleware(app, max_bytes=4)
    asyncio.run(middleware(_scope(), receive, send))

    assert called is False
    assert len(spools) == 1
    assert spools[0].closed is True


def test_partial_body_disconnect_closes_spool_without_calling_app(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    spools = _track_spools(monkeypatch)
    messages = iter(
        [
            {"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.disconnect"},
        ]
    )
    called = False
    sent = []

    async def receive():
        return next(messages)

    async def app(scope, replay, send):
        nonlocal called
        called = True

    async def send(message):
        sent.append(message)

    middleware = _middleware(app, max_bytes=4)
    asyncio.run(middleware(_scope(), receive, send))

    assert called is False
    assert sent == []
    assert len(spools) == 1
    assert spools[0].closed is True


def test_body_over_memory_threshold_rolls_to_disk_and_replays_in_chunks(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    spools = _track_spools(monkeypatch)
    payload = b"x" * 1_048_577
    replayed = []

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def app(scope, replay, send):
        while True:
            message = await replay()
            replayed.append(message)
            if not message.get("more_body", False):
                break

    async def send(_message):
        return None

    middleware = _middleware(app, max_bytes=1_048_577)
    asyncio.run(middleware(_scope(), receive, send))

    assert len(spools) == 1
    assert spools[0]._rolled is True
    assert spools[0].closed is True
    assert b"".join(message["body"] for message in replayed) == payload
    assert len(replayed) > 1
    assert all(message["more_body"] for message in replayed[:-1])
    assert replayed[-1]["more_body"] is False
    assert max(len(message["body"]) for message in replayed) <= 65_536


def test_single_large_event_rolls_before_writing_to_memory(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    original = media_request_limit.SpooledTemporaryFile
    events = []

    class TrackingSpool:
        def __init__(self, *args, **kwargs):
            self.spool = original(*args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.spool.close()

        def __getattr__(self, name):
            return getattr(self.spool, name)

        def rollover(self):
            events.append(("rollover", self.spool.tell()))
            return self.spool.rollover()

        def write(self, body):
            events.append(("write", len(body), self.spool._rolled))
            return self.spool.write(body)

    monkeypatch.setattr(media_request_limit, "SpooledTemporaryFile", TrackingSpool)
    payload = b"x" * 8_388_608
    replayed_bytes = 0
    replayed_chunks = 0

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def app(scope, replay, send):
        nonlocal replayed_bytes, replayed_chunks
        while True:
            message = await replay()
            replayed_bytes += len(message["body"])
            replayed_chunks += 1
            if not message.get("more_body", False):
                break

    async def send(_message):
        return None

    middleware = _middleware(app, max_bytes=len(payload))
    asyncio.run(middleware(_scope(), receive, send))

    assert events[:2] == [
        ("rollover", 0),
        ("write", len(payload), True),
    ]
    write_events = [event for event in events if event[0] == "write"]
    assert all(rolled or size <= 1_048_576 for _, size, rolled in write_events)
    assert replayed_bytes == len(payload)
    assert replayed_chunks == 128


def test_downstream_exception_closes_request_spool(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    spools = _track_spools(monkeypatch)

    async def receive():
        return {"type": "http.request", "body": b"abcd", "more_body": False}

    async def app(scope, replay, send):
        await replay()
        raise RuntimeError("downstream failed")

    async def send(_message):
        return None

    middleware = _middleware(app, max_bytes=4)
    with pytest.raises(RuntimeError, match="downstream failed"):
        asyncio.run(middleware(_scope(), receive, send))

    assert len(spools) == 1
    assert spools[0].closed is True


def test_stream_over_limit_closes_spool_before_response(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    spools = _track_spools(monkeypatch)
    messages = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"de", "more_body": False},
        ]
    )
    called = False
    sent = []

    async def receive():
        return next(messages)

    async def app(scope, replay, send):
        nonlocal called
        called = True

    async def send(message):
        sent.append(message)

    middleware = _middleware(app, max_bytes=4)
    asyncio.run(middleware(_scope(), receive, send))

    assert called is False
    assert sent[0]["status"] == 413
    assert len(spools) == 1
    assert spools[0].closed is True


def test_receive_cancellation_closes_request_spool(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    spools = _track_spools(monkeypatch)

    async def receive():
        raise asyncio.CancelledError

    async def app(scope, replay, send):
        raise AssertionError("cancelled receive must not call the app")

    async def send(_message):
        return None

    middleware = _middleware(app, max_bytes=4)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(middleware(_scope(), receive, send))

    assert len(spools) == 1
    assert spools[0].closed is True


def test_task_cancellation_closes_request_spool(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    spools = _track_spools(monkeypatch)
    app_started = asyncio.Event()

    async def receive():
        return {"type": "http.request", "body": b"abcd", "more_body": False}

    async def app(scope, replay, send):
        await replay()
        app_started.set()
        await asyncio.Event().wait()

    async def send(_message):
        return None

    middleware = _middleware(app, max_bytes=4)

    async def cancel_during_app():
        task = asyncio.create_task(middleware(_scope(), receive, send))
        await app_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_during_app())

    assert len(spools) == 1
    assert spools[0].closed is True


def test_tempfile_creation_enospc_propagates_without_read_or_response(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    received = 0
    called = False
    sent = []

    def fail_creation(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "temporary storage full")

    monkeypatch.setattr(media_request_limit, "SpooledTemporaryFile", fail_creation)

    async def receive():
        nonlocal received
        received += 1
        return {"type": "http.request", "body": b"abcd", "more_body": False}

    async def app(scope, replay, send):
        nonlocal called
        called = True

    async def send(message):
        sent.append(message)

    middleware = _middleware(app, max_bytes=4)
    with pytest.raises(OSError) as exc:
        asyncio.run(middleware(_scope(), receive, send))

    assert exc.value.errno == errno.ENOSPC
    assert received == 0
    assert called is False
    assert sent == []


def test_tempfile_write_enospc_closes_spool_without_app_or_response(monkeypatch):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    original = media_request_limit.SpooledTemporaryFile
    created = []
    called = False
    sent = []

    class FailingWriteSpool:
        def __init__(self, *args, **kwargs):
            self.spool = original(*args, **kwargs)
            created.append(self.spool)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.spool.close()

        def __getattr__(self, name):
            return getattr(self.spool, name)

        def write(self, _body):
            raise OSError(errno.ENOSPC, "temporary storage full")

    monkeypatch.setattr(
        media_request_limit,
        "SpooledTemporaryFile",
        FailingWriteSpool,
    )

    async def receive():
        return {"type": "http.request", "body": b"abcd", "more_body": False}

    async def app(scope, replay, send):
        nonlocal called
        called = True

    async def send(message):
        sent.append(message)

    middleware = _middleware(app, max_bytes=4)
    with pytest.raises(OSError) as exc:
        asyncio.run(middleware(_scope(), receive, send))

    assert exc.value.errno == errno.ENOSPC
    assert called is False
    assert sent == []
    assert len(created) == 1
    assert created[0].closed is True
