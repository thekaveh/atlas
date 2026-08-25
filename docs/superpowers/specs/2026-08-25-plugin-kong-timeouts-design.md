# Plugin Kong Timeouts Design

## 1. Problem

Backend plugin routes are proxied through Kong's shared `backend-api` service. Kong applies `connect_timeout`, `write_timeout`, and `read_timeout` at the service level, and Atlas does not emit those fields. Every plugin therefore inherits Kong's 60,000 ms defaults, even when its work legitimately takes longer.

## 2. Goals

1. Let a version-1 `plugin.yml` declare any subset of `connect_timeout`, `write_timeout`, and `read_timeout` in milliseconds.
2. Validate the same integer range on the bootstrapper and backend sides: 1 through 2,147,483,646, inclusive.
3. Emit declared values as Kong service fields without manufacturing values for omitted fields.
4. Preserve the exact shared `backend-api` service shape when no plugin declares a timeout.
5. Preserve per-plugin `inherit`, `open`, and `key-auth` behavior when a timed plugin is moved to its own Kong service.
6. Keep generation deterministic and ensure every Kong service and route name is unique.

## 3. Non-goals

- No live Atlas stack is required or changed.
- No global backend timeout setting is added.
- No default above Kong's existing 60,000 ms is imposed.
- No asynchronous job protocol or plugin execution behavior is introduced.
- No Docker image, base configuration, or Kong version is changed.

## 4. Manifest contract

The canonical JSON Schema gains three optional properties:

```yaml
connect_timeout: 120000
write_timeout: 120000
read_timeout: 900000
```

Each value is a strict JSON/YAML integer in milliseconds with `minimum: 1` and `maximum: 2147483646`. Booleans, numeric strings, zero, negatives, and values above the Kong-supported range are invalid. Omitting a property preserves Kong's default for that property.

The bootstrapper's frozen `PluginManifest` dataclass and the backend's strict Pydantic `PluginManifest` expose the same three optional integer fields. The backend inventory includes a `timeouts` object containing only explicitly declared fields so operators can inspect the effective plugin contract without inventing defaults.

## 5. Kong generation

Kong timeouts belong to services, not routes. Atlas therefore keeps plugins with no timeout override on the shared `backend-api` service and creates one dedicated service for each plugin that declares at least one timeout.

Each dedicated service:

- has a deterministic name derived from the validated unique plugin name;
- points to the existing `http://backend:8000/` upstream;
- contains one host-and-path route for the plugin's `route_prefix`;
- copies only the timeout fields explicitly declared by the plugin;
- retains the service-level CORS plugin;
- applies the plugin's explicit auth mode, or the backend default when `auth: inherit`.

The shared service keeps its host-only catch-all route. Kong selects a timed plugin's path-bearing route ahead of that catch-all. If a timed plugin also declares an auth override, its prefix is excluded from the shared service's auth-specific routes so there is exactly one route for that prefix.

Route names are allocated once across shared and dedicated backend services. The existing slug and numeric-suffix behavior remains deterministic and collision-safe, including `/all`, `/a/b`, and `/a-b` edge cases.

## 6. Bootstrap flow

Plugin manifests are discovered once during Kong configuration. From that discovery Atlas derives:

1. non-`inherit` route auth policies; and
2. timeout service policies for manifests declaring at least one timeout.

Both policy collections are attached to `KongConfigGenerator`. Malformed or conflicting manifests continue to be excluded by the existing discovery boundary and surfaced by the consumer doctor. Atlas-internal failures still fail closed rather than silently dropping auth or timeout policy.

## 7. Documentation

The plugin contract walkthrough documents all three fields, their millisecond units and valid range, the per-field omission behavior, and why a timed plugin receives a dedicated Kong service. The changelog records the new consumer-facing capability.

## 8. Test strategy

Automated coverage will prove:

- host and backend validators accept partial and complete timeout declarations;
- both validators reject wrong types and both range boundaries;
- policy derivation excludes plugins without timeouts and preserves explicit values;
- no-timeout generation is byte-shape compatible with the existing backend service;
- partial overrides emit only their declared keys;
- multiple timed plugins receive separate services with deterministic unique names;
- timed plugins preserve inherited, open, and key-auth policy;
- timeout-plus-auth prefixes are not duplicated on the shared service;
- route-name collisions remain unique across all backend services;
- startup wiring transfers both derived policy collections from one discovery result;
- the plugin inventory exposes declared timeouts only;
- focused backend/bootstrapper tests, the full bootstrapper suite, backend suite, docs checks, and GitHub-required checks pass.

## 9. Success criteria

A consumer can set `read_timeout: 900000` in `plugin.yml`, generate Kong configuration locally, and observe a dedicated plugin service containing `read_timeout: 900000`. Plugins with no timeout keys generate the same shared backend service as before, and all auth and validation invariants remain enforced.
