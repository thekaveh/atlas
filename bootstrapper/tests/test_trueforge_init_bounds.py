"""Bounds contract for trueforge-init's bootstrap HTTP calls (#1173).

The init script must never run (or hang a `docker compose up` dependency
chain) indefinitely: every fetch carries an AbortSignal, one finite total
budget covers the whole bootstrap, failures exit nonzero naming the
operation without leaking credentials, and SIGTERM/SIGINT abort in-flight
work immediately.

These tests run the real script under the host's `node` against local
sockets that accept connections and never reply — the exact adversarial
peer the ticket describes. No Docker and no Atlas stack are involved.
"""

from __future__ import annotations

import http.server
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INIT_SCRIPT = REPO_ROOT / "services" / "trueforge" / "init" / "scripts" / "init.mjs"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None, reason="node is required to exercise the init script"
)

# Deliberately fake, recognizable credentials: the assertions below prove
# they never surface in the process output on any failure path.
SECRET_ENV = {
    "LITELLM_MASTER_KEY": "sk-secret-master-key-do-not-print",
    "TRUEFORGE_API_KEY": "tf-secret-service-credential",
}


class _HangingServer:
    """Accepts TCP connections, reads the request, and never responds."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._open: list[socket.socket] = []
        self._closing = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._closing:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self._open.append(conn)
            # Drain whatever arrives so the client finishes sending, then
            # go silent forever — the connection stays open, unanswered.
            conn.settimeout(0.2)
            try:
                while conn.recv(65536):
                    pass
            except (TimeoutError, OSError):
                pass

    def close(self) -> None:
        self._closing = True
        for conn in self._open:
            try:
                conn.close()
            except OSError:
                pass
        self._sock.close()


class _HealthzOkServer(http.server.ThreadingHTTPServer):
    """Answers 200 to every request (used as a healthy TrueForge stand-in)."""

    def __init__(self) -> None:
        class Handler(http.server.BaseHTTPRequestHandler):
            def _ok(self) -> None:  # pragma: no cover - trivial plumbing
                body = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _ok
            do_PUT = _ok
            do_POST = _ok

            def log_message(self, *args: object) -> None:  # noqa: D102
                pass

        super().__init__(("127.0.0.1", 0), Handler)
        self.port = self.server_address[1]
        threading.Thread(target=self.serve_forever, daemon=True).start()


def _run_init(env_overrides: dict[str, str], timeout: float) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        **SECRET_ENV,
        # Tiny budgets so a hung peer is decided in seconds; the wall-clock
        # assertions stay generous for loaded CI runners.
        "TRUEFORGE_INIT_TOTAL_BUDGET_MS": "4000",
        "TRUEFORGE_INIT_REQUEST_TIMEOUT_MS": "700",
        "TRUEFORGE_INIT_HEALTH_TIMEOUT_MS": "400",
        "TRUEFORGE_INIT_HEALTH_INTERVAL_MS": "150",
        **env_overrides,
    }
    return subprocess.run(
        [NODE, str(INIT_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _assert_no_secret_leak(result: subprocess.CompletedProcess) -> None:
    output = result.stdout + result.stderr
    for secret in SECRET_ENV.values():
        assert secret not in output, "credential leaked into init output"


def test_hung_healthz_endpoint_is_abandoned_within_the_total_budget() -> None:
    hang = _HangingServer()
    try:
        started = time.monotonic()
        result = _run_init(
            {
                "TRUEFORGE_URL": f"http://127.0.0.1:{hang.port}",
                "LITELLM_BASE_URL": f"http://127.0.0.1:{hang.port}",
            },
            timeout=30,
        )
        elapsed = time.monotonic() - started
    finally:
        hang.close()

    assert result.returncode != 0
    # 4 s budget, generous slack for CI: far below "runs until the loop count".
    assert elapsed < 20
    assert "FAILED at" in result.stderr
    assert "healthz" in result.stderr
    _assert_no_secret_leak(result)


def test_hung_litellm_endpoint_fails_naming_the_operation() -> None:
    healthy = _HealthzOkServer()
    hang = _HangingServer()
    try:
        result = _run_init(
            {
                "TRUEFORGE_URL": f"http://127.0.0.1:{healthy.port}",
                "LITELLM_BASE_URL": f"http://127.0.0.1:{hang.port}",
            },
            timeout=30,
        )
    finally:
        healthy.shutdown()
        hang.close()

    # Key delete/generate time out (warn + master-key fallback, no in-run
    # retry of the credential write), then the catalog read's failure is
    # terminal and names the operation.
    assert result.returncode != 0
    assert "FAILED at litellm model catalog read" in result.stderr
    assert "falling back to the master key" in result.stderr
    _assert_no_secret_leak(result)


def test_sigterm_aborts_outstanding_http_work_immediately() -> None:
    hang = _HangingServer()
    proc = subprocess.Popen(
        [NODE, str(INIT_SCRIPT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            **SECRET_ENV,
            "TRUEFORGE_URL": f"http://127.0.0.1:{hang.port}",
            "LITELLM_BASE_URL": f"http://127.0.0.1:{hang.port}",
            # Generous budgets: only the signal can end this run quickly.
            "TRUEFORGE_INIT_TOTAL_BUDGET_MS": "60000",
            "TRUEFORGE_INIT_REQUEST_TIMEOUT_MS": "30000",
            "TRUEFORGE_INIT_HEALTH_TIMEOUT_MS": "30000",
        },
    )
    try:
        time.sleep(1.0)  # let it get into the hung healthz fetch
        started = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=10)
        elapsed = time.monotonic() - started
    finally:
        hang.close()
        if proc.poll() is None:  # pragma: no cover - cleanup guard
            proc.kill()

    assert proc.returncode != 0
    assert elapsed < 5
    assert "SIGTERM" in stderr
    for secret in SECRET_ENV.values():
        assert secret not in stdout + stderr
