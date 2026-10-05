---
service: redis
category: data
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - services/redis/service.yml
  - services/redis/compose.yml
  - services/redis/README.md
  - services/comfyui/compose.yml
  - services/local-deep-researcher/compose.yml
  - services/n8n/compose.yml
  - services/kong/compose.yml
  - services/litellm/compose.yml
  - https://redis.io/docs/latest/commands/xadd/
  - https://redis.io/docs/latest/operate/oss_and_stack/stack-with-enterprise/
  - https://github.com/RedisInsight/RedisInsight
---

# redis — Integration Research

## 1. Missing-pair integrations

- **redis ↔ comfyui**
  - Why valuable: ComfyUI's compose already declares `depends_on: redis` (startup ordering only) but the container receives no `REDIS_URL`. A real link would let n8n/backend enqueue generation jobs to a Redis list/stream and read `progress`/`executed` websocket events back via a sidecar publisher, replacing the current "open a websocket per caller" pattern.
  - Mechanism sketch: ComfyUI custom node + `redis-py` writing `XADD comfyui:events` on `progress`/`executed`; producers `BLPOP comfyui:jobs` from a tiny worker that calls `/prompt`.
  - Effort: medium
  - Risks / open questions: needs a small custom-node or sidecar (ComfyUI core has no Redis hook); event payloads include image bytes — store URLs, not blobs.
  - Confidence: medium (XADD/BLPOP are core Redis commands — see XADD upstream doc; ComfyUI websocket events are documented).

- **redis ↔ local-deep-researcher**
  - Why valuable: The manifest comment in `services/redis/service.yml` already reserves `db=3` for local-deep-researcher, but the service's compose has no `REDIS_URL`. LangGraph offers a Redis checkpointer that would let long-running research runs survive container restarts and let the backend stream node-by-node progress to the UI.
  - Mechanism sketch: `redis://:${REDIS_PASSWORD}@redis:6379/3` consumed by `langgraph.checkpoint.redis.RedisSaver` and a `PUBSUB` channel `ldr:run:<id>` for progress.
  - Effort: small
  - Risks / open questions: LangGraph Redis checkpointer is newer than the Postgres one — pin the version.
  - Confidence: high (db slot already documented in the manifest; LangGraph ships an official Redis checkpointer).

- **redis ↔ hermes**
  - Why valuable: Hermes-agent currently has no shared state between requests; conversation memory, tool-call rate-limits, and per-user budget counters all live in process. A small `redis://` URL turns Hermes into a horizontally-scalable agent.
  - Mechanism sketch: Hermes skill / tool reading-writing `hermes:session:<id>` hashes and `hermes:ratelimit:<user>` counters via `redis-py`; same `REDIS_URL` already exported.
  - Effort: small
  - Risks / open questions: Hermes provider config is opinionated (see `reference_hermes_provider_config.md`); needs a custom skill, not a built-in.
  - Confidence: medium (mechanism is standard, but no first-class Hermes-Redis integration upstream).

- **redis ↔ openclaw**
  - Why valuable: Messaging-platform agent gateways need cross-process session state (which channel maps to which agent thread) and dedup of inbound webhook deliveries — both classic Redis use-cases. Openclaw is not currently wired to Redis (note openclaw is opt-in — `OPENCLAW_SOURCE` defaults to `disabled`).
  - Mechanism sketch: `redis://:${REDIS_PASSWORD}@redis:6379/4` for `openclaw:webhook:dedup` (SETEX with 5-min TTL) and `openclaw:channel:<id>` session hashes.
  - Effort: small
  - Risks / open questions: openclaw's upstream Redis support not yet audited in this session.
  - Confidence: low (mechanism is sound; haven't confirmed openclaw exposes a Redis config knob).

- **redis ↔ doc-processor**
  - Why valuable: Document parsing is expensive and idempotent on file SHA. A Redis cache keyed on `sha256(file)` would let repeat ingests (common during n8n flow iteration) short-circuit, and a Redis stream would broadcast `doc:parsed` events to backend + weaviate ingest.
  - Mechanism sketch: Cache: `SETEX doc:parsed:<sha> 86400 <json>`; event bus: `XADD doc:events` consumed by backend.
  - Effort: small
  - Risks / open questions: parsed payloads can be MB-scale — cap at metadata + S3/MinIO pointer.
  - Confidence: medium.

- **redis ↔ weaviate**
  - Why valuable: Embedding generation dominates ingest latency; a content-hash → vector cache cuts repeat-ingest cost dramatically and de-duplicates concurrent embeddings of the same chunk across n8n/backend.
  - Mechanism sketch: `GET emb:<model>:<sha>` before calling Weaviate's vectorizer; `SETEX` on miss. Lives behind a tiny helper in backend.
  - Effort: medium
  - Risks / open questions: vector blobs are ~4 KB each — tune `maxmemory-policy allkeys-lru` (currently unset).
  - Confidence: medium.

## 2. Candidate new services

- **RedisInsight** → `../candidates/redisinsight.md`
  - Headline: Official Redis GUI for browsing keys, profiling commands, and inspecting streams across all stack consumers.
  - Other consumers in stack: backend, n8n, kong, litellm, open-webui, jupyterhub

- **Redis Stack (redis-stack-server)** → `../candidates/redis-stack.md`
  - Headline: Drop-in Redis image bundling RediSearch, RedisJSON, RedisBloom, and RedisTimeSeries — unlocks vector + JSON queries without a second datastore.
  - Other consumers in stack: backend, weaviate (overlap), n8n, hermes

## 3. Per-service feature gaps

- **Redis Streams (XADD/XREAD/consumer groups)** — Why pursue: replace ad-hoc HTTP fan-out between backend, n8n, ComfyUI, and doc-processor with a single durable event bus already present in the image. Effort: medium.
- **Pub/Sub channels** — Why pursue: live progress streaming for ComfyUI and local-deep-researcher to the open-webui chat surface without polling. Effort: small.
- **Redis ACL users** — Why pursue: replace the single shared `REDIS_PASSWORD` with per-service users so a compromised n8n container cannot read LiteLLM's budget counters. Effort: small.
- **`maxmemory` + eviction policy** — Why pursue: cache use-cases (embedding cache, doc cache) need `allkeys-lru`; currently unbounded. Effort: small.
- **RDB snapshots alongside AOF** — Why pursue: faster cold-start restore; current `--appendonly yes` is durable but slow to replay. Effort: small.

