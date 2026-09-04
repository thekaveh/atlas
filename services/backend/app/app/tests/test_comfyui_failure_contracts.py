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
def test_legacy_polling_timeout_returns_truthful_503(
    fastapi_client, monkeypatch, route
):
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
                "prompt_id": "prompt-timeout",
                "client_id": "client-timeout",
                "parameters": kwargs,
            }

        async def queue_prompt(self, _workflow):
            return {
                "success": True,
                "prompt_id": "prompt-timeout",
                "client_id": "client-timeout",
            }

        async def wait_for_completion(self, *_args, **_kwargs):
            raise asyncio.TimeoutError

    monkeypatch.setattr(main, "ComfyUIClient", Client)
    payload = (
        {"prompt": "blue observatory"}
        if route == "/comfyui/generate"
        else {"workflow": {"1": {"class_type": "SaveImage"}}}
    )

    response = fastapi_client.post(route, json=payload)

    assert response.status_code == 503
    assert response.json() == {"detail": "ComfyUI is unavailable"}


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

    result = tool.generate_image("blue orbital archive", cfg=0.0)
    tool.generate_image("blue orbital archive", cfg=30.0)

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

    result = module.Tools().generate_image("blue orbital archive")

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

    result = module.Tools().generate_image("blue orbital archive")

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

    result = module.Tools().check_comfyui_status()

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
