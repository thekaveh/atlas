from __future__ import annotations

import asyncio
import json

import pytest

from media_request_limit import (
    MediaRequestLimitMiddleware,
    media_request_max_bytes_from_env,
)


def _scope(*, content_length: int | None = None) -> dict:
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/media/generate",
        "raw_path": b"/media/generate",
        "query_string": b"",
        "headers": headers,
        "client": ("test", 1),
        "server": ("test", 80),
    }


@pytest.mark.parametrize("value", ["0", "-1", "many", "1.5"])
def test_media_request_config_rejects_invalid_limit(monkeypatch, value):
    monkeypatch.setenv("MEDIA_REQUEST_MAX_BYTES", value)
    with pytest.raises(ValueError, match="MEDIA_REQUEST_MAX_BYTES"):
        media_request_max_bytes_from_env()


def test_media_request_rejects_declared_oversize_body_before_app():
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    middleware = MediaRequestLimitMiddleware(app, max_bytes=4)
    sent = []

    async def receive():
        raise AssertionError("declared oversize body must not be read")

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(_scope(content_length=5), receive, send))

    assert called is False
    assert sent[0]["status"] == 413


def test_media_request_rejects_stream_that_crosses_limit():
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

    middleware = MediaRequestLimitMiddleware(app, max_bytes=4)
    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(_scope(), receive, send))

    assert called is False
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["detail"].startswith("Media request body exceeds")


def test_media_request_replays_bounded_body_to_app():
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

    middleware = MediaRequestLimitMiddleware(app, max_bytes=4)
    asyncio.run(middleware(_scope(), receive, send))

    assert received == [
        {"type": "http.request", "body": b"abcd", "more_body": False}
    ]
