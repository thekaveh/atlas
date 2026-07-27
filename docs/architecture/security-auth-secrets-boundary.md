# 6.11. Security, Auth, And Secrets Boundary

Supabase, Kong, service auth notes, API keys, local secrets, cloud keys, and intentionally unauthenticated local surfaces.

## 1. Diagram

[Open the interactive diagram](./security-auth-secrets-boundary.html).

## 2. Notes

Not every surface sits behind Supabase auth: Backend's `/health`, `/ready`, `/metrics`, and API-doc routes are intentionally public (no bearer token) — don't publish them beyond the intended network boundary. Kong's own Admin API (8001) is loopback-only, reachable via `docker exec`, never published. JupyterHub is explicitly operator-trusted, with direct database and service access rather than a policy gate.

## 3. Source Files

- `services/kong/service.yml`
- `services/supabase/service.yml`
- `bootstrapper/generate_supabase_keys.py`
