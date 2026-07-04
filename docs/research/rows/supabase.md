---
service: supabase
category: data
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - services/supabase/service.yml
  - services/supabase/db/scripts/01-extensions.sql
  - services/supabase/README.md
  - services/hermes/service.yml
  - services/docling/service.yml
  - services/openclaw/service.yml
  - services/parakeet/service.yml
  - services/chatterbox/service.yml
  - https://supabase.com/docs/guides/functions
  - https://github.com/supabase/supavisor
  - https://github.com/imgproxy/imgproxy
  - https://github.com/timescale/timescaledb
---

# supabase — Integration Research

## 1. Missing-pair integrations

- **supabase <-> hermes**
  - Why valuable: Hermes today persists agent state to a `hermes-data` volume only (per `services/hermes/service.yml` header comment "no Postgres/Redis dependency"). Backing sessions, skills, and tool-call history with Postgres gives durable cross-restart memory, multi-replica safety, and queryability from the rest of the stack.
  - Mechanism sketch: `postgresql://supabase_admin@supabase-db:5432/postgres` with a dedicated `hermes` schema; hermes-init creates tables under `IF NOT EXISTS`.
  - Effort: medium
  - Risks / open questions: Hermes upstream may not ship a Postgres backend — could require a thin shim writing JSON blobs; schema migration story across Hermes versions.
  - Confidence: medium (manifest comment confirms current file-only persistence; Postgres availability via supabase is trivial).

- **supabase <-> doc-processor**
  - Why valuable: Docling extracts structured chunks that today flow only into Weaviate as vectors. Persisting the raw chunk text + source metadata in Postgres (alongside the embedding pointer) gives RLS-scoped tenant isolation, exact-match search, and a source-of-truth row that Weaviate can be rebuilt from.
  - Mechanism sketch: docling writes via `supabase-api` PostgREST at `http://supabase-api:3000/rest/v1/doc_chunks` using `SUPABASE_SERVICE_KEY`; embeddings still go to weaviate.
  - Effort: medium
  - Risks / open questions: needs a chunk-table schema decision (parquet-shaped vs JSONB); duplication with Weaviate's object store must be intentional.
  - Confidence: medium (docling has no Postgres wiring today per its manifest; PostgREST is the supported insert path).

- **supabase <-> openclaw**
  - Why valuable: OpenClaw is the messaging-platform gateway and depends only on litellm; conversation history, user-to-channel mappings, and rate-limit counters currently live in-memory. Persisting them to Postgres unlocks restart-safe transcripts and the same RLS model the rest of the stack uses.
  - Mechanism sketch: `postgresql://supabase_admin@supabase-db:5432/postgres` schema `openclaw`; tables seeded by a small `openclaw-init` SQL script alongside the existing supabase-db-init chain.
  - Effort: small
  - Risks / open questions: depends on OpenClaw exposing a `DATABASE_URL` (or equivalent) env knob upstream; needs verification.
  - Confidence: medium (manifest confirms only litellm dependency; database wiring upstream support needs confirmation).

- **supabase <-> tts-provider**
  - Why valuable: Generated audio is ephemeral today. Storing TTS output objects in `supabase-storage` keyed by `(user_id, text_hash, voice)` gives a free cache (skip re-synth on identical inputs) and a per-user history pane.
  - Mechanism sketch: chatterbox (post-synth) -> `PUT http://supabase-storage:5000/object/tts/<user>/<hash>.wav` with `SUPABASE_SERVICE_KEY`; metadata row via PostgREST.
  - Effort: small
  - Risks / open questions: where the cache-key hashing lives (provider vs backend); storage growth needs a TTL policy.
  - Confidence: high (Supabase Storage upload semantics are standard; no manifest dependency exists today).

- **supabase <-> stt-provider**
  - Why valuable: Transcripts produced by parakeet/speaches vanish after the response. Writing them to a Postgres `transcripts` table with the caller's JWT `sub` enables history search, RAG-over-meetings, and per-user RLS isolation.
  - Mechanism sketch: stt-provider POSTs to PostgREST `/rest/v1/transcripts` with the forwarded `Authorization: Bearer <jwt>` header so RLS picks up the user.
  - Effort: small
  - Risks / open questions: large transcripts may belong in Storage with a pointer row; needs a size threshold.
  - Confidence: medium (parakeet/speaches manifests show no supabase wiring; PostgREST is the supported insert path).

## 2. Candidate new services

- **Supabase Edge Functions** -> `../candidates/supabase-edge-functions.md`
  - Headline: Deferred Deno runtime for edge workflow not covered by backend, n8n, Celery, or Airflow.
  - Other consumers: litellm, n8n, supabase-storage, kong

- **Supavisor** -> `../candidates/supavisor.md`
  - Headline: Supabase Postgres pooler for the 10+ stack services that open their own pools.
  - Other consumers: backend, n8n, litellm, jupyterhub, local-deep-researcher

- **imgproxy** -> `../candidates/imgproxy.md`
  - Headline: Image transform/resize sidecar for Supabase Storage's `IMGPROXY_URL`.
  - Other consumers: supabase-storage, minio, comfyui, open-webui, backend

- **TimescaleDB** -> `../candidates/timescaledb.md`
  - Headline: Watchlisted time-series path for a later paper trading-data slice.
  - Other consumers: jupyterhub, minio, redpanda, mlflow, grafana or superset.

## 3. Per-service feature gaps

- **pg_cron + pg_net extensions** — Why pursue: enables scheduled jobs and outbound HTTP from inside Postgres (database webhooks to Hermes/n8n/Edge Functions); `01-extensions.sql` only enables vector/postgis/pgcrypto. Effort: small.
- **Database Webhooks** — Why pursue: lets row-level changes trigger LiteLLM calls or n8n flows without a polling worker; depends on pg_net. Effort: small.
- **RLS gaps** — Why pursue: research tables have user/service policies and memory is service-role scoped, but `public.users` and ComfyUI tables still need table-specific RLS before broad PostgREST exposure. Effort: medium.
- **GoTrue OAuth providers (Google, GitHub)** — Why pursue: stack ships with email-only login; SSO is a near-zero-code add via `GOTRUE_EXTERNAL_*` envs. Effort: small.
- **pg_graphql endpoint** — Why pursue: README mentions "GraphQL endpoint available" but Kong has no route and no consumer; would give n8n/backend a typed schema. Effort: small.
- **Realtime broadcast + presence channels** — Why pursue: `supabase-realtime` runs but nothing subscribes; broadcast channels would let backend push job-status updates to open-webui without polling. Effort: medium.
- **Storage image transformation** — Why pursue: prerequisite for the imgproxy candidate; lights up resize URLs once `IMGPROXY_URL` is set. Effort: small.
