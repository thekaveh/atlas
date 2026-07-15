---
category-fit: infra
generated: 2026-05-19
license: Apache-2.0
name: Prometheus
referenced-by: [kong]
slug: prometheus
type: external-service
upstream: https://prometheus.io/docs/introduction/overview/
---

# Prometheus

> **Status: SHIPPED 2026-05-31** — the observability bundle integrated this candidate as `services/prometheus/` together with `services/grafana/` and the postgres-exporter/redis-exporter sidecars. See `docs/CHANGELOG.md` → [Unreleased] → "Added — observability bundle" for the as-shipped surface. The text below is the original pre-implementation research artifact, retained for historical context.

## 1. Headline
Time-series database and scrape engine that turns Kong's bundled `prometheus` plugin (and per-service exporters across the stack) into a single observability spine.

## 2. Problem it solves
Today the stack has no metrics surface — `docker logs` is the only operator visibility into latency, error rate, or upstream health. Kong 3.9 OSS already ships the `prometheus` plugin and exposes `/metrics` natively, but nothing scrapes it. Adding Prometheus unlocks immediate p50/p95/error-rate dashboards for every route and is a prerequisite for Grafana, alerting, and capacity planning.

## 3. Stack wiring sketch
- kong → prometheus via `/metrics` on Kong's status listener (plugin emits histogram, counter, gauge series)
- redis → prometheus via the bundled `redis_exporter` sidecar (port 9121, scrape `/metrics`)
- supabase → prometheus via `postgres_exporter` against `db:5432`
- n8n → prometheus via n8n's built-in `/metrics` endpoint (env: `N8N_METRICS=true`)
- ollama → prometheus via Ollama's `/metrics` (since 0.1.34)
- litellm → prometheus via LiteLLM's built-in Prometheus exporter (`/metrics` on the same port)
- backend → prometheus via `prometheus_fastapi_instrumentator` on `/metrics`

## 4. Effort
medium — single new compose service (Prometheus is one container + a config file), but each consumer needs an exporter enabled, a service-discovery target added, and a Kong route (`metrics.localhost`) for the UI.

## 5. Risks & open questions
- Scrape interval vs. CPU cost on resource-constrained dev laptops; default 15s should be fine.
- Retention policy: TSDB grows quickly; default 15 days might need to drop to 3–5 days for laptop dev environments.
- Whether to bundle Grafana in the same proposal or keep Prometheus standalone (Grafana would be a follow-up candidate).
- Auth: `/metrics` endpoints typically have no auth; Kong should expose `metrics.localhost` behind basic-auth to avoid drive-by scraping.

## 6. Why now (and why not sooner)
The stack just crossed 20+ containers and operators are starting to debug latency between Kong → LiteLLM → Ollama hops. Without metrics, root-cause requires reading every container's log stream. Earlier the stack was small enough to debug via `docker stats` and `htop`; that no longer scales.

## 7. Upstream evidence
- https://prometheus.io/docs/introduction/overview/
- https://docs.konghq.com/hub/kong-inc/prometheus/ — Kong's bundled OSS plugin
- https://github.com/oliver006/redis_exporter
- https://docs.litellm.ai/docs/proxy/prometheus
