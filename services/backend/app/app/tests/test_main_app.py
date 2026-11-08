"""App-level behavior in main.py: research/start user_id validation and the
lifespan shutdown that closes the long-lived n8n client.

Identity-bearing and operator routes have a Backend bearer-auth dependency.
The shared test fixture selects the explicit rollback mode unless a security
test enables the required policy.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest


def _stub_required_env(monkeypatch):
    """main.py validates a few env vars at import time; provide stubs so
    `from main import app` works without a running stack."""
    for var, default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        if not os.environ.get(var):
            monkeypatch.setenv(var, default)


def test_research_start_rejects_non_uuid_user_id(monkeypatch):
    """POST /research/start with a non-UUID user_id returns a clean 400,
    not an opaque 500 from UUID() deep inside research_service."""
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    resp = client.post(
        "/research/start",
        json={"query": "anything", "user_id": "not-a-uuid"},
    )
    assert resp.status_code == 400
    assert "user_id" in resp.json()["detail"]


def test_cors_regex_without_explicit_origins_does_not_leave_wildcard_enabled(monkeypatch):
    _stub_required_env(monkeypatch)
    import main

    assert main._resolve_cors_origins("", "https://.*\\.example\\.com") == []
    assert main._resolve_cors_origins(None, "https://.*\\.example\\.com") == []
    assert main._resolve_cors_origins("*", "https://.*\\.example\\.com") == []
    assert main._resolve_cors_origins("", None) == ["*"]
    assert main._resolve_cors_origins("https://atlas.localhost,http://localhost:3000", None) == [
        "https://atlas.localhost",
        "http://localhost:3000",
    ]


def test_research_start_rejects_unbounded_request_values(monkeypatch):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    resp = client.post(
        "/research/start",
        json={"query": "", "max_loops": 999, "search_api": "commercial-search"},
    )

    assert resp.status_code == 422


def test_comfyui_generate_rejects_unbounded_request_values(monkeypatch):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    resp = client.post(
        "/comfyui/generate",
        json={"prompt": "", "width": 32, "height": 99999, "steps": 999, "cfg": 99},
    )

    assert resp.status_code == 422


def test_storage_upload_returns_503_for_storage_dependency_failure(monkeypatch):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import main

    class BrokenStorage:
        def from_(self, bucket):
            raise RuntimeError("internal storage URL leaked")

    monkeypatch.setattr(main, "storage_client", BrokenStorage())
    client = TestClient(main.app)

    resp = client.post(
        "/storage/upload",
        files={"file": ("example.txt", b"hello", "text/plain")},
    )

    assert resp.status_code == 503
    assert resp.json()["detail"] == "Supabase Storage is unavailable"


def test_storage_upload_maps_an_existing_object_to_409(monkeypatch):
    """storage3 does not upsert; a duplicate path is a permanent conflict,
    and the old 503 made clients retry it forever."""
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    from storage3.exceptions import StorageApiError
    import main

    class Bucket:
        def upload(self, **_kwargs):
            raise StorageApiError("The resource already exists", "Duplicate", "409")

    class Storage:
        def from_(self, _bucket):
            return Bucket()

    monkeypatch.setattr(main, "storage_client", Storage())
    resp = TestClient(main.app).post(
        "/storage/upload",
        files={"file": ("example.txt", b"hello", "text/plain")},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"] == "default/example.txt already exists"


def test_storage_upload_maps_a_storage_size_rejection_to_413(monkeypatch):
    """Storage caps objects at 50 MiB, below MAX_UPLOAD_BYTES; its 413 must not
    become a retryable 503."""
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    from storage3.exceptions import StorageApiError
    import main

    class Bucket:
        def upload(self, **_kwargs):
            raise StorageApiError("The object exceeded the maximum allowed size", "EntityTooLarge", "413")

    class Storage:
        def from_(self, _bucket):
            return Bucket()

    monkeypatch.setattr(main, "storage_client", Storage())
    resp = TestClient(main.app).post(
        "/storage/upload",
        files={"file": ("example.txt", b"hello", "text/plain")},
    )

    assert resp.status_code == 413


@pytest.mark.parametrize("sent,stored", [
    ("text/html", "application/octet-stream"),
    ("image/svg+xml", "application/octet-stream"),
    ("application/javascript", "application/octet-stream"),
    ("TEXT/PLAIN; charset=utf-8", "text/plain"),
    ("image/png", "image/png"),
])
def test_storage_upload_stores_only_inert_content_types(monkeypatch, sent, stored):
    """A public bucket would serve a client-typed text/html upload from a Kong
    origin, so the stored type comes from a server-side allowlist (#1387)."""
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import main

    uploads = []

    class Bucket:
        def upload(self, **kwargs):
            uploads.append(kwargs["file_options"])

        def get_public_url(self, path):
            return f"http://storage/{path}"

    class Storage:
        def from_(self, _bucket):
            return Bucket()

    monkeypatch.setattr(main, "storage_client", Storage())
    resp = TestClient(main.app).post(
        "/storage/upload", files={"file": ("page.bin", b"<script>1</script>", sent)},
    )

    assert resp.status_code == 200
    assert uploads == [{"content-type": stored}]


def test_storage_upload_rejects_unapproved_bucket_and_path_filename(monkeypatch):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import main

    client = TestClient(main.app)

    bucket_resp = client.post(
        "/storage/upload?bucket=private",
        files={"file": ("example.txt", b"hello", "text/plain")},
    )
    path_resp = client.post(
        "/storage/upload",
        files={"file": ("../secret.txt", b"hello", "text/plain")},
    )

    assert bucket_resp.status_code == 400
    assert path_resp.status_code == 400


def test_document_extract_rejects_oversized_upload_before_extractor(monkeypatch):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import main

    async def fail_extract(**kwargs):
        raise AssertionError("extractor should not be called for oversized uploads")

    monkeypatch.setattr(
        main,
        "document_extractor",
        SimpleNamespace(config=SimpleNamespace(max_file_size=4), extract=fail_extract),
    )
    client = TestClient(main.app)

    resp = client.post(
        "/documents/extract",
        files={"file": ("large.txt", b"12345", "text/plain")},
    )

    assert resp.status_code == 413


def test_memory_requests_reject_unbounded_payloads(monkeypatch):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    user_id = "00000000-0000-4000-8000-000000000001"
    extract_resp = client.post(
        "/memory/extract",
        json={
            "user_id": user_id,
            "namespace": "x" * 101,  # memory_facts.namespace is VARCHAR(100)
            "messages": [{"role": "user", "content": "x" * 20001}],
        },
    )
    recall_resp = client.post(
        "/memory/recall",
        json={"user_id": user_id, "query": "", "namespace": "default"},
    )

    assert extract_resp.status_code == 422
    assert recall_resp.status_code == 422


def test_memory_update_rejects_oversized_metadata(monkeypatch):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)

    resp = client.put(
        "/memory/00000000-0000-4000-8000-000000000001",
        json={"metadata": {"blob": "x" * 20001}},
    )

    assert resp.status_code == 422


@pytest.mark.parametrize("content", ["", "   "])
def test_memory_update_rejects_blank_content(monkeypatch, content):
    """Extraction drops blank facts; PUT must not store one either."""
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import main

    calls = []

    async def fake_update(memory_id, user_id, updates):
        calls.append(updates)
        return {"id": memory_id, "user_id": user_id, "content": updates["content"]}

    monkeypatch.setattr(main.memory_service, "update_memory", fake_update)
    client = TestClient(main.app)

    resp = client.put(
        "/memory/00000000-0000-4000-8000-000000000001"
        "?user_id=00000000-0000-4000-8000-000000000002",
        json={"content": content},
    )

    assert resp.status_code == 422
    assert "content" in resp.text
    assert calls == []


def test_memory_update_requires_and_forwards_user_id(monkeypatch):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import main

    calls = []

    async def fake_update(memory_id, user_id, updates):
        calls.append((memory_id, user_id, updates))
        return {
            "id": memory_id,
            "user_id": user_id,
            "content": updates["content"],
        }

    monkeypatch.setattr(main.memory_service, "update_memory", fake_update)
    client = TestClient(main.app)
    memory_id = "00000000-0000-4000-8000-000000000001"
    user_id = "00000000-0000-4000-8000-000000000002"

    missing_user = client.put(f"/memory/{memory_id}", json={"content": "updated"})
    ok = client.put(
        f"/memory/{memory_id}?user_id={user_id}",
        json={"content": "updated"},
    )

    assert missing_user.status_code == 422
    assert ok.status_code == 200
    assert calls == [(memory_id, user_id, {"content": "updated"})]


def test_memory_delete_requires_and_forwards_user_id(monkeypatch):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import main

    calls = []

    async def fake_delete(memory_id, user_id):
        calls.append((memory_id, user_id))
        return {"success": True, "deletion": "soft"}

    monkeypatch.setattr(main.memory_service, "delete_memory_report", fake_delete)
    client = TestClient(main.app)
    memory_id = "00000000-0000-4000-8000-000000000001"
    user_id = "00000000-0000-4000-8000-000000000002"

    missing_user = client.delete(f"/memory/{memory_id}")
    ok = client.delete(f"/memory/{memory_id}?user_id={user_id}")

    assert missing_user.status_code == 422
    assert ok.status_code == 200
    assert calls == [(memory_id, user_id)]


def test_list_routes_reject_negative_limits(monkeypatch):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    user_id = "00000000-0000-4000-8000-000000000001"

    research_resp = client.get("/research/sessions?limit=-1")
    memory_resp = client.get(f"/memory/user/{user_id}?limit=-1")

    assert research_resp.status_code == 422
    assert memory_resp.status_code == 422


def test_research_cancel_reports_best_effort_local_cancellation(monkeypatch):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import main

    async def fake_cancel(session_id, owner_user_id=None):
        return "cancelled"

    monkeypatch.setattr(main.research_service, "cancel_research_outcome", fake_cancel)
    client = TestClient(main.app)

    resp = client.post("/research/00000000-0000-4000-8000-000000000001/cancel")

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "cancel_requested"
    assert "makes LangGraph cancel the remote run" in body["message"]


@pytest.mark.parametrize(("outcome", "code"), [("not_found", 404), ("not_running", 409)])
def test_research_cancel_separates_unknown_from_finished_sessions(monkeypatch, outcome, code):
    """#1354: an unknown or foreign session is 404; one that already finished
    is 409. Both were 400."""
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import main

    async def fake_cancel(session_id, owner_user_id=None):
        return outcome

    monkeypatch.setattr(main.research_service, "cancel_research_outcome", fake_cancel)

    resp = TestClient(main.app).post("/research/00000000-0000-4000-8000-000000000001/cancel")

    assert resp.status_code == code


def test_research_logs_returns_404_when_session_is_absent(monkeypatch):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import main

    async def fake_logs(session_id, owner_user_id=None):
        return None

    monkeypatch.setattr(main.research_service, "get_research_logs", fake_logs)
    client = TestClient(main.app)

    resp = client.get("/research/00000000-0000-4000-8000-000000000099/logs")

    assert resp.status_code == 404


def test_lifespan_closes_n8n_client(monkeypatch):
    """App shutdown awaits n8n_client.aclose() so httpx doesn't leak the
    process-lifetime client on reload/shutdown."""
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import main

    closed = {"n8n": False, "research": False, "research_started": False}

    async def fake_aclose():
        closed["n8n"] = True

    async def fake_research_start():
        closed["research_started"] = True

    async def fake_research_close():
        closed["research"] = True

    async def fake_memory_init():
        return None

    monkeypatch.setattr(main.n8n_client, "aclose", fake_aclose)
    monkeypatch.setattr(
        main.research_service, "start_maintenance", fake_research_start
    )
    monkeypatch.setattr(main.research_service, "aclose", fake_research_close)
    monkeypatch.setattr(main.memory_service, "_ensure_initialized", fake_memory_init)
    # Entering and exiting the context manager runs lifespan startup +
    # shutdown.
    with TestClient(main.app):
        pass
    assert closed == {"n8n": True, "research": True, "research_started": True}


def test_lifespan_closes_remaining_resources_after_one_closer_fails(monkeypatch):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import db_connection
    import main

    closed = []

    async def fake_start():
        return None

    async def fail_research_close():
        closed.append("research")
        raise RuntimeError("research close failed")

    async def close_pg():
        closed.append("postgres")

    async def close_n8n():
        closed.append("n8n")

    async def close_operations():
        closed.append("operations")

    async def fake_memory_init():
        return None

    monkeypatch.setattr(main.research_service, "start_maintenance", fake_start)
    monkeypatch.setattr(main.research_service, "aclose", fail_research_close)
    monkeypatch.setattr(db_connection, "close_pg_pools", close_pg)
    monkeypatch.setattr(main.n8n_client, "aclose", close_n8n)
    monkeypatch.setattr(main.MEDIA_OPERATION_STORE, "aclose", close_operations)
    monkeypatch.setattr(main.memory_service, "_ensure_initialized", fake_memory_init)

    with pytest.raises(RuntimeError, match="research close failed"):
        with TestClient(main.app):
            pass
    assert closed == ["research", "postgres", "n8n", "operations"]


def test_lifespan_rejects_invalid_postgres_pool_configuration(monkeypatch):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import db_connection
    import main

    monkeypatch.setattr(db_connection, "_POOL_MIN", 11)
    monkeypatch.setattr(db_connection, "_POOL_MAX", 10)

    with pytest.raises(db_connection.PoolConfigurationError):
        with TestClient(main.app):
            pass


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MEDIA_LEDGER_RECOVERY_BATCH_SIZE", "0"),
        ("MEDIA_LEDGER_RECOVERY_BATCH_SIZE", "not-an-integer"),
        ("MEDIA_LEDGER_RECOVERY_MAX_CYCLES", "0"),
        ("MEDIA_LEDGER_RECOVERY_MAX_CYCLES", "not-an-integer"),
    ],
)
def test_lifespan_rejects_invalid_media_recovery_config_before_task_start(
    monkeypatch, name, value
):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setenv(name, value)
    started = False

    async def forbidden_loop():
        nonlocal started
        started = True

    async def no_op():
        return None

    monkeypatch.setattr(main, "_media_ledger_intent_loop", forbidden_loop)
    monkeypatch.setattr(main.research_service, "start_maintenance", no_op)
    monkeypatch.setattr(main.memory_service, "_ensure_initialized", no_op)

    with pytest.raises(ValueError, match=name):
        with TestClient(main.app):
            pass
    assert started is False


def test_research_defaults_follow_the_operator_ldr_settings(monkeypatch):
    """Omitted values use LOCAL_DEEP_RESEARCHER_*; a run's own values win in LDR."""
    import main

    monkeypatch.setenv("LOCAL_DEEP_RESEARCHER_LOOPS", "5")
    monkeypatch.setenv("LOCAL_DEEP_RESEARCHER_SEARCH_API", "duckduckgo")
    assert main._research_default_loops() == 5
    assert main._research_default_search_api() == "duckduckgo"
    monkeypatch.setenv("LOCAL_DEEP_RESEARCHER_LOOPS", "50")
    monkeypatch.delenv("LOCAL_DEEP_RESEARCHER_SEARCH_API")
    assert main._research_default_loops() == 3
    assert main._research_default_search_api() == "searxng"
    request = main.ResearchStartRequest(query="q")
    assert request.max_loops is None and request.search_api is None


# --- owned memory: scope and deletion report (#1206) ------------------------


def _memory_client_as(monkeypatch, subject: str):
    import time
    import uuid

    import jwt
    from fastapi.testclient import TestClient

    _stub_required_env(monkeypatch)
    import main

    secret = "atlas-test-supabase-jwt-secret-32-bytes"
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    token = jwt.encode({"sub": subject, "role": "authenticated", "aud": "authenticated",
                        "exp": int(time.time()) + 60}, secret, algorithm="HS256")
    client = TestClient(main.app)
    client.headers["Authorization"] = f"Bearer {token}"
    return main, client, str(uuid.uuid4())


def test_memory_routes_reject_another_users_facts(monkeypatch):
    import uuid

    owner = str(uuid.uuid4())
    main, client, _other_fact = _memory_client_as(monkeypatch, owner)
    stranger = str(uuid.uuid4())
    fact = str(uuid.uuid4())
    touched = []
    for name in ("list_memories", "update_memory", "delete_memory_report"):
        async def record(*_a, _n=name, **_k):
            touched.append(_n)
        monkeypatch.setattr(main.memory_service, name, record)

    responses = [
        client.get(f"/memory/user/{stranger}"),
        client.put(f"/memory/{fact}", params={"user_id": stranger}, json={"content": "x"}),
        client.delete(f"/memory/{fact}", params={"user_id": stranger}),
    ]

    assert [r.status_code for r in responses] == [403, 403, 403]
    assert touched == []


def test_memory_delete_returns_the_store_by_store_report(monkeypatch):
    import uuid

    owner = str(uuid.uuid4())
    main, client, fact = _memory_client_as(monkeypatch, owner)
    report = {"success": True, "deletion": "soft", "message": "m",
              "postgres": {"is_active": False, "row_retained": True},
              "weaviate": {"object_id": "w", "object_removed": False, "deactivated": True,
                           "sync_pending": False},
              "pgvector": {"embedding_cleared": False, "embedding_present": True},
              "retained": ["r"], "recall": "x", "re_extraction": "y"}

    async def delete(memory_id, user_id):
        return report if user_id == owner else None

    monkeypatch.setattr(main.memory_service, "delete_memory_report", delete)
    body = client.delete(f"/memory/{fact}", params={"user_id": owner}).json()

    for key in ("deletion", "postgres", "weaviate", "pgvector", "retained", "recall", "re_extraction"):
        assert key in body
    assert body["weaviate"]["object_removed"] is False and body["pgvector"]["embedding_cleared"] is False


def test_lifespan_rejects_an_invalid_identity_auth_mode(monkeypatch):
    """validate_identity_auth_mode is only useful if startup calls it: a typo
    otherwise failed every request with 503 while /ready stayed green."""
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import main

    async def no_op():
        return None

    monkeypatch.setattr(main.research_service, "start_maintenance", no_op)
    monkeypatch.setattr(main.memory_service, "_ensure_initialized", no_op)
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "requried")
    with pytest.raises(ValueError, match="BACKEND_IDENTITY_AUTH"):
        with TestClient(main.app):
            pass


def _anonymous_upload_scope(path: str) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"host", b"backend"),
            (b"content-type", b"multipart/form-data; boundary=atlas"),
            (b"content-length", b"5242880"),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("backend", 8000),
    }


@pytest.mark.parametrize("path", ["/storage/upload", "/documents/extract"])
def test_multipart_routes_refuse_anonymous_callers_before_reading_the_body(
    monkeypatch, path
):
    """FastAPI spools a multipart body before route dependencies run, so the
    request-limit middleware authenticates these routes first (2026-10-30)."""
    _stub_required_env(monkeypatch)
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    import asyncio
    import main

    sent = []

    async def receive():  # pragma: no cover - must never be called
        raise AssertionError("body must not be read before authentication")

    async def send(message):
        sent.append(message)

    asyncio.run(main.app(_anonymous_upload_scope(path), receive, send))

    assert next(m["status"] for m in sent if m["type"] == "http.response.start") == 401


def _scope_with_bearer(token: str) -> dict:
    scope = _anonymous_upload_scope("/storage/upload")
    scope["headers"] = [(b"authorization", f"Bearer {token}".encode("ascii"))]
    return scope


def test_upload_scope_checks_match_their_route_dependencies(monkeypatch):
    import asyncio
    import time

    import jwt
    from fastapi import HTTPException

    from backend_identity import (
        BackendPrincipal,
        authenticate_n8n_operator_scope,
        authenticate_stateless_scope,
    )

    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("BACKEND_N8N_API_TOKEN", "n8n-secret")
    monkeypatch.setenv("BACKEND_NOTEBOOK_API_TOKEN", "notebook-secret")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "atlas-test-supabase-jwt-secret-32-bytes")
    user_token = jwt.encode(
        {
            "sub": "00000000-0000-4000-8000-000000000002",
            "role": "authenticated",
            "aud": "authenticated",
            "exp": int(time.time()) + 60,
        },
        "atlas-test-supabase-jwt-secret-32-bytes",
        algorithm="HS256",
    )

    def check(authenticate, token):
        return asyncio.run(authenticate(_scope_with_bearer(token)))

    # /storage/upload: the n8n operator scope (n8n token, internal token).
    assert check(authenticate_n8n_operator_scope, "n8n-secret") == BackendPrincipal(
        kind="n8n", subject="n8n"
    )
    for token in ("notebook-secret", user_token):
        with pytest.raises(HTTPException) as exc:
            check(authenticate_n8n_operator_scope, token)
        assert exc.value.status_code == 403

    # /documents/extract: the stateless scope (users, notebook token).
    assert check(authenticate_stateless_scope, "notebook-secret") == BackendPrincipal(
        kind="notebook", subject="jupyterhub"
    )
    assert check(authenticate_stateless_scope, user_token).kind == "user"
    with pytest.raises(HTTPException) as exc:
        check(authenticate_stateless_scope, "n8n-secret")
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("COMFYUI_MAX_IMAGE_BYTES", "20MB"),
        ("COMFYUI_MAX_IMAGE_BYTES", "0"),
        ("COMFYUI_MAX_IMAGE_BYTES", "-1"),
        ("COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS", "http://images.example.com"),
    ),
)
def test_comfyui_media_config_validator_rejects_malformed_settings(monkeypatch, name, value):
    """A malformed setting fails startup, not every request with a 400 that
    blames the caller."""
    monkeypatch.delenv("COMFYUI_MAX_IMAGE_BYTES", raising=False)
    monkeypatch.delenv("COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS", raising=False)
    monkeypatch.setenv(name, value)
    from comfyui_media_client import validate_comfyui_media_config

    with pytest.raises(ValueError, match=name) as exc:
        validate_comfyui_media_config()
    assert "invalid literal" not in str(exc.value)


def test_comfyui_media_config_validator_accepts_defaults_and_valid_values(monkeypatch):
    from comfyui_media_client import validate_comfyui_media_config

    monkeypatch.delenv("COMFYUI_MAX_IMAGE_BYTES", raising=False)
    monkeypatch.delenv("COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS", raising=False)
    validate_comfyui_media_config()

    monkeypatch.setenv("COMFYUI_MAX_IMAGE_BYTES", " 1048576 ")
    monkeypatch.setenv("COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS", "https://images.example.com")
    validate_comfyui_media_config()


def test_both_comfyui_clients_share_the_image_byte_parser(monkeypatch):
    from comfyui_client import ComfyUIClient
    from comfyui_media_client import ComfyUIMediaClient

    monkeypatch.delenv("COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS", raising=False)
    monkeypatch.setenv("COMFYUI_MAX_IMAGE_BYTES", "1048576")
    assert ComfyUIMediaClient(base_url="http://comfyui.test").max_image_bytes == 1048576
    assert ComfyUIClient(base_url="http://comfyui.test").max_image_bytes == 1048576

    monkeypatch.setenv("COMFYUI_MAX_IMAGE_BYTES", "20MB")
    with pytest.raises(ValueError, match="COMFYUI_MAX_IMAGE_BYTES must be a positive integer"):
        ComfyUIClient(base_url="http://comfyui.test")


def test_main_validates_comfyui_media_config_at_startup():
    """Pin the startup call next to the other provider validators."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert "\nvalidate_comfyui_media_config()\n" in source


def test_package_without_a_router_is_skipped_not_reported_loaded(tmp_path, monkeypatch):
    """A package that imports but exposes no module-level router mounts
    nothing, so GET /plugins must not call it loaded, and its streaming
    prefix must not reach the request-limit middleware."""
    pkg = tmp_path / "no_router_plugin"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("IMPORTED = True\n")
    (pkg / "plugin.yml").write_text(
        "plugin_manifest_version: 1\nname: no-router\nroute_prefix: /no-router\n"
        "request_buffering: false\n",
        encoding="utf-8",
    )
    from fastapi import FastAPI
    app = FastAPI()
    before = len(app.router.routes)
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    import plugin_seam
    try:
        inventory = plugin_seam.load_plugins(app)
    finally:
        sys.modules.pop("no_router_plugin", None)

    entry = next(e for e in inventory if e["name"] == "no-router")
    assert entry["status"] == "skipped"
    assert entry["error"] == "package exposes no module-level router"
    assert len(app.router.routes) == before
    assert "/no-router" not in plugin_seam.streaming_prefixes(inventory)
