from __future__ import annotations

import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
            for _ in range(40):
                self.request.sendall(b"1\r\nx\r\n")
                time.sleep(0.03)
            self.request.sendall(b"0\r\n\r\n")
        except OSError:
            pass


class _ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _ReconcileHandler(BaseHTTPRequestHandler):
    delete_status = 204

    def do_POST(self):
        self.send_response(200)
        self.end_headers()

    def do_DELETE(self):
        if self.delete_status is None:
            threading.Event().wait(1)
            return
        self.send_response(self.delete_status)
        self.end_headers()

    def log_message(self, *_args):
        pass


class _OversizedBodyHandler(BaseHTTPRequestHandler):
    chunked = False

    def do_GET(self):
        body = b"x" * 256
        self.send_response(200)
        if self.chunked:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for offset in range(0, len(body), 32):
                    chunk = body[offset : offset + 32]
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
            except OSError:
                pass
        else:
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *_args):
        pass


class _Utf8SplitHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for chunk in (b'{"name":"caf\xc3', b'\xa9"}'):
            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            self.wfile.flush()
            time.sleep(0.03)
        self.wfile.write(b"0\r\n\r\n")

    def log_message(self, *_args):
        pass


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
    assert elapsed < 0.8


@pytest.mark.parametrize(
    ("delete_status", "expected", "message"),
    [
        (204, "true", "reconciled: removed orphaned workflow"),
        (404, "false", "deletion returned HTTP 404"),
        (500, "false", "deletion returned HTTP 500"),
        (None, "false", "deletion returned HTTP none"),
    ],
)
def test_orphan_reconcile_reports_delete_result(delete_status, expected, message):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    _ReconcileHandler.delete_status = delete_status
    with ThreadingHTTPServer(("127.0.0.1", 0), _ReconcileHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = {
            **os.environ,
            "N8N_SEED_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
            "N8N_SEED_HTTP_TIMEOUT_MS": "50",
            "N8N_API_KEY": "test-key",
        }
        result = subprocess.run(
            [
                node,
                "-e",
                "console.log=(m)=>process.stderr.write(String(m)+'\\n');"
                f"require({str(SEEDER)!r}).removeOrphan('atlas-consumer-old').then(r => "
                "process.stdout.write(String(r)))",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        server.shutdown()

    assert result.returncode == 0
    assert result.stdout == expected
    assert message in result.stderr
    assert ("reconciled: removed" in result.stderr) is (delete_status == 204)


@pytest.mark.parametrize("chunked", [False, True])
def test_http_response_body_limit_covers_fixed_and_chunked_responses(chunked):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    _OversizedBodyHandler.chunked = chunked
    with ThreadingHTTPServer(("127.0.0.1", 0), _OversizedBodyHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = {
            **os.environ,
            "N8N_SEED_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
            "N8N_SEED_MAX_RESPONSE_BYTES": "64",
        }
        result = subprocess.run(
            [
                node,
                "-e",
                f"require({str(SEEDER)!r}).request('GET', '/large').then(r => "
                "process.stdout.write(r.status + ':' + r.body.length))",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        server.shutdown()

    assert result.returncode == 0
    assert result.stdout == "0:0"


def test_http_response_decodes_split_utf8_once():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    with ThreadingHTTPServer(("127.0.0.1", 0), _Utf8SplitHandler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = {
            **os.environ,
            "N8N_SEED_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
        }
        result = subprocess.run(
            [
                node,
                "-e",
                f"require({str(SEEDER)!r}).request('GET', '/utf8').then(r => "
                "process.stdout.write(r.body))",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        server.shutdown()

    assert result.returncode == 0
    assert result.stdout == '{"name":"café"}'
