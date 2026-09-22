"""Runtime contract for the backend's route-class body limits (#1167).

Drives the real ASGI middleware (and the Ragas evidence bounds) in-process —
starlette and pydantic come from the dev dependency group, so this runs in
the required CI unit-test job, unlike the backend container's own suite.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

BACKEND_APP = Path(__file__).resolve().parents[2] / "services" / "backend" / "app" / "app"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, BACKEND_APP / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


limits = _load("media_request_limit")
rag_eval = _load("rag_eval_service")


async def _noop_authenticate(scope):  # pragma: no cover - default stub
    return None


def _middleware(app, *, rules=(), default_max_bytes=64, authenticate=_noop_authenticate):
    policy = limits.LimitPolicy(rules=list(rules), default_max_bytes=default_max_bytes)
    return limits.RequestLimitMiddleware(app, policy=policy, authenticate=authenticate)


def _http_scope(method="POST", path="/api/chunk", headers=()):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [
            (key.encode(), value.encode()) for key, value in headers
        ],
    }


def _chunked_receive(chunks):
    queue = list(chunks)

    async def receive():
        body = queue.pop(0)
        return {
            "type": "http.request",
            "body": body,
            "more_body": bool(queue),
        }

    return receive


class _Recorder:
    """Downstream app that records what reached it and answers 200."""

    def __init__(self):
        self.bodies = []
        self.disconnected = False

    async def __call__(self, scope, receive, send):
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                self.disconnected = True
                break
            self.bodies.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _run(coro):
    return asyncio.run(coro)


def _sent_status(sent):
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def test_oversized_declared_content_length_is_rejected_before_any_read():
    app = _Recorder()

    async def receive():  # pragma: no cover - must never be called
        raise AssertionError("body must not be read for an oversized Content-Length")

    sent = []

    async def send(message):
        sent.append(message)

    scope = _http_scope(headers=[("content-length", "1000")])
    _run(_middleware(app, default_max_bytes=64)(scope, receive, send))
    assert _sent_status(sent) == 413
    assert app.bodies == []


def test_chunked_body_lying_about_content_length_is_cut_off_at_the_cap():
    app = _Recorder()
    sent = []

    async def send(message):
        sent.append(message)

    # Declares a small size, streams past the cap: the counter, not the
    # header, decides.
    scope = _http_scope(headers=[("content-length", "10")])
    receive = _chunked_receive([b"x" * 40, b"y" * 40, b"z" * 40])
    _run(_middleware(app, default_max_bytes=64)(scope, receive, send))
    assert _sent_status(sent) == 413
    assert app.disconnected, "the app must see a disconnect, not a body"
    # The app's own 200 must have been swallowed after our 413:
    statuses = [m["status"] for m in sent if m["type"] == "http.response.start"]
    assert statuses == [413]


def test_under_cap_requests_pass_through_untouched():
    app = _Recorder()
    sent = []

    async def send(message):
        sent.append(message)

    receive = _chunked_receive([b"a" * 30, b"b" * 30])
    _run(_middleware(app, default_max_bytes=64)(_http_scope(), receive, send))
    assert b"".join(app.bodies) == b"a" * 30 + b"b" * 30
    assert _sent_status(sent) == 200


def test_ruled_upload_path_uses_its_own_envelope_not_the_default():
    app = _Recorder()
    sent = []

    async def send(message):
        sent.append(message)

    rule = limits.BodyLimitRule(method="POST", path="/storage/upload", max_bytes=200)
    scope = _http_scope(path="/storage/upload")
    receive = _chunked_receive([b"x" * 150])  # over default 64, under rule 200
    _run(_middleware(app, rules=[rule], default_max_bytes=64)(scope, receive, send))
    assert _sent_status(sent) == 200
    assert b"".join(app.bodies) == b"x" * 150


def test_media_rule_authenticates_before_reading_any_body():
    from starlette.exceptions import HTTPException

    app = _Recorder()
    sent = []

    async def send(message):
        sent.append(message)

    async def deny(scope):
        raise HTTPException(status_code=401, detail="nope")

    async def receive():  # pragma: no cover - must never be called
        raise AssertionError("body must not be read before authentication")

    middleware = _middleware(
        app,
        rules=[limits.media_rule(1024)],
        default_max_bytes=None,
        authenticate=deny,
    )
    _run(middleware(_http_scope(path="/media/generate"), receive, send))
    assert _sent_status(sent) == 401
    assert app.bodies == []


def test_get_requests_and_unruled_paths_without_default_pass_through_identically():
    app = _Recorder()

    async def receive():
        return {"type": "http.request", "body": b"raw", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    middleware = _middleware(app, rules=[limits.media_rule(1024)], default_max_bytes=None)
    _run(middleware(_http_scope(method="GET", path="/media/generate"), receive, send))
    _run(middleware(_http_scope(path="/api/anything"), receive, send))
    assert _sent_status(sent) == 200


# --- Ragas evidence bounds ---------------------------------------------------


def _record(**overrides):
    base = {
        "question": "q",
        "answer": "a",
        "contexts": ["context"],
    }
    base.update(overrides)
    return base


def test_ragas_rejects_an_oversized_single_context_deterministically():
    with pytest.raises(Exception) as excinfo:
        rag_eval.RagEvaluationRecord(**_record(contexts=["c" * 40_000]))
    assert "32000" in str(excinfo.value) or "32_000" in str(excinfo.value)


def test_ragas_rejects_oversized_record_metadata():
    request = rag_eval.RagEvaluationRequest(
        records=[_record(metadata={"blob": "m" * 70_000})],
    )
    with pytest.raises(ValueError, match=r"records\[0\].metadata exceeds"):
        rag_eval.evaluate_rag_records(request, runner=lambda *a: [])


def test_ragas_rejects_an_oversized_aggregate_batch_at_the_configured_cap():
    # The ticket's example: many records whose combined context bytes pass
    # the aggregate cap even though each record is individually valid.
    records = [
        _record(contexts=["c" * 30_000] * 4)  # ~120 KB per record
    ] * 100
    request = rag_eval.RagEvaluationRequest(records=records)
    with pytest.raises(ValueError, match="exceeds 8388608 bytes of combined evidence"):
        rag_eval.evaluate_rag_records(request, runner=lambda *a: [])


def test_ragas_accepts_a_legitimate_batch_through_the_bounds(monkeypatch):
    monkeypatch.setenv("RAGAS_EVALUATOR_MODEL", "test-model")
    ran = []

    def runner(rows, metrics, config):
        ran.append((rows, metrics))
        return [{"faithfulness": 1.0} for _ in rows]

    request = rag_eval.RagEvaluationRequest(
        records=[_record() for _ in range(5)],
    )
    response = rag_eval.evaluate_rag_records(request, runner=runner)
    assert response.record_count == 5
    assert ran, "the runner must have been invoked for a legitimate batch"
