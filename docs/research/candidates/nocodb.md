---
category-fit: apps
generated: 2026-07-04
license: AGPL-3.0
name: NocoDB
referenced-by: [n8n]
slug: nocodb
type: external-service
upstream: https://github.com/nocodb/nocodb
---

# NocoDB

## Headline
Open-source Airtable-style spreadsheet UI that exposes any Postgres schema as editable tables, kanbans, and forms — backed by an existing relational store rather than a new datastore.

## Watchlist decision (2026-07-04)

Keep NocoDB on the watchlist for now: Atlas **must not add `services/nocodb/service.yml` yet** because the stack does not have a concrete human-review queue that needs an end-user spreadsheet UI, and the SSO and route-auth posture for multi-user operational editing is still unsettled. NocoDB is useful, but it should enter Atlas as a product workflow surface, not as another generic database console.

NocoDB is not a Supabase Studio replacement. Supabase Studio remains the admin/operator surface for the core database and should not be broadened into an end-user queue by default. NocoDB is also not a Label Studio replacement; Label Studio already owns ML/RAG annotation and dataset-review workflows. A future NocoDB slice should be for operational rows that n8n/backend workflows create, humans edit or approve, and automation consumes again with provenance.

Future service shape, if a later human-review ticket promotes this:

- Track membership: `platform` by default; optionally `agents` when tied to n8n workflow approvals, and optionally `rag` when tied to RAG curation queues. Do not add it to every track just because it can view tables.
- Service category: `apps`, because users interact with it as a browser UI.
- Source values/default: `NOCODB_SOURCE=disabled|container`, disabled by default.
- Wizard placement: apps or workflow/human-review section after n8n, with copy that it is an end-user review queue surface, not the admin database UI.
- Topology and port strategy: allocate one `apps` topology slot for the NocoDB web/API container; worker-mode/background-job containers stay internal.
- Kong alias and route behavior: `nocodb.localhost` only when `NOCODB_SOURCE=container`; route must be protected by NocoDB auth and/or Atlas route auth before any production profile.
- Direct URL expectations: direct host port for local development; Kong URL for browser use.
- Required dependencies: Supabase/Postgres for NocoDB metadata and review-table storage, plus Redis if the chosen NocoDB deployment uses upstream cache/job-queue behavior.
- Optional dependencies: n8n's first-party NocoDB node for workflow CRUD, backend REST calls for admin or queue lifecycle operations, MinIO/S3 if attachments belong in review rows, and the future Atlas SSO provider if/when the auth track lands.
- Downstream consumers: `n8n -> nocodb` for row CRUD and `backend -> nocodb` only for scoped admin/review operations.
- `data_flow.calls` topology edges for a future service: `nocodb -> supabase`, `nocodb -> redis`, optional `nocodb -> minio`, optional `n8n -> nocodb`, and optional `backend -> nocodb`.
- Init companion: likely yes. It must create the isolated schema/database/role, generate first-user/bootstrap secrets without hardcoding them, and seed only a safe example base if the selected workflow requires one.
- Containers: expect at least a web/API container and possibly a worker-mode container if imports, exports, automations, or background jobs are enabled.
- Review-workflow readiness gate: identify the producer, reviewer, state machine, table/schema ownership, downstream consumer, retention, and provenance columns before adding the service.
- Tests required for a future service PR: manifest validation, source validation, env assembly, topology/category, track membership, Kong route/auth, compose source-permutation coverage, init idempotency, disabled-dependency behavior, custom `BASE_PORT`, docs drift, first-user/bootstrap secret checks, SSO/route-auth documentation, and at least one smoke for the chosen review queue.
- Edge cases: disabled n8n, disabled backend, disabled Redis if deployment can run without workers, disabled SSO, existing Supabase schemas with sensitive tables, stale `.env`, custom `BASE_PORT`, route exposure without auth, duplicate ownership with Label Studio, and generated-doc drift.

## Problem it solves
n8n workflows may eventually need a lightweight CRUD surface for human-in-the-loop data (review queues, prompt libraries, ComfyUI generation logs, tagged transcripts). Today the stack has Supabase Studio for admin database work, Label Studio for annotation, and n8n/backend queues for automation. NocoDB becomes valuable when Atlas can name an end-user review workflow that should be editable as rows without exposing the whole database.

## Stack wiring sketch
- nocodb → supabase via `postgresql://supabase-db:5432/<db>` with a `nocodb` schema (mirrors how n8n uses an `n8n` schema).
- nocodb → redis for cache/job-queue behavior if Atlas follows the current upstream self-hosting shape.
- n8n → nocodb via the built-in NocoDB node (`http://nocodb:8080`) for row CRUD inside workflows.
- backend → nocodb via REST for admin operations.
- kong → nocodb via a `nocodb.localhost` alias.

## Effort
medium — the container itself is straightforward, but a useful Atlas integration needs workflow-specific schema ownership, first-user/bootstrap secrets, route/auth decisions, and likely a web/API plus worker-mode deployment shape. It should not be treated as just one manifest and one Kong alias.

## Risks & open questions
- AGPL-3.0 — fine for self-host, requires source disclosure if exposed as a SaaS.
- NocoDB writes to its own metadata tables on startup; the `nocodb` schema needs createable on first boot.
- Auth model is separate from Supabase Auth — users have to log in twice unless Atlas adds a route-auth or SSO bridge. Current upstream OIDC/SSO documentation is plan/licensing-sensitive for self-hosted deployments, so this cannot be assumed as an OSS default.
- Product overlap is real: Supabase Studio is the admin DB UI, Label Studio is the annotation UI, and NocoDB must justify a separate operational queue UI.
- Review data ownership needs a clear boundary so users do not accidentally expose internal Supabase tables through a spreadsheet surface.

## Why now (and why not sooner)
Not now. Revisit when Atlas has a named human-review workflow and an auth story that does not turn `nocodb.localhost` into an unaudited database-editing surface.

## Upstream evidence
- https://github.com/nocodb/nocodb
- https://nocodb.com/docs/self-hosting/installation/quickstart
- https://nocodb.com/docs/self-hosting/environment-variables
- https://nocodb.com/docs/product-docs/account-settings/authentication
- https://nocodb.com/docs/product-docs/account-settings/authentication/oidc-sso/auth0
- https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.nocodb/
