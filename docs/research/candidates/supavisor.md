---
category-fit: data
generated: 2026-05-19
license: Apache-2.0
name: Supavisor
referenced-by: [supabase]
slug: supavisor
type: external-service
upstream: https://github.com/supabase/supavisor
---

# Supavisor

## 1. Headline
Supabase's Elixir-based Postgres connection pooler (transaction + session modes) that sits in front of `supabase-db` and shields it from the herd of stack consumers each opening their own pool.

## 2. Problem it solves
The current `supabase-db` is hit directly by backend, n8n, litellm, jupyterhub, local-deep-researcher, supabase-api (PostgREST), supabase-auth (GoTrue), supabase-realtime, supabase-storage, and any user notebook — easily 100+ idle connections at moderate scale, well past Postgres's default `max_connections=100`. PgBouncer would work, but Supavisor is the project's own choice, speaks the Postgres wire protocol, and handles JWT-scoped tenants in a way that matches the stack's auth model.

## 3. Stack wiring sketch
- backend -> supavisor:6543 (transaction mode) -> supabase-db:5432
- n8n -> supavisor:6543 -> supabase-db:5432
- litellm -> supavisor:6543 -> supabase-db:5432 (LiteLLM uses Postgres for spend/log persistence when configured)
- jupyterhub -> supavisor:6543 -> supabase-db:5432 (notebook-driven analytics)
- supabase-api (PostgREST) -> supabase-db:5432 directly (session-mode features required)
- supabase-realtime -> supabase-db:5432 directly (logical replication slot)

(PostgREST + Realtime intentionally bypass the pooler because both need session-level state.)

## 4. Effort
medium — one new container, one new env var family (`SUPAVISOR_*`), and updating each consumer's `*_DB_HOST`/`*_DB_PORT` env to point at the pooler. The bigger task is teaching the docs to distinguish "transaction-mode-safe" consumers from PostgREST/Realtime.

## 5. Risks & open questions
- Prepared-statement-heavy clients break in transaction mode; needs a per-consumer audit.
- Adds a hop, so query latency grows ~1-2 ms — irrelevant for app traffic, noticeable for tight loops.
- Supavisor's own Postgres dependency for tenant metadata adds a small bootstrap ordering wrinkle.

## 6. Why now (and why not sooner)
The stack only recently reached the point where 10+ services share one Postgres. Below that headcount, raw connections were fine. With JupyterHub, hermes, and local-deep-researcher all potentially opening pools, the connection budget is now the binding constraint.

## 7. Upstream evidence
- https://github.com/supabase/supavisor
- https://supabase.com/blog/supavisor-1-million
