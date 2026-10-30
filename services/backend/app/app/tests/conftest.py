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
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Unit tests deliberately opt into process-local state. Production defaults to
# Redis; tests must not receive memory state through an implicit fallback.
os.environ.setdefault("BACKEND_STATE_STORE_MODE", "memory")
# Importing main runs the plugin seam, which empties its site (#1340): keep
# test runs away from the default /tmp/atlas-plugins-site a backend may use.
os.environ.setdefault("BACKEND_PLUGINS_SITE_DIR", tempfile.mkdtemp(prefix="atlas-test-plugin-site-"))

# Ensure ``app/`` is on sys.path so bare imports like ``import ray_client``
# and ``from main import app`` resolve correctly regardless of where pytest
# is invoked from.
_APP_DIR = str(Path(__file__).parent.parent)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

# Load the pinned Ray SDK (requirements.txt) before any test module is
# collected. Several modules install a bare ``ray`` stub when ``ray`` is not
# yet in sys.modules; importing the real SDK first keeps those stubs inert, so
# the actual-SDK tests in test_ray_client/test_ray_routes really run.
try:
    import ray  # noqa: F401
    import ray.job_submission  # noqa: F401
    import ray.dashboard.modules.job.common  # noqa: F401
except ImportError:
    pass


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
    # #817: "real .env wins" affordance — the stub is applied ONLY when the var
    # is absent, so a developer can point these tests at a live stack by
    # exporting real KONG_URL / SUPABASE_SERVICE_KEY / DATABASE_URL. Trade-off:
    # an exported value silently overrides the stub, making the fixture
    # shell-dependent. It's inert for the unit tests here (they never open those
    # connections), but if you see a test behave differently between shells,
    # check for one of these exported in your environment.
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


class _OfflineRayDashboard:
    """Drive the real pinned SDK without opening any network connection."""

    def __init__(self):
        self.outcome = "success"
        self.preflight = None
        self.calls = []
        self.accepted = []

    @staticmethod
    def response(status, payload):
        import json
        import requests

        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(payload).encode()
        return response

    def request(self, method, url, **kwargs):
        import requests

        self.calls.append((method, url, kwargs))
        if self.preflight and self.preflight in url:
            raise requests.ReadTimeout("synthetic preflight failure")
        if url.endswith("/api/version"):
            return self.response(200, {"version": "4", "ray_version": "2.56.0"})
        if method == "GET" and "/api/jobs/" in url:
            if self.outcome != "duplicate":
                return self.response(404, "synthetic job missing")
            return self.response(200, {
                "type": "SUBMISSION", "status": "RUNNING", "entrypoint": "echo synthetic",
                "submission_id": url.rsplit("/", 1)[-1],
            })
        assert method == "POST" and url.endswith("/api/jobs/")
        return self.submit_response(kwargs["json"]["submission_id"])

    def submit_response(self, submission_id):
        import requests

        if self.outcome in {"rejected", "duplicate"}:
            return self.response(400, "synthetic explicit rejection")
        self.accepted.append(submission_id)
        failures = {
            "connection": requests.ConnectionError,
            "chunked": requests.exceptions.ChunkedEncodingError,
            "decoding": requests.exceptions.ContentDecodingError,
            "timeout": requests.ReadTimeout,
        }
        if self.outcome in failures:
            raise failures[self.outcome]("synthetic accepted job, lost reply")
        response = self.response(200, {"job_id": submission_id, "submission_id": submission_id})
        if self.outcome == "malformed-json":
            response._content = b"not-json-after-acceptance"
        elif self.outcome == "missing-field":
            response = self.response(200, {"submission_id": submission_id})
        elif self.outcome == "wrong-id":
            response = self.response(200, {"job_id": submission_id, "submission_id": "raysubmit_other"})
        return response


@pytest.fixture
def offline_ray_dashboard(monkeypatch):
    """Point RayClient at the real pinned SDK, served by _OfflineRayDashboard."""
    import ray
    import requests
    import ray_client

    # conftest imports the pinned SDK before collection; a stub here means the
    # real SDK is missing or shadowed, which must fail rather than skip.
    assert getattr(ray, "__file__", None), "real Ray SDK is shadowed by a test stub"
    assert ray.__version__ == "2.56.0"
    dashboard = _OfflineRayDashboard()
    monkeypatch.setattr(requests, "request", dashboard.request)
    monkeypatch.setenv("RAY_ADDRESS", "ray://synthetic-ray:10001")
    for name in ("RAY_DASHBOARD_URL", "RAY_API_SERVER_ADDRESS"):
        monkeypatch.setenv(name, "http://synthetic-ray.invalid:8265")
    monkeypatch.setenv("RAY_JOB_API_TOKEN", "ray-test-token")
    monkeypatch.setattr(ray_client.RayClient, "_instance", None)
    return dashboard
