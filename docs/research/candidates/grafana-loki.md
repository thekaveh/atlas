---
category-fit: infra
generated: 2026-05-19
license: AGPL-3.0
name: Grafana Loki
referenced-by: [kong]
slug: grafana-loki
type: external-service
upstream: https://grafana.com/docs/loki/latest/
---

# Grafana Loki

## 1. Headline
Horizontally-scalable, log-aggregation backend that pairs with Kong's bundled `http-log` plugin to give the stack a single queryable log store across every routed service.

## 2. Problem it solves
Debugging a request that flows kong → litellm → ollama → backend → supabase requires tailing five container log streams and manually correlating timestamps. Loki ingests structured logs from Kong's `http-log` plugin (already OSS) and from every other container via Promtail or Docker's Loki log driver. With a `correlation-id` plugin on Kong, every log line carries the same `X-Request-ID` and a single LogQL query reconstructs the trace.

## 3. Stack wiring sketch
- kong → loki via the `http-log` plugin posting to `http://loki:3100/loki/api/v1/push`
- backend → loki via Promtail sidecar (or Docker's `loki` log driver) tailing the FastAPI container
- litellm → loki via the same Docker log driver (LiteLLM logs structured JSON natively)
- n8n → loki via Docker log driver (n8n emits execution traces)
- hermes → loki via Docker log driver (Hermes agents emit per-skill spans)
- comfyui → loki via Docker log driver (workflow execution logs)
- supabase → loki via Postgres log shipping (`log_destination = 'stderr'` → Docker driver)

## 4. Effort
medium — Loki itself is one compose service plus Promtail (or Docker driver config) per source container. The Kong `http-log` plugin needs one route-level config block. No code changes to consumers — log shipping is sidecar-level.

## 5. Risks & open questions
- AGPL-3.0 license — operators shipping commercial forks of atlas may prefer an MIT/Apache alternative (VictoriaLogs, ClickHouse + Vector). Document the license choice.
- Disk growth: even with compression, Ollama generation logs are verbose. Default retention should be 7 days for dev.
- Without a paired Grafana, LogQL is only accessible via CLI/API — the proposal probably needs to bundle Grafana as a UI (or defer to a follow-up candidate).
- `http-log` plugin doubles every request's egress traffic; on a constrained host this can interfere with model-streaming throughput.

## 6. Why now (and why not sooner)
Same arc as Prometheus — the stack now has enough hops that single-container log tailing breaks down. Loki is the natural complement: Prometheus for "is it slow?", Loki for "why did it fail?". Earlier, structured logging across the stack wasn't consistent enough for centralized aggregation to pay off; recent backend/litellm/hermes refactors all emit JSON logs now.

## 7. Upstream evidence
- https://grafana.com/docs/loki/latest/get-started/
- https://docs.konghq.com/hub/kong-inc/http-log/ — Kong OSS plugin that posts to Loki's push endpoint
- https://grafana.com/docs/loki/latest/send-data/promtail/
- https://grafana.com/docs/loki/latest/send-data/docker-driver/
