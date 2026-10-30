from __future__ import annotations

import asyncio
import gc
import importlib
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest


def _stub_required_env(monkeypatch):
    for var, default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        if not os.environ.get(var):
            monkeypatch.setenv(var, default)


def _fresh_main(monkeypatch):
    _stub_required_env(monkeypatch)
    sys.modules.pop("main", None)
    return importlib.import_module("main")


def test_chunk_endpoint_returns_structured_chunks(monkeypatch):
    main = _fresh_main(monkeypatch)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/api/chunk",
        json={
            "text": "Atlas chunks text for retrieval. It returns stable offsets.",
            "strategy": "token",
            "chunk_size": 6,
            "overlap": 1,
            "tokenizer": "gpt2",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["strategy"] == "token"
    assert body["chunk_count"] >= 1
    assert body["chunks"][0]["index"] == 0
    assert body["chunks"][0]["start_char"] == 0
    assert body["chunks"][0]["end_char"] > 0
    assert body["chunks"][0]["content"]
    assert "token_count" in body["chunks"][0]
    assert body["metadata"]["tokenizer"] == "gpt2"


def test_chunk_endpoint_rejects_invalid_payload(monkeypatch):
    main = _fresh_main(monkeypatch)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/api/chunk",
        json={"text": "", "strategy": "token", "chunk_size": 0, "overlap": -1},
    )

    assert response.status_code == 422


# --- bounded heavy work and upstream errors (#1354) --------------------------


def test_chunker_failure_is_a_502_with_a_fixed_detail(monkeypatch):
    main = _fresh_main(monkeypatch)
    from chunking_service import ChunkingUpstreamError
    from fastapi.testclient import TestClient

    def fail(_request):
        raise ChunkingUpstreamError("tokenizer download failed: https://hf.co/secret-path 403")

    monkeypatch.setattr(main, "chunk_text", fail)
    response = TestClient(main.app).post("/api/chunk", json={"text": "x", "strategy": "token"})

    assert response.status_code == 502
    assert response.json()["detail"] == "Chunking failed"


def test_invalid_chunk_input_stays_a_400(monkeypatch):
    main = _fresh_main(monkeypatch)
    from chunking_service import ChunkingError
    from fastapi.testclient import TestClient

    def reject(_request):
        raise ChunkingError("overlap must be smaller than chunk_size")

    monkeypatch.setattr(main, "chunk_text", reject)
    response = TestClient(main.app).post("/api/chunk", json={"text": "x", "strategy": "token"})

    assert response.status_code == 400


def test_heavy_work_past_the_bound_is_refused_without_starving_the_default_pool(monkeypatch):
    """One slot held by a stuck job: the next heavy call gets 503 at once, and
    an ordinary asyncio.to_thread call still completes."""
    from fastapi import HTTPException

    main = _fresh_main(monkeypatch)
    monkeypatch.setenv("BACKEND_HEAVY_WORK_CONCURRENCY", "1")
    monkeypatch.setattr(main, "_HEAVY_WORK_GATE", None)
    release = threading.Event()

    async def scenario():
        stuck = asyncio.create_task(main._run_heavy_work(lambda _r: release.wait(5), None, "Chunking"))
        await asyncio.sleep(0.05)
        try:
            await main._run_heavy_work(lambda _r: "never", None, "Chunking")
        except HTTPException as exc:
            refused = exc
        other = await asyncio.wait_for(asyncio.to_thread(lambda: "ok"), 1)
        release.set()
        await stuck
        after = await main._run_heavy_work(lambda r: r, "freed", "Chunking")
        return refused, other, after

    refused, other, after = asyncio.run(scenario())

    assert refused.status_code == 503 and refused.headers["Retry-After"]
    assert other == "ok"
    assert after == "freed"


def test_heavy_work_past_its_deadline_is_a_504_and_keeps_its_slot_until_done(monkeypatch):
    from fastapi import HTTPException

    main = _fresh_main(monkeypatch)
    monkeypatch.setenv("BACKEND_HEAVY_WORK_CONCURRENCY", "1")
    monkeypatch.setenv("BACKEND_HEAVY_WORK_TIMEOUT_SECONDS", "0.1")
    monkeypatch.setattr(main, "_HEAVY_WORK_GATE", None)
    release = threading.Event()

    async def scenario():
        try:
            await main._run_heavy_work(lambda _r: release.wait(5), None, "RAG evaluation")
        except HTTPException as exc:
            timed_out = exc
        in_flight = main._heavy_work_gate().in_flight
        release.set()
        await asyncio.sleep(0.2)
        return timed_out, in_flight, main._heavy_work_gate().in_flight

    timed_out, in_flight, later = asyncio.run(scenario())

    assert timed_out.status_code == 504
    assert in_flight == 1  # the thread is still running, so the slot stays taken
    assert later == 0


def test_chunker_errors_split_into_caller_and_server_faults(monkeypatch):
    """#1354: a tokenizer the caller named that cannot load is a 400 (as before);
    any other chunker failure is a 502."""
    import chunking_service

    class InvalidTokenizerError(Exception):
        pass

    def run(error):
        monkeypatch.setattr(chunking_service, "_call_chunker", lambda *_a, **_k: (_ for _ in ()).throw(error))
        try:
            chunking_service.chunk_text(chunking_service.ChunkRequest(text="x y", strategy="token"))
        except chunking_service.ChunkingError as exc:
            return type(exc)

    assert run(InvalidTokenizerError("Tokenizer 'nope' could not be loaded")) is chunking_service.ChunkingError
    assert run(RuntimeError("model download failed")) is chunking_service.ChunkingUpstreamError


def test_heavy_work_slot_frees_even_if_the_request_loop_is_gone(monkeypatch):
    """The slot is released from the worker thread, not the event loop, so a
    job that outlives its loop cannot pin the gate shut."""
    import time

    from fastapi import HTTPException

    main = _fresh_main(monkeypatch)
    monkeypatch.setenv("BACKEND_HEAVY_WORK_CONCURRENCY", "1")
    monkeypatch.setenv("BACKEND_HEAVY_WORK_TIMEOUT_SECONDS", "0.05")
    monkeypatch.setattr(main, "_HEAVY_WORK_GATE", None)
    release = threading.Event()

    async def time_out():
        try:
            await main._run_heavy_work(lambda _r: release.wait(5), None, "Chunking")
        except HTTPException as exc:
            return exc.status_code

    assert asyncio.run(time_out()) == 504  # this loop is now closed
    release.set()
    deadline = time.monotonic() + 2
    while main._heavy_work_gate().in_flight and time.monotonic() < deadline:
        time.sleep(0.01)
    assert main._heavy_work_gate().in_flight == 0


def test_a_timeout_raised_by_the_job_is_not_read_as_the_deadline(monkeypatch):
    main = _fresh_main(monkeypatch)
    monkeypatch.setattr(main, "_HEAVY_WORK_GATE", None)

    def job(_r):
        raise TimeoutError("tokenizer fetch timed out")

    with pytest.raises(TimeoutError, match="tokenizer fetch"):
        asyncio.run(main._run_heavy_work(job, None, "Chunking"))


# Late worker outcomes stay owned after request timeout or cancellation.
@pytest.fixture
def _owned_heavy_work_main(monkeypatch, tmp_path):
    for name in ("BACKEND_PLUGINS_DIR", "BACKEND_PLUGINS_SITE_DIR"):
        directory = tmp_path / name.lower()
        directory.mkdir()
        monkeypatch.setenv(name, str(directory))
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "owned-internal-token")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "owned-synthetic-jwt-key-36-characters")
    main = _fresh_main(monkeypatch)
    gate = main._HeavyWorkGate(1)
    monkeypatch.setattr(main, "_HEAVY_WORK_GATE", gate)
    try:
        yield main, gate
    finally:
        gate.executor.shutdown(wait=True)
        assert not list(Path(os.environ["BACKEND_PLUGINS_DIR"]).iterdir())
        assert {entry.name for entry in Path(os.environ["BACKEND_PLUGINS_SITE_DIR"]).iterdir()} == {".atlas-plugin-site"}


async def _wait_for_heavy_work_state(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.002)


async def _assert_heavy_work_auth_rejected(client, case, state):
    denied = await client.post(
        case.path, json=case.payload,
        headers={"Authorization": "Bearer wrong-owned-token"},
    )
    assert denied.status_code == 401
    assert not state.entered.is_set()


def _heavy_work_route_case(main, route):
    if route == "chunk":
        return SimpleNamespace(
            path="/api/chunk", payload={"text": "owned input"}, name="chunk_text",
            error=main.ChunkingUpstreamError,
            output={"strategy": "recursive", "chunk_size": 512, "overlap": 64,
                    "chunk_count": 0, "chunks": [], "metadata": {}},
        )
    return SimpleNamespace(
        path="/api/rag/evaluate", name="evaluate_rag_records",
        payload={"records": [{"question": "owned?", "answer": "owned", "contexts": []}],
                 "metrics": ["answer_relevancy"]},
        error=main.RagEvaluationUpstreamError,
        output={"metrics": [], "record_count": 0, "evaluator_model": "owned",
                "embeddings_model": None, "results": [], "metadata": {}},
    )


async def _await_heavy_work_response(client, case, ending, state):
    task = asyncio.create_task(client.post(case.path, json=case.payload))
    await _wait_for_heavy_work_state(state.entered.is_set)
    if ending == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        if ending in {"normal_error", "normal_success"}:
            state.release.set()
        response = await task
        expected = {"normal_error": 502, "normal_success": 200}.get(ending, 504)
        assert response.status_code == expected
        assert "owned-late-failure-canary" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["chunk", "evaluate"])
@pytest.mark.parametrize("ending", [
    "timeout", "cancel", "normal_error", "timeout_success", "normal_success",
])
async def test_stateless_routes_retrieve_late_heavy_work_outcomes(
    monkeypatch, _owned_heavy_work_main, route, ending,
):
    main, gate = _owned_heavy_work_main
    # Only timeout, timeout_success and cancel must cross the deadline; normal
    # endings get headroom so a loaded runner cannot turn their 200/502 into 504.
    deadline = "5" if ending.startswith("normal_") else "0.05"
    monkeypatch.setenv("BACKEND_HEAVY_WORK_TIMEOUT_SECONDS", deadline)
    case = _heavy_work_route_case(main, route)
    state = SimpleNamespace(entered=threading.Event(), release=threading.Event())
    contexts = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))

    def worker(_request):
        state.entered.set()
        assert state.release.wait(2)
        if ending not in {"timeout_success", "normal_success"}:
            raise case.error("owned-late-failure-canary")
        return case.output

    monkeypatch.setattr(main, case.name, worker)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://owned.invalid",
            headers={"Authorization": "Bearer owned-internal-token"},
        ) as client:
            await _assert_heavy_work_auth_rejected(client, case, state)
            await _await_heavy_work_response(client, case, ending, state)
            if ending not in {"normal_error", "normal_success"}:
                assert gate.in_flight == 1
            state.release.set()
            await _wait_for_heavy_work_state(lambda: gate.in_flight == 0)
            for _ in range(8):
                gc.collect()
                await asyncio.sleep(0.002)
            assert contexts == [], [
                (context.get("message"), type(context.get("exception")).__name__)
                for context in contexts
            ]
    finally:
        state.release.set()
        loop.set_exception_handler(previous_handler)
