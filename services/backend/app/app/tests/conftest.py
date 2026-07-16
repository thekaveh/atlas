"""Shared pytest fixtures for the Backend app's tests.

These fixtures support the Backend's route, service, security, and integration
contract tests. Follow the established identity and import patterns when
extending the suite.

Production identity-bearing and operator routes require backend bearer
authentication. Tests default that boundary to the explicit rollback mode;
security tests opt back into ``required`` and exercise real credentials.

Import strategy: Backend modules use bare absolute imports (e.g.
``from ray_client import ...``), not relative dot-imports. The conftest
adds the parent ``app/`` directory to ``sys.path`` so pytest can find
them when run from ``services/backend/app/app/``.
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure ``app/`` is on sys.path so bare imports like ``import ray_client``
# and ``from main import app`` resolve correctly regardless of where pytest
# is invoked from.
_APP_DIR = str(Path(__file__).parent.parent)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)


@pytest.fixture(autouse=True)
def backend_identity_auth_disabled(monkeypatch):
    """Keep legacy route tests focused; auth-specific tests re-enable it."""
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")


@pytest.fixture
def ray_disabled_env(monkeypatch):
    """Force RAY_ADDRESS empty → RayClient raises RayDisabledError on any call."""
    monkeypatch.setenv("RAY_ADDRESS", "")
    monkeypatch.delenv("RAY_DASHBOARD_URL", raising=False)
    # Reset the singleton so the new env takes effect.
    import ray_client  # noqa: sys.path set above
    ray_client.RayClient._instance = None
    yield
    ray_client.RayClient._instance = None


@pytest.fixture
def ray_enabled_env(monkeypatch):
    """Set RAY_ADDRESS to a fake URL → RayClient will attempt to use it."""
    monkeypatch.setenv("RAY_ADDRESS", "ray://ray-head:10001")
    monkeypatch.setenv("RAY_DASHBOARD_URL", "http://ray-head:8265")
    import ray_client  # noqa
    ray_client.RayClient._instance = None
    yield
    ray_client.RayClient._instance = None


@pytest.fixture
def mock_job_submission_client(monkeypatch):
    """Stand in for ray.job_submission.JobSubmissionClient. Configure
    methods per-test via ``mock_job_submission_client.return_value.submit_job.return_value = "..."``.
    """
    mock_class = MagicMock()
    monkeypatch.setattr(
        "ray.job_submission.JobSubmissionClient",
        mock_class,
        raising=False,
    )
    return mock_class


@pytest.fixture
def fastapi_client(monkeypatch, ray_enabled_env, mock_job_submission_client):
    """A TestClient bound to the Backend app, with Ray-enabled env + mocked
    JobSubmissionClient. Identity auth is disabled by the autouse test fixture.

    Sets required env vars that main.py validates at module load time so
    that importing ``from main import app`` succeeds in the test environment
    without a running Docker stack.
    """
    # Provide stub values for env vars main.py requires at import time.
    # Only set if not already present so a real .env still wins.
    for _var, _default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        if not os.environ.get(_var):
            monkeypatch.setenv(_var, _default)
    from fastapi.testclient import TestClient
    from main import app  # noqa: sys.path set above
    return TestClient(app)
