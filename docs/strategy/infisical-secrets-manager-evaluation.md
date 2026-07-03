# Infisical Secrets Manager Evaluation

Generated for issue #204, "Build Next: Secrets Manager With Infisical First".

## 1. Decision

Atlas should keep an **Infisical-first** secrets-management strategy for the next implementation slice, with **OpenBao watchlist** status for the Vault-lineage alternative.

Infisical is the better Atlas-first candidate because it is developer-oriented, self-hostable with familiar Postgres and Redis dependencies, and has machine identity flows suitable for service-to-service access. OpenBao is stronger when Atlas needs Vault-compatible APIs, dynamic secrets, PKI, seal/unseal procedures, or integrated Raft storage, but that operational model is heavier than Atlas needs for the first secrets-management slice.

The first implementation must be optional, disabled by default, and conservative: existing `.env` flows remain authoritative. Only new high-risk credentials should move to the secrets manager at first. Existing generated credentials, `.env` backfill, placeholder rotation, and Docker Compose rendering must continue to work without Infisical.

## 2. Current Upstream Findings

Official Infisical docs reviewed:

- Docker Compose deployment: https://infisical.com/docs/self-hosting/deployment-options/docker-compose
- Standalone Docker deployment: https://infisical.com/docs/self-hosting/deployment-options/standalone-infisical
- Hardware/database requirements: https://infisical.com/docs/self-hosting/configuration/requirements
- Machine identities: https://infisical.com/docs/documentation/platform/identities/machine-identities
- Secrets delivery concepts: https://infisical.com/docs/documentation/platform/secrets-mgmt/concepts/secrets-delivery

Observed on July 3, 2026:

- Latest GitHub release: `Infisical/infisical` `v0.161.12`, published July 3, 2026.
- Docker image: `infisical/infisical:latest`, multi-arch index digest `sha256:7ca5f0a7b96c271488df6afc83d1111212e64d02e80cd227e14ad7460ea50ed2`.
- Infisical self-hosting expects Postgres and Redis. The standalone image supplies only the app and needs external Postgres and Redis.
- Infisical machine identity authentication supports Token Auth, Universal Auth, Kubernetes Auth, AWS Auth, Azure Auth, and GCP Auth. Atlas Compose should prefer machine identity plus Universal Auth for service retrieval because it is platform-agnostic and does not require user credentials inside containers.
- Infisical docs identify PostgreSQL as the supported database and recommend Postgres 14+. Audit logs are database-backed, so storage growth and backup policy matter.

Official OpenBao docs reviewed:

- Integrated storage overview: https://openbao.org/docs/concepts/integrated-storage/
- Integrated storage internals: https://openbao.org/docs/internals/integrated-storage/
- Docker image: https://hub.docker.com/r/openbao/openbao

Observed on July 3, 2026:

- Latest GitHub release: `openbao/openbao` `v2.5.5`, published June 17, 2026.
- Docker image: `openbao/openbao:latest`, multi-arch index digest `sha256:6150c4a6b62067db6141c8da7a6a6b5763f4f47c315343d0c848b40fecdfd452`.
- OpenBao's integrated storage uses a Raft-style backend and does not require an external database, but it adds operator responsibilities around initialization, unseal or auto-unseal, token policy, audit devices, and backup/restore.

## 3. Why Not Migrate Existing Secrets First

Atlas already has a working bootstrap model:

- `.env.example` is generated from manifests.
- `.env` is backfilled and migrated in place.
- `KeyGenerator` rotates placeholder or missing secrets.
- ServiceConfig derives runtime env from SOURCE values.
- Compose renders from `.env` without requiring a separate control plane.

Replacing that foundation in the first Infisical slice would create a bootstrapping loop: Atlas would need Infisical to fetch secrets that are required to start Infisical. The first slice must not require Infisical to fetch the secrets needed to start Infisical.

The safe sequence is:

1. Keep `.env` as the bootstrap authority.
2. Add Infisical as an optional UI/API service, disabled by default.
3. Store only new high-risk credentials in Infisical first.
4. Add per-consumer retrieval hooks only when a specific service has a clear risk reduction.
5. Consider migrating existing credentials only after backup, recovery, auth, and operator workflows are proven.

## 4. First Candidate Secret Classes

Only new high-risk credentials should be candidates for the first Infisical-backed path:

| Secret class | Why it is high-risk | First-slice posture |
|---|---|---|
| Trading API keys | Can imply real financial exposure if live trading ever appears. | Store only read-only or paper-trading keys; block live exchange keys by default. |
| External MCP tool credentials | Tool access can expand agent blast radius. | Use scoped read-only credentials and explicit namespace/policy docs. |
| Paid model/provider keys for optional tracks | Can leak spend or data to external vendors. | Keep existing cloud-provider `.env` keys unchanged; use Infisical only for newly added provider integrations. |
| Webhook or bridge tokens for new services | Often copied across n8n/backend/notebook surfaces. | Prefer short-lived or easily rotated values when provider supports it. |

Existing Supabase, Redis, LiteLLM, MinIO, Neo4j, Label Studio, MLflow, Langfuse, and generated Atlas secrets should stay in `.env` for the first slice.

## 5. Service Admission Contract

This is the recommended contract for a future implementation issue, not code added by #204.

- Service name: `infisical`.
- Track: `identity-security` at the GitHub Project level. Atlas runtime tracks do not currently include `identity-security`; the first implementation should either expose Infisical only in `all`/Custom or add a dedicated runtime track in a separate decision.
- category: `infra`.
- SOURCE values: `INFISICAL_SOURCE=container|disabled`.
- Default: `disabled by default`.
- Wizard placement: security/infra prompt after locked core services and before high-risk optional consumers. Copy should say existing `.env` flows remain authoritative.
- Port/topology: allocate `INFISICAL_PORT` in the infra category block. Do not consume an app/data port.
- Kong alias: `infisical.localhost`, local dashboard auth only in the first slice. Do not expose publicly without TLS and SSO guidance.
- Direct URL: `http://localhost:${INFISICAL_PORT}`.
- Kong URL: `http://infisical.localhost:${KONG_HTTP_PORT}`.
- Required dependencies: Postgres and Redis.
- Dependency decision: evaluate reusing Supabase Postgres and Atlas Redis before adding dedicated Infisical-owned Postgres/Redis. Reuse is lighter; dedicated dependencies are cleaner but heavier.
- Downstream consumers: initially none by default. Add explicit consumers only for new high-risk credentials.
- Topology edges if reusing Atlas primitives: `infisical -> supabase`, `infisical -> redis`, and `kong -> infisical` when routed.
- Init companion: likely `infisical-init`, used only for idempotent DB/project/bootstrap checks that do not expose long-lived admin credentials in logs.
- Volumes/secrets: Infisical app secrets, encryption/signing material, bootstrap admin values, machine identity client id/secret, and database credentials must be separate from secrets managed inside Infisical.
- Backup: document how Infisical state and audit logs are backed up before moving any critical secret.

## 6. Integration Notes

Kong:

- Route the UI/API only when enabled.
- Keep local dashboard auth in front.
- Avoid public Cloudflare exposure until SSO/TLS posture is documented.

Supabase and Redis:

- Reusing existing services reduces container count and matches Atlas' current architecture.
- Reuse also couples secret-manager availability to the same database/cache as many other apps. That is acceptable for a first local-first slice if clearly documented.

n8n:

- n8n has its own external secrets capabilities, but Atlas should not wire n8n to Infisical before there is a specific workflow and policy model.

Backend, Airflow, JupyterHub, MCP, trading:

- Consumers should not receive broad Infisical admin tokens.
- Use machine identity, preferably Universal Auth, with scoped project/environment permissions.
- Runtime retrieval should fail closed and log only secret names/paths, never values.

Prometheus and Grafana:

- Do not emit secret values into metrics, labels, logs, or generated dashboards.
- Health checks should prove service readiness without disclosing project IDs or token material.

## 7. OpenBao Watchlist

OpenBao should remain the watchlist option when Atlas has one of these needs:

- Vault-compatible APIs or client libraries.
- Dynamic database/cloud credentials.
- PKI or certificate authority workflows.
- Strong seal/unseal or auto-unseal lifecycle requirements.
- Raft/integrated-storage clustering independent of Supabase Postgres.

Until then, OpenBao is likely heavier than necessary for a Docker Compose-first local engineering platform.

## 8. Acceptance Criteria For The Future Implementation Ticket

- Infisical service manifest exists with `INFISICAL_SOURCE=container|disabled`, default `disabled`.
- The service is category: `infra` and associated with track: `identity-security` in roadmap docs.
- The setup wizard explains that existing `.env` flows remain authoritative.
- Enabling Infisical does not remove, rewrite, or require migration of existing Atlas generated secrets.
- The service fails fast if its chosen Postgres/Redis dependencies are unavailable.
- Kong route `infisical.localhost` appears only when enabled.
- No downstream service receives Infisical credentials unless explicitly added by that implementation issue.
- Tests cover manifest admission, env-example generation, source validation, route gating, dependency gates, disabled mode, and stale `.env` preservation.
- Docs cover backup/restore, admin credential recovery, audit log growth, machine identity setup, Universal Auth, and the bootstrapping rule that Infisical cannot be required to start itself.
- OpenBao remains documented as the Vault-lineage alternative and is not implemented in the same first slice.

## 9. Recommendation

Close #204 as an evaluation artifact after this document lands. Create a separate implementation issue for an optional disabled-by-default Infisical service only after the team accepts the constraints above. That implementation should be narrow: add the service, route, docs, tests, and zero default consumers. A second follow-up should choose exactly one new high-risk credential class to move into Infisical.
