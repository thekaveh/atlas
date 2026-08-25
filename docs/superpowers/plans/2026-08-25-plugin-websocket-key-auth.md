# Plugin WebSocket Key Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make plugin `key-auth` work for HTTP and WebSocket routes, including browser-compatible query-string credentials.

**Architecture:** Replace the HTTP-only `APIKeyHeader` dependency with credential extraction from Starlette's shared `HTTPConnection` interface. Preserve the existing validation and route-level dependency contract so every plugin route type is protected uniformly.

**Tech Stack:** Python 3.12, FastAPI, Starlette, pytest, FastAPI `TestClient`

## Global Constraints

- Preserve existing HTTP header authentication and error semantics.
- Accept `apikey` from the query string only when the header is absent.
- Keep constant-time credential comparison.
- Do not require a live Atlas stack.

---

### Task 1: Prove and fix connection-neutral key extraction

**Files:**
- Modify: `services/backend/app/app/tests/test_backend_identity.py`
- Modify: `services/backend/app/app/backend_identity.py`

**Interfaces:**
- Consumes: `HTTPConnection.headers`, `HTTPConnection.query_params`, `BACKEND_KONG_API_KEY`
- Produces: `async require_plugin_gateway_key(connection: HTTPConnection) -> str`

- [ ] **Step 1: Add failing direct dependency tests**

Create request and WebSocket scopes and assert valid header/query keys return
`"gateway-key"`; assert a present header takes precedence over the query;
assert missing, incorrect, and non-ASCII keys raise 401; assert an unset server
key raises 503.

- [ ] **Step 2: Run the direct tests and verify RED**

Run:

```bash
uv run --python 3.12 --with-requirements services/backend/app/app/requirements.txt --with-requirements services/backend/app/app/requirements-dev.txt pytest services/backend/app/app/tests/test_backend_identity.py -q
```

Expected: the new calls fail because `APIKeyHeader` cannot consume a WebSocket
connection and the dependency does not inspect query parameters.

- [ ] **Step 3: Implement minimal connection-neutral extraction**

Remove `APIKeyHeader`, type the dependency parameter as `HTTPConnection`, read
`connection.headers.get("apikey")` before
`connection.query_params.get("apikey")`, and retain the current validation and
return value.

- [ ] **Step 4: Run the direct tests and verify GREEN**

Run the command from Step 2. Expected: all backend identity tests pass.

### Task 2: Protect real plugin WebSocket routes end to end

**Files:**
- Modify: `services/backend/app/app/tests/test_plugin_seam.py`

**Interfaces:**
- Consumes: `plugin_seam.load_plugins(app)` and plugin `auth: key-auth`
- Produces: regression coverage for `APIWebSocketRoute` dependencies and a real WebSocket handshake

- [ ] **Step 1: Add a failing key-auth WebSocket plugin test**

Generate a temporary plugin with one HTTP route and one WebSocket route. Assert
both route objects include `require_plugin_gateway_key`. Connect with
`?apikey=gateway-secret` and receive a message. Assert missing and incorrect
credentials are rejected before the handler accepts the connection.

- [ ] **Step 2: Run the seam test and verify RED against the original code**

Run:

```bash
uv run --python 3.12 --with-requirements services/backend/app/app/requirements.txt --with-requirements services/backend/app/app/requirements-dev.txt pytest services/backend/app/app/tests/test_plugin_seam.py -q
```

Expected on the original implementation: the valid WebSocket handshake fails
because `APIKeyHeader` requests an HTTP `Request` object.

- [ ] **Step 3: Verify GREEN with the Task 1 implementation**

Run the command from Step 2. Expected: every plugin seam test passes.

### Task 3: Verify and commit #973

**Files:**
- Verify all files changed by Tasks 1-2 and this plan/design pair.

**Interfaces:**
- Consumes: completed implementation and regression tests
- Produces: one reviewable Git commit for issue #973

- [ ] **Step 1: Run focused verification**

```bash
uv run --python 3.12 --with-requirements services/backend/app/app/requirements.txt --with-requirements services/backend/app/app/requirements-dev.txt pytest services/backend/app/app/tests/test_backend_identity.py services/backend/app/app/tests/test_plugin_seam.py services/backend/app/app/tests/test_plugin_manifest.py -q
```

- [ ] **Step 2: Run the complete backend unit suite**

```bash
uv run --python 3.12 --with-requirements services/backend/app/app/requirements.txt --with-requirements services/backend/app/app/requirements-dev.txt pytest services/backend/app/app/tests -q
```

- [ ] **Step 3: Inspect the final diff and commit**

```bash
git diff --check
git diff --stat
git add docs/superpowers/specs/2026-08-25-plugin-websocket-key-auth-design.md docs/superpowers/plans/2026-08-25-plugin-websocket-key-auth.md services/backend/app/app/backend_identity.py services/backend/app/app/tests/test_backend_identity.py services/backend/app/app/tests/test_plugin_seam.py
git commit -m "fix(plugins): support key-auth WebSocket routes"
```
