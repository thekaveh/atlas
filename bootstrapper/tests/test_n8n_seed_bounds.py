from __future__ import annotations

import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import threading
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
SEEDER = ROOT / "services/n8n/init/scripts/seed-workflows.js"


class _HangingHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.recv(4096)
        threading.Event().wait(5)


class _TricklingHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.recv(4096)
        self.request.sendall(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        )
        try:
            for _ in range(20):
                self.request.sendall(b"1\r\nx\r\n")
                time.sleep(0.02)
            self.request.sendall(b"0\r\n\r\n")
        except OSError:
            pass


class _ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def test_http_request_timeout_terminates_stalled_peer():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    with _ThreadingServer(("127.0.0.1", 0), _HangingHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = {
            **os.environ,
            "N8N_SEED_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
            "N8N_SEED_HTTP_TIMEOUT_MS": "50",
        }
        result = subprocess.run(
            [
                node,
                "-e",
                f"require({str(SEEDER)!r}).request('GET', '/hang').then(r => "
                "process.stdout.write(String(r.status)))",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        server.shutdown()

    assert result.returncode == 0
    assert result.stdout == "0"


def test_child_process_timeout_terminates_stalled_command():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    result = subprocess.run(
        [
            node,
            "-e",
            f"const s=require({str(SEEDER)!r}); const r=s.runCommand("
            "process.execPath,['-e','setTimeout(() => {}, 5000)'],50); "
            "process.stdout.write(String(Boolean(r.error)))",
        ],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "true"


def test_http_request_timeout_is_absolute_during_trickled_response():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    with _ThreadingServer(("127.0.0.1", 0), _TricklingHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = {
            **os.environ,
            "N8N_SEED_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
            "N8N_SEED_HTTP_TIMEOUT_MS": "60",
        }
        started = time.monotonic()
        result = subprocess.run(
            [
                node,
                "-e",
                f"require({str(SEEDER)!r}).request('GET', '/trickle').then(r => "
                "process.stdout.write(String(r.status)))",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        elapsed = time.monotonic() - started
        server.shutdown()

    assert result.returncode == 0
    assert result.stdout == "0"
    assert elapsed < 0.3
