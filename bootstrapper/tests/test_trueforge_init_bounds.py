"""Contracts for trueforge-init: HTTP bounds (#1173) and managed-MCP-connector
reconciliation when the mcp-servers family is disabled (#1174).

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
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlparse
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

    # The catalog read runs first and its failure is terminal and names the
    # operation; the key is not rotated (deleting it first left TrueForge
    # holding a deleted key when the catalog read then failed).
    assert result.returncode != 0
    assert "FAILED at litellm model catalog read" in result.stderr
    assert "falling back to the master key" not in result.stderr
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


class _FakeStackServer(http.server.ThreadingHTTPServer):
    """Stateful TrueForge + LiteLLM stand-in for reconciliation tests.

    Keeps an in-memory MCP-connector store keyed by manifest name, so
    enable -> disable -> enable convergence and user-connector preservation
    are observable without Docker or the live stack.
    """

    model_ids: tuple[str, ...] = ("test-model",)
    provider_manifest: dict

    def _store_provider(self, manifest: dict) -> dict:
        self.provider_manifest = manifest
        public = dict(manifest, auth={"api_key": _masked_key(manifest["auth"]["api_key"])})
        return {"data": {"name": manifest["name"], "manifest": public}}

    def _key_request(self, path: str, payload: dict) -> tuple[int, dict]:
        if path == "/key/generate":
            key = f"sk-fake-virtual-key-{len(self.keys) + 1}"
            token = hashlib.sha256(key.encode()).hexdigest()
            self.keys[token] = dict(payload, token=token)
            return 200, {"key": key}
        if path == "/key/update":
            token = payload["key"]
            self.keys[token]["metadata"] = payload["metadata"]
            return 200, dict(self.keys[token], key=token)
        if path == "/key/delete":
            for token in payload.get("keys", []):
                self.keys.pop(token, None)
            return 200, {}
        return 404, {}

    def _get_request(self, path: str) -> tuple[int, dict]:
        if path == "/healthz":
            return 200, {}
        if path == "/v1/models":
            return 200, {"data": [{"id": model_id} for model_id in self.model_ids]}
        if path.startswith("/key/list?"):
            return 200, {"keys": list(self.keys.values())}
        prefix = "/api/v1/settings/mcp-servers/"
        if path.startswith(prefix):
            name = path[len(prefix):]
            manifest = self.connectors.get(name)
            if manifest is not None:
                return 200, {"data": {"name": name, "manifest": manifest, "auth_status": "none"}}
        return 404, {"error": "not found"}

    def __init__(self) -> None:
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _json(self, code: int, payload: dict) -> None:
                import json as _json

                body = _json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                self._json(*server._get_request(self.path))

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                self._json(*server._key_request(self.path, payload))

            def do_PUT(self) -> None:
                import json as _json

                length = int(self.headers.get("Content-Length", "0"))
                payload = _json.loads(self.rfile.read(length) or b"{}")
                manifest = payload.get("manifest", {})
                if self.path == "/api/v1/settings/mcp-servers":
                    server.connectors[manifest.get("name")] = manifest
                    server.mcp_put_count += 1
                    return self._json(200, {})
                if self.path == "/api/v1/settings/model-providers":
                    return self._json(200, server._store_provider(manifest))
                return self._json(404, {"error": "not found"})

            def log_message(self, *args: object) -> None:  # noqa: D102
                pass

        super().__init__(("127.0.0.1", 0), Handler)
        self.port = self.server_address[1]
        self.connectors: dict[str, dict] = {}
        self.keys: dict[str, dict] = {}
        self.mcp_put_count = 0
        threading.Thread(target=self.serve_forever, daemon=True).start()


def _run_against_fake_stack(server: _FakeStackServer, mcp_scale: str) -> subprocess.CompletedProcess:
    result = _run_init(
        {
            "TRUEFORGE_URL": f"http://127.0.0.1:{server.port}",
            "LITELLM_BASE_URL": f"http://127.0.0.1:{server.port}",
            "MCP_SERVERS_SCALE": mcp_scale,
            # comfortable budgets: nothing hangs in these tests
            "TRUEFORGE_INIT_TOTAL_BUDGET_MS": "30000",
            "TRUEFORGE_INIT_REQUEST_TIMEOUT_MS": "5000",
        },
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    _assert_no_secret_leak(result)
    return result


DISABLED_CONNECTOR_URL = "http://atlas-mcp-servers.disabled.invalid/mcp"


@pytest.mark.parametrize("count", [99, 100, 120])
def test_colliding_model_names_fit_the_resource_name_contract(count: int) -> None:
    model_ids = ["a" * 70 + f"/{index}" for index in range(count)]
    server = _FakeStackServer()
    server.model_ids = tuple(model_ids)
    try:
        _run_against_fake_stack(server, "0")
        models = server.provider_manifest["models"]
        names = [model["name"] for model in models]
        assert [model["model_id"] for model in models] == model_ids
        assert len(set(names)) == count
        assert all(re.fullmatch(r"[a-z][a-z0-9-]{0,62}[a-z0-9]", name) for name in names)
        # Keep names already usable by saved agents, including suffixes 2–99.
        legacy_names = ["a" * 64] + ["a" * 61 + f"-{n}" for n in range(2, 100)]
        assert names[:99] == legacy_names[:count]
    finally:
        server.shutdown()
        server.server_close()


def test_enable_disable_enable_converges_without_stale_or_duplicate_connector() -> None:
    server = _FakeStackServer()
    try:
        _run_against_fake_stack(server, "1")
        assert server.connectors["atlas-tools"]["url"] == "http://mcp-servers:8000/mcp"

        result = _run_against_fake_stack(server, "0")
        parked = server.connectors["atlas-tools"]
        assert parked["url"] == DISABLED_CONNECTOR_URL
        assert "disabled" in parked["description"]
        assert "parked" in result.stdout

        # A second disabled run converges without another write.
        puts_before = server.mcp_put_count
        result = _run_against_fake_stack(server, "0")
        assert server.mcp_put_count == puts_before
        assert "already parked" in result.stdout

        _run_against_fake_stack(server, "1")
        assert server.connectors["atlas-tools"]["url"] == "http://mcp-servers:8000/mcp"
        assert list(server.connectors).count("atlas-tools") == 1
    finally:
        server.shutdown()


def test_user_created_connectors_survive_reconciliation_untouched() -> None:
    server = _FakeStackServer()
    user_connector = {
        "type": "remote",
        "name": "my-own-tools",
        "url": "http://example.internal/mcp",
        "description": "user-created connector",
    }
    server.connectors["my-own-tools"] = dict(user_connector)
    try:
        _run_against_fake_stack(server, "0")
        assert server.connectors["my-own-tools"] == user_connector
        # No managed connector was ever seeded, so none may be invented:
        assert "atlas-tools" not in server.connectors
    finally:
        server.shutdown()


def _rotation_handler(server):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _json(self, code, payload):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass  # a deliberate request timeout can close the test peer

        def do_GET(self):
            self._json(*server.read(self.path))

        def _body(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")

        def do_POST(self):
            self._json(*server.post(self.path, self._body()))

        def do_PUT(self):
            self._json(*server.put(self._body()))

        def log_message(self, *args):
            pass

    return Handler


def _masked_key(key):
    return f"{key[:3]}-***REDACTED***-{key[-3:]}" if len(key) >= 10 else "***REDACTED***"


class _RotationStack(http.server.ThreadingHTTPServer):
    """Pinned key/settings response shapes, with only fake credentials."""

    def __init__(self):
        self.keys = {}
        self.generated = []
        self.deleted = []
        self.provider_mode = "ok"
        self.update_mode = "ok"
        self.delete_mode = "ok"
        self.key_api_down = False
        self.inventory_unavailable = False
        self.provider_entered = threading.Event()
        self.provider_committed = threading.Event()
        self.release_provider = threading.Event()
        self.delayed_key = None
        self.active_key = "sk-legacy-do-not-print"
        self.add_key(self.active_key, "trueforge")
        super().__init__(("127.0.0.1", 0), _rotation_handler(self))
        self.port = self.server_address[1]
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def add_key(self, key, alias, state=None):
        token = hashlib.sha256(key.encode()).hexdigest()
        metadata = {"service": "trueforge", "managed_by": "trueforge-init"}
        if state is not None:
            metadata["rotation_state"] = state
        self.keys[token] = {"token": token, "key_alias": alias, "metadata": metadata}
        return token

    def read(self, path):
        parsed = urlparse(path)
        if parsed.path == "/healthz":
            return 200, {}
        if parsed.path == "/v1/models":
            return 200, {"data": [{"id": "test-model"}]}
        if parsed.path == "/key/list":
            if self.key_api_down or self.inventory_unavailable:
                return 500, {}
            params = parse_qs(parsed.query)
            size = int(params.get("size", ["100"])[0])
            page = int(params.get("page", ["1"])[0])
            rows = list(self.keys.values())
            start = (page - 1) * size
            return 200, {"keys": rows[start:start + size], "total_count": len(rows)}
        return 404, {}

    def post(self, path, body):
        handlers = {"/key/generate": self.generate, "/key/update": self.update, "/key/delete": self.delete}
        handler = handlers.get(path)
        return handler(body) if handler else (404, {})

    def generate(self, body):
        if self.key_api_down:
            return 500, {}
        alias = body["key_alias"]
        key = f"sk-candidate-{len(self.generated) + 1}-do-not-print"
        token = self.add_key(key, alias)
        self.keys[token]["metadata"] = body["metadata"]
        self.generated.append(key)
        return 200, {"key": key, "token_id": token}

    def update(self, body):
        if self.update_mode == "fail":
            return 500, {}
        token = body["key"]
        if token not in self.keys:
            return 404, {}
        self.keys[token]["metadata"] = body["metadata"]  # pinned replacement semantics
        row = dict(self.keys[token], key=token)
        row["metadata"] = json.dumps(row["metadata"])
        if self.update_mode == "wrong-hash":
            row["token"] = "0" * 64
        return 200, row

    def delete(self, body):
        if self.key_api_down:
            return 500, {}
        hashes = list(body.get("keys", []))
        aliases = body.get("key_aliases", [])
        hashes += [token for token, row in self.keys.items() if row["key_alias"] in aliases]
        self.deleted.extend(hashes)
        if self.delete_mode != "echo-only":
            for token in hashes:
                self.keys.pop(token, None)
        return 200, {"deleted_keys": hashes}  # echo is not a removal proof

    def put(self, body):
        manifest = body["manifest"]
        key = manifest.get("auth", {}).get("api_key")
        if self.provider_mode == "fail":
            return 500, {}
        if self.provider_mode == "malformed":
            return 200, {}
        if key == self.delayed_key:
            self.provider_entered.set()
            assert self.release_provider.wait(10), "owned delayed PUT was not released"
        if key is not None:
            self.active_key = key
        if key == self.delayed_key:
            self.provider_committed.set()
        public = dict(manifest)
        if key is not None:
            public["auth"] = {"api_key": _masked_key(key)}
        if self.provider_mode.startswith("missing-"):
            public.pop(self.provider_mode.removeprefix("missing-"), None)
        status = 202 if self.provider_mode == "accepted" else 200
        return status, {"data": {"name": manifest["name"], "manifest": public}}

    def active_key_is_valid(self):
        return hashlib.sha256(self.active_key.encode()).hexdigest() in self.keys

    def close(self):
        self.release_provider.set()
        self.shutdown()
        self.server_close()


def _run_rotation(server, request_timeout="5000"):
    result = _run_init({
        "TRUEFORGE_URL": f"http://127.0.0.1:{server.port}",
        "LITELLM_BASE_URL": f"http://127.0.0.1:{server.port}",
        "MCP_SERVERS_SCALE": "0",
        "TRUEFORGE_INIT_TOTAL_BUDGET_MS": "30000",
        "TRUEFORGE_INIT_REQUEST_TIMEOUT_MS": request_timeout,
    }, timeout=30)
    _assert_no_secret_leak(result)
    for key in ["sk-legacy-do-not-print", *server.generated]:
        assert key not in result.stdout + result.stderr
    return result


def _assert_active_credential(server, expected):
    assert server.active_key == expected
    assert server.active_key_is_valid()


def _confirmed_tokens(server):
    return {token for token, row in server.keys.items() if row["metadata"].get("rotation_state") == "confirmed"}


@pytest.mark.parametrize("mode", ["fail", "malformed", "accepted", "missing-auth", "missing-models"])
def test_failed_provider_confirmation_preserves_all_potential_credentials(mode):
    server = _RotationStack()
    server.provider_mode = mode
    try:
        result = _run_rotation(server)
        assert result.returncode != 0
        assert server.active_key_is_valid()
        assert not server.deleted
        assert all(row["metadata"].get("rotation_state") != "confirmed" for row in server.keys.values())
    finally:
        server.close()


@pytest.mark.parametrize("mode", ["fail", "wrong-hash"])
def test_failed_key_confirmation_does_not_retire_older_keys(mode):
    server = _RotationStack()
    server.update_mode = mode
    try:
        result = _run_rotation(server)
        assert result.returncode != 0
        assert server.active_key_is_valid()
        assert not server.deleted
    finally:
        server.close()


def test_settled_generations_retire_but_legacy_and_pending_keys_remain():
    server = _RotationStack()
    try:
        pending = server.add_key("sk-pending-do-not-print", "trueforge-11111111-1111-4111-8111-111111111111", "pending")
        unknown = server.add_key("sk-user-do-not-print", "trueforge-user-choice", "confirmed")
        unrelated = server.add_key("sk-unrelated-do-not-print", "trueforge-22222222-2222-4222-8222-222222222222", "confirmed")
        server.keys[unrelated]["metadata"]["managed_by"] = "user"
        assert _run_rotation(server).returncode == 0
        first = hashlib.sha256(server.generated[-1].encode()).hexdigest()
        assert server.keys[first]["metadata"]["rotation_state"] == "confirmed"
        assert _run_rotation(server).returncode == 0
        assert server.active_key_is_valid()
        assert first not in server.keys
        assert pending in server.keys and unknown in server.keys and unrelated in server.keys
        assert hashlib.sha256(b"sk-legacy-do-not-print").hexdigest() in server.keys
        assert all(re.fullmatch(r"trueforge-[0-9a-f-]{36}", row["key_alias"]) for token, row in server.keys.items() if token == hashlib.sha256(server.generated[-1].encode()).hexdigest())
    finally:
        server.close()


def test_echoed_delete_response_is_not_treated_as_successful_retirement():
    server = _RotationStack()
    try:
        assert _run_rotation(server).returncode == 0
        server.delete_mode = "echo-only"
        result = _run_rotation(server)
        assert result.returncode != 0
        assert server.active_key_is_valid()
    finally:
        server.close()


def test_delayed_prior_provider_write_keeps_its_pending_credential_valid():
    server = _RotationStack()
    server.delayed_key = "sk-candidate-1-do-not-print"
    result = []
    first = threading.Thread(target=lambda: result.append(_run_rotation(server, "200")))
    try:
        first.start()
        assert server.provider_entered.wait(5)
        first.join(5)
        assert not first.is_alive()
        assert result[0].returncode != 0
        assert _run_rotation(server).returncode == 0
        server.release_provider.set()
        assert server.provider_committed.wait(5)
        _assert_active_credential(server, server.delayed_key)
        assert _run_rotation(server).returncode == 0
        assert server.active_key_is_valid()
        pending_hash = hashlib.sha256(server.delayed_key.encode()).hexdigest()
        assert pending_hash in server.keys
    finally:
        server.close()
        first.join(5)


def test_retirement_inventory_reads_all_pages_and_string_metadata():
    server = _RotationStack()
    try:
        old = set()
        for index in range(110):
            token = server.add_key(f"sk-settled-{index}", f"trueforge-{index:08x}-1111-4111-8111-111111111111", "confirmed")
            old.add(token)
            server.keys[token]["metadata"] = json.dumps(server.keys[token]["metadata"])
        assert _run_rotation(server).returncode == 0
        assert old.isdisjoint(server.keys)
        assert set(server.deleted) == old
        assert server.active_key_is_valid()
    finally:
        server.close()


def test_cleanup_does_not_expand_its_pre_mint_snapshot():
    class LaterRowStack(_RotationStack):
        def generate(self, body):
            response = super().generate(body)
            self.later = self.add_key("sk-other-settled-generation", "trueforge-33333333-3333-4333-8333-333333333333", "confirmed")
            return response

    server = LaterRowStack()
    try:
        assert _run_rotation(server).returncode == 0
        assert server.later in server.keys
        assert not server.deleted
        assert server.active_key_is_valid()
    finally:
        server.close()


def test_overlapping_successes_preserve_both_candidates_then_converge():
    server = _RotationStack()
    server.delayed_key = "sk-candidate-1-do-not-print"
    results = []
    first = threading.Thread(target=lambda: results.append(_run_rotation(server)))
    try:
        first.start()
        assert server.provider_entered.wait(5)
        assert _run_rotation(server).returncode == 0
        server.release_provider.set()
        first.join(5)
        assert not first.is_alive()
        assert results[0].returncode == 0
        _assert_active_credential(server, server.delayed_key)
        settled = _confirmed_tokens(server)
        assert len(settled) == 2
        assert _run_rotation(server).returncode == 0
        assert settled.isdisjoint(server.keys)
        assert server.active_key_is_valid()
    finally:
        server.close()
        first.join(5)


@pytest.mark.parametrize("all_keys_down", [False, True])
def test_unavailable_inventory_defers_retirement_and_preserves_seeding(all_keys_down):
    server = _RotationStack()
    server.inventory_unavailable = True
    server.key_api_down = all_keys_down
    try:
        result = _run_rotation(server)
        assert result.returncode == 0
        expected = SECRET_ENV["LITELLM_MASTER_KEY"] if all_keys_down else server.generated[-1]
        assert server.active_key == expected
        assert "retirement deferred" in result.stdout + result.stderr
        assert not server.deleted
    finally:
        server.close()


@pytest.mark.parametrize("path,operation", [
    ("/v1/models", "litellm model catalog read"),
    ("/api/v1/settings/mcp-servers/atlas-tools", "mcp connector reconcile read"),
])
def test_invalid_read_json_names_operation_without_logging_response_body(path, operation):
    class MalformedReadStack(_RotationStack):
        def read(self, request_path):
            return (200, b"PRIVATE-RESPONSE-BODY") if request_path == path else super().read(request_path)

    server = MalformedReadStack()
    try:
        result = _run_rotation(server)
        output = result.stdout + result.stderr
        assert result.returncode != 0
        assert "PRIVATE-" not in output
        assert f"FAILED at {operation}: invalid JSON response" in output
    finally:
        server.close()
