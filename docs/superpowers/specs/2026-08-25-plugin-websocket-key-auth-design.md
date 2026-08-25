# Plugin WebSocket Key Authentication Design

## Problem

Plugin routers declare authentication once in `plugin.yml`. The backend mounts
that policy as a router-level dependency. `auth: key-auth` currently delegates
credential extraction to FastAPI's `APIKeyHeader`, which requires an HTTP
`Request`; WebSocket routes provide a `WebSocket` connection instead and fail
before the plugin handler can run. Browser WebSocket clients also cannot add an
arbitrary `apikey` header, while Kong key-auth accepts a query parameter.

## Design

Replace the request-only security helper with one dependency that accepts
Starlette's common `HTTPConnection` base type. Read `apikey` from the header
first and then from the query string when the header is absent. Header
precedence preserves a single deterministic credential when both transports
are present. Keep the existing constant-time comparison, 401 response for an
invalid or absent key, 503 response for missing backend configuration, and the
opaque `"gateway-key"` dependency result.

No plugin manifest or route-mounting contract changes. The existing
router-level dependency continues to protect both `APIRoute` and
`APIWebSocketRoute` instances.

## Test Strategy

- Exercise the dependency directly with HTTP and WebSocket connection scopes.
- Cover valid header, valid query string, header precedence, missing key,
  incorrect key, non-ASCII input, and missing server configuration.
- Add a real key-auth plugin WebSocket route and connect through FastAPI's
  `TestClient` using the browser-compatible query-string credential.
- Assert missing and incorrect WebSocket credentials are rejected.
- Assert the key-auth dependency is mounted on both HTTP and WebSocket plugin
  routes so future route-type filtering cannot silently reopen the gap.

## Non-Goals

- Changing bearer authentication for non-plugin backend APIs.
- Introducing cookies or additional key names.
- Changing Kong's key-auth configuration.
