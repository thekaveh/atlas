"""App-level behavior in main.py: research/start user_id validation and the
lifespan shutdown that closes the long-lived n8n client.

Backend has no auth dependency (Kong gates external access at the edge),
so these tests don't override any auth dependency.
"""

from __future__ import annotations

import os
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
            "namespace": "x" * 129,
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

    async def fake_cancel(session_id):
        return True

    monkeypatch.setattr(main.research_service, "cancel_research", fake_cancel)
    client = TestClient(main.app)

    resp = client.post("/research/00000000-0000-4000-8000-000000000001/cancel")

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "cancel_requested"
    assert "remote LangGraph cancellation is not supported" in body["message"]


def test_lifespan_closes_n8n_client(monkeypatch):
    """App shutdown awaits n8n_client.aclose() so httpx doesn't leak the
    process-lifetime client on reload/shutdown."""
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    import main

    closed = {"v": False}

    async def fake_aclose():
        closed["v"] = True

    monkeypatch.setattr(main.n8n_client, "aclose", fake_aclose)
    # Entering and exiting the context manager runs lifespan startup +
    # shutdown.
    with TestClient(main.app):
        pass
    assert closed["v"] is True
