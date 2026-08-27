"""Tests for RayClient (services/backend/app/app/ray_client.py).

Covers:
- Disabled-when-empty: RAY_ADDRESS="" makes every method raise RayDisabledError.
- Address-derivation: ray://ray-head:10001 → dashboard URL http://ray-head:8265.
- Override: RAY_DASHBOARD_URL takes precedence over derivation.
- submit_job calls through to the underlying JobSubmissionClient.
- get/stop/cluster also call through correctly.
"""

from __future__ import annotations

import sys
import types

import pytest

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

from ray_client import (
    RayClient,
    RayDisabledError,
    RayJobAlreadyExistsError,
    RayJobSubmission,
    _ray_address,
)


def test_ray_address_empty_returns_none(monkeypatch):
    monkeypatch.setenv("RAY_ADDRESS", "")
    monkeypatch.delenv("RAY_DASHBOARD_URL", raising=False)
    assert _ray_address() is None


def test_ray_address_lan_form_returns_http(monkeypatch):
    monkeypatch.setenv("RAY_ADDRESS", "ray://ray-head:10001")
    monkeypatch.delenv("RAY_DASHBOARD_URL", raising=False)
    assert _ray_address() == "http://ray-head:8265"


def test_ray_address_external_returns_https(monkeypatch):
    monkeypatch.setenv("RAY_ADDRESS", "ray://my-cluster.anyscale.com:10001")
    monkeypatch.delenv("RAY_DASHBOARD_URL", raising=False)
    assert _ray_address() == "https://my-cluster.anyscale.com:8265"


def test_ray_dashboard_url_override(monkeypatch):
    """Explicit RAY_DASHBOARD_URL wins over derivation."""
    monkeypatch.setenv("RAY_ADDRESS", "ray://ray-head:10001")
    monkeypatch.setenv("RAY_DASHBOARD_URL", "https://custom-ray.example.com")
    assert _ray_address() == "https://custom-ray.example.com"


def test_submit_job_disabled(ray_disabled_env):
    client = RayClient.get()
    with pytest.raises(RayDisabledError):
        client.submit_job(RayJobSubmission("echo hi", "raysubmit_disabled"))


def test_get_job_status_disabled(ray_disabled_env):
    with pytest.raises(RayDisabledError):
        RayClient.get().get_job_status("job_xyz")


def test_stop_job_disabled(ray_disabled_env):
    with pytest.raises(RayDisabledError):
        RayClient.get().stop_job("job_xyz")


def test_cluster_status_disabled(ray_disabled_env):
    with pytest.raises(RayDisabledError):
        RayClient.get().cluster_status()


def test_submit_job_succeeds_when_enabled(ray_enabled_env, mock_job_submission_client):
    mock_instance = mock_job_submission_client.return_value
    mock_instance.submit_job.return_value = "raysubmit_abc123"
    client = RayClient.get()
    job_id = client.submit_job(RayJobSubmission("echo hi", "raysubmit_abc123"))
    assert job_id == "raysubmit_abc123"
    mock_instance.submit_job.assert_called_once()


def test_submit_job_forwards_stable_submission_id(
    ray_enabled_env, mock_job_submission_client
):
    mock_instance = mock_job_submission_client.return_value
    mock_instance.submit_job.return_value = "raysubmit_stable_123"

    job_id = RayClient.get().submit_job(
        RayJobSubmission("echo hi", "raysubmit_stable_123")
    )
    assert job_id == "raysubmit_stable_123"
    mock_instance.submit_job.assert_called_once_with(
        entrypoint="echo hi",
        runtime_env={},
        metadata={},
        submission_id="raysubmit_stable_123",
    )


def test_submit_job_classifies_existing_stable_id(
    ray_enabled_env, mock_job_submission_client
):
    mock_instance = mock_job_submission_client.return_value
    mock_instance.submit_job.side_effect = RuntimeError("server returned 400")
    mock_instance.get_job_info.return_value = object()

    with pytest.raises(RayJobAlreadyExistsError) as exc_info:
        RayClient.get().submit_job(
            RayJobSubmission("echo hi", "raysubmit_stable_123")
        )

    assert exc_info.value.submission_id == "raysubmit_stable_123"


def test_get_job_status_succeeds_when_enabled(ray_enabled_env, mock_job_submission_client):
    from types import SimpleNamespace
    mock_instance = mock_job_submission_client.return_value
    mock_status = SimpleNamespace(value="SUCCEEDED")
    mock_info = SimpleNamespace(__dict__={"status": "SUCCEEDED", "entrypoint": "echo hi"})
    mock_instance.get_job_status.return_value = mock_status
    mock_instance.get_job_info.return_value = mock_info
    result = RayClient.get().get_job_status("job_xyz")
    assert result["job_id"] == "job_xyz"
    assert result["status"] == "SUCCEEDED"
