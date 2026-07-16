---
category-fit: data
generated: 2026-07-04
license: RSALv2 / SSPLv1 / AGPLv3 depending on Redis version/distribution
name: Redis Stack (redis-stack-server)
referenced-by: [redis]
slug: redis-stack
type: external-service
upstream: https://redis.io/docs/latest/operate/oss_and_stack/stack-with-enterprise/
---

# Redis Stack (redis-stack-server)

## 1. Headline
Drop-in replacement for the base `redis:7-alpine` image that bundles RediSearch, RedisJSON, RedisBloom, and RedisTimeSeries — unlocks vector search, native JSON storage, probabilistic dedup, and time-series metrics on the existing Redis instance.

## 2. Problem it solves
The stack uses Redis only as a key-value cache/queue today, leaving advanced workloads (embedding caches, semantic-response caches, BullMQ JSON payloads, agent tool-call dedup, request-rate time-series) to roll their own structures on top of plain strings. Redis Stack ships the four canonical modules in one image, so consumers gain `FT.SEARCH`, `JSON.*`, `BF.*`, and `TS.*` commands without operating a second datastore.

## 3. Deferred decision (2026-07-04)

Keep Redis Stack deferred. Redis 8 changes the old framing because RediSearch, RedisJSON, RedisBloom, and RedisTimeSeries are now integrated with Redis Open Source under Redis' tri-license path, while Redis 7.4-era distributions remain source-available under RSALv2/SSPLv1. That makes the license story better documented but not simpler enough to swap Atlas' current BSD-licensed `redis:7.2.14-alpine` baseline without a concrete module-backed workflow.

Future contract if reopened:

- Tracks: `data-eng`, `gen-ai-eng`, and `all`; do not make Stack the default for RAG, creative, or ML tracks until a selected feature actually uses module commands.
- Category: `data`, because this is a replacement variant for the existing Redis substrate.
- Source values: keep `REDIS_SOURCE=container`; add `REDIS_VARIANT=oss|stack` only when Atlas has at least one tested module-backed workflow. Default remains `oss`, not Stack.
- Wizard placement: inside the Redis advanced-options step, with copy explaining license acceptance, image-size cost, module persistence, AOF behavior, and why Weaviate remains the durable vector database.
- Ports and routes: continue using `REDIS_PORT`; honor custom `BASE_PORT`; expose no default Kong route because Redis is infrastructure.
- Dependencies and consumers: n8n, Kong, Open WebUI, LightRAG, JupyterHub, Celery, LiteLLM, backend, Weaviate, Hermes, redis-exporter, Prometheus, and Grafana must continue to work against vanilla Redis commands. Module use must be opt-in per consumer and tested behind feature flags.
- Topology: do not change `data_flow.calls` merely because the image changes; add calls only when a consumer actually uses RediSearch, RedisJSON, RedisBloom, or RedisTimeSeries.
- `init companion`: none for the image swap itself, but add one if Atlas creates module indexes, ACL users, RedisJSON schema conventions, time-series retention, or RedisBloom filters.
- Edge cases: Redis 7.2 BSD vs Redis 8 tri-license drift, RSALv2/SSPLv1/AGPLv3 license acceptance, module persistence across AOF/RDB restore, index rebuild time, memory overhead, ACL compatibility, noeviction behavior, RedisInsight visibility of module data, Weaviate overlap, stale `.env`, and generated-doc drift.

Revisit when a named consumer ships a tested need for `FT.SEARCH`, `JSON.*`, `BF.*`, or `TS.*` that cannot be met by current Redis plus Weaviate/Postgres without meaningful complexity.

## 4. Stack wiring sketch
- Replace `REDIS_IMAGE` default in `services/redis/service.yml` from `redis:7.2.14-alpine` to `redis/redis-stack-server:7.4` behind a new `REDIS_VARIANT` toggle (`oss | stack`).
- backend → redis (Stack) via `FT.SEARCH` for lightweight semantic-cache lookups (avoid a full Weaviate hop for short-TTL queries).
- weaviate ↔ redis (Stack) — Redis Stack's vector index is NOT a Weaviate replacement, but it's a fast L1 cache for embeddings (`SET emb:<sha> <vec>` with `FT.SEARCH` over a HNSW index).
- n8n → redis (Stack) via the JSON.* commands for richer queue payloads than the current Bull encoding.
- hermes → redis (Stack) via RedisBloom `BF.EXISTS` for tool-call dedup.

## 5. Effort
small — Image swap + version bump; all existing consumers (kong, n8n, backend, litellm, open-webui, jupyterhub) continue to work because Redis Stack is wire-compatible with vanilla Redis. The work is one env var, one manifest note, and one CHANGELOG entry.

## 6. Risks & open questions
- Image is ~10× larger than `redis:7.2.14-alpine` (250 MB vs 30 MB); justify the gain.
- License: Redis Stack uses the Redis Source Available License v2 (RSALv2) since Redis 7.4 — fine for self-hosting, blocks redistribution-as-a-service.
- Existing `--appendonly yes` flag continues to work; module-specific persistence (FT indices) needs a brief audit.
- AGPLv3 of some bundled modules vs RSALv2 of others — confirm before adoption.

## 7. Why now (and why not sooner)
The stack already runs Weaviate for heavy vector search, but several emerging use-cases (embedding cache for the doc-processor, semantic-cache for LiteLLM responses, agent-tool-call dedup) want sub-millisecond latency that Weaviate doesn't deliver. Adding Redis Stack costs one image swap and keeps every existing consumer working unchanged.

## 8. Upstream evidence
- https://redis.io/legal/licenses/
- https://redis.io/docs/latest/operate/oss_and_stack/stack-with-enterprise/
- https://hub.docker.com/r/redis/redis-stack-server
- https://redis.io/docs/latest/develop/interact/search-and-query/
