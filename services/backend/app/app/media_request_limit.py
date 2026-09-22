"""Bound request bodies before parsing, by route class (#1167).

One ASGI middleware enforces byte envelopes for every body-bearing request,
rejecting with 413 BEFORE FastAPI reads the body — which matters because
FastAPI reads (and for multipart, spools) the entire body before route
dependencies, including authentication, are resolved. Route classes:

- ``POST /media/generate`` — the original media gate, unchanged in behavior:
  header authentication first, then a spool-and-replay bounded read, so the
  route still receives the full (bounded) body after the auth check.
- Declared upload routes (``/storage/upload``, ``/documents/extract``) — the
  route-level ``UploadFile`` reads are already streamed and bounded, but the
  multipart parser spools the whole body to disk before those bounds apply;
  a rule caps that spool at the route's own documented limit plus parser
  overhead.
- Every other body-bearing method — a default JSON envelope far above any
  documented payload (Ragas batches are service-bounded, chunking text is
  capped at 1M characters) but far below "buffer arbitrary bytes".

Content-Length is validated when present but never trusted: chunked and
lying-length bodies are counted as they stream and cut off at the cap.
Responses are stable 413/400 JSON that never echo request bytes.

``MediaRequestLimitMiddleware`` remains as the single-route configuration of
the general middleware (media rule only, no default envelope), preserving its
original contract byte-for-byte for existing deployments and tests.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Sequence

from starlette.exceptions import HTTPException

DEFAULT_MEDIA_REQUEST_MAX_BYTES = 40 * 1024 * 1024
#: Default envelope for JSON APIs (chunking, Ragas, Ray, ComfyUI, memory…).
#: A constant, not an env knob: the largest documented JSON payload (a Ragas
#: batch at its service-side evidence cap) fits with comfortable headroom.
DEFAULT_JSON_REQUEST_MAX_BYTES = 16 * 1024 * 1024
#: Slack added to upload-route caps for multipart framing (boundaries,
#: part headers, the non-file form fields).
MULTIPART_OVERHEAD_BYTES = 1024 * 1024
_MEDIA_REQUEST_SPOOL_MEMORY_BYTES = 1024 * 1024
_MEDIA_REQUEST_REPLAY_CHUNK_BYTES = 64 * 1024
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def media_request_max_bytes_from_env() -> int:
    raw = (
        os.getenv("MEDIA_REQUEST_MAX_BYTES")
        or str(DEFAULT_MEDIA_REQUEST_MAX_BYTES)
    ).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("MEDIA_REQUEST_MAX_BYTES must be a positive integer") from exc
    if value <= 0:
        raise ValueError("MEDIA_REQUEST_MAX_BYTES must be a positive integer")
    return value


@dataclass(frozen=True)
class BodyLimitRule:
    """An exact (method, path) byte envelope.

    ``authenticate``/``replay`` select the media-style gate: authenticate the
    scope first, then spool the bounded body and replay it to the app. Rules
    without them stream-count the body and reject mid-flight at the cap.
    ``limit_message`` overrides the 413 detail (the media rule keeps its
    original wording).
    """

    method: str
    path: str
    max_bytes: int
    authenticate: bool = False
    replay: bool = False
    limit_message: Optional[str] = None

    def too_large_detail(self) -> str:
        if self.limit_message is not None:
            return self.limit_message
        return (
            "Request body exceeds the limit for this endpoint "
            f"({self.max_bytes} bytes)"
        )


@dataclass(frozen=True)
class LimitPolicy:
    """The middleware's full configuration: exact rules + default envelope.

    ``default_max_bytes=None`` disables the default envelope: requests that
    match no rule pass through with the original receive/send untouched
    (the legacy single-route behavior).
    """

    rules: Sequence[BodyLimitRule]
    default_max_bytes: Optional[int] = DEFAULT_JSON_REQUEST_MAX_BYTES

    def __post_init__(self) -> None:
        if self.default_max_bytes is not None and self.default_max_bytes <= 0:
            raise ValueError("default_max_bytes must be a positive integer")
        for rule in self.rules:
            if rule.max_bytes <= 0:
                raise ValueError(
                    f"max_bytes for {rule.method} {rule.path} must be positive"
                )


class RequestLimitMiddleware:
    """Enforce per-route-class body limits before any parsing happens."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        policy: LimitPolicy,
        authenticate: Callable[[Dict[str, Any]], Awaitable[Any]],
    ):
        self.app = app
        self.authenticate = authenticate
        self.default_max_bytes = policy.default_max_bytes
        self._rules = {(rule.method, rule.path): rule for rule in policy.rules}

    async def __call__(self, scope: Dict[str, Any], receive, send) -> None:
        if scope.get("type") != "http" or scope.get("method") not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return

        rule = self._rules.get((scope["method"], scope.get("path", "")))
        if rule is None and self.default_max_bytes is None:
            await self.app(scope, receive, send)
            return
        max_bytes = rule.max_bytes if rule else self.default_max_bytes
        too_large_detail = (
            rule.too_large_detail()
            if rule
            else (
                "Request body exceeds the limit for this endpoint "
                f"({max_bytes} bytes)"
            )
        )

        if rule is not None and rule.authenticate:
            try:
                await self.authenticate(scope)
            except HTTPException as exc:
                await self._reject(send, exc.status_code, exc.detail, exc.headers)
                return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                declared_size = int(declared)
            except ValueError:
                await self._reject(send, 400, "Content-Length must be an integer")
                return
            if declared_size < 0:
                await self._reject(send, 400, "Content-Length must not be negative")
                return
            if declared_size > max_bytes:
                await self._reject(send, 413, too_large_detail)
                return

        transfer = _BoundedTransfer(self.app, max_bytes, too_large_detail)
        if rule is not None and rule.replay:
            await transfer.spool_and_replay(scope, receive, send)
        else:
            await transfer.stream_with_cap(scope, receive, send)

    @staticmethod
    async def _reject(
        send,
        status: int,
        detail: Any,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        await _reject(send, status, detail, headers)


class _BoundedTransfer:
    """Body enforcement for one request under one resolved limit."""

    def __init__(self, app, max_bytes: int, too_large_detail: str):
        self.app = app
        self.max_bytes = max_bytes
        self.too_large_detail = too_large_detail

    async def stream_with_cap(self, scope, receive, send) -> None:
        """Pass the request through, counting bytes; 413 at the cap.

        Content-Length is not trusted — a chunked or lying body trips the
        counter mid-stream. Once tripped, the app sees a disconnect and its
        own response messages are swallowed (ours already went out).
        """
        total = 0
        tripped = False
        response_started = False

        async def guarded_receive() -> Dict[str, Any]:
            nonlocal total, tripped
            if tripped:
                return {"type": "http.disconnect"}
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    tripped = True
                    if not response_started:
                        await _reject(send, 413, self.too_large_detail)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Dict[str, Any]) -> None:
            nonlocal response_started
            if tripped:
                return  # our 413 already answered; drop the app's response
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        await self.app(scope, guarded_receive, guarded_send)

    async def spool_and_replay(self, scope, receive, send) -> None:
        max_bytes = self.max_bytes
        spool_memory_bytes = min(max_bytes, _MEDIA_REQUEST_SPOOL_MEMORY_BYTES)
        with SpooledTemporaryFile(
            max_size=spool_memory_bytes,
            mode="w+b",
        ) as body_file:
            total = 0
            while True:
                message = await receive()
                if message.get("type") == "http.disconnect":
                    return
                body = message.get("body", b"")
                previous_total = total
                total += len(body)
                if total > max_bytes:
                    await _reject(send, 413, self.too_large_detail)
                    return
                if previous_total <= spool_memory_bytes < total:
                    body_file.rollover()
                body_file.write(body)
                if not message.get("more_body", False):
                    break

            body_file.seek(0)
            replay_complete = False

            async def replay() -> Dict[str, Any]:
                nonlocal replay_complete
                if replay_complete:
                    return await receive()
                body = body_file.read(_MEDIA_REQUEST_REPLAY_CHUNK_BYTES)
                more_body = body_file.tell() < total
                replay_complete = not more_body
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": more_body,
                }

            await self.app(scope, replay, send)

async def _reject(
    send,
    status: int,
    detail: Any,
    headers: Mapping[str, str] | None = None,
) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    response_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    response_headers.extend(
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    )
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": response_headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


def media_rule(max_bytes: int) -> BodyLimitRule:
    """The original ``POST /media/generate`` gate as a rule."""
    return BodyLimitRule(
        method="POST",
        path="/media/generate",
        max_bytes=max_bytes,
        authenticate=True,
        replay=True,
        limit_message=(
            "Media request body exceeds MEDIA_REQUEST_MAX_BYTES "
            f"({max_bytes} bytes)"
        ),
    )


class MediaRequestLimitMiddleware(RequestLimitMiddleware):
    """Authenticate and bound ``POST /media/generate`` before route parsing.

    The original single-route configuration: only the media route is gated,
    everything else passes through with the original receive/send untouched.
    """

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        max_bytes: int | None = None,
        *,
        authenticate: Callable[[Dict[str, Any]], Awaitable[Any]],
    ):
        resolved = (
            media_request_max_bytes_from_env() if max_bytes is None else max_bytes
        )
        if resolved <= 0:
            raise ValueError("MEDIA_REQUEST_MAX_BYTES must be a positive integer")
        super().__init__(
            app,
            policy=LimitPolicy(rules=[media_rule(resolved)], default_max_bytes=None),
            authenticate=authenticate,
        )
        self.max_bytes = resolved
