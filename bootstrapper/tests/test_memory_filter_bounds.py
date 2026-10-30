from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "services/open-webui/extras/functions/memory_filter.py"


def _runtime_memory_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_module():
    pydantic = ModuleType("pydantic")
    pydantic.BaseModel = type("BaseModel", (), {})
    pydantic.Field = lambda default, **_kwargs: default
    previous = sys.modules.get("pydantic")
    sys.modules["pydantic"] = pydantic
    try:
        module = _runtime_memory_module(MODULE_PATH, "atlas_memory_filter")
    finally:
        if previous is None:
            sys.modules.pop("pydantic", None)
        else:
            sys.modules["pydantic"] = previous
    return module


def test_executor_rejects_work_beyond_worker_and_queue_capacity():
    module = _load_module()
    executor = module._BoundedDaemonExecutor(max_workers=1, max_queue=1)
    started = threading.Event()
    release = threading.Event()

    assert executor.submit(lambda: (started.set(), release.wait(1)))
    assert started.wait(1)
    assert executor.submit(lambda: None)
    assert not executor.submit(lambda: None)
    release.set()


def test_extraction_checks_http_status_and_redacts_error(monkeypatch, capsys):
    module = _load_module()

    class Response:
        def raise_for_status(self):
            raise RuntimeError("SENTINEL_BACKEND_SECRET")

    monkeypatch.setattr(module.requests, "post", lambda *_args, **_kwargs: Response())
    memory_filter = module.Filter()
    memory_filter._extract(
        "user-id",
        [{"role": "user", "content": "remember this"}] * 4,
    )

    error = capsys.readouterr().err
    assert "error_type=RuntimeError" in error
    assert "SENTINEL_BACKEND_SECRET" not in error


def test_message_text_fits_the_backend_memory_message_contract():
    # List content (attachments) and long pastes used to 422 the whole turn.
    text = _load_module()._message_text
    parts = [{"type": "text", "text": "see this"}, {"type": "image_url", "image_url": {"url": "x"}}]
    assert text(parts) == "see this"
    assert len(text("x" * 30000)) == 20000
    assert text([{"type": "image_url"}]) == ""
    assert text(None) == ""


@pytest.fixture
def memory_schema_boundary(monkeypatch):
    import asyncio
    import socket
    from types import SimpleNamespace
    import httpx2 as httpx
    from fastapi import FastAPI

    producer = _runtime_memory_module(MODULE_PATH, "bounded_runtime_memory_filter")
    consumer = _runtime_memory_module(ROOT / "services/backend/app/app/memory_models.py", "bounded_memory_schema")
    app = FastAPI()
    async def endpoint(request):
        return {"accepted": len(request.messages)}
    endpoint.__annotations__["request"] = consumer.MemoryExtractRequest
    app.post("/memory/extract")(endpoint)
    jobs, observations = [], []
    async def dispatch(payload):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://owned.invalid") as client:
            return await client.post("/memory/extract", json=payload)
    def post(url, **kwargs):
        assert url == "http://owned.invalid/memory/extract"
        assert kwargs["headers"] == {"Authorization": "Bearer owned-synthetic"}
        assert kwargs["timeout"] == 17
        assert kwargs["json"]["namespace"] == "default"
        response = asyncio.run(dispatch(kwargs["json"]))
        observations.append((response.status_code, kwargs["json"]))
        return response
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Memory batch fixture forbids external network")
    monkeypatch.setenv("BACKEND_OPEN_WEBUI_API_TOKEN", "owned-synthetic")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(producer.requests, "post", post)
    monkeypatch.setattr(producer, "_EXECUTOR", SimpleNamespace(submit=lambda job: jobs.append(job) or True))
    value = producer.Filter()
    return SimpleNamespace(filter=value, producer=producer, jobs=jobs, observations=observations)


def _submit_memory_messages(case, messages, minimum, user=True):
    import asyncio

    case.filter.valves = case.filter.Valves(
        min_messages=minimum, backend_url="http://owned.invalid", timeout=17,
    )
    body = {"messages": messages}
    identity = {"id": "00000000-0000-4000-8000-000000000001"} if user else None
    returned = asyncio.run(case.filter.outlet(body, identity))
    assert returned is body and returned["messages"] is messages
    for job in case.jobs:
        job()


@pytest.mark.parametrize("scenario", [(101, 101, 1, 100), (150, 150, 50, 100), (0, 150, 50, 100), (-2, 151, 51, 100), (4, 150, 146, 4), (100, 150, 50, 100)])
def test_memory_batch_respects_schema_without_changing_trigger(memory_schema_boundary, scenario):
    case = memory_schema_boundary
    minimum, count, first, expected_count = scenario
    messages = [{"role": "user", "content": f"private fact{n}"} for n in range(count)]
    _submit_memory_messages(case, messages, minimum)
    assert len(case.jobs) == 1 and len(case.observations) == 1
    status, payload = case.observations[0]
    assert status == 200
    assert len(payload["messages"]) == expected_count
    assert payload["messages"][0]["content"] == f"private fact{first}"
    assert payload["messages"][-1]["content"] == f"private fact{count - 1}"
    assert payload["user_id"] == "00000000-0000-4000-8000-000000000001"


@pytest.mark.parametrize("mode", ["below-trigger", "disabled", "missing-user", "empty-content", "queue-full"])
def test_memory_batch_skips_preserve_existing_no_request_behavior(memory_schema_boundary, mode, monkeypatch, capsys):
    from types import SimpleNamespace

    case = memory_schema_boundary
    messages = [{"role": "user", "content": " " if mode == "empty-content" else "private fact"} for _ in range(100 if mode == "below-trigger" else 101)]
    if mode == "queue-full":
        monkeypatch.setattr(case.producer, "_EXECUTOR", SimpleNamespace(submit=lambda job: False))
    if mode == "disabled":
        _submit_disabled_memory(case, messages)
    else:
        _submit_memory_messages(case, messages, 101, mode != "missing-user")
    assert not case.observations
    if mode == "queue-full":
        assert capsys.readouterr().err == "memory_filter: extraction queue full; request skipped\n"


def _submit_disabled_memory(case, messages):
    import asyncio

    case.filter.valves.enabled = False
    body = {"messages": messages}
    assert asyncio.run(case.filter.outlet(body, {"id": "private-user"})) is body
    assert not case.jobs


def _memory_part_messages(sparse):
    messages = [{"role": "assistant", "content": [{"type": "text", "text": f" note{n} "}, {"type": "image_url", "image_url": {"url": "owned://image"}}]} for n in range(150)]
    if sparse:
        for message in messages[50:]:
            message["content"] = [{"type": "image_url"}]
    else:
        messages[-1]["content"] = [{"type": "text", "text": "x" * 30000}]
    return messages


@pytest.mark.parametrize("sparse", [False, True])
def test_memory_batch_caps_after_parts_filtering_and_keeps_text_limit(memory_schema_boundary, sparse):
    case = memory_schema_boundary
    _submit_memory_messages(case, _memory_part_messages(sparse), 150)
    assert len(case.observations) == 1
    status, payload = case.observations[0]
    assert status == 200
    assert len(payload["messages"]) == (50 if sparse else 100)
    assert payload["messages"][0] == {"role": "assistant", "content": "note0" if sparse else "note50"}
    assert payload["messages"][-1]["content"] == ("note49" if sparse else "x" * 20000)
