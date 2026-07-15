---
category-fit: apps
generated: 2026-07-04
license: Apache-2.0
name: Supabase Edge Functions (Deno runtime)
referenced-by: [supabase]
slug: supabase-edge-functions
type: external-service
upstream: https://supabase.com/docs/guides/functions
---

# Supabase Edge Functions (Deno runtime)

## 1. Headline
Self-hostable Deno function runtime for short TypeScript, JavaScript, and WASM
handlers, useful only after Atlas has an edge-specific need that backend, n8n,
Celery/Flower, and Airflow do not already cover.

## 2. Problem it solves
Supabase Edge Functions are attractive when an app needs Supabase-compatible
`/functions/v1/*` URLs, database-webhook or `pg_net` proximity, short-lived
Deno/TypeScript handlers, or request handling that should stay near Supabase
Auth, Storage, and PostgREST. Current official self-hosting docs describe
edge-runtime as a Deno-based web server that can self-host Edge Functions or act
as a programmable HTTP proxy, but they also mark self-hosted functions as beta
with expected breaking changes.

## 3. Deferred decision (2026-07-04)
Atlas should keep Supabase Edge Functions deferred and must not add `services/supabase-edge-functions/service.yml` yet.
Atlas already has backend, n8n, Celery/Flower, and Airflow for server-side
execution: FastAPI request/route logic, workflow automation, retryable async
jobs, and scheduled DAGs. A second Deno function surface would duplicate those
patterns until Atlas can name a specific edge use case.

The self-hosting beta posture is the deciding factor. Edge Functions are
Apache-2.0 and technically aligned with Supabase, but Atlas should not add a
new runtime until it has upgrade guidance, auth/secret boundaries, and clear
rules for when to choose Edge Functions instead of backend, n8n, Celery, or
Airflow.

## 4. Stack wiring sketch
No current Atlas wiring should be added while Supabase Edge Functions are
deferred. If adopted later, the expected topology would be:

- Kong -> Edge Functions through `/functions/v1/*`, preserving Supabase URL
  shape and JWT behavior.
- Supabase DB/PostgREST/database webhooks/`pg_net` -> Edge Functions for
  row-triggered short handlers only when n8n/Celery/Airflow are too heavy or too
  far from Supabase auth semantics.
- Edge Functions -> LiteLLM for short LLM calls, with long-running work handed
  to Celery, n8n, or Airflow.
- Edge Functions -> Supabase Storage/PostgREST for user-scoped app actions with
  strict service-role boundaries.
- backend/n8n/Celery/Airflow remain the preferred execution surfaces unless the
  function's edge/Supabase URL-shape requirement is explicit.

## 5. Effort
Medium. The container is conceptually simple, but the Atlas work is mostly
policy and integration: manifest/source wiring, function-code mounting or
packaging, Kong path-route behavior, JWT verification defaults, import-map and
per-function env handling, secrets, docs, examples, and worker-choice guidance.

## 6. Risks & open questions
- Self-hosted Edge Functions are currently documented as beta with breaking
  change risk, so Atlas needs an upgrade and pinning posture before adoption.
- JWT verification and `SUPABASE_JWT_SECRET` drift can break all functions or
  accidentally allow execution with the wrong token set.
- Service-role keys are powerful; exposing them to arbitrary function code would
  bypass normal RLS expectations.
- CORS, public URL shape, function timeout/CPU/memory limits, and local
  hot-reload versus production deployment need explicit defaults.
- The largest product risk is execution-surface ambiguity: users should know
  when to use backend, n8n, Celery, Airflow, or Edge Functions.

## 7. Revisit criteria
Reconsider Supabase Edge Functions only when all of these are true:

- Backend, n8n, Celery/Flower, and Airflow do not cover a concrete server-side
  execution need.
- Atlas has an edge-specific use case such as Supabase URL compatibility,
  database webhook proximity, short Deno handlers, or user-scoped Supabase Auth
  behavior.
- Atlas accepts the self-hosting beta and breaking-change posture.
- Atlas has documented guidance that prevents the Deno function surface from
  duplicating the established async-job pattern.

## 8. Future service contract if adopted
- **Tracks:** `async-jobs` and `all`; add `gen-ai-eng` or `gen-ai-rag` only if a
  concrete app/RAG webhook workflow needs it.
- **Category:** choose deliberately between `agents` and `apps`. It is an
  execution/runtime surface, not core Supabase infra.
- **Sources:** `SUPABASE_EDGE_FUNCTIONS_SOURCE=disabled|container`; disabled by default.
  Consider `localhost` only for an operator-managed edge-runtime with the same
  URL/auth contract.
- **Wizard placement:** in an advanced async/serverless step after backend, n8n,
  Celery, and Airflow choices, with prompt copy warning that self-hosted Edge
  Functions are beta and overlap existing execution surfaces.
- **Ports and routes:** allocate ports through Atlas topology/category slots and
  custom `BASE_PORT` math. Preserve Supabase compatibility through
  `/functions/v1/*`; add a `functions.localhost` alias only if a product use case
  justifies it. There should be no public unauthenticated route by default.
- **Dependencies:** Kong, Supabase Auth, JWT secret, Supabase DB/REST/Storage,
  and optional LiteLLM. Backend, n8n, Celery, and Airflow are alternatives or
  handoff targets, not required dependencies.
- **Init/secrets:** version or mount function code deliberately; provide
  `SUPABASE_URL`, anon/service-role keys, JWT secret, verify-JWT defaults, import
  maps, and per-function env/secrets without leaking service-role keys to
  arbitrary user code.
- **Edge cases:** stale `.env`, JWT mismatch, service-role key exposure, CORS,
  function hot-reload/prod deployment mismatch, per-function env drift, local
  file/import-map failures, custom `BASE_PORT`, disabled Supabase subservices,
  timeout/CPU/memory limits, `pg_net` retry semantics, and generated-doc drift.

## 9. Tests required if adopted later
- Manifest schema and topology tests for source values, category, aliases,
  generated env vars, and track membership.
- Compose/source permutation coverage for disabled/container and any future
  localhost mode.
- Kong path-route tests for `/functions/v1/*`, including JWT and no-public-route
  expectations.
- Consumer guidance tests/docs that keep backend/n8n/Celery/Airflow versus Edge
  Functions boundaries explicit.
- Docs drift, research schema, link checks, and generated README/diagram checks.

## 10. Why now (and why not sooner)
Not now. Edge Functions should wait until Atlas has an edge-specific workflow
that the existing execution surfaces do not cover and until the self-hosting
beta posture is acceptable for the project.

## 11. Upstream evidence
- https://supabase.com/docs/guides/functions
- https://supabase.com/docs/reference/self-hosting-functions/introduction
- https://github.com/supabase/edge-runtime
