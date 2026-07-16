---
category-fit: infra
generated: 2026-07-04
license: MPL-2.0
name: OpenBao
referenced-by: [kong]
slug: openbao
type: external-service
upstream: https://github.com/openbao/openbao
---

# OpenBao

## 1. Headline
Vault-lineage secrets, dynamic credentials, and encryption-management system for a future Atlas secrets lifecycle that needs more than Infisical.

## 2. Watchlist decision (2026-07-04)

Keep OpenBao on the watchlist for now: Atlas **must not add `services/openbao/service.yml` yet** because the repo already has an Infisical-first secrets-management decision and no concrete secrets lifecycle and operator story that requires Vault-lineage compatibility. OpenBao is powerful, but its storage, unseal, backup, and bootstrap model is heavier than Atlas should adopt speculatively.

OpenBao should be revisited only if Infisical is insufficient or Atlas needs Vault-compatible APIs/client libraries, dynamic database/cloud credentials, PKI/certificate authority flows, transit encryption, Shamir unseal, auto-unseal, or integrated storage independent of Supabase Postgres.

Future service shape, if a later secrets ticket promotes this:

- Track membership: roadmap track `identity-security` and runtime `all` until Atlas adds a first-class identity/security runtime track.
- Service category: `infra`, because it becomes foundational secret infrastructure rather than an application surface.
- Source values/default: `OPENBAO_SOURCE=disabled|container`, disabled by default.
- Relationship to Infisical: keep Atlas Infisical-first unless a specific Vault-lineage requirement wins. Do not run both as overlapping default secrets managers.
- Wizard placement: identity/security section after the Infisical decision, with copy explaining the unseal/bootstrap responsibilities.
- Topology and port strategy: allocate one `infra` topology slot for the API/UI only if a manifest is added. Cluster/raft listener ports stay internal unless a production profile explicitly exposes them.
- Kong route behavior: `openbao.localhost` only when enabled and protected; no public route by default.
- Required dependencies: OpenBao storage, audit-device storage, bootstrap/unseal material, and backup path. Integrated storage is preferred for a first evaluation because it avoids another database but adds Raft/operator duties.
- Optional dependencies: Infisical migration/bridge scripts, future Authentik/Keycloak OIDC, backup/MinIO for snapshots, and high-risk consumers such as trading, MCP servers, model-download credentials, or PKI clients.
- Operator bootstrap gate: define initialization, root token handling, unseal key custody, bootstrap token lifetime, admin recovery, policy seeding, and whether Shamir unseal or auto-unseal is used.
- Storage/backup gate: choose integrated storage versus external storage, backup/restore process, audit device retention, cold-start behavior, and disaster-recovery drills before storing critical secrets.
- Consumer gate: no service receives OpenBao tokens until it has a narrow policy, auth method, lease/renewal behavior, and rotation story.
- Init companion: likely yes, but it must never log root tokens or unseal keys. It may validate readiness, seed policies, and create short-lived bootstrap material only after the operator story is approved.
- Tests required for a future service PR: manifest validation, source validation, env assembly, topology/category, disabled default, Kong route/auth, custom `BASE_PORT`, init idempotency without secret leakage, storage/unseal docs, backup docs, policy fixtures, no default consumers, compose source-permutation coverage, and docs drift.
- Edge cases: lost unseal keys, lost root token, sealed server restart, corrupt storage volume, stale `.env`, cold start, backup restore mismatch, audit log growth, running alongside Infisical, and generated-doc drift.

## 3. Problem it solves
OpenBao is the right direction when Atlas needs Vault-compatible workflows: dynamic secrets, transit encryption, PKI, database credential leasing, or stronger seal/unseal ceremonies. Those are real needs for future trading, MCP, multi-user, or PKI-heavy deployments, but not for the current local-first stack where `.env` remains the bootstrap authority and Infisical is the first optional secrets-manager slice.

## 4. Stack wiring sketch
- browser/operator -> kong -> openbao only when `openbao.localhost` is explicitly enabled and protected.
- services -> openbao only through narrow policies and explicit auth methods, never broad root/admin tokens.
- openbao -> integrated storage for first evaluation, or openbao -> external storage only after an operator decision.
- backup -> openbao for storage snapshots and audit-log retention once a backup path is approved.

## 5. Effort
medium-to-large — the container is easy; the hard parts are initialization, unseal or auto-unseal, root-token custody, policy seeding, audit devices, backup/restore, and safe downstream consumption.

## 6. Risks & open questions
- Bootstrap loop: Atlas must not require OpenBao to fetch the secrets needed to start OpenBao.
- Operator burden: sealed restarts, unseal key custody, recovery, and backup drills are mandatory responsibilities.
- Overlap: running OpenBao and Infisical without a crisp division would confuse operators and consumers.
- Exposure: a secrets manager route is high-value infrastructure and should not be exposed as another casual `*.localhost` UI.

## 7. Upstream evidence
- https://openbao.org/docs/install/
- https://openbao.org/docs/concepts/seal/
- https://openbao.org/docs/concepts/storage/
- https://openbao.org/docs/concepts/integrated-storage/
- https://openbao.org/docs/configuration/
- https://hub.docker.com/r/openbao/openbao
