---
category-fit: data
generated: 2026-07-04
license: SSPL-1.0 / Redis agreement terms
name: RedisInsight
referenced-by: [redis]
slug: redisinsight
type: external-service
upstream: https://github.com/RedisInsight/RedisInsight
---

# RedisInsight

## 1. Headline
Official self-hostable Redis GUI for browsing keys, inspecting streams, profiling commands, and debugging the half-dozen stack services that share the redis instance.

## 2. Problem it solves
Today the only way to inspect Redis state across the stack — kong's rate-limit cache, n8n's BullMQ queues, open-webui's websocket store on db=2, jupyterhub's session store on db=3, the litellm cache, the backend's session keys — is `redis-cli` inside the container. There's no view of stream lag, BullMQ stuck jobs, or a slow-command profile across consumers. RedisInsight surfaces all of this in one web UI and adds bulk operations + a CLI workbench with auto-complete.

## 3. Deferred decision (2026-07-04)

Keep RedisInsight deferred. RedisInsight 3.6.0 is active and Docker-friendly, and it can preconfigure Atlas' Redis connection through `RI_REDIS_HOST`, `RI_REDIS_PASSWORD`, and related variables. It is still a sensitive operator GUI over every Redis database index, so Atlas should not add `services/redisinsight/service.yml` until route auth, data exposure, license terms, and a concrete debugging workflow beat the extra UI and maintenance surface.

Future contract if reopened:

- Tracks: `data-eng`, `gen-ai-eng`, and `all`; avoid defaulting it into user-facing tracks unless the operator explicitly asks for Redis debugging.
- Category: `apps` for a future service because RedisInsight is a browser UI, even though this research row remains `data` because it is attached to Redis.
- Source values: `REDISINSIGHT_SOURCE=disabled|container|localhost`; disabled by default and dev/operator-focused.
- Wizard placement: in Redis advanced options, after Redis itself and after Prometheus/Grafana observability choices. Copy must explain that this is a sensitive operator GUI and requires `RI_ACCEPT_TERMS_AND_CONDITIONS=true` or an equivalent explicit acceptance path.
- Ports and routes: allocate `REDISINSIGHT_PORT`, honor custom `BASE_PORT`, and expose no default Kong route until route auth is implemented. If routed later, prefer a protected `redisinsight.localhost` route with explicit auth middleware rather than a public utility dashboard.
- Dependencies and consumers: RedisInsight reads Redis via `REDIS_PASSWORD`; it observes n8n, Kong, Open WebUI, LightRAG, JupyterHub, Celery, LiteLLM, backend, and future Redis Stack module data. It does not become a runtime dependency for those services.
- Topology: no `data_flow.calls` from application services to RedisInsight; only RedisInsight calls Redis. Do not imply RedisInsight participates in normal request flow.
- `init companion`: likely needed to render preconfigured databases, set `RI_ENCRYPTION_KEY`, create/persist its `/data` volume, accept terms in a visible way, and avoid storing raw Redis credentials in generated docs.
- Edge cases: route auth bypass, exposed Redis keys/secrets, destructive key deletion, Redis DB index confusion, stale preconfigured connection after password rotation, volume permission errors, SSPL or Redis agreement acceptance, reverse-proxy path limitations, no TLS in-cluster, and generated-doc drift.

Revisit when an operator workflow needs a GUI for BullMQ, streams, Redis module indexes, or slow-command profiling and Atlas has an auth story consistent with other admin surfaces.

## 4. Stack wiring sketch
- redisinsight → redis via `REDIS_HOST=redis` + `REDIS_PASSWORD=${REDIS_PASSWORD}` (single connection, browses all DB indices).
- kong → redisinsight via a new `redisinsight.localhost` alias (Kong route, `preserve_host: true`).
- backend → redisinsight only indirectly (operators inspecting backend's session keys via the GUI).

## 5. Effort
small — One container, one Kong alias, one SOURCE variant (`container | disabled`); image is `redis/redisinsight:latest`, default port 5540, healthcheck on `/api/health`.

## 6. Risks & open questions
- License is SSPL-1.0 (Server Side Public License), not OSI-approved — needs a note in the SOURCE description and probably defaults to `disabled` in the wizard.
- No built-in auth in the OSS image; must sit behind Kong with a basic-auth plugin or be marked dev-only.
- v2 stores its own config in a volume — small but worth a named volume entry.

## 7. Why now (and why not sooner)
Eight stack services now write to Redis on five different DB indices. Debugging "why is n8n's BullMQ stuck?" or "what's eating Kong's rate-limit memory?" requires per-service `redis-cli` sessions today. A single GUI cuts the loop from minutes to seconds and makes Redis observable in the same way Supabase Studio makes Postgres observable.

## 8. Upstream evidence
- https://github.com/RedisInsight/RedisInsight
- https://redis.io/docs/latest/operate/redisinsight/
- https://redis.io/docs/latest/operate/redisinsight/configuration/
- https://hub.docker.com/r/redis/redisinsight
