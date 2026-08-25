# Plugin WebSocket Key Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make plugin `key-auth` work for HTTP and WebSocket routes, including browser-compatible query-string credentials.

**Architecture:** Extend `APIKeyHeader` with credential extraction from Starlette's shared `HTTPConnection` interface, preserving OpenAPI metadata while protecting every plugin route type uniformly. Redact query credentials from Uvicorn logs and make Kong's proxy access format query-free so the browser-compatible WebSocket transport does not leak the shared key.

**Tech Stack:** Python 3.12, FastAPI, Starlette, pytest, FastAPI `TestClient`

## 1. Global Constraints

- Preserve existing HTTP header authentication and error semantics.
- Accept `apikey` from the query string only when the header is absent.
- Keep constant-time credential comparison.
- Preserve the existing HTTP OpenAPI API-key security scheme.
- Never emit an `apikey` query value in backend or Kong proxy access logs.
- Do not require a live Atlas stack.

---

### 1.1. Task 1: Prove and fix connection-neutral plugin key authentication

**Files:**
- Modify: `services/backend/app/app/tests/test_backend_identity.py`
- Modify: `services/backend/app/app/tests/test_plugin_seam.py`
- Modify: `services/backend/app/app/backend_identity.py`

**Interfaces:**
- Consumes: `HTTPConnection.headers`, `HTTPConnection.query_params`, `BACKEND_KONG_API_KEY`
- Produces: `async require_plugin_gateway_key(connection: HTTPConnection) -> str` and end-to-end WebSocket regression coverage

- [ ] **Step 1: Add failing direct dependency tests**

Create request and WebSocket scopes and assert valid header/query keys return
`"gateway-key"`; assert a present header takes precedence over the query;
assert missing, incorrect, and non-ASCII keys raise 401; assert an unset server
key raises 503. Generate a temporary key-auth plugin with one HTTP route and
one WebSocket route; assert both route objects mount the dependency, a client
can connect with `?apikey=gateway-secret`, and missing/incorrect credentials
are rejected before the handler accepts the connection.

- [ ] **Step 2: Run the direct tests and verify RED**

Run:

```bash
uv run --python 3.12 --with-requirements services/backend/app/app/requirements.txt --with-requirements services/backend/app/app/requirements-dev.txt pytest services/backend/app/app/tests/test_backend_identity.py services/backend/app/app/tests/test_plugin_seam.py -q
```

Expected: the new direct calls and WebSocket handshake fail because the
dependency does not accept a connection or inspect query parameters.

- [ ] **Step 3: Implement minimal connection-neutral extraction**

Remove `APIKeyHeader`, type the dependency parameter as `HTTPConnection`, read
`connection.headers.get("apikey")` before
`connection.query_params.get("apikey")`, and retain the current validation and
return value.

- [ ] **Step 4: Run the direct tests and verify GREEN**

Run the command from Step 2. Expected: all backend identity and plugin seam
tests pass.

### 1.2. Task 2: Preserve metadata and prevent credential logging

**Files:**
- Create: `services/backend/app/app/access_log.py`
- Create: `services/backend/app/app/tests/test_access_log.py`
- Modify: `services/backend/app/app/backend_identity.py`
- Modify: `services/backend/app/app/main.py`
- Modify: `services/backend/app/app/tests/test_backend_identity.py`
- Modify: `services/backend/app/app/tests/test_plugin_seam.py`
- Modify: `services/kong/compose.yml`
- Create: `bootstrapper/tests/test_kong_access_log_security.py`
- Modify: `docs/deployment/reusing-atlas.md`

**Interfaces:**
- Consumes: FastAPI `APIKeyHeader`, Starlette `HTTPConnection`, Python `logging.LogRecord`, Kong Nginx directive injection
- Produces: connection-neutral `_PluginAPIKeyHeader`, `configure_uvicorn_access_log_redaction()`, and query-free Kong proxy logs

- [ ] **Step 1: Add failing compatibility and log-safety tests**

Assert a mounted key-auth HTTP route retains an OpenAPI `apiKey` header scheme
and operation security requirement. Assert accepted and denied Uvicorn
WebSocket log records replace the `apikey` value, preserve other query
parameters, and never install duplicate filters. Assert the Kong Compose
environment defines a named proxy log format using `$uri` and rejects
`$request` / `$request_uri`.

- [ ] **Step 2: Run the new tests and verify RED**

```bash
uv run --python 3.12 --with-requirements services/backend/app/app/requirements.txt --with-requirements services/backend/app/app/requirements-dev.txt pytest services/backend/app/app/tests/test_backend_identity.py services/backend/app/app/tests/test_plugin_seam.py services/backend/app/app/tests/test_access_log.py -q
uv run --project bootstrapper pytest bootstrapper/tests/test_kong_access_log_security.py -q
```

Expected: OpenAPI has no plugin security requirement, the access-log module is
absent, and Kong still uses its default query-bearing log format.

- [ ] **Step 3: Implement the minimal compatibility and redaction changes**

Subclass `APIKeyHeader` with `__call__(HTTPConnection)`, retain it as the
dependency consumed by `require_plugin_gateway_key`, install an idempotent
redaction filter on `uvicorn.access` and `uvicorn.error` during app import, and
configure a named Kong proxy access format using method + `$uri` + protocol.
Document header-first HTTP usage and the WebSocket query fallback without ever
printing a real credential.

- [ ] **Step 4: Run the new tests and verify GREEN**

Run both commands from Step 2. Expected: all tests pass with no warnings.

### 1.3. Task 3: Verify and commit #973

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
git add docs/superpowers/specs/2026-08-25-plugin-websocket-key-auth-design.md docs/superpowers/plans/2026-08-25-plugin-websocket-key-auth.md docs/deployment/reusing-atlas.md services/backend/app/app/access_log.py services/backend/app/app/backend_identity.py services/backend/app/app/main.py services/backend/app/app/tests/test_access_log.py services/backend/app/app/tests/test_backend_identity.py services/backend/app/app/tests/test_plugin_seam.py services/kong/compose.yml bootstrapper/tests/test_kong_access_log_security.py
git commit -m "fix(plugins): support key-auth WebSocket routes"
```
