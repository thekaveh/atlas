from __future__ import annotations

import asyncio
import importlib.util
import logging
from pathlib import Path

import httpx
import pytest


def _response_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_queue_prompt_maps_transport_failure_to_typed_unavailable(monkeypatch, caplog):
    import comfyui_client

    def handler(request):
        raise httpx.ConnectError("SENTINEL_COMFY_TRANSPORT_SECRET", request=request)

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(handler)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(
            comfyui_client.ComfyUIUnavailableError,
            match="ComfyUI is unavailable",
        ):
            await client.queue_prompt({"1": {"class_type": "SaveImage"}})
    await client.client.aclose()

    assert "SENTINEL_COMFY_TRANSPORT_SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_rejected_workflow_is_a_client_error_without_upstream_detail():
    # ComfyUI's 400 (bad node graph) was reported as a 502 outage.
    import comfyui_client

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(
        lambda _request: httpx.Response(400, json={"error": "SENTINEL_NODE_ERRORS"})
    )
    with pytest.raises(comfyui_client.ComfyUIWorkflowRejectedError):
        await client.queue_prompt({"1": {"class_type": "SaveImage"}})
    await client.client.aclose()


def test_rejected_workflow_maps_to_400(fastapi_client):
    import comfyui_client
    import main

    mapped = main._comfyui_gateway_error(
        comfyui_client.ComfyUIWorkflowRejectedError("ComfyUI rejected the workflow")
    )
    assert mapped.status_code == 400
    assert "SENTINEL" not in mapped.detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, json={"error": "SENTINEL_COMFY_RESPONSE_SECRET"}),
        httpx.Response(200, content=b"not-json"),
    ],
    ids=["non-2xx", "invalid-json"],
)
async def test_queue_prompt_maps_bad_upstream_response_to_typed_gateway_error(
    response, caplog
):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(lambda _request: response)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(
            comfyui_client.ComfyUIResponseError,
            match="ComfyUI returned an invalid response",
        ):
            await client.queue_prompt({"1": {"class_type": "SaveImage"}})
    await client.client.aclose()

    assert "SENTINEL_COMFY_RESPONSE_SECRET" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"prompt_id": None},
        {"prompt_id": 7},
        {"prompt_id": True},
        {"prompt_id": ""},
        {"prompt_id": "   "},
    ],
    ids=["missing", "null", "integer", "boolean", "empty", "whitespace"],
)
async def test_queue_prompt_requires_nonempty_string_prompt_id(payload):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(
        lambda _request: httpx.Response(200, json=payload)
    )

    with pytest.raises(
        comfyui_client.ComfyUIResponseError,
        match="ComfyUI returned an invalid response",
    ):
        await client.queue_prompt({"1": {"class_type": "SaveImage"}})
    await client.client.aclose()


@pytest.mark.asyncio
async def test_queue_prompt_preserves_valid_prompt_id_verbatim():
    import comfyui_client

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(
        lambda _request: httpx.Response(
            200, json={"prompt_id": "prompt-7", "number": 11}
        )
    )

    result = await client.queue_prompt({"1": {"class_type": "SaveImage"}})
    await client.client.aclose()

    assert result["success"] is True
    assert result["prompt_id"] == "prompt-7"
    assert result["number"] == 11


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "arguments"),
    [
        ("get_models", ()),
        ("get_history", ("prompt-1",)),
        ("get_queue_status", ()),
        ("cancel_prompt", ("prompt-1",)),
    ],
)
async def test_legacy_client_reads_never_collapse_transport_failure(
    method_name, arguments
):
    import comfyui_client

    def handler(request):
        raise httpx.ReadTimeout("SENTINEL_COMFY_TIMEOUT_SECRET", request=request)

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(handler)

    expected = (
        comfyui_client.ComfyUIHistoryUnavailableError
        if method_name == "get_history"
        else comfyui_client.ComfyUIUnavailableError
    )
    with pytest.raises(expected):
        await getattr(client, method_name)(*arguments)
    await client.client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "arguments", "payload"),
    [
        ("get_models", (), []),
        ("get_history", ("prompt-1",), []),
        ("get_queue_status", (), []),
        ("cancel_prompt", ("prompt-1",), {}),
    ],
)
async def test_legacy_client_reads_never_collapse_malformed_json_shape(
    method_name, arguments, payload
):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(
        lambda _request: httpx.Response(200, json=payload)
    )

    with pytest.raises(comfyui_client.ComfyUIResponseError):
        await getattr(client, method_name)(*arguments)
    await client.client.aclose()


@pytest.mark.asyncio
async def test_cancel_prompt_preserves_valid_false_result():
    import comfyui_client

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(
        lambda _request: httpx.Response(200, json={"cancelled": False})
    )

    assert await client.cancel_prompt("prompt-1") is False
    await client.client.aclose()


@pytest.mark.parametrize("field", ["negative_prompt", "checkpoint"])
def test_generate_rejects_explicit_null_for_nonnullable_defaults(
    fastapi_client, monkeypatch, field
):
    import main

    monkeypatch.setenv("FAL_SOURCE", "disabled")

    class UnexpectedClient:
        def __init__(self):
            raise AssertionError("request validation must run before ComfyUI access")

    monkeypatch.setattr(main, "ComfyUIClient", UnexpectedClient)
    response = fastapi_client.post(
        "/comfyui/generate",
        json={"prompt": "blue observatory", field: None},
    )

    assert response.status_code == 422


def test_generate_omitted_defaults_are_concrete_and_seed_null_remains_optional(
    fastapi_client, monkeypatch
):
    import main

    monkeypatch.setenv("FAL_SOURCE", "disabled")
    captured = {}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def generate_simple_image(self, **kwargs):
            captured.update(kwargs)
            return {
                "success": True,
                "prompt_id": "prompt-defaults",
                "client_id": "client-defaults",
                "parameters": kwargs,
            }

    monkeypatch.setattr(main, "ComfyUIClient", Client)
    response = fastapi_client.post(
        "/comfyui/generate",
        json={
            "prompt": "blue observatory",
            "seed": None,
            "wait_for_completion": False,
        },
    )

    assert response.status_code == 200
    assert captured["negative_prompt"] == ""
    assert captured["checkpoint"] == "v1-5-pruned-emaonly.safetensors"
    assert captured["seed"] is None


@pytest.mark.parametrize("route", ["/comfyui/generate", "/comfyui/workflow"])
def test_legacy_polling_history_outage_returns_503(
    fastapi_client, monkeypatch, route
):
    import comfyui_client
    import main

    monkeypatch.setenv("FAL_SOURCE", "disabled")

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def generate_simple_image(self, **kwargs):
            return {
                "success": True,
                "prompt_id": "prompt-poll",
                "client_id": "client-poll",
                "parameters": kwargs,
            }

        async def queue_prompt(self, _workflow):
            return {
                "success": True,
                "prompt_id": "prompt-poll",
                "client_id": "client-poll",
            }

        async def wait_for_completion(self, *_args, **_kwargs):
            raise comfyui_client.ComfyUIHistoryUnavailableError(
                "SENTINEL_COMFY_POLL_SECRET"
            )

    monkeypatch.setattr(main, "ComfyUIClient", Client)
    payload = (
        {"prompt": "blue observatory"}
        if route == "/comfyui/generate"
        else {"workflow": {"1": {"class_type": "SaveImage"}}}
    )

    response = fastapi_client.post(route, json=payload)

    assert response.status_code == 503
    assert response.json() == {"detail": "ComfyUI is unavailable"}
    assert "SENTINEL_COMFY_POLL_SECRET" not in response.text


@pytest.mark.parametrize("route", ["/comfyui/generate", "/comfyui/workflow"])
def test_legacy_timeout_cancels_the_prompt_and_returns_its_id(
    fastapi_client, monkeypatch, route
):
    """#676 for the legacy routes: a prompt that outlives timeout_seconds is
    cancelled, and the 504 names it so the caller can poll it, not re-queue."""
    import asyncio

    import main

    cancelled = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def generate_simple_image(self, **kwargs):
            return {"success": True, "prompt_id": "prompt-slow",
                    "client_id": "c", "parameters": kwargs}

        async def queue_prompt(self, _workflow):
            return {"success": True, "prompt_id": "prompt-slow", "client_id": "c"}

        async def wait_for_completion(self, *_args, **_kwargs):
            raise asyncio.TimeoutError

        async def cancel_prompt(self, prompt_id):
            cancelled.append(prompt_id)
            return True

    monkeypatch.setattr(main, "ComfyUIClient", Client)
    payload = (
        {"prompt": "blue observatory"}
        if route == "/comfyui/generate"
        else {"workflow": {"1": {"class_type": "SaveImage"}}}
    )

    response = fastapi_client.post(route, json=payload)

    assert response.status_code == 504
    assert response.json()["detail"]["prompt_id"] == "prompt-slow"
    assert cancelled == ["prompt-slow"]


@pytest.mark.parametrize(
    ("route", "method_name", "http_method"),
    [
        ("/comfyui/models", "get_models", "get"),
        ("/comfyui/generate", "generate_simple_image", "post"),
        ("/comfyui/workflow", "queue_prompt", "post"),
        ("/comfyui/history/prompt-1", "get_history", "get"),
        ("/comfyui/queue", "get_queue_status", "get"),
        ("/comfyui/cancel/prompt-1", "cancel_prompt", "post"),
    ],
)
@pytest.mark.parametrize(
    ("exception_name", "expected_status"),
    [
        ("ComfyUIUnavailableError", 503),
        ("ComfyUIResponseError", 502),
    ],
)
def test_legacy_routes_map_typed_comfyui_failures_truthfully(
    fastapi_client,
    monkeypatch,
    route,
    method_name,
    http_method,
    exception_name,
    expected_status,
):
    import comfyui_client
    import main

    monkeypatch.setenv("FAL_SOURCE", "disabled")
    exception_type = getattr(comfyui_client, exception_name)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    async def fail(*_args, **_kwargs):
        raise exception_type("SENTINEL_COMFY_ROUTE_SECRET")

    setattr(Client, method_name, fail)
    monkeypatch.setattr(main, "ComfyUIClient", Client)
    if route == "/comfyui/generate":
        kwargs = {"json": {"prompt": "blue observatory"}}
    elif route == "/comfyui/workflow":
        kwargs = {"json": {"workflow": {"1": {"class_type": "SaveImage"}}}}
    else:
        kwargs = {}

    response = getattr(fastapi_client, http_method)(route, **kwargs)

    assert response.status_code == expected_status
    assert "SENTINEL_COMFY_ROUTE_SECRET" not in response.text
    assert response.json()["detail"] == (
        "ComfyUI is unavailable"
        if expected_status == 503
        else "ComfyUI returned an invalid response"
    )


@pytest.mark.asyncio
async def test_queue_prompt_redacts_upstream_failure(monkeypatch, caplog):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()

    async def fail(*_args, **_kwargs):
        request = httpx.Request("POST", "http://comfyui:18188/prompt")
        raise httpx.ConnectError("SENTINEL_COMFY_SECRET", request=request)

    monkeypatch.setattr(client.client, "post", fail)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(comfyui_client.ComfyUIUnavailableError):
            await client.queue_prompt({})
    await client.client.aclose()

    assert "SENTINEL_COMFY_SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_history_outage_is_distinct_from_empty_history(monkeypatch, caplog):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()

    async def fail(*_args, **_kwargs):
        request = httpx.Request("GET", "http://comfyui:18188/history/prompt-1")
        raise httpx.ConnectError("SENTINEL_HISTORY_SECRET", request=request)

    monkeypatch.setattr(client.client, "get", fail)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(
            comfyui_client.ComfyUIHistoryUnavailableError,
            match="ComfyUI history is unavailable",
        ):
            await client.get_history("prompt-1")
    await client.client.aclose()

    assert "SENTINEL_HISTORY_SECRET" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        None,
        "record",
        [],
        {"outputs": None},
        {"outputs": []},
        {"status": 7},
        {"status": []},
        {"status": {"status_str": None}},
        {"status": {"status_str": 7}},
    ],
)
async def test_prompt_history_read_rejects_malformed_target(target):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(
        lambda _request: httpx.Response(200, json={"prompt-1": target})
    )

    with pytest.raises(
        comfyui_client.ComfyUIResponseError,
        match="ComfyUI returned an invalid response",
    ):
        await client.get_history("prompt-1")
    await client.client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"prompt-1": {}},
        {"prompt-1": {"outputs": {}, "status": {"status_str": "success"}}},
    ],
)
async def test_prompt_history_read_preserves_absent_and_valid_targets(payload):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(
        lambda _request: httpx.Response(200, json=payload)
    )

    assert await client.get_history("prompt-1") == payload
    await client.client.aclose()


@pytest.mark.asyncio
async def test_generic_history_read_preserves_unselected_records():
    import comfyui_client

    payload = {"another-prompt": None}
    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(
        lambda _request: httpx.Response(200, json=payload)
    )

    assert await client.get_history() == payload
    await client.client.aclose()


@pytest.mark.parametrize(
    "target",
    [
        None,
        "record",
        [],
        {"outputs": None},
        {"outputs": []},
        {"status": 7},
        {"status": []},
        {"status": {"status_str": None}},
        {"status": {"status_str": 7}},
    ],
)
def test_prompt_history_route_maps_malformed_target_to_502(
    fastapi_client, monkeypatch, target
):
    import comfyui_client
    import main

    class Client(comfyui_client.ComfyUIClient):
        def __init__(self):
            self.base_url = "http://comfyui:18188"
            self.max_image_bytes = 1024
            self.client = _response_client(
                lambda _request: httpx.Response(200, json={"prompt-1": target})
            )

    monkeypatch.setattr(main, "ComfyUIClient", Client)
    response = fastapi_client.get("/comfyui/history/prompt-1")

    assert response.status_code == 502
    assert response.json() == {"detail": "ComfyUI returned an invalid response"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"prompt-1": {}},
        {"prompt-1": {"outputs": {}, "status": {"status_str": "success"}}},
    ],
)
def test_prompt_history_route_preserves_absent_and_valid_targets(
    fastapi_client, monkeypatch, payload
):
    import comfyui_client
    import main

    class Client(comfyui_client.ComfyUIClient):
        def __init__(self):
            self.base_url = "http://comfyui:18188"
            self.max_image_bytes = 1024
            self.client = _response_client(
                lambda _request: httpx.Response(200, json=payload)
            )

    monkeypatch.setattr(main, "ComfyUIClient", Client)
    response = fastapi_client.get("/comfyui/history/prompt-1")

    assert response.status_code == 200
    assert response.json() == {"success": True, "history": payload}


@pytest.mark.asyncio
async def test_completion_history_outage_fails_without_polling(monkeypatch):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()

    async def fail(_prompt_id):
        raise comfyui_client.ComfyUIHistoryUnavailableError(
            "ComfyUI history is unavailable"
        )

    monkeypatch.setattr(client, "get_history", fail)
    with pytest.raises(
        comfyui_client.ComfyUIHistoryUnavailableError,
        match="ComfyUI history is unavailable",
    ):
        await client.wait_for_completion("prompt-1", timeout=300)
    await client.client.aclose()


@pytest.mark.asyncio
async def test_completion_deadline_bounds_slow_history(monkeypatch):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()

    async def slow(_prompt_id):
        await asyncio.sleep(10)
        return {}

    monkeypatch.setattr(client, "get_history", slow)
    with pytest.raises(asyncio.TimeoutError):
        await client.wait_for_completion("prompt-1", timeout=0.01)
    await client.client.aclose()


@pytest.mark.asyncio
async def test_completion_internal_deadline_propagates_timeout(monkeypatch):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()

    async def pending(_prompt_id):
        return {}

    monkeypatch.setattr(client, "get_history", pending)
    with pytest.raises(asyncio.TimeoutError):
        await client.wait_for_completion("prompt-1", timeout=0.001)
    await client.client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        None,
        "record",
        [],
        {"outputs": None},
        {"outputs": []},
        {"status": 7},
        {"status": []},
        {"status": {"status_str": None}},
        {"status": {"status_str": 7}},
        {"outputs": {}, "status": []},
    ],
    ids=[
        "record-null",
        "record-string",
        "record-list",
        "outputs-null",
        "outputs-list",
        "status-scalar",
        "status-list",
        "status-str-null",
        "status-str-scalar",
        "success-with-bad-status",
    ],
)
async def test_completion_rejects_malformed_target_history(monkeypatch, target):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()

    async def malformed(_prompt_id):
        return {"prompt-1": target}

    monkeypatch.setattr(client, "get_history", malformed)
    with pytest.raises(
        comfyui_client.ComfyUIResponseError,
        match="ComfyUI returned an invalid response",
    ):
        await client.wait_for_completion("prompt-1", timeout=1)
    await client.client.aclose()


@pytest.mark.asyncio
async def test_completion_accepts_valid_success_history(monkeypatch):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()

    async def completed(_prompt_id):
        return {"prompt-1": {"outputs": {"7": {"images": []}}}}

    monkeypatch.setattr(client, "get_history", completed)
    result = await client.wait_for_completion("prompt-1", timeout=1)
    await client.client.aclose()

    assert result == {
        "success": True,
        "outputs": {"7": {"images": []}},
        "status": {},
        "prompt_id": "prompt-1",
    }


@pytest.mark.asyncio
async def test_completion_accepts_valid_error_history(monkeypatch):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()

    async def failed(_prompt_id):
        return {"prompt-1": {"status": {"status_str": "error"}}}

    monkeypatch.setattr(client, "get_history", failed)
    result = await client.wait_for_completion("prompt-1", timeout=1)
    await client.client.aclose()

    assert result == {
        "success": False,
        "error": "ComfyUI generation failed",
        "prompt_id": "prompt-1",
    }


@pytest.mark.asyncio
async def test_completion_error_status_wins_over_partial_outputs(monkeypatch):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()

    async def failed_with_outputs(_prompt_id):
        return {
            "prompt-1": {
                "outputs": {"partial": {"images": []}},
                "status": {"status_str": "error"},
            }
        }

    monkeypatch.setattr(client, "get_history", failed_with_outputs)
    result = await client.wait_for_completion("prompt-1", timeout=1)
    await client.client.aclose()

    assert result == {
        "success": False,
        "error": "ComfyUI generation failed",
        "prompt_id": "prompt-1",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_history",
    [
        {},
        {"prompt-1": {}},
        {"prompt-1": {"status": {"status_str": "running"}}},
    ],
    ids=["absent", "pending-without-status", "pending-running"],
)
async def test_completion_continues_after_valid_pending_or_absent_history(
    monkeypatch, first_history
):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()
    histories = iter(
        [
            first_history,
            {"prompt-1": {"outputs": {"7": {"images": []}}}},
        ]
    )

    async def next_history(_prompt_id):
        return next(histories)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(client, "get_history", next_history)
    monkeypatch.setattr(comfyui_client.asyncio, "sleep", no_sleep)
    result = await client.wait_for_completion("prompt-1", timeout=1)
    await client.client.aclose()

    assert result["success"] is True


@pytest.mark.parametrize("route", ["/comfyui/generate", "/comfyui/workflow"])
def test_legacy_polling_malformed_history_returns_truthful_502(
    fastapi_client, monkeypatch, route
):
    import comfyui_client
    import main

    monkeypatch.setenv("FAL_SOURCE", "disabled")

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def generate_simple_image(self, **kwargs):
            return {
                "success": True,
                "prompt_id": "prompt-malformed",
                "client_id": "client-malformed",
                "parameters": kwargs,
            }

        async def queue_prompt(self, _workflow):
            return {
                "success": True,
                "prompt_id": "prompt-malformed",
                "client_id": "client-malformed",
            }

        async def wait_for_completion(self, *_args, **_kwargs):
            raise comfyui_client.ComfyUIResponseError(
                "SENTINEL_COMFY_MALFORMED_HISTORY"
            )

    monkeypatch.setattr(main, "ComfyUIClient", Client)
    payload = (
        {"prompt": "blue observatory"}
        if route == "/comfyui/generate"
        else {"workflow": {"1": {"class_type": "SaveImage"}}}
    )

    response = fastapi_client.post(route, json=payload)

    assert response.status_code == 502
    assert response.json() == {"detail": "ComfyUI returned an invalid response"}
    assert "SENTINEL_COMFY_MALFORMED_HISTORY" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [True, False])
async def test_cancel_prompt_uses_targeted_job_endpoint(cancelled):
    import comfyui_client

    calls = []

    def handler(request):
        calls.append((request.method, request.url.raw_path))
        return httpx.Response(200, json={"cancelled": cancelled})

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    assert await client.cancel_prompt("prompt/one") is cancelled
    assert calls == [("POST", b"/api/jobs/prompt%2Fone/cancel")]
    await client.client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (500, {"cancelled": False}),
        (200, None),
        (200, {}),
        (200, {"cancelled": 1}),
        (200, {"cancelled": "true"}),
    ],
)
async def test_cancel_prompt_rejects_failed_or_malformed_response(status, payload):
    import comfyui_client

    def handler(request):
        if payload is None:
            return httpx.Response(status, content=b"invalid-json")
        return httpx.Response(status, json=payload)

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(comfyui_client.ComfyUIResponseError):
        await client.cancel_prompt("prompt")
    await client.client.aclose()


def test_open_webui_tool_propagates_deadline_and_returns_artifact(monkeypatch):
    tool_path = (
        Path(__file__).resolve().parents[4]
        / "open-webui/extras/tools/comfyui_image_generation_tool.py"
    )
    spec = importlib.util.spec_from_file_location("comfyui_image_tool_test", tool_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured = []

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setattr(
        module.requests,
        "get",
        lambda *_args, **_kwargs: Response({"status": "healthy"}),
    )

    def post(*_args, **kwargs):
        captured.append(kwargs)
        return Response(
            {
                "success": True,
                "prompt_id": "prompt-1",
                "data": {
                    "outputs": {
                        "7": {"images": [{"filename": "atlas-output.png"}]}
                    },
                    "parameters": {},
                },
            }
        )

    monkeypatch.setattr(module.requests, "post", post)
    tool = module.Tools()
    tool.valves.timeout = 321

    # Tool methods are async (blocking work runs in asyncio.to_thread).
    result = asyncio.run(tool.generate_image("blue orbital archive", cfg=0.0))
    asyncio.run(tool.generate_image("blue orbital archive", cfg=30.0))

    assert captured[0]["json"]["timeout_seconds"] == 321
    assert captured[0]["timeout"] == 326
    assert captured[0]["json"]["cfg"] == 0.0
    assert captured[1]["json"]["cfg"] == 30.0
    assert "atlas-output.png" in result
    assert "base64" not in result.lower()


def test_open_webui_tool_returns_fal_artifact_url(monkeypatch):
    tool_path = (
        Path(__file__).resolve().parents[4]
        / "open-webui/extras/tools/comfyui_image_generation_tool.py"
    )
    spec = importlib.util.spec_from_file_location("comfyui_fal_tool_test", tool_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setattr(
        module.requests,
        "get",
        lambda *_args, **_kwargs: Response(
            {"service": "fal", "status": "configured"}
        ),
    )
    monkeypatch.setattr(
        module.requests,
        "post",
        lambda *_args, **_kwargs: Response(
            {
                "success": True,
                "prompt_id": "fal-1",
                "data": {
                    "provider": "fal",
                    "outputs": {
                        "images": [
                            {"url": "https://cdn.example/fal-output.png"}
                        ]
                    },
                    "parameters": {},
                },
            }
        ),
    )

    result = asyncio.run(module.Tools().generate_image("blue orbital archive"))

    assert "1 image(s) created" in result
    assert "https://cdn.example/fal-output.png" in result


@pytest.mark.parametrize(
    "health",
    [
        {"service": "fal", "status": "unknown"},
        {"service": "fal", "status": "unhealthy"},
        {"service": "media", "status": "disabled"},
        {"service": "comfyui", "status": "configured"},
        {"status": "configured"},
    ],
)
def test_open_webui_tool_rejects_nonready_or_nonfal_configured_health(
    monkeypatch, health
):
    tool_path = (
        Path(__file__).resolve().parents[4]
        / "open-webui/extras/tools/comfyui_image_generation_tool.py"
    )
    spec = importlib.util.spec_from_file_location(
        "comfyui_nonready_tool_test", tool_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Response:
        status_code = 200

        def json(self):
            return health

    monkeypatch.setattr(module.requests, "get", lambda *_args, **_kwargs: Response())

    def unexpected_post(*_args, **_kwargs):
        raise AssertionError("generation must not start for non-ready health")

    monkeypatch.setattr(module.requests, "post", unexpected_post)

    result = asyncio.run(module.Tools().generate_image("blue orbital archive"))

    assert result == "❌ ComfyUI service is unavailable. Please try again later."


def test_open_webui_tool_renders_fal_configured_status_honestly(monkeypatch):
    tool_path = (
        Path(__file__).resolve().parents[4]
        / "open-webui/extras/tools/comfyui_image_generation_tool.py"
    )
    spec = importlib.util.spec_from_file_location(
        "comfyui_configured_status_tool_test", tool_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    responses = iter(
        [
            Response({"service": "fal", "status": "configured"}),
            Response({"success": False}),
        ]
    )
    monkeypatch.setattr(
        module.requests, "get", lambda *_args, **_kwargs: next(responses)
    )

    result = asyncio.run(module.Tools().check_comfyui_status())

    assert "⚠️ **Health Check:** configured" in result
    assert "Healthy" not in result


def test_comfyui_tool_does_not_embed_image_data_or_backend_url():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[4]
        / "open-webui/extras/tools/comfyui_image_generation_tool.py"
    ).read_text(encoding="utf-8")

    assert "base64.b64encode" not in source
    assert "Backend URL:" not in source
    assert "result.get('error'" not in source


class _StreamResponse:
    def __init__(self, chunks, content_length=None):
        self._chunks = chunks
        self.headers = {}
        if content_length is not None:
            self.headers["content-length"] = str(content_length)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        _StreamResponse([], content_length=5),
        _StreamResponse([b"123", b"45"]),
    ],
)
async def test_image_download_rejects_declared_and_streamed_oversize(response):
    import comfyui_client

    class Client:
        def stream(self, *_args, **_kwargs):
            return response

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = Client()
    client.max_image_bytes = 4

    with pytest.raises(ValueError, match="byte limit"):
        await client.get_image_data("large.png")


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
async def test_image_download_maps_transport_failures_to_typed_unavailable(
    error_type, caplog
):
    import comfyui_client

    def handler(request):
        raise error_type("SENTINEL_COMFY_IMAGE_TRANSPORT", request=request)

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(handler)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(
            comfyui_client.ComfyUIUnavailableError,
            match="ComfyUI is unavailable",
        ):
            await client.get_image_data("out.png")
    await client.client.aclose()

    assert "SENTINEL_COMFY_IMAGE_TRANSPORT" not in caplog.text


@pytest.mark.asyncio
async def test_image_download_maps_non_404_http_failure_to_response_error():
    import comfyui_client

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(
        lambda _request: httpx.Response(500, content=b"SENTINEL_IMAGE_BODY")
    )

    with pytest.raises(
        comfyui_client.ComfyUIResponseError,
        match="ComfyUI returned an invalid response",
    ):
        await client.get_image_data("out.png")
    await client.client.aclose()


@pytest.mark.asyncio
async def test_image_download_preserves_upstream_404_for_route_mapping():
    import comfyui_client

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(
        lambda _request: httpx.Response(404, content=b"missing")
    )

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.get_image_data("missing.png")
    await client.client.aclose()

    assert exc_info.value.response.status_code == 404


@pytest.mark.asyncio
async def test_image_download_preserves_valid_opaque_bytes():
    import comfyui_client

    opaque = b"\x00\xffPNG\r\nopaque"
    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(
        lambda _request: httpx.Response(200, content=opaque)
    )

    assert await client.get_image_data("out.png") == opaque
    await client.client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("content_length", ["not-an-integer", "-1"])
async def test_image_download_rejects_malformed_content_length_as_upstream_error(
    content_length,
):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = _response_client(
        lambda _request: httpx.Response(
            200,
            headers={"content-length": content_length},
            content=b"opaque",
        )
    )

    with pytest.raises(comfyui_client.ComfyUIResponseError):
        await client.get_image_data("out.png")
    await client.client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("content_length", ["", "+1", " 1 ", "1.0", "\u0661"])
async def test_image_download_requires_ascii_decimal_content_length(content_length):
    import comfyui_client

    class Client:
        def stream(self, *_args, **_kwargs):
            return _StreamResponse([b"x"], content_length=content_length)

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = Client()

    with pytest.raises(comfyui_client.ComfyUIResponseError):
        await client.get_image_data("out.png")


@pytest.mark.asyncio
async def test_image_download_allows_ascii_decimal_content_length_with_leading_zeroes():
    import comfyui_client

    class Client:
        def stream(self, *_args, **_kwargs):
            return _StreamResponse([b"x"], content_length="0001")

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = Client()

    assert await client.get_image_data("out.png") == b"x"


@pytest.mark.asyncio
async def test_image_download_rejects_unrepresentable_decimal_content_length():
    import comfyui_client

    class Client:
        def stream(self, *_args, **_kwargs):
            return _StreamResponse([b"x"], content_length="9" * 5000)

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = Client()

    with pytest.raises(comfyui_client.ComfyUIResponseError):
        await client.get_image_data("out.png")


@pytest.mark.parametrize(
    ("exception_name", "expected_status", "expected_detail"),
    [
        ("ComfyUIUnavailableError", 503, "ComfyUI is unavailable"),
        (
            "ComfyUIResponseError",
            502,
            "ComfyUI returned an invalid response",
        ),
    ],
)
def test_image_route_maps_typed_binary_upstream_failures(
    fastapi_client,
    monkeypatch,
    exception_name,
    expected_status,
    expected_detail,
):
    import comfyui_client
    import main

    exception_type = getattr(comfyui_client, exception_name)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get_image_data(self, *_args):
            raise exception_type("SENTINEL_COMFY_IMAGE_ROUTE")

    monkeypatch.setattr(main, "ComfyUIClient", Client)
    response = fastapi_client.get("/comfyui/image/out.png")

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert "SENTINEL_COMFY_IMAGE_ROUTE" not in response.text


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
def test_image_route_maps_raw_transport_failures_to_503(
    fastapi_client, monkeypatch, error_type
):
    import main

    request = httpx.Request("GET", "http://comfyui:18188/view")
    transport_error = error_type(
        "SENTINEL_COMFY_IMAGE_RAW_TRANSPORT", request=request
    )

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get_image_data(self, *_args):
            raise transport_error

    monkeypatch.setattr(main, "ComfyUIClient", Client)
    response = fastapi_client.get("/comfyui/image/out.png")

    assert response.status_code == 503
    assert response.json() == {"detail": "ComfyUI is unavailable"}
    assert "SENTINEL_COMFY_IMAGE_RAW_TRANSPORT" not in response.text


def test_image_route_maps_raw_non_404_http_failure_to_502(
    fastapi_client, monkeypatch
):
    import main

    request = httpx.Request("GET", "http://comfyui:18188/view")
    upstream = httpx.Response(500, request=request)
    response_error = httpx.HTTPStatusError(
        "SENTINEL_COMFY_IMAGE_500",
        request=request,
        response=upstream,
    )

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get_image_data(self, *_args):
            raise response_error

    monkeypatch.setattr(main, "ComfyUIClient", Client)
    response = fastapi_client.get("/comfyui/image/out.png")

    assert response.status_code == 502
    assert response.json() == {"detail": "ComfyUI returned an invalid response"}
    assert "SENTINEL_COMFY_IMAGE_500" not in response.text


def test_image_route_preserves_exact_upstream_404(fastapi_client, monkeypatch):
    import main

    request = httpx.Request("GET", "http://comfyui:18188/view")
    upstream = httpx.Response(404, request=request)
    not_found = httpx.HTTPStatusError(
        "SENTINEL_COMFY_IMAGE_404",
        request=request,
        response=upstream,
    )

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get_image_data(self, *_args):
            raise not_found

    monkeypatch.setattr(main, "ComfyUIClient", Client)
    response = fastapi_client.get("/comfyui/image/missing.png")

    assert response.status_code == 404
    assert response.json() == {"detail": "Image missing.png not found"}
    assert "SENTINEL_COMFY_IMAGE_404" not in response.text


def test_image_route_preserves_valid_opaque_bytes(fastapi_client, monkeypatch):
    import main

    opaque = b"\x00\xffPNG\r\nopaque"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get_image_data(self, *_args):
            return opaque

    monkeypatch.setattr(main, "ComfyUIClient", Client)
    response = fastapi_client.get("/comfyui/image/out.png")

    assert response.status_code == 200
    assert response.content == opaque


def test_image_route_preserves_byte_limit_as_client_error(fastapi_client, monkeypatch):
    import comfyui_client
    import main

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get_image_data(self, *_args):
            raise comfyui_client.ComfyUIImageTooLargeError(
                "SENTINEL_COMFY_IMAGE_LIMIT"
            )

    monkeypatch.setattr(main, "ComfyUIClient", Client)
    response = fastapi_client.get("/comfyui/image/out.png")

    assert response.status_code == 400
    assert response.json() == {
        "detail": "ComfyUI image exceeds configured byte limit"
    }
    assert "SENTINEL_COMFY_IMAGE_LIMIT" not in response.text


def test_history_and_workflow_ids_are_path_encoded(monkeypatch):
    """A `%3F` in the path parameter must not become an upstream query."""
    import asyncio

    import comfyui_client
    import n8n_client

    seen = []

    class _Http:
        async def get(self, url, **_kwargs):
            seen.append(url)
            raise RuntimeError("stop")

    comfy = comfyui_client.ComfyUIClient.__new__(comfyui_client.ComfyUIClient)
    comfy.base_url, comfy.client = "http://comfyui:18188", _Http()
    n8n = n8n_client.N8nClient.__new__(n8n_client.N8nClient)
    n8n.base_url, n8n._client, n8n.headers = "http://n8n:5678", _Http(), {}
    for call in (lambda: comfy.get_history("?max_items=1000"),
                 lambda: n8n.get_workflow("?limit=250")):
        try:
            asyncio.run(call())
        except Exception:  # noqa: BLE001 - only the URL matters here
            pass
    assert seen == [
        "http://comfyui:18188/history/%3Fmax_items%3D1000",
        "http://n8n:5678/api/v1/workflows/%3Flimit%3D250",
    ]


# Media provider polling and artifact typing (kept here: the provider test
# module is at its size ceiling).
from tests.test_comfyui_media_provider import _run as _provider_run  # noqa: E402


def test_poll_rereads_history_when_a_job_finishes_between_the_two_reads():
    """ComfyUI moves a finished job from the queue into history in one step;
    finishing between the history and queue reads used to be reported as a
    permanent "failed" with its image never surfaced."""
    history_reads = []

    def handler(request):
        if "/history/" in request.url.path:
            history_reads.append(1)
            if len(history_reads) == 1:
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"pid-race": {
                "outputs": {"9": {"images": [
                    {"filename": "a.png", "subfolder": "", "type": "output"}
                ]}},
                "status": {"status_str": "success", "completed": True},
            }})
        return httpx.Response(200, json={"queue_running": [], "queue_pending": []})

    async def body(client):
        payload = await client.get_media_operation(operation_id="pid-race", modality="image")
        assert payload["status"] == "succeeded"
        assert len(history_reads) == 2

    _provider_run(handler, body)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [("a.png", "image/png"), ("a.JPG", "image/jpeg"), ("a.webp", "image/webp"),
     ("a.gif", "image/gif"), ("clip.mp4", "video/mp4"), ("weights.unknownext", "application/octet-stream"),
     # Script-capable types never get an inline-renderable media type.
     ("page.html", "application/octet-stream"), ("x.svg", "application/octet-stream")],
)
def test_artifact_content_type_follows_the_extension(filename, expected):
    """Custom workflows emit GIF/video/audio too; all were labelled image/png."""
    from comfyui_media_client import _content_type_for

    assert _content_type_for(filename) == expected


def test_strength_and_size_inputs_are_validated_not_defaulted():
    import comfyui_media_client as cmc

    # "nope" used to become 0.75 and width=0 became 1024.
    assert cmc._strength("0.4") == 0.4
    assert cmc._strength(None) == 0.75
    assert cmc._strength(1.5) == 1.5  # clamped to 1.0 when building the graph
    for bad in ("nope", "nan"):
        with pytest.raises(ValueError, match="strength"):
            cmc._strength(bad)
    assert cmc._first_given(0, 512, 1024) == 0
    assert cmc._first_given(None, None, 1024) == 1024


def test_init_image_larger_than_the_side_cap_is_refused():
    # img2img encodes the init image at its own size; a 6000x6000 input
    # bypassed the 4096 width/height cap.
    import io

    import comfyui_media_client as cmc
    from PIL import Image

    def png(width, height):
        buffer = io.BytesIO()
        Image.new("RGB", (width, height)).save(buffer, format="PNG")
        return buffer.getvalue()

    cmc._reject_oversized_init_image(png(4096, 64))
    with pytest.raises(ValueError, match="4097x64"):
        cmc._reject_oversized_init_image(png(4097, 64))


def test_a_prompt_that_may_have_been_queued_is_not_a_retryable_outage():
    """A read timeout after sending /prompt can mean ComfyUI queued it; only a
    connect failure proves it was not delivered (#676)."""
    import asyncio

    import httpx
    import pytest

    from comfyui_client import ComfyUIClient, ComfyUISubmissionUnknownError, ComfyUIUnavailableError

    async def run(error):
        def handler(request):
            raise error("boom", request=request)

        client = ComfyUIClient()
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await client.queue_prompt({"1": {}})
        finally:
            await client.client.aclose()

    with pytest.raises(ComfyUISubmissionUnknownError):
        asyncio.run(run(httpx.ReadTimeout))
    with pytest.raises(ComfyUIUnavailableError):
        asyncio.run(run(httpx.ConnectError))


def test_a_queued_poll_never_returns_other_callers_queue_items():
    """The poll returned ComfyUI's whole /queue body, whose items carry other
    callers' prompt graphs (text, models, inputs)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/history"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json={
            "queue_running": [[1, "someone-else", {"6": {"inputs": {"text": "SECRET other prompt"}}}, {}, []]],
            "queue_pending": [[2, "mine", {}, {}, []]],
        })

    payload = _provider_run(handler, lambda c: c.get_media_operation(operation_id="mine", modality="image"))
    assert payload["status"] == "queued"
    assert "SECRET" not in __import__('json').dumps(payload) and "someone-else" not in __import__('json').dumps(payload)


def test_non_json_upstream_and_infinite_sizes_are_not_reported_as_parser_text():
    """A 200 HTML body raised JSONDecodeError (a ValueError → 400 with the
    parser's text); `1e400` parsed as inf raised OverflowError (→ 502)."""
    import comfyui_media_client as module

    def html(_request):
        return httpx.Response(200, text="<html>proxy</html>", headers={"content-type": "text/html"})

    with pytest.raises(RuntimeError, match="non-JSON"):
        _provider_run(html, lambda c: c._get_queue())
    with pytest.raises(ValueError, match="width must be an integer"):
        module._bounded_int(float("inf"), field="width", minimum=64, maximum=2048)


from tests.test_async_jobs import _reload_main as _reload_async_main  # noqa: E402
from tests.test_backend_identity import _user_headers as _identity_user_headers  # noqa: E402


def _media_main_with_prompt_handler(monkeypatch, handler):
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    monkeypatch.setenv("FAL_SOURCE", "disabled")
    monkeypatch.setenv("COMFYUI_SOURCE", "container-cpu")
    main = _reload_async_main(monkeypatch)
    import comfyui_media_client as cmc

    original_init = cmc.ComfyUIMediaClient.__init__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(cmc.ComfyUIMediaClient, "__init__", init)
    monkeypatch.setattr(main, "ComfyUIMediaClient", cmc.ComfyUIMediaClient)
    return main


def _post_media(main):
    from fastapi.testclient import TestClient

    return TestClient(main.app).post("/media/generate", json={
        "modality": "image", "provider": "comfyui", "model": "x.safetensors", "input": {"prompt": "cat"},
    })


@pytest.mark.parametrize("lost", [httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_media_generate_reports_a_lost_prompt_response_as_maybe_queued(monkeypatch, lost):
    """A read timeout or dropped response after /prompt was sent returned a
    retryable 502 with no operation record; the retry rendered twice (#676)."""
    def handler(request):
        if request.url.path == "/prompt":
            raise lost("lost after send", request=request)
        return httpx.Response(404)

    response = _post_media(_media_main_with_prompt_handler(monkeypatch, handler))
    assert response.status_code == 504 and "may be queued" in response.json()["detail"]


@pytest.mark.parametrize("upstream, expected", [
    (lambda: httpx.Response(400, json={"error": {"message": "failed validation"},
                                       "node_errors": {"1": "ckpt SECRET_internal_model.safetensors"}}), 400),
    (lambda: httpx.Response(413, text="<html>nginx internal-host comfy-gpu-01.corp</html>"), 502),
])
def test_media_generate_does_not_echo_comfyui_error_bodies(monkeypatch, upstream, expected):
    """The 4xx body (a proxy's HTML page, raw node_errors with private model
    names) reached any signed-in caller; a non-400 4xx is upstream-side."""
    def handler(request):
        return upstream() if request.url.path == "/prompt" else httpx.Response(404)

    response = _post_media(_media_main_with_prompt_handler(monkeypatch, handler))
    assert response.status_code == expected
    assert "SECRET_internal" not in response.text and "corp" not in response.text


def test_user_jwts_cannot_reach_fleet_wide_memory_operator_routes(monkeypatch):
    """/memory/health counted every user's facts and /memory/vector-store/probe
    forced the global Weaviate failback, for any self-registered user."""
    from fastapi.testclient import TestClient

    main = _reload_async_main(monkeypatch)
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")
    calls = []

    async def probe():
        calls.append("probe")
        return {}

    monkeypatch.setattr(main.memory_service, "probe_weaviate", probe)
    client = TestClient(main.app)
    headers = _identity_user_headers(monkeypatch, "00000000-0000-4000-8000-000000000001")
    assert client.post("/memory/vector-store/probe", headers=headers).status_code == 403
    assert client.get("/memory/health", headers=headers).status_code == 403
    assert calls == []
