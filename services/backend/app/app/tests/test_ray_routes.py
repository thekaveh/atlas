"""Tests for /api/ray/* (services/backend/app/app/ray_routes.py).

Coverage:
- 503 on disabled: when RAY_ADDRESS empty, every endpoint returns 503.
- 401 on missing or invalid Ray API bearer credentials.
- 200 on enabled: submit returns job_id, status returns status payload, etc.
- 422 on invalid payloads.
"""

from __future__ import annotations

import sys
import types

import pytest


RAY_HEADERS = {"Authorization": "Bearer ray-test-token"}

# Pre-stub the `ray` package so that `monkeypatch.setattr("ray.job_submission.JobSubmissionClient", ...)`
# can resolve the dotted path without requiring a real ray installation.
# This must happen at import time (module level) — before conftest fixtures run.
if "ray" not in sys.modules:
    _ray_stub = types.ModuleType("ray")
    _ray_job_stub = types.ModuleType("ray.job_submission")
    _ray_job_stub.JobSubmissionClient = None  # placeholder; tests override this via monkeypatch
    _ray_stub.job_submission = _ray_job_stub
    sys.modules["ray"] = _ray_stub
    sys.modules["ray.job_submission"] = _ray_job_stub


def test_submit_returns_503_when_ray_disabled(monkeypatch):
    """Override env directly + reset singleton; can't use ray_disabled_env
    + fastapi_client together because they conflict (one enables, one
    disables)."""
    monkeypatch.setenv("RAY_ADDRESS", "")
    monkeypatch.delenv("RAY_DASHBOARD_URL", raising=False)
    # Provide stub values for env vars main.py validates at import time.
    import os as _os
    for _var, _default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        if not _os.environ.get(_var):
            monkeypatch.setenv(_var, _default)
    monkeypatch.setenv("RAY_JOB_API_TOKEN", "ray-test-token")
    import ray_client
    ray_client.RayClient._instance = None

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    resp = client.post(
        "/api/ray/jobs/submit",
        json={"entrypoint": "echo hi"},
        headers=RAY_HEADERS,
    )
    assert resp.status_code == 503, resp.text
    body = resp.json()["detail"].lower()
    assert "not set" in body or "disabled" in body


def test_ray_routes_reject_missing_or_wrong_token(fastapi_client, monkeypatch):
    monkeypatch.setenv("RAY_JOB_API_TOKEN", "ray-test-token")

    missing = fastapi_client.get("/api/ray/cluster/status")
    wrong = fastapi_client.get(
        "/api/ray/cluster/status",
        headers={"Authorization": "Bearer wrong"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_submit_returns_200_when_ray_enabled(
    fastapi_client, mock_job_submission_client, monkeypatch,
):
    monkeypatch.setenv("RAY_JOB_API_TOKEN", "ray-test-token")
    mock_instance = mock_job_submission_client.return_value
    mock_instance.submit_job.return_value = "raysubmit_abc"
    resp = fastapi_client.post(
        "/api/ray/jobs/submit",
        json={"entrypoint": "python -c 'print(1)'"},
        headers=RAY_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"job_id": "raysubmit_abc"}


def test_get_status_returns_200_when_enabled(
    fastapi_client, mock_job_submission_client, monkeypatch,
):
    monkeypatch.setenv("RAY_JOB_API_TOKEN", "ray-test-token")
    from types import SimpleNamespace
    mock_instance = mock_job_submission_client.return_value
    mock_instance.get_job_status.return_value = SimpleNamespace(value="RUNNING")
    mock_instance.get_job_info.return_value = SimpleNamespace(__dict__={"status": "RUNNING"})
    resp = fastapi_client.get("/api/ray/jobs/raysubmit_abc", headers=RAY_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "raysubmit_abc"
    assert body["status"] == "RUNNING"


def test_stop_job_returns_200_when_enabled(
    fastapi_client, mock_job_submission_client, monkeypatch,
):
    monkeypatch.setenv("RAY_JOB_API_TOKEN", "ray-test-token")
    mock_instance = mock_job_submission_client.return_value
    mock_instance.stop_job.return_value = True
    resp = fastapi_client.delete("/api/ray/jobs/raysubmit_abc", headers=RAY_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"stopped": True}


def test_invalid_payload_returns_422(fastapi_client, monkeypatch):
    """Missing the required `entrypoint` field → FastAPI returns 422 unprocessable."""
    monkeypatch.setenv("RAY_JOB_API_TOKEN", "ray-test-token")
    resp = fastapi_client.post("/api/ray/jobs/submit", json={}, headers=RAY_HEADERS)
    assert resp.status_code == 422
