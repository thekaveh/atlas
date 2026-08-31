# 5.2.40. OpenTelemetry Collector

## 1. Overview

OpenTelemetry Collector is Atlas' disabled by default, internal-only telemetry ingest point. It receives OTLP traces and logs, forwards traces to Tempo, and persists redacted logs in Loki. The stock backend, Celery, and LiteLLM wiring currently emits traces; any application that emits OTLP logs to the same endpoint uses the Loki path.

This is a local development service. It is not exposed through Kong, has no browser UI, and should not be treated as an internet-facing ingestion endpoint.

## 2. Access

- SOURCE: `OTEL_COLLECTOR_SOURCE=disabled` by default.
- Internal OTLP HTTP endpoint when enabled: `http://otel-collector:4318`.
- Internal OTLP gRPC endpoint when enabled: `http://otel-collector:4317`.
- Direct host URL: none in the first slice.
- Kong URL: none; no Kong route is generated.
- Grafana surface: use the Tempo datasource for traces and Loki for logs.

## 3. Configuration

The service reads `./config/config.yaml`, mounted to `/etc/otelcol/config.yaml`. Atlas computes `OTEL_COLLECTOR_ENDPOINT`, `OTEL_COLLECTOR_OTLP_HTTP_ENDPOINT`, `OTEL_COLLECTOR_OTLP_GRPC_ENDPOINT`, and `ATLAS_OTEL_ENABLED` from SOURCE choices. Container mode requires both `TEMPO_SOURCE=container` and `LOKI_SOURCE=container`; startup rejects either missing sink rather than silently retrying forever.

The receiver caps gRPC messages at 4 MiB and HTTP request bodies at 4,194,304 bytes; larger gRPC messages are rejected by the transport, while the pinned HTTP receiver returns `400 Bad Request` with `request body too large` before OTLP parsing. The logs pipeline then applies the memory limiter, redacts credentials, batches at most 1,024 records, and sends through a 512-request queue with two consumers and exponential retry. The queue is deliberately bounded and in memory: it retries a Loki outage while the Collector stays alive, but queued records do not survive a Collector restart. Logs accepted by Loki persist according to `LOKI_RETENTION_PERIOD`.

Attribute redaction deletes top-level log and resource attributes whose keys case-insensitively match this exact allowlist: `authorization`, `proxy-authorization`, `http.request.header.authorization`, `http.request.header.proxy-authorization`, `http.request.header.x-api-key`, `x-api-key`, `api_key`, `api-key`, `token`, `access_token`, `access-token`, `refresh_token`, `refresh-token`, `client_secret`, `client-secret`, `password`, and `secret`. It does not recursively inspect nested attribute maps. For string bodies only, case-insensitive patterns replace Bearer/Basic authorization values and the listed API-key, token, client-secret, password, or secret spellings in `key=value` or `key: value` text. A body key must start the string or follow whitespace or one of `{`, `[`, `,`, `?`, `&`, or `;`; optional single or double quotes around keys and values cover JSON-like and logfmt text without treating suffixes such as `xauthorization`, `not_token`, or `secretive` as sensitive keys. Transform errors propagate instead of sending the affected batch unredacted. Body filtering is best-effort; it does not traverse structured nested or non-string bodies, encodings, arbitrary identifiers, or unknown keys. Applications must still avoid logging secrets.

The pinned upstream image is distroless. Its container health check therefore
runs the Collector's own `validate` subcommand against the exact mounted config;
Docker separately observes main-process liveness. Backend startup fails fast if
tracing is explicitly enabled without an exporter endpoint or required OTel
packages instead of silently dropping telemetry.

## 4. Architecture & Wiring

Backend, Celery, and LiteLLM export OTLP HTTP spans to the collector. The collector batches and forwards traces to Tempo. OTLP log producers use the same receiver; after redaction and bounded live retry, the collector sends logs to Loki's native `/otlp` endpoint. The collector stays stateless and uses no persistent volume.

Loki preserves OTLP `trace_id` and `span_id` as structured metadata. Query a correlated record with LogQL such as `{service_name="backend"} | trace_id = "0123456789abcdef0123456789abcdef"`; Grafana's Loki datasource reads the `trace_id` structured-metadata label to derive the Tempo link without scanning the rendered log line.

Trace correlation uses W3C `traceparent` first. Backend spans start or continue request traces, and LiteLLM's OTel v2 integration continues an incoming `traceparent` header when present. Kong is not instrumented as a tracing producer in this slice, so Kong access logs and request IDs are adjacent correlation clues rather than Tempo spans. A future Kong `correlation-id` plugin pass should standardize `X-Request-ID` injection and forwarding once the backend/LiteLLM trace path is proven.

## 5. Dependencies & Integrations

### 5.1. Current — Upstream (this service calls)

| Service | Category |
|---|---|
| loki | infra |
| tempo | infra |

### 5.2. Current — Downstream (services that call this)

| Service | Category |
|---|---|
| litellm | llm |
| celery | agents |
| backend | apps |

### 5.3. Architecture diagram

![otel-collector architecture](./architecture.svg)

[Open the full-size diagram](./architecture.html) for a full-screen view.

### 5.4. Future — Missing pair integrations

_No high-confidence opportunities identified._

### 5.5. Future — Candidate new services

_No high-confidence opportunities identified._

### 5.6. Future — Unused features in this service

_No high-confidence opportunities identified._

## 6. Troubleshooting

- If backend or LiteLLM do not emit traces, confirm `OTEL_COLLECTOR_SOURCE=container` and `TEMPO_SOURCE=container`.
- If logs do not appear, confirm `LOKI_SOURCE=container`, query the normalized `service_name` label, and inspect Collector retry errors. A Collector restart discards records that Loki had not accepted.
- If the collector is unhealthy, run the same mounted-config validation shown in the Compose health check and inspect the reported receiver, processor, or exporter error.
- If Grafana shows no traces, check the Tempo datasource and the collector logs.
- Roll back by setting `OTEL_COLLECTOR_SOURCE=disabled`; backend and LiteLLM tracing env collapses to no-op values.

## 7. Capabilities & limitations

| Capability | Status | Verification | Notes |
|---|---|---|---|
| OTLP trace ingestion and Tempo export | supported | tested | Atlas accepts internal OTLP over gRPC and HTTP, batches traces, and exports them to the required Tempo service. |
| Log export to Loki | supported | tested | Atlas redacts a documented top-level credential-key allowlist, sends OTLP logs through a bounded live retry queue, and persists accepted logs in the required local Loki service. |
| Public telemetry ingestion | not-supported | tested | Collector receivers are backend-network only with no published host port or Kong route in the stock deployment. |
