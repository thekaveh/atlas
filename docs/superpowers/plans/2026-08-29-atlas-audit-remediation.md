# Atlas Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all 35 findings from the 2026-08-29 Atlas repository audit on one consolidated, reviewable branch.

**Architecture:** Apply severity-ordered, test-first changes as atomic commits on `codex/audit-remediation-all-findings`. Preserve compatibility through explicit migrations and operator opt-ins; use disposable service harnesses for stateful integration proof and keep the canonical documentation pipeline authoritative.

**Tech Stack:** Docker Compose, Bash, Python 3.12, FastAPI, PostgreSQL/Supabase, Redis, pgvector, Neo4j, Weaviate, ComfyUI, OpenTelemetry, MkDocs, pytest, GitHub Actions.

## 1. Global Constraints

- Preserve all existing service/source/track behavior except the exact audited defect being corrected.
- Default host publication is `127.0.0.1`; explicit `HOST_BIND_IP=0.0.0.0` remains valid.
- Tests never use the user's `.env`, Atlas volumes, or authoritative database.
- PostgreSQL security changes follow expand-migrate-contract and never strand an upgrade between commits.
- Every new production behavior starts with a failing regression test.
- Every network/subprocess path has a finite deadline and stable redacted error.
- Generated documentation trees and root `mkdocs.yml` remain ignored.
- Each task ends in one or more atomic commits and an independent task review.

---

### 1.1. Task 1: Loopback-by-default host binding (C2)

**Files:**
- Modify: `.env.example`
- Modify: `services/globals/service.yml`
- Modify: `bootstrapper/services/env_assembler.py`
- Test: `bootstrapper/tests/test_env_assembler.py`
- Test: `bootstrapper/tests/test_fragment_equivalence.py`
- Test: `bootstrapper/tests/test_source_permutations.py`

**Interfaces:**
- Produces: `HOST_BIND_IP` default `127.0.0.1`; explicit operator values pass through unchanged.

- [ ] Add failing tests that render fresh defaults and assert every published binding begins with `127.0.0.1:` while an explicit `0.0.0.0` remains unchanged.
- [ ] Run the focused tests and confirm failure reports the current empty default.
- [ ] Change the manifest-owned default and regenerate `.env.example`; add upgrade guidance for existing empty values.
- [ ] Run env assembler, fragment equivalence, source permutations, and `docker compose config` against generated defaults.
- [ ] Commit as `fix(security): bind published services to loopback by default`.

### 1.2. Task 2: Failure-atomic PostgreSQL restore (C1)

**Files:**
- Modify: `services/backup/init/scripts/restore-postgres.sh`
- Modify: `services/backup/service.yml`
- Modify: `services/backup/README.md`
- Test: `bootstrapper/tests/test_cloudflared_backup_contracts.py`
- Create: `bootstrapper/tests/test_postgres_restore_safety.py`

**Interfaces:**
- Produces: staged restore phases `preflight`, `restore`, `validate`, and `cutover`; original database is untouched before cutover.

- [ ] Add shell-contract tests for `pg_restore --list`, `--exit-on-error`, a temporary database, validation, and cutover ordering.
- [ ] Add disposable-PostgreSQL tests for corrupt archive, mid-restore SQL error, failed validation, and successful cutover; confirm the failure cases currently mutate or target the live database.
- [ ] Implement strict preflight and staged restore with traps that remove only the generated temporary database.
- [ ] Run ShellCheck and the focused disposable-container suite.
- [ ] Update backup documentation with maintenance-mode and rollback semantics.
- [ ] Commit as `fix(backup): make PostgreSQL restore failure atomic`.

### 1.3. Task 3: Scoped PostgreSQL roles and SCRAM (H1)

**Files:**
- Modify: `services/supabase/db/scripts/`
- Modify: `services/supabase/compose.yml`
- Modify: affected service manifests and Compose files
- Test: `bootstrapper/tests/test_seed_scripts_equivalence.py`
- Create: `bootstrapper/tests/test_database_role_boundaries.py`

**Interfaces:**
- Produces: idempotent per-service roles, least-privilege grants, SCRAM host authentication, and scoped connection URLs.

- [ ] Inventory every consumer's tables, schemas, extensions, and required DDL/DML operations in the test fixture.
- [ ] Write failing integration tests proving one service can currently read/drop another service's schema and authenticate without a password.
- [ ] Add idempotent role/grant creation while retaining upgrade compatibility.
- [ ] Switch each consumer to its scoped credential and add missing-secret startup failures.
- [ ] Replace host `trust` with SCRAM and remove owner credentials from application containers.
- [ ] Test fresh initialization, upgrade, restart, backup, restore, and cross-role denial.
- [ ] Commit expand, migrate, and contract stages separately.

### 1.4. Task 4: Pre-body media authentication and one-copy buffering (H4)

**Files:**
- Modify: `services/backend/app/app/media_request_limit.py`
- Modify: `services/backend/app/app/backend_identity.py`
- Modify: `services/backend/app/app/main.py`
- Test: `services/backend/app/app/tests/test_media_request_limit.py`
- Test: `bootstrapper/tests/test_backend_route_auth.py`

**Interfaces:**
- Produces: an early media auth gate that rejects before ASGI `receive`; bounded single-copy/spooled request handling.

- [ ] Write a failing ASGI test whose `receive` raises if called for an unauthorized request.
- [ ] Add authorized chunked, declared-length, auth-disabled, and concurrent-limit tests.
- [ ] Implement the early header gate using the existing principal validation contract.
- [ ] Replace chunk-list plus join with bounded spooling or one-copy accumulation.
- [ ] Run the full media/auth test group under `-W error`.
- [ ] Commit as `fix(backend): authenticate media requests before buffering`.

### 1.5. Task 5: Independent external backup credentials (M11)

**Files:**
- Modify: `services/backup/service.yml`
- Modify: `services/backup/compose.yml`
- Modify: backup scripts and README
- Test: `bootstrapper/tests/test_cloudflared_backup_contracts.py`

- [ ] Add failing rendered-Compose tests showing external S3 currently reuses MinIO root credentials and requires MinIO.
- [ ] Add `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`, region, session-token, endpoint, and TLS variables with redaction coverage.
- [ ] Make local MinIO dependency conditional for external endpoints.
- [ ] Test local MinIO and disposable S3-compatible external modes.
- [ ] Commit as `fix(backup): separate external S3 credentials`.

### 1.6. Task 6: Consistent Neo4j and Weaviate backup/restore (H2)

**Files:**
- Modify: `services/backup/init/scripts/backup-all.sh`
- Create or modify: backup restore scripts
- Modify: backup manifest/README
- Create: `bootstrapper/tests/test_database_volume_backup_contracts.py`

- [ ] Pin executable backup contracts for the repository's exact Neo4j and Weaviate versions.
- [ ] Add failing tests showing ordinary live `tar` is used and no restore path exists.
- [ ] Implement native online backup where supported; otherwise quiesce, snapshot, restart, and report the service boundary explicitly.
- [ ] Record checksums, versions, timestamps, and completeness metadata.
- [ ] Run write-during-backup and full restore drills against disposable services.
- [ ] Commit as `fix(backup): capture consistent database snapshots`.

### 1.7. Task 7: Immutable verified ComfyUI model catalog (H3)

**Files:**
- Modify: `services/comfyui/models.yaml`
- Modify: `services/comfyui/init/scripts/download_models.sh`
- Modify: model catalog validators/tests

- [ ] Add failing validation tests for mutable revisions and absent SHA-256 values.
- [ ] Resolve all 13 artifacts to immutable revisions and record verified hashes.
- [ ] Require hashes for cached and new files before atomic rename.
- [ ] Test wrong hash, corrupt cache, interrupted transfer, empty response, and valid cache reuse.
- [ ] Commit as `fix(comfyui): pin and verify every model artifact`.

### 1.8. Task 8: Minimal NLTK security refresh (M1)

**Files:**
- Modify: `services/jupyterhub/build/requirements-locked.txt`
- Test: runtime lock and security-floor tests

- [ ] Add/adjust a failing security-floor assertion requiring NLTK `>=3.10.2`.
- [ ] Regenerate both JupyterHub architecture locks without unrelated upgrades.
- [ ] Run lock byte-equivalence, notebook imports, and `scripts.audit_runtime_locks`.
- [ ] Commit as `fix(security): upgrade NLTK past PYSEC-2026-3726`.

### 1.9. Task 9: Reproducible init and build dependencies (M13)

Task 5 moved the backup runner's `mc` pinning forward: it now downloads one exact official release, verifies architecture-specific SHA-256 values and `mc --version`, and no longer installs Alpine's mutable `minio-client`. This task inherits only the backup runner's remaining runtime-install reproducibility work (OpenSSL), alongside the other listed services.

**Files:**
- Modify: affected init Dockerfiles/scripts
- Modify: Backend/JupyterHub Dockerfiles
- Create: ComfyUI custom-node compiled lock inputs/outputs
- Test: build-validation and runtime-lock suites

- [ ] Add failing static tests rejecting runtime `apk add`, unconstrained custom-node installs, and mutable `apt-get upgrade` behavior.
- [ ] Bake required init tools into digest-pinned images or exact snapshot repositories.
- [ ] Compile and hash custom-node dependency closures.
- [ ] Replace mutable upgrades with reviewed base-image refresh policy.
- [ ] Build every affected image and run installability/audit checks.
- [ ] Commit each independent closure as an atomic supply-chain commit.

### 1.10. Task 10: Remote digest drift and first live container scan (M15, L10)

**Files:**
- Modify: `.container-scan-exclusions.yml`
- Modify if proven necessary: `.github/workflows/container-security.yml`
- Test: `bootstrapper/tests/test_container_security.py`

- [ ] Verify the observed `python:3.12-slim` index and remote Dockerfile provenance.
- [ ] Add/update a test fixture for the reviewed digest and record evidence in the contract ledger.
- [ ] Update the baseline only after provenance review.
- [ ] Dispatch the workflow and fix any matrix/execution defect through failing tests first.
- [ ] Commit as `chore(security): review remote base-image drift`.

### 1.11. Task 11: Re-review expiring advisory exceptions (L8)

**Files:**
- Modify: `scripts/audit_runtime_locks.py`
- Modify: affected requirements/locks when fixes resolve
- Test: `bootstrapper/tests/test_runtime_lock_audit.py`

- [ ] Re-resolve each JupyterHub and Parakeet advisory against current upstream constraints.
- [ ] Remove fixed or stale exception IDs; upgrade compatible dependencies.
- [ ] For any retained unreachable advisory, update exact call-path evidence and a bounded date.
- [ ] Run every runtime vulnerability audit and stale-exception test.
- [ ] Commit as `chore(security): renew runtime advisory evidence`.

### 1.12. Task 12: Authoritative pgvector fallback and dimension contract (M2, M3)

**Files:**
- Modify: Backend memory store/service
- Modify: model resolver and Supabase memory migration
- Test: memory failure, resolver, and disposable-pgvector tests

- [ ] Write failing tests for disabled Weaviate, successful pgvector pending clearance, outage recovery, and non-768 model operations.
- [ ] Introduce validated embedding-dimension configuration and an expand/backfill/index/contract migration.
- [ ] Treat pgvector success as authoritative when selected; make Weaviate re-probe/failback explicit.
- [ ] Run Backend memory suites and real pgvector integration tests for 768, 1536, and 3072 dimensions.
- [ ] Commit migration and runtime changes as separately reversible commits.

### 1.13. Task 13: Bounded Redis state and explicit outage contract (M8, M9, L2)

**Files:**
- Modify: RAG ingestion and media operation stores
- Modify: list API models/routes
- Test: RAG, async job, media operation, and live Redis contract tests

- [ ] Add failing high-cardinality tests that detect `SMEMBERS`, unbounded `MGET`, and unpaginated responses.
- [ ] Add a failing unreachable-Redis route test expecting typed 503.
- [ ] Add live Redis tests for exact-owner Lua takeover, unrelated owner, terminal record, TTL, and stale index cleanup.
- [ ] Implement cursor-based bounded batches, pagination, and capped recovery cycles.
- [ ] Restrict memory fallback to explicit single-process mode.
- [ ] Run focused Backend tests and disposable Redis tests.
- [ ] Commit as `fix(backend): bound Redis recovery and fail outages explicitly`.

### 1.14. Task 14: Strict legacy ComfyUI API contracts (M4, M5, L1)

**Files:**
- Modify: `comfyui_client.py` and legacy routes/models
- Test: ComfyUI failure/provider/route tests

- [ ] Add failing tests for transport errors, non-2xx, invalid JSON, `{}`, null/wrong prompt IDs, and explicit null request fields.
- [ ] Add typed upstream exceptions and 502/503 route mappings.
- [ ] Require nonempty prompt IDs and normalize/reject explicit null defaults.
- [ ] Run all ComfyUI and media gateway tests under `-W error`.
- [ ] Commit as `fix(backend): enforce truthful ComfyUI contracts`.

### 1.15. Task 15: Truthful aggregate health (M6)

**Files:**
- Modify: Backend research/media health routes and services
- Test: health component-state matrices

- [ ] Add failing matrices for database-up/client-down, invalid FAL key, provider timeout, configured-only, and fully healthy cases.
- [ ] Aggregate required component health; represent unprobed FAL as `configured` or `unknown`.
- [ ] Run health and readiness suites.
- [ ] Commit as `fix(backend): report feature health from required components`.

### 1.16. Task 16: ComfyUI provisioning-gated readiness (M10)

**Files:**
- Modify: model/node catalogs, provisioning scripts, Compose healthcheck
- Test: provisioning and Compose contract tests

- [ ] Add failing tests for required download/node failure currently producing healthy state.
- [ ] Mark assets required or optional and write an atomic verified provisioning manifest.
- [ ] Gate readiness on the selected required asset plan.
- [ ] Test required failure, optional warning, retry, cache reuse, and success.
- [ ] Commit as `fix(comfyui): gate readiness on required provisioning`.

### 1.17. Task 17: Supported runtime knob propagation (M7)

**Files:**
- Modify: Backend manifest, Backend/Celery Compose, env docs
- Test: env assembler and rendered-Compose tests

- [ ] Add failing tests for missing CHONKIE model, LightRAG timeout, and ingestion TTL propagation.
- [ ] Declare variables, inject worker-shared values, and validate positive/range constraints before use.
- [ ] Regenerate `.env.example` and documentation.
- [ ] Commit as `fix(config): expose supported backend runtime knobs`.

### 1.18. Task 18: JupyterHub MCP connectivity (M12)

**Files:**
- Modify: JupyterHub manifest/Compose and adaptive config
- Test: rendered Compose and notebook smoke tests

- [ ] Add failing tests that enable MCP and observe an empty notebook endpoint.
- [ ] Inject the internal URL only when MCP is enabled; preserve disabled/localhost modes.
- [ ] Execute the curated notebook against a disposable MCP server.
- [ ] Commit as `fix(jupyterhub): connect curated notebooks to MCP`.

### 1.19. Task 19: Durable OTLP-to-Loki logging (M14)

**Files:**
- Modify: OTel collector configuration/Compose
- Modify: Loki manifest/docs and Grafana provisioning if needed
- Test: observability configuration and disposable-stack smoke tests

- [ ] Add failing config tests showing logs terminate at the debug exporter.
- [ ] Add a supported Loki exporter/receiver path, bounded collection, redaction, and retention settings.
- [ ] Emit a trace-correlated test log and query it from disposable Loki.
- [ ] Test authorization-header and secret redaction.
- [ ] Commit as `feat(observability): persist OTLP logs in Loki`.

### 1.20. Task 20: Complete canonical documentation indexing (M16)

**Files:**
- Modify: `docs/README.md`, `docs/manifest.yaml`, docs checks

- [ ] Add a failing reachability test for every manifest-owned canonical page.
- [ ] Generate or complete authoritative sub-index links without hand-copying the service inventory.
- [ ] Run `make docs-check`, strict MkDocs, wiki dry run, and local link checks.
- [ ] Commit as `docs: complete the canonical documentation index`.

### 1.21. Task 21: Correct AGENTS architecture and testing guidance (M17, M18)

**Files:**
- Modify: `AGENTS.md`
- Test: focused documentation-claim tests

- [ ] Add failing assertions for the Backend path/test command, migration v4, and current adaptive examples.
- [ ] Correct prose and replace fast-changing lists with source links where practical.
- [ ] Run documentation drift/link checks.
- [ ] Commit as `docs: align agent guidance with current architecture`.

### 1.22. Task 22: Reconcile release and tag history (M19)

**Files:**
- Modify: `docs/CHANGELOG.md`, releasing documentation
- Test: new changelog/tag consistency test

- [ ] Add a failing test requiring every release heading to map to a release-style tag or be labeled a historical milestone.
- [ ] Relabel pre-tag 1.0.0-3.0.0 entries and document the v0.1.0 immutable-tag reset.
- [ ] Run docs checks and tag consistency tests.
- [ ] Commit as `docs: reconcile milestone and release history`.

### 1.23. Task 23: Documentation-policy cleanup (L3, L4, L5, L6)

**Files:**
- Modify: contributor/service guides, schema, validator catalog, docs index
- Test: manifest and documentation contract tests

- [ ] Add failing tests for Qdrant count disagreement, stale validator catalog, obsolete plan date range, and virtual manifests without docs.
- [ ] Correct the prose; generate the validator catalog from one registry; require docs or a validated exception for every manifest.
- [ ] Run manifest, schema, docs drift, and three-surface checks.
- [ ] Commit as `docs: close service authoring policy gaps`.

### 1.24. Task 24: Fail-fast Docker seed harness (L9)

**Files:**
- Modify: seed harness and legacy user-ID integration tests
- Test: seed harness lifecycle tests

- [ ] Add failing tests for a container that exits before readiness and for bounded log-tail reporting.
- [ ] Detect terminal container state, capture status/log tail, and stop polling immediately.
- [ ] Test Docker unavailable, startup timeout, early exit, and successful readiness.
- [ ] Commit as `test: fail fast when seed containers exit`.

### 1.25. Task 25: Repository cleanup and final branch validation (L7 plus closure)

**Files:**
- Modify only if needed: audit closure ledger/changelog

- [ ] Verify issue 973/974 branches have no unique unpublished work and no open PR consumers.
- [ ] Remove only confirmed obsolete worktrees and local/remote branches; fast-forward local `main` after branch work is safely pushed.
- [ ] Run full bootstrapper, Backend, MCP, asset, docs, Compose, lock, security, notebook, ShellCheck, and disposable-service suites.
- [ ] Run three independent full-branch adversarial reviews; fix and re-review every Critical/Important item and triage every Minor item.
- [ ] Push all commits and open the consolidated PR to `develop` with finding-to-commit and test-evidence tables.
- [ ] Share the PR URL and any residual external limitations.
