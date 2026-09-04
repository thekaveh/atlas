# Atlas Audit Remediation Design

## 1. Objective

Close every finding from the 2026-08-29 whole-repository audit: 2 Critical,
4 High, 19 Medium, and 10 Low. The work lands on one consolidated branch and
one pull request, as requested, while preserving atomic commits and a
reviewable finding-to-commit ledger.

## 2. Delivery model

The branch is `codex/audit-remediation-all-findings`. Work is ordered by risk:

1. network and data-loss boundaries;
2. database and backup trust;
3. supply-chain integrity;
4. memory, Redis, and provider correctness;
5. configuration and observability;
6. documentation, tests, release history, and repository hygiene.

Each finding receives a failing regression test before production changes.
Each task is committed independently. A task is not closed until its focused
review reports both specification compliance and code-quality approval.

## 3. Global constraints

- Preserve every existing service, source mode, track, progress indicator,
  color, and UI element unless the finding explicitly requires a behavioral
  change.
- Default published host ports bind to `127.0.0.1`; an explicit
  `HOST_BIND_IP=0.0.0.0` remains supported.
- Never test restore or migration code against the user's Atlas volumes or
  `.env`; use disposable containers and generated fixtures.
- PostgreSQL authorization changes use expand-migrate-contract sequencing.
- No application container receives the PostgreSQL owner credential after the
  database-hardening task.
- Every network call and subprocess retains a finite deadline.
- Runtime artifacts, model artifacts, and build inputs are immutable or
  integrity-verified.
- Canonical documentation remains the only hand-edited documentation source;
  generated site/wiki trees stay ignored.
- Direct pushes to `develop` and `main` are forbidden. Integration uses PRs and
  all live `gitflow` checks.

## 4. Architecture decisions

### 4.1. Network exposure

`HOST_BIND_IP` owns host publication. Its generated default becomes
`127.0.0.1`; explicit remote binding is an operator decision. Container-to-
container traffic continues on Compose networks and never relies on published
host ports.

### 4.2. PostgreSQL restore and authorization

Restore is staged: validate the archive, restore into a temporary database,
validate, quiesce writers, and cut over. The original database survives every
pre-cutover failure. Authentication moves from host `trust` and shared owner
credentials to SCRAM and scoped roles using an expand-migrate-contract rollout.

### 4.3. Backups

PostgreSQL uses logical archives with staged restore. Neo4j and Weaviate use
their pinned versions' supported online backup/snapshot interface where
available; otherwise Atlas explicitly quiesces the service before a storage
snapshot. External backup credentials are independent from local MinIO root
credentials.

### 4.4. Memory and Redis

pgvector success is authoritative when pgvector is the selected fallback. The
embedding dimension is validated before startup and represented in schema
migration state. Redis listing and recovery use bounded cursor-based batches.
Redis-backed multi-process ingestion fails with a typed 503 when Redis is
unavailable; process-local fallback is limited to explicit single-process mode.

### 4.5. Provider contracts and readiness

Upstream transport, HTTP, JSON, and schema failures remain distinguishable.
ComfyUI success requires a non-empty prompt identifier. Health reports required
component state rather than configuration presence. ComfyUI readiness requires
successful provisioning of every selected required asset.

### 4.6. Supply chain

ComfyUI model URLs use immutable revisions and mandatory SHA-256 values. Init
tools move into pinned images or exact repository snapshots. Custom-node Python
dependencies are compiled and hashed. The NLTK advisory is fixed in a minimal
lock update. The container-security workflow is executed live before closure.

## 5. Review model

Every implementation task has one implementer self-review and one independent
task review. At branch completion, three independent adversarial reviews inspect
the complete branch from distinct perspectives:

1. correctness, concurrency, error handling, and data-loss paths;
2. security, supply chain, secrets, and deployment boundaries;
3. tests, documentation, compatibility, operability, and gitflow readiness.

All Critical and Important review findings are fixed and re-reviewed. Minor
findings are fixed unless doing so would expand scope beyond an audited issue;
any retained item must be explicitly documented in the PR.

## 6. Completion criteria

All 35 finding IDs map to committed changes or verified operational cleanup;
all targeted and repository-wide checks pass in a suitable environment; the
branch is pushed; a PR to `develop` is open; and the PR description carries the
finding ledger, test evidence, residual limitations, and rollback notes.
