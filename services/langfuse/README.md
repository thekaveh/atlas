# 5.2.25. Langfuse (LLM traces + evals)

## 1. Overview

Langfuse is an optional, disabled-by-default observability surface for LLM traces, prompt/eval history, latency, and cost inspection. Atlas wires it to LiteLLM first: calls that already pass through LiteLLM can emit Langfuse traces through LiteLLM's `success_callback`.

Langfuse complements Prometheus and Grafana. Prometheus/Grafana remain the infrastructure metrics and dashboard layer; Langfuse is the LLM behavior layer. Direct ComfyUI traces, Hermes custom spans, backend custom spans, n8n step spans, and OpenTelemetry fan-out are out of scope for the first slice.

## 2. Access

| Surface | URL | Notes |
| --- | --- | --- |
| Kong | `http://langfuse.localhost:${KONG_HTTP_PORT}` | Routed only when `LANGFUSE_SOURCE=container`. |
| Direct | `http://localhost:${LANGFUSE_PORT}` | Bound through `HOST_BIND_IP`; the default is loopback-only, while an explicit non-empty value enables deliberate remote access. |

The first-run user is controlled by `LANGFUSE_INIT_USER_EMAIL`, `LANGFUSE_INIT_USER_NAME`, and `LANGFUSE_INIT_USER_PASSWORD`. The initial project keys are `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`; LiteLLM uses those for gateway tracing.

## 3. Configuration

```dotenv
LANGFUSE_SOURCE=disabled              # container | disabled
LANGFUSE_PORT=                        # topology-assigned
LANGFUSE_ENDPOINT=                    # auto-managed
LANGFUSE_PUBLIC_KEY=                  # auto-generated
LANGFUSE_SECRET_KEY=                  # auto-generated
LANGFUSE_SALT=                        # auto-generated
LANGFUSE_ENCRYPTION_KEY=              # auto-generated
LANGFUSE_NEXTAUTH_SECRET=             # auto-generated
LANGFUSE_CLICKHOUSE_PASSWORD=         # auto-generated
MINIO_BUCKET_LANGFUSE=langfuse
```

Current Langfuse self-hosting uses a web container, worker container, Postgres, ClickHouse, Redis/Valkey, and S3/blob storage. Atlas reuses Supabase Postgres, Redis, and MinIO, and adds a Langfuse-owned ClickHouse container. This is a low-scale local Docker Compose deployment, not a high-availability production Langfuse cluster.

## 4. Architecture & Wiring

When enabled, the family starts:

- `langfuse-init`: creates the Langfuse Postgres database after `minio-init` provisions the bucket/service account.
- `langfuse-clickhouse`: stores traces, observations, and scores.
- `langfuse-web`: serves the UI and ingestion APIs.
- `langfuse-worker`: processes queued ingestion work.

LiteLLM receives `LANGFUSE_HOST`, `LANGFUSE_BASE_URL`, `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY`. The generated LiteLLM config adds `success_callback: ["langfuse"]` only while `LANGFUSE_SOURCE=container`; disabling Langfuse removes the callback on the next config render. Existing Prometheus callbacks stay in place.

### 4.1. Why two host variables

Both `LANGFUSE_HOST` and `LANGFUSE_BASE_URL` are set to the same endpoint on purpose, because the name changed across SDK majors and getting it wrong **fails silently**:

| SDK | Reads | If unset |
|---|---|---|
| langfuse-python **v2** (bundled in the pinned LiteLLM image) | `LANGFUSE_HOST` only | defaults to `https://cloud.langfuse.com` |
| langfuse-python **v4** | `LANGFUSE_BASE_URL`, with `LANGFUSE_HOST` as a deprecated alias | defaults to `https://cloud.langfuse.com` |

With only `LANGFUSE_BASE_URL` set, the v2 SDK sent every trace to the public cloud using locally-generated keys, where they were rejected and dropped — while LiteLLM logged `Initialized Success Callbacks - ['langfuse']` and every call succeeded. Nothing appeared locally and nothing errored (#929). Setting both keeps tracing correct on the current pin *and* after a future image bumps the bundled SDK.

### 4.2. What is actually traced

Coverage is **exactly what passes through the LiteLLM gateway**. That is the large majority of Atlas LLM traffic — Open WebUI (`OPENAI_API_BASE_URLS: http://litellm:4000/v1`), the backend, and LightRAG's default binding all route through it.

The documented exception is LightRAG's **per-role binding overrides**. `LIGHTRAG_EXTRACT_LLM_BINDING_HOST`, `LIGHTRAG_KEYWORD_LLM_BINDING_HOST` and `LIGHTRAG_QUERY_LLM_BINDING_HOST` can point a role straight at a native provider (e.g. Ollama), bypassing LiteLLM entirely. Those calls produce **no Langfuse traces**, and nothing warns about it — if you have set any of them, expect a gap in coverage for that role.

Direct ComfyUI traces, Hermes custom spans, backend custom spans, n8n step spans, and OpenTelemetry fan-out remain out of scope; LiteLLM's OTel export (`LITELLM_OTEL_V2`) is independent of the Langfuse callback.

## 5. Dependencies & Integrations

### 5.1. Current — Upstream (this service calls)

| Service | Category |
|---|---|
| minio | data |
| redis | data |
| supabase | data |

### 5.2. Current — Downstream (services that call this)

| Service | Category |
|---|---|
| kong | infra |
| litellm | llm |

### 5.3. Architecture diagram

![langfuse architecture](./architecture.svg)

[Open the full-size diagram](./architecture.html) for a full-screen view.

### 5.4. Future — Missing pair integrations

_No high-confidence opportunities identified._

### 5.5. Future — Candidate new services

_No high-confidence opportunities identified._

### 5.6. Future — Unused features in this service

_No high-confidence opportunities identified._

## 6. Troubleshooting

- **No traces appear, and nothing is erroring:** this failure mode is silent by design of the SDK, so check in this order. (1) Confirm `LANGFUSE_SOURCE=container` and that the LiteLLM config regenerated with the callback. (2) Verify the gateway container actually has the host var the SDK reads — `docker exec <project>-litellm printenv LANGFUSE_HOST` must print your local endpoint, **not** empty; an unset value means the SDK is shipping traces to `https://cloud.langfuse.com`, where your local keys are rejected and the data is dropped without a log line (#929). (3) Check `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` match the initial project keys. (4) Confirm the call actually went through LiteLLM — see §4.2; a LightRAG role bound to a native provider never reaches the gateway. A quick end-to-end probe: make one chat completion through LiteLLM, then `GET /api/public/traces` and check `meta.totalItems` moved.
- **`langfuse-web` is `(unhealthy)` but the UI works:** fixed in #928 by pinning `HOSTNAME=0.0.0.0`. Next.js standalone binds `process.env.HOSTNAME`, and Docker sets that to the container ID, so the server listened on the container IP only while the healthcheck probed loopback. If it recurs, compare `docker exec <project>-langfuse-web printenv HOSTNAME` against what the probe targets.
- **Langfuse fails to start with MinIO errors:** keep `MINIO_SOURCE=container`; Langfuse requires S3-compatible event storage in this Atlas slice.
- **Rollback to direct LiteLLM behavior:** the rollback path is to set `LANGFUSE_SOURCE=disabled` and rerun `./start.sh`. The Langfuse containers scale to zero, Kong stops routing `langfuse.localhost`, and LiteLLM no longer emits the Langfuse `success_callback`.
- **ClickHouse timezone or empty queries:** keep ClickHouse and Postgres on UTC. The compose fragment sets ClickHouse `TZ=UTC`.

## 7. Capabilities & limitations

| Capability | Status | Verification | Notes |
|---|---|---|---|
| Self-hosted LLM observability stack | supported | tested | Atlas configures the Langfuse web, worker, ClickHouse, Postgres, Redis, and MinIO dependencies as one optional self-hosted deployment. |
| Automatic LiteLLM trace capture | partial | tested | LiteLLM success callbacks emit traces when Langfuse is enabled, but calls that bypass LiteLLM or use native provider bindings are not captured automatically. |
| Highly available Langfuse deployment | not-supported | documented | The stock topology is a local low-scale deployment without replicated Langfuse, ClickHouse, or supporting data-store services. |
