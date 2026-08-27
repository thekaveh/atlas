from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request
from starlette.websockets import WebSocket

from backend_identity import (
    BackendPrincipal,
    authorize_media_scope,
    authorize_user_id,
    principal_scope_key,
    require_backend_principal,
    require_memory_principal,
    require_research_principal,
    require_service_principal,
    require_stateless_principal,
)


TEST_JWT_SECRET = "atlas-test-supabase-jwt-secret-32-bytes"
WRONG_JWT_SECRET = "atlas-test-wrong-jwt-secret-32-bytes"


def _credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _http_connection(
    *, headers: list[tuple[bytes, bytes]] | None = None, query_string: bytes = b""
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": query_string,
            "headers": headers or [],
        }
    )


async def _websocket_receive() -> dict:
    return {"type": "websocket.connect"}


async def _websocket_send(message: dict) -> None:
    return None


def _websocket_connection(
    *, headers: list[tuple[bytes, bytes]] | None = None, query_string: bytes = b""
) -> WebSocket:
    return WebSocket(
        {
            "type": "websocket",
            "scheme": "ws",
            "path": "/",
            "raw_path": b"/",
            "query_string": query_string,
            "headers": headers or [],
        },
        _websocket_receive,
        _websocket_send,
    )


def _plugin_api_key(connection: Request | WebSocket) -> str | None:
    from backend_identity import _PLUGIN_API_KEY

    return asyncio.run(_PLUGIN_API_KEY(connection))


def _user_headers(monkeypatch, subject: str) -> dict[str, str]:
    secret = TEST_JWT_SECRET
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
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
    return {"Authorization": f"Bearer {token}"}


def test_internal_service_token_can_delegate_user_identity(monkeypatch) -> None:
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")

    principal = asyncio.run(require_backend_principal(_credentials("internal-secret")))

    assert principal == BackendPrincipal(kind="service", subject="internal-service")
    delegated_user = str(uuid4())
    assert authorize_user_id(principal, delegated_user) == delegated_user


def test_authenticated_supabase_jwt_is_bound_to_its_subject(monkeypatch) -> None:
    secret = TEST_JWT_SECRET
    subject = str(uuid4())
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
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

    principal = asyncio.run(require_backend_principal(_credentials(token)))

    assert principal == BackendPrincipal(kind="user", subject=subject)
    assert authorize_user_id(principal, None) == subject
    assert authorize_user_id(principal, subject) == subject
    with pytest.raises(HTTPException) as exc:
        authorize_user_id(principal, str(uuid4()))
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "token_factory",
    [
        lambda secret: jwt.encode(
            {
                "sub": str(uuid4()),
                "role": "anon",
                "aud": "authenticated",
                "exp": int(time.time()) + 60,
            },
            secret,
            algorithm="HS256",
        ),
        lambda secret: jwt.encode(
            {
                "sub": str(uuid4()),
                "role": "authenticated",
                "aud": "authenticated",
                "exp": int(time.time()) - 60,
            },
            secret,
            algorithm="HS256",
        ),
        lambda secret: jwt.encode(
            {
                "sub": str(uuid4()),
                "role": "authenticated",
                "aud": "unexpected-audience",
                "exp": int(time.time()) + 60,
            },
            secret,
            algorithm="HS256",
        ),
        lambda secret: jwt.encode(
            {
                "sub": str(uuid4()),
                "role": "authenticated",
                "aud": "authenticated",
                "exp": int(time.time()) + 60,
            },
            WRONG_JWT_SECRET,
            algorithm="HS256",
        ),
        lambda secret: "not-a-jwt",
    ],
)
def test_untrusted_tokens_are_rejected(monkeypatch, token_factory) -> None:
    secret = TEST_JWT_SECRET
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_backend_principal(_credentials(token_factory(secret))))

    assert exc.value.status_code == 401


def test_missing_token_is_rejected_when_identity_auth_is_required(monkeypatch) -> None:
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_backend_principal(None))

    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    ("connection", "expected"),
    [
        (_http_connection(headers=[(b"apikey", b"gateway-secret")]), "gateway-secret"),
        (_websocket_connection(query_string=b"apikey=gateway-secret"), "gateway-secret"),
        (_http_connection(headers=[(b"apikey", b"")]), None),
        (_websocket_connection(query_string=b"apikey="), None),
    ],
)
def test_plugin_api_key_extraction_is_independent_of_fastapi_helper(
    monkeypatch, connection, expected
) -> None:
    from fastapi.security import APIKeyHeader
    from backend_identity import _PLUGIN_API_KEY

    def incompatible_helper(*args, **kwargs):
        raise AssertionError("version-dependent FastAPI helper was called")

    monkeypatch.setattr(APIKeyHeader, "check_api_key", incompatible_helper)

    assert asyncio.run(_PLUGIN_API_KEY(connection)) == expected


@pytest.mark.parametrize("make_connection", [_http_connection, _websocket_connection])
def test_plugin_gateway_key_accepts_valid_header_or_query_key(
    monkeypatch, make_connection
) -> None:
    from backend_identity import require_plugin_gateway_key

    monkeypatch.setenv("BACKEND_KONG_API_KEY", "gateway-secret")
    header_connection = make_connection(headers=[(b"apikey", b"gateway-secret")])
    query_connection = make_connection(query_string=b"apikey=gateway-secret")

    assert (
        asyncio.run(require_plugin_gateway_key(_plugin_api_key(header_connection)))
        == "gateway-key"
    )
    assert (
        asyncio.run(require_plugin_gateway_key(_plugin_api_key(query_connection)))
        == "gateway-key"
    )


@pytest.mark.parametrize("make_connection", [_http_connection, _websocket_connection])
def test_plugin_gateway_key_prefers_header_over_query(monkeypatch, make_connection) -> None:
    from backend_identity import require_plugin_gateway_key

    monkeypatch.setenv("BACKEND_KONG_API_KEY", "gateway-secret")
    connection = make_connection(
        headers=[(b"apikey", b"gateway-secret")], query_string=b"apikey=wrong"
    )

    assert (
        asyncio.run(require_plugin_gateway_key(_plugin_api_key(connection)))
        == "gateway-key"
    )


@pytest.mark.parametrize("make_connection", [_http_connection, _websocket_connection])
def test_plugin_gateway_key_does_not_fallback_from_invalid_header_to_query(
    monkeypatch, make_connection
) -> None:
    from backend_identity import require_plugin_gateway_key

    monkeypatch.setenv("BACKEND_KONG_API_KEY", "gateway-secret")
    connection = make_connection(
        headers=[(b"apikey", b"wrong")], query_string=b"apikey=gateway-secret"
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_plugin_gateway_key(_plugin_api_key(connection)))

    assert exc.value.status_code == 401


@pytest.mark.parametrize("make_connection", [_http_connection, _websocket_connection])
@pytest.mark.parametrize(
    ("headers", "query_string"),
    [
        ([], b""),
        ([(b"apikey", b"wrong")], b""),
        ([], "apikey=caf%C3%A9-key".encode()),
    ],
)
def test_plugin_gateway_key_rejects_missing_invalid_and_non_ascii_keys(
    monkeypatch, make_connection, headers, query_string
) -> None:
    from backend_identity import require_plugin_gateway_key

    monkeypatch.setenv("BACKEND_KONG_API_KEY", "gateway-secret")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            require_plugin_gateway_key(
                _plugin_api_key(
                    make_connection(headers=headers, query_string=query_string)
                )
            )
        )

    assert exc.value.status_code == 401


@pytest.mark.parametrize("make_connection", [_http_connection, _websocket_connection])
def test_plugin_gateway_key_requires_server_configuration(monkeypatch, make_connection) -> None:
    from backend_identity import require_plugin_gateway_key

    monkeypatch.delenv("BACKEND_KONG_API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            require_plugin_gateway_key(
                _plugin_api_key(
                    make_connection(headers=[(b"apikey", b"gateway-secret")])
                )
            )
        )

    assert exc.value.status_code == 503


def test_notebook_token_is_limited_to_stateless_routes(monkeypatch) -> None:
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_NOTEBOOK_API_TOKEN", "notebook-secret")
    credentials = _credentials("notebook-secret")

    principal = asyncio.run(require_stateless_principal(credentials))
    assert principal == BackendPrincipal(kind="notebook", subject="jupyterhub")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_backend_principal(credentials))
    assert exc.value.status_code == 403


def test_first_party_tokens_are_limited_to_their_route_families(monkeypatch) -> None:
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_N8N_API_TOKEN", "n8n-secret")
    monkeypatch.setenv("BACKEND_OPEN_WEBUI_API_TOKEN", "webui-secret")

    n8n = asyncio.run(require_research_principal(_credentials("n8n-secret")))
    assert n8n == BackendPrincipal(kind="n8n", subject="n8n")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_backend_principal(_credentials("n8n-secret")))
    assert exc.value.status_code == 403

    webui = asyncio.run(require_memory_principal(_credentials("webui-secret")))
    assert webui == BackendPrincipal(kind="open-webui", subject="open-webui")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_research_principal(_credentials("webui-secret")))
    assert exc.value.status_code == 403


def test_comfyui_cancel_route_requires_automation_principal(
    fastapi_client, monkeypatch
) -> None:
    import main

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def cancel_prompt(self, prompt_id):
            return prompt_id == "prompt-1"

    monkeypatch.setattr(main, "ComfyUIClient", Client)
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_OPEN_WEBUI_API_TOKEN", "webui-secret")
    subject = str(uuid4())

    user = fastapi_client.post(
        "/comfyui/cancel/prompt-1", headers=_user_headers(monkeypatch, subject)
    )
    assert user.status_code == 403

    service = fastapi_client.post(
        "/comfyui/cancel/prompt-1",
        headers={"Authorization": "Bearer webui-secret"},
    )
    assert service.status_code == 200
    assert service.json()["success"] is True


def test_every_builtin_backend_route_has_an_explicit_identity_boundary(
    fastapi_client,
) -> None:
    """Fail when a new route is added without a reviewed access policy."""
    from fastapi.routing import APIRoute
    import main

    public_paths = {
        "/",
        "/docs",
        "/docs/oauth2-redirect",
        "/health",
        "/metrics",
        "/openapi.json",
        "/ready",
        "/redoc",
    }
    accepted_boundaries = {
        "_require_lightrag_rerank_token",
        "_require_ray_job_token",
        "require_backend_principal",
        "require_comfy_automation_principal",
        "require_comfy_read_principal",
        "require_memory_automation_principal",
        "require_memory_principal",
        "require_n8n_operator_principal",
        "require_plugin_gateway_key",
        "require_research_principal",
        "require_service_principal",
        "require_stateless_principal",
    }

    def dependency_names(dependant) -> set[str]:
        names: set[str] = set()
        for dependency in dependant.dependencies:
            names.add(getattr(dependency.call, "__name__", type(dependency.call).__name__))
            names.update(dependency_names(dependency))
        return names

    unclassified = []
    for route in main.app.routes:
        if not isinstance(route, APIRoute) or route.path in public_paths:
            continue
        if not dependency_names(route.dependant).intersection(accepted_boundaries):
            unclassified.append(f"{','.join(sorted(route.methods))} {route.path}")

    assert unclassified == [], f"routes without an identity boundary: {unclassified}"


def test_user_principal_cannot_claim_service_or_another_media_consumer(monkeypatch) -> None:
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    subject = str(uuid4())
    principal = BackendPrincipal(kind="user", subject=subject)

    assert authorize_media_scope(principal, None, "demo") == (subject, "demo")
    assert principal_scope_key(principal) == f"user:{subject}"
    with pytest.raises(HTTPException) as exc:
        authorize_media_scope(principal, "another-consumer", "demo")
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        header = _user_headers(monkeypatch, subject)["Authorization"]
        asyncio.run(require_service_principal(_credentials(header.split(" ", 1)[1])))
    assert exc.value.status_code == 403


def test_memory_route_rejects_missing_and_cross_user_identity(
    fastapi_client, monkeypatch
) -> None:
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")
    user_id = str(uuid4())
    other_user_id = str(uuid4())
    payload = {
        "user_id": other_user_id,
        "messages": [{"role": "user", "content": "Remember Atlas"}],
    }

    assert fastapi_client.post("/memory/extract", json=payload).status_code == 401
    response = fastapi_client.post(
        "/memory/extract",
        json=payload,
        headers=_user_headers(monkeypatch, user_id),
    )
    assert response.status_code == 403


def test_user_jwt_supplies_research_owner_when_body_omits_user_id(
    fastapi_client, monkeypatch
) -> None:
    import main

    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")
    user_id = str(uuid4())
    seen = {}

    async def fake_start_research(**kwargs):
        seen.update(kwargs)
        return {
            "session_id": str(uuid4()),
            "status": "pending",
            "message": "queued",
        }

    monkeypatch.setattr(main.research_service, "start_research", fake_start_research)
    response = fastapi_client.post(
        "/research/start",
        json={"query": "Atlas identity boundaries"},
        headers=_user_headers(monkeypatch, user_id),
    )

    assert response.status_code == 200
    assert seen["user_id"] == user_id


def test_research_capacity_returns_retryable_429(fastapi_client, monkeypatch) -> None:
    import main
    from research_service import ResearchCapacityError

    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")

    async def reject_research(**_kwargs):
        raise ResearchCapacityError("Research capacity is full")

    monkeypatch.setattr(main.research_service, "start_research", reject_research)
    response = fastapi_client.post(
        "/research/start",
        json={"query": "Atlas capacity"},
        headers={"Authorization": "Bearer internal-secret"},
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert response.json() == {"detail": "Research capacity is full"}


def test_research_reads_pass_jwt_owner_to_service(fastapi_client, monkeypatch) -> None:
    import main

    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")
    user_id = str(uuid4())
    session_id = str(uuid4())
    seen = {}

    async def fake_status(requested_session_id, owner_user_id=None):
        seen["owner_user_id"] = owner_user_id
        return {
            "session_id": requested_session_id,
            "query": "Atlas",
            "status": "running",
            "max_loops": 3,
            "search_api": "searxng",
            "user_id": owner_user_id,
        }

    monkeypatch.setattr(main.research_service, "get_research_status", fake_status)
    response = fastapi_client.get(
        f"/research/{session_id}/status",
        headers=_user_headers(monkeypatch, user_id),
    )

    assert response.status_code == 200
    assert seen["owner_user_id"] == user_id


def test_operator_route_rejects_user_jwt_and_accepts_internal_service(
    fastapi_client, monkeypatch
) -> None:
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")

    user_response = fastapi_client.get(
        "/plugins", headers=_user_headers(monkeypatch, str(uuid4()))
    )
    service_response = fastapi_client.get(
        "/plugins", headers={"Authorization": "Bearer internal-secret"}
    )

    assert user_response.status_code == 403
    assert service_response.status_code == 200


def test_media_operation_is_non_enumerating_across_user_principals(
    fastapi_client, monkeypatch
) -> None:
    import main
    from media_operation_store import InMemoryMediaOperationStore

    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "internal-secret")
    owner_id = str(uuid4())
    operation_id = "fal-owner-test"
    original_store = main.MEDIA_OPERATION_STORE
    main.MEDIA_OPERATION_STORE = InMemoryMediaOperationStore()
    asyncio.run(main.MEDIA_OPERATION_STORE.create({
        "operation_id": operation_id,
        "owner_scope": f"user:{owner_id}",
        "provider": "fal",
        "modality": "image",
        "model": "fal-ai/flux/dev",
        "created_at_epoch": time.time(),
        "timeout_seconds": 60,
        "last_payload": {
            "operation_id": operation_id,
            "status": "succeeded",
            "provider": "fal",
            "model": "fal-ai/flux/dev",
            "modality": "image",
        },
    }))
    try:
        owner_response = fastapi_client.get(
            f"/media/operations/{operation_id}",
            headers=_user_headers(monkeypatch, owner_id),
        )
        peer_response = fastapi_client.get(
            f"/media/operations/{operation_id}",
            headers=_user_headers(monkeypatch, str(uuid4())),
        )
    finally:
        main.MEDIA_OPERATION_STORE = original_store

    assert owner_response.status_code == 200
    assert peer_response.status_code == 404


def test_authorize_media_scope_rejects_non_ascii_consumer_cleanly() -> None:
    # A benign Unicode consumer label from a user principal must yield a clean
    # 403 (ownership check), not a TypeError→500 from secrets.compare_digest.
    principal = BackendPrincipal(kind="user", subject=str(uuid4()))
    with pytest.raises(HTTPException) as exc:
        authorize_media_scope(principal, "café", None)
    assert exc.value.status_code == 403


def test_plugin_gateway_key_non_ascii_is_401_not_500(monkeypatch) -> None:
    from backend_identity import require_plugin_gateway_key

    monkeypatch.setenv("BACKEND_KONG_API_KEY", "expected-key")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            require_plugin_gateway_key(
                _plugin_api_key(
                    _http_connection(query_string="apikey=caf%C3%A9-key".encode())
                )
            )
        )
    assert exc.value.status_code == 401
