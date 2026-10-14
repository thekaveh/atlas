from __future__ import annotations

import os
import json
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


# Execute the real entry point while denying every real transport and command.
_SEED_PAGE_PRELOAD = r"""
const fs=require('fs'), Module=require('module'), EventEmitter=require('events');
const config=JSON.parse(fs.readFileSync(process.env.OWNED_SEED_CONFIG,'utf8'));
const counts={list:0,import:0,deactivate:0,delete:0,probe:0,events:[],unexpected:0};
const original=Module._load;
Module._load=function(id,parent,isMain){
 if(id==='http'||id==='https')return {request(options,callback){
  if(options.hostname!=='owned-n8n.invalid')throw Error('unexpected transport');
  const req=new EventEmitter();req.setTimeout=()=>req;req.write=()=>{};
  req.destroy=e=>req.emit('error',e);
  req.end=()=>process.nextTick(()=>{
   const res=new EventEmitter();res.headers={};res.statusCode=200;
   res.destroy=e=>res.emit('error',e);let body='{}';
   counts.events.push(options.method+' '+options.path);
   if(options.path==='/healthz'){}
   else if(options.method==='GET'&&options.path.startsWith('/api/v1/workflows?')){
    body=config.pages[Math.min(counts.list,config.pages.length-1)];counts.list++;
   }else if(options.method==='POST'&&options.path.endsWith('/deactivate')){
    counts.deactivate++;res.statusCode=config.deactivate_status||200;
   }else if(options.method==='DELETE'&&options.path.startsWith('/api/v1/workflows/')){
    counts.delete++;res.statusCode=config.delete_status||204;
   }else if(options.path==='/owned-probe')counts.probe++;
   else{counts.unexpected++;throw Error('unexpected route');}
   callback(res);res.emit('data',Buffer.from(body));res.emit('end');
  });return req;
 }};
 if(id==='child_process')return {spawnSync(command,args){
  if(command!=='n8n'||args[0]!=='import:workflow')throw Error('unexpected command');
  counts.import++;return {status:config.import_status||0};
 }};
 return original.call(this,id,parent,isMain);
};
process.on('exit',()=>fs.writeFileSync(process.env.OWNED_SEED_PROOF,JSON.stringify(counts)));
"""


def _run_seed_pages(tmp_path, pages, **settings):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    workflow = tmp_path / "owned-workflow.json"
    workflow.write_text('{"active": false}')
    plan = tmp_path / "owned-plan.json"
    plan.write_text(json.dumps({"namespace": "atlas-consumer-", "workflows": [{
        "id": "owned", "seed_id": "atlas-consumer-declared", "consumer": "owned",
        "file": str(workflow), "active": "false", "webhooks": [
            {"method": "GET", "path": "/owned-probe", "expect_status": 200, "probe": True},
        ],
    }]}))
    preload = tmp_path / "owned-preload.js"
    preload.write_text(_SEED_PAGE_PRELOAD)
    config, proof = tmp_path / "owned-config.json", tmp_path / "owned-proof.json"
    config.write_text(json.dumps({"pages": [p if isinstance(p, str) else json.dumps(p) for p in pages], **settings}))
    env = {**os.environ, "N8N_SEED_PLAN": str(plan), "N8N_SEED_BASE_URL": "http://owned-n8n.invalid",
           "N8N_API_KEY": "owned-test-key", "OWNED_SEED_CONFIG": str(config), "OWNED_SEED_PROOF": str(proof)}
    result = subprocess.run([node, "--require", str(preload), str(SEEDER)], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    counts = json.loads(proof.read_text())
    assert counts["unexpected"] == 0
    assert counts["import"] == 1
    return counts, result.stdout


def _inventory_page(cursor="page-two"):
    return {"data": [{"id": "atlas-consumer-owned-orphan"}], "nextCursor": cursor}


@pytest.mark.parametrize("bad_page", [
    "SENTINEL_INVALID_PAGE", None, [], {}, {"data": None}, {"data": ""},
    {"data": {}}, {"data": [None]}, {"data": [[]]}, {"data": [{}]},
    {"data": [{"id": None}]}, {"data": [{"id": ""}]},
    {"data": [{"id": " "}]},
    {"data": [{"id": "atlas-consumer-\ud800"}]},
    *[{"data": [], "nextCursor": cursor} for cursor in [False, 0, 1, [], {}, ["token"], "\ud800"]],
])
def test_seed_rejects_incomplete_inventory_before_any_orphan_mutation(tmp_path, bad_page):
    counts, output = _run_seed_pages(tmp_path, [_inventory_page(), bad_page])

    assert counts["list"] == 2
    assert counts["deactivate"] == counts["delete"] == 0
    assert counts["probe"] == 1
    assert "seed summary: imported=1 failed=0" in output
    assert "SENTINEL_INVALID_PAGE" not in output


@pytest.mark.parametrize("cursors, expected", [(["A", "A"], 2), (["A", "B", "A"], 3)])
def test_seed_refuses_repeated_or_cyclic_cursor_before_cleanup(tmp_path, cursors, expected):
    counts, output = _run_seed_pages(tmp_path, [_inventory_page(cursor) for cursor in cursors])

    assert counts["list"] == expected
    assert counts["deactivate"] == counts["delete"] == 0
    assert counts["probe"] == 1
    assert "seed summary:" in output


@pytest.mark.parametrize("terminal", [True, False])
def test_seed_requires_completed_inventory_at_page_bound(tmp_path, terminal):
    pages = [_inventory_page(str(index)) for index in range(1000)]
    if terminal:
        pages[-1]["nextCursor"] = None
    counts, output = _run_seed_pages(tmp_path, pages)

    assert counts["list"] == 1000
    assert counts["deactivate"] == counts["delete"] == (1 if terminal else 0)
    assert counts["probe"] == 1
    assert "seed summary:" in output


@pytest.mark.parametrize("terminal", [{}, {"nextCursor": None}, {"nextCursor": ""}])
def test_seed_preserves_terminal_inventory_and_deletes_each_owned_orphan_once(tmp_path, terminal):
    pages = [_inventory_page("opaque +/token?&"), {"data": [
        {"id": "atlas-consumer-owned-orphan"}, {"id": "atlas-consumer-declared"},
        {"id": "foreign-workflow"},
    ], **terminal}]
    counts, output = _run_seed_pages(tmp_path, pages, import_status=1)

    assert counts["list"] == 2
    assert counts["deactivate"] == counts["delete"] == 1
    assert counts["probe"] == 1
    mutations = [event for event in counts["events"] if event.startswith(("POST", "DELETE"))]
    assert all("atlas-consumer-owned-orphan" in event for event in mutations)
    assert counts["events"].index(mutations[0]) > 2
    assert "cursor=opaque%20%2B%2Ftoken%3F%26" in counts["events"][2]
    assert "seed summary: imported=0 failed=1" in output


def test_seed_preserves_genuine_empty_inventory_and_later_probe(tmp_path):
    counts, output = _run_seed_pages(tmp_path, [{"data": [], "nextCursor": None}])
    assert counts["list"] == 1
    assert counts["deactivate"] == counts["delete"] == 0
    assert counts["probe"] == 1
    assert "seed summary: imported=1 failed=0" in output


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
        # Read the body and send Content-Length: an unread body makes the
        # close a TCP reset, and a bodiless 200 without a length ends at EOF.
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Length", "0")
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
