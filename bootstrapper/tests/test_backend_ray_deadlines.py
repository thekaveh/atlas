"""CI contract for finite Ray control-plane deadlines (#1170).

Loads the backend's real ``ray_client.py`` with a stubbed ``ray`` package
(the SDK itself is deliberately not a CI dependency) and pins:

- the transport-deadline seam: defaults injected, explicit values preserved,
  fail-closed when the pinned SDK shape changes;
- timeout classification: an unanswered submission is AMBIGUOUS and carries
  the stable submission_id; reads/stops raise the reconcilable
  control-plane timeout;
- the transport bound itself, end to end: ``requests`` with the exact
  injected ``(connect, read)`` tuple abandons an accept-and-never-reply
  socket within the read deadline — the inert-control-plane scenario the
  ticket names, minus the SDK layer verified against the pinned source.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
import threading
import time
import types
from pathlib import Path

import pytest
import requests

BACKEND_APP = Path(__file__).resolve().parents[2] / "services" / "backend" / "app" / "app"


def _load_ray_client():
    if "ray" not in sys.modules:
        ray_stub = types.ModuleType("ray")
        job_stub = types.ModuleType("ray.job_submission")
        job_stub.JobSubmissionClient = None  # per-test replacement
        ray_stub.job_submission = job_stub
        sys.modules["ray"] = ray_stub
        sys.modules["ray.job_submission"] = job_stub
    spec = importlib.util.spec_from_file_location(
        "atlas_backend_ray_client", BACKEND_APP / "ray_client.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ray_client = _load_ray_client()


def test_bind_injects_defaults_and_preserves_explicit_timeouts():
    calls = []

    class _Client:
        def _do_request(self, method, endpoint, **kwargs):
            calls.append(kwargs)

    client = _Client()
    ray_client._bind_transport_deadline(client)
    client._do_request("GET", "/api/version")
    client._do_request("POST", "/api/jobs/", timeout=2.0)

    assert calls[0]["timeout"] == (
        ray_client._RAY_CONNECT_TIMEOUT_SECONDS,
        ray_client._RAY_READ_TIMEOUT_SECONDS,
    )
    assert calls[1]["timeout"] == 2.0


def test_bind_fails_closed_when_the_pinned_seam_disappears():
    class _NoSeam:
        pass

    with pytest.raises(RuntimeError, match="#1170"):
        ray_client._bind_transport_deadline(_NoSeam())


def _client_with(fake_class, monkeypatch):
    monkeypatch.setenv("RAY_ADDRESS", "ray://ray-head:10001")
    monkeypatch.delenv("RAY_DASHBOARD_URL", raising=False)
    sys.modules["ray.job_submission"].JobSubmissionClient = fake_class
    return ray_client.RayClient()


def test_submission_timeout_is_ambiguous_and_reconcilable(monkeypatch):
    class _Fake:
        def __init__(self, _addr):
            pass

        def _do_request(self, *a, **k):  # seam for the bind
            pass

        def submit_job(self, **kwargs):
            raise requests.exceptions.ReadTimeout("no reply")

    client = _client_with(_Fake, monkeypatch)
    with pytest.raises(ray_client.RaySubmissionAmbiguousError) as excinfo:
        client.submit_job(
            ray_client.RayJobSubmission(
                entrypoint="echo hi", submission_id="raysubmit_amb"
            )
        )
    assert excinfo.value.submission_id == "raysubmit_amb"
    assert "raysubmit_amb" in str(excinfo.value)


def test_stop_timeout_raises_the_reconcilable_control_plane_error(monkeypatch):
    class _Fake:
        def __init__(self, _addr):
            pass

        def _do_request(self, *a, **k):
            pass

        def stop_job(self, job_id):
            raise requests.exceptions.ConnectTimeout()

    client = _client_with(_Fake, monkeypatch)
    with pytest.raises(ray_client.RayControlPlaneTimeoutError) as excinfo:
        client.stop_job("raysubmit_x")
    assert excinfo.value.job_id == "raysubmit_x"


def test_injected_timeout_tuple_abandons_an_inert_control_plane():
    """The exact scenario #1170 names: a control plane that accepts the TCP
    connection and never replies. With the injected (connect, read) bound,
    requests abandons the attempt within the read deadline instead of
    retaining the thread indefinitely."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    port = listener.getsockname()[1]
    held: list[socket.socket] = []

    def _accept_and_go_silent():
        try:
            conn, _ = listener.accept()
            held.append(conn)
            conn.settimeout(0.2)
            try:
                while conn.recv(65536):
                    pass
            except (TimeoutError, OSError):
                pass
        except OSError:
            pass

    thread = threading.Thread(target=_accept_and_go_silent, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        with pytest.raises(requests.exceptions.ReadTimeout):
            requests.request(
                "GET",
                f"http://127.0.0.1:{port}/api/version",
                timeout=(ray_client._RAY_CONNECT_TIMEOUT_SECONDS, 0.5),
            )
        elapsed = time.monotonic() - started
        assert elapsed < 10  # bounded, generous slack for loaded runners
    finally:
        for conn in held:
            try:
                conn.close()
            except OSError:
                pass
        listener.close()
