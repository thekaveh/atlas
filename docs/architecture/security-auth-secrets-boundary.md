# 6.11. Security, Auth, And Secrets Boundary

Supabase, Kong, service auth notes, API keys, local secrets, cloud keys, and intentionally unauthenticated local surfaces.

## 1. Diagram

[Open the interactive diagram](./security-auth-secrets-boundary.html).

## 2. How To Read This View

Supabase identities and scoped service credentials protect Backend data planes, while Kong applies gateway policy at published aliases. Generated local secrets and cloud keys stay in runtime configuration. Any deliberately unauthenticated local port remains an operator-trusted development boundary.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `bootstrapper/services/topology.py`
- `docs/deployment/source-configuration.md`
