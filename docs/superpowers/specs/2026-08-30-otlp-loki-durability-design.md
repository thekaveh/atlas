# Durable OTLP-to-Loki Logging Design

## 1. Goal

Persist OTLP logs accepted by Atlas' pinned OpenTelemetry Collector in the
pinned local Loki service without creating an unbounded failure buffer or
claiming broader secret detection than the implementation provides.

## 2. Architecture

The existing Collector remains the only telemetry ingest point. Its traces
pipeline continues to export to Tempo. Its logs pipeline changes from the
diagnostic `debug` exporter to Loki 3.7's native OTLP HTTP endpoint at
`http://loki:3100/otlp`. This uses the Collector's supported `otlp_http`
exporter; Atlas will not add the retired Loki exporter or a second log agent.

Because the committed Collector configuration is static rather than generated
from source selections, `OTEL_COLLECTOR_SOURCE=container` requires both Tempo
and Loki in container mode. Startup validation rejects a disabled Loki source
instead of starting a Collector that silently retries toward a nonexistent
sink. The Collector manifest therefore records calls to both stores.

## 3. Processing and durability

The logs pipeline orders processors as follows:

1. `memory_limiter` protects the Collector process.
2. `transform/logs` redacts the exact fields described below.
3. `batch/logs` uses explicit maximum batch sizes and a short flush interval.
4. `otlp_http/loki` uses a bounded sending queue and exponential retry.

The pinned Collector exposes `file_storage`, but its non-root UID cannot create
storage in a fresh Docker named volume. Making that work would require running
the Collector as root, changing the pinned image, or adding a privileged init
image. Atlas therefore uses a bounded in-memory queue and applies backpressure
when it is full. Retry survives a Loki outage only while the Collector process
remains alive; only logs accepted by Loki are durable across Collector restarts.

Loki remains a single-replica filesystem store with TSDB schema v13,
structured metadata enabled, and compactor deletion governed by the existing
24-hour default `LOKI_RETENTION_PERIOD` setting. This is local-development
durability, not high availability.

## 4. Redaction contract

Redaction runs before batching, queueing, or persistence. It deletes top-level
log and resource attributes whose keys case-insensitively match a documented,
anchored allowlist. The allowlist covers canonical authorization, proxy
authorization, API-key, token, client-secret, password, and secret key
spellings used by Atlas and common OTel HTTP semantic conventions. Nested
attribute maps are out of scope and are not traversed.

For string log bodies only, two case-insensitive regular expressions redact:

- `Authorization` or `Proxy-Authorization` followed by `Bearer` or `Basic`
  credentials; and
- named `api_key`, `api-key`, `x-api-key`, `token`, `access_token`,
  `refresh_token`, `client_secret`, `password`, or `secret` values written as
  `key=value` or `key: value` text.

A named body key must start the string or follow whitespace or one of `{`, `[`,
`,`, `?`, `&`, or `;`. Keys and values may have optional single or double
quotes, covering JSON-like and logfmt strings. The left boundary prevents
suffix keys such as `xauthorization`, `not_token`, and `secretive` from being
redacted. The RE2-compatible expressions use captured prefixes rather than
lookbehind.

The body filter is best-effort. It does not traverse structured non-string or
nested object bodies, encoded/encrypted values, arbitrary identifiers, or
unknown key names. Applications must still avoid logging secrets.

## 5. Correlation and query surface

The native OTLP path preserves `trace_id` and `span_id` as Loki structured
metadata. A smoke test emits a log with fixed identifiers and proves it can be
queried by service and trace identifier. Grafana's Loki datasource treats the
native `trace_id` structured metadata as a label-derived field and links its
value to the provisioned Tempo datasource without scanning the rendered line.

## 6. Verification

Static tests first fail on the current debug-only pipeline, then assert the
supported receiver, processor order, exact redaction rules, bounded queue and
retry, retention/structured-metadata settings, source dependency validation,
Compose volume/network contracts, and rendered configuration baselines.

A disposable stack using exactly Collector 0.154.0 and Loki 3.7.0 validates
both configurations, emits an OTLP log containing fixed trace/span IDs and
secret sentinels in attributes, resource attributes, and body text, queries
Loki, and proves the correlated log persists while the covered secrets do not.
An outage/restart exercise verifies queued delivery if persistent storage is
supported. Documentation drift, generated diagrams, Compose permutations, and
the three documentation surfaces must all remain clean.
