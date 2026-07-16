# Plan C — Backup / Restore Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cross-service backup tooling that dumps the Supabase Postgres database and snapshots the critical named volumes, pushes the artifacts to S3-compatible object storage (MinIO on-box, or any external S3), and can restore them — runnable on demand and on a schedule.

**Architecture:** A new `services/backup/` service holding shell scripts that run in a pinned `minio/mc` container (which already bundles `mc`; add `pg_dump`/`tar` via a tiny inline `apk` step, or use the `postgres` image for the DB dump and `mc` for upload). One-shot, opt-in (`BACKUP_SOURCE=disabled` by default). Scheduling is host cron (documented in the runbook) invoking `docker compose run --rm backup`, not an in-container scheduler.

**Tech Stack:** Docker Compose fragment + manifest; `pg_dump`/`psql` against `${PROJECT_NAME}-supabase-db`; `tar` for volume snapshots; `minio/mc` for S3 push; pytest init-script compile guards.

## 1. Global Constraints

- `main` protected — PR with 3 green checks; no direct push.
- Commits terse third-person, no emoji, no Claude trailer.
- `.env.example` generated from manifests; declare new vars in the manifest.
- Init/one-shot containers: vanilla image + inline `apk` (never a Dockerfile that clobbers a global tag). For `mc`, use the pinned `minio/mc:RELEASE.YYYY-MM-DD...` image (Alpine's `mc` is GNU Midnight Commander, NOT MinIO Client).
- Shell init scripts must pass `bash -n`; Python init scripts must `py_compile` AND be line-buffered (`sys.stdout.reconfigure(line_buffering=True)` or `flush=True` on every print). Enforced by `bootstrapper/tests/test_init_scripts_compile.py`.
- Postgres creds: `PGHOST=supabase-db`, `PGUSER=${SUPABASE_DB_USER}`, `PGPASSWORD=${SUPABASE_DB_PASSWORD}`, `PGDATABASE=${SUPABASE_DB_NAME}`. MinIO creds: `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`; endpoint `http://minio:9000`.

---

### 1.1. Task 1: Backup scripts (shell)

**Files:**
- Create: `services/backup/init/scripts/backup-all.sh`
- Create: `services/backup/init/scripts/restore-postgres.sh`
- Test: `bootstrapper/tests/test_init_scripts_compile.py` (auto-discovers the new `.sh`)

**Interfaces:**
- Produces: `backup-all.sh` (pg_dump + tar volumes + `mc cp` to `s3/<bucket>/<timestamp>/`), `restore-postgres.sh` (pull latest dump, `psql` restore). Env in: PG* + MINIO* + `BACKUP_BUCKET`, `BACKUP_S3_ALIAS_URL` (optional external S3), `BACKUP_TIMESTAMP` (passed in or computed).

- [ ] **Step 1: Write `backup-all.sh`**

```sh
#!/bin/sh
# Cross-service backup: Postgres dump + named-volume tarballs -> S3 (MinIO or external).
# One-shot; intended to be invoked via `docker compose run --rm backup`.
set -eu

: "${SUPABASE_DB_USER:?required}"; : "${SUPABASE_DB_PASSWORD:?required}"; : "${SUPABASE_DB_NAME:?required}"
: "${MINIO_ROOT_USER:?required}"; : "${MINIO_ROOT_PASSWORD:?required}"
BUCKET="${BACKUP_BUCKET:-atlas-backups}"
TS="${BACKUP_TIMESTAMP:-$(date +%Y%m%d_%H%M%S)}"
WORK=/tmp/backup
mkdir -p "$WORK"

echo "backup: pg_dump ${SUPABASE_DB_NAME}..."
PGPASSWORD="$SUPABASE_DB_PASSWORD" pg_dump -h supabase-db -U "$SUPABASE_DB_USER" -d "$SUPABASE_DB_NAME" -Fc -f "$WORK/postgres.dump"

echo "backup: snapshot mounted volumes..."
# Volumes to snapshot are bind-mounted read-only at /volumes/<name> by the fragment.
for d in /volumes/*; do
  [ -d "$d" ] || continue
  name="$(basename "$d")"
  tar czf "$WORK/${name}.tar.gz" -C "$d" .
  echo "backup: archived ${name}"
done

echo "backup: push to s3://${BUCKET}/${TS}/..."
mc alias set s3 "${BACKUP_S3_ALIAS_URL:-http://minio:9000}" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc mb --ignore-existing "s3/${BUCKET}"
mc cp --recursive "$WORK/" "s3/${BUCKET}/${TS}/"
echo "backup: done -> s3/${BUCKET}/${TS}/"
```

- [ ] **Step 2: Write `restore-postgres.sh`**

```sh
#!/bin/sh
# Restore the Postgres dump from a given (or latest) S3 backup timestamp.
set -eu
: "${SUPABASE_DB_USER:?required}"; : "${SUPABASE_DB_PASSWORD:?required}"; : "${SUPABASE_DB_NAME:?required}"
: "${MINIO_ROOT_USER:?required}"; : "${MINIO_ROOT_PASSWORD:?required}"
BUCKET="${BACKUP_BUCKET:-atlas-backups}"
mc alias set s3 "${BACKUP_S3_ALIAS_URL:-http://minio:9000}" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"

TS="${BACKUP_TIMESTAMP:-}"
if [ -z "$TS" ]; then
  TS="$(mc ls "s3/${BUCKET}/" | awk '{print $NF}' | tr -d / | sort | tail -1)"
fi
[ -n "$TS" ] || { echo "restore: no backups found in s3/${BUCKET}/" >&2; exit 1; }
echo "restore: using backup ${TS}"
mkdir -p /tmp/restore
mc cp "s3/${BUCKET}/${TS}/postgres.dump" /tmp/restore/postgres.dump
echo "restore: pg_restore into ${SUPABASE_DB_NAME} (clean)..."
PGPASSWORD="$SUPABASE_DB_PASSWORD" pg_restore -h supabase-db -U "$SUPABASE_DB_USER" -d "$SUPABASE_DB_NAME" --clean --if-exists /tmp/restore/postgres.dump
echo "restore: done"
```

- [ ] **Step 3: Run the init-script parse guard**

Run: `cd bootstrapper && uv run pytest tests/test_init_scripts_compile.py -q`
Expected: PASS (`bash -n` clean on both scripts). If `bash` isn't on PATH the shell tests skip — also run `bash -n services/backup/init/scripts/backup-all.sh services/backup/init/scripts/restore-postgres.sh` manually.

- [ ] **Step 4: Commit**

```bash
git add services/backup/init/scripts/backup-all.sh services/backup/init/scripts/restore-postgres.sh
git commit -m "Add backup/restore shell scripts"
```

---

### 1.2. Task 2: Manifest + compose fragment

**Files:**
- Create: `services/backup/service.yml`, `services/backup/compose.yml`
- Modify: `docker-compose.yml` (include line)
- Test: manifest + fragment-equivalence

**Interfaces:**
- Produces: `backup` container (one-shot), `BACKUP_SOURCE` (default `disabled`), `BACKUP_BUCKET`, `BACKUP_S3_ALIAS_URL`, `BACKUP_SCALE` (auto-managed; 0 by default so it never auto-runs); uses the `pg_dump`-capable image + `mc`.

- [ ] **Step 1: Manifest** (`services/backup/service.yml`) — mirror the Redis manifest shape:
```yaml
name: backup
label: "Backup / restore (Postgres + volumes -> S3)"
category: infra
docs: services/backup/README.md

containers:
  - backup

images:
  - var: BACKUP_IMAGE
    default: "postgres:15-alpine"
    container: backup
    notes: "Provides pg_dump/pg_restore; mc is installed inline via apk in the entrypoint, or swap to minio/mc and add postgres-client."

sources:
  backup:
    var: BACKUP_SOURCE
    default: disabled
    options:
      - id: container
        description: "Enable the backup runner (invoke via docker compose run --rm backup)."
      - id: disabled
        description: "No backup service (default)."

env:
  - name: BACKUP_BUCKET
    default: "atlas-backups"
    description: "Target bucket for backups."
  - name: BACKUP_S3_ALIAS_URL
    default: "http://minio:9000"
    description: "S3 endpoint. Default = on-box MinIO; set to an external S3 URL for offsite."
  - name: BACKUP_SCALE
    auto_managed: true
    description: "0 (never long-running); the runner is invoked on demand via `docker compose run`."

depends_on:
  required:
    - supabase
    - minio
  optional: []

rows:
  - display_name: "Backup / restore"
    source_var: BACKUP_SOURCE
    description: "On-demand Postgres dump + volume snapshot to S3."

exports: []

runtime_sc:
  backup:
    container:
      scale: 0
      environment: {}
      deploy: {}
      extra_hosts: []
    disabled:
      scale: 0
      environment: {}
      deploy: {}
      extra_hosts: []

data_flow:
  calls:
    - supabase
    - minio
```

- [ ] **Step 2: Fragment** (`services/backup/compose.yml`):
```yaml
# On-demand backup runner. Never long-running (replicas 0); invoke with:
#   docker compose run --rm backup /scripts/backup-all.sh
services:
  backup:
    image: ${BACKUP_IMAGE:-postgres:15-alpine}
    container_name: ${PROJECT_NAME}-backup
    restart: "no"
    deploy:
      replicas: ${BACKUP_SCALE:-0}
    entrypoint: ["sh", "-c"]
    command: ["apk add --no-cache minio-client >/dev/null 2>&1 || true; /scripts/backup-all.sh"]
    environment:
      SUPABASE_DB_USER: ${SUPABASE_DB_USER}
      SUPABASE_DB_PASSWORD: ${SUPABASE_DB_PASSWORD}
      SUPABASE_DB_NAME: ${SUPABASE_DB_NAME}
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
      BACKUP_BUCKET: ${BACKUP_BUCKET}
      BACKUP_S3_ALIAS_URL: ${BACKUP_S3_ALIAS_URL}
    volumes:
      - ./init/scripts:/scripts:ro
      - ${PROJECT_NAME}-supabase-storage-data:/volumes/supabase-storage:ro
      - ${PROJECT_NAME}-graph-db-data:/volumes/graph-db:ro
      - ${PROJECT_NAME}-weaviate-data:/volumes/weaviate:ro
    networks:
      - backend-network
```
Note: `apk add minio-client` on the Alpine `postgres` image provides `mc` (Alpine's package IS the MinIO client on recent Alpine; verify on the pinned image — if it resolves to Midnight Commander, instead mount from the `minio/mc` image via a second container, or bake a tiny build context). Validate in Step 4.

- [ ] **Step 3: Include line** in `docker-compose.yml` (after the data tier):
```yaml
  - services/backup/compose.yml
```

- [ ] **Step 4: Validate the image actually has `mc`**

Run: `docker run --rm postgres:15-alpine sh -c "apk add --no-cache minio-client >/dev/null 2>&1 && mc --version"`
Expected: prints a MinIO Client version (not Midnight Commander). If it's the wrong `mc`, switch the fragment to a two-step approach: a `minio/mc` container for upload + a `postgres` container for the dump, sharing a volume. Adjust the scripts/fragment accordingly before continuing.

- [ ] **Step 5: Regenerate + test**

```bash
cd bootstrapper && uv run python -m services.env_assembler
PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen backup
cd /Users/kaveh/repos/genai-vanilla && docker compose -f docker-compose.yml config -q
cd bootstrapper && uv run pytest -q
```
Expected: PASS (regen the fragment baseline if `test_fragment_equivalence` flags the new replicas-0 service, as in Plan A Task 3).

- [ ] **Step 6: Commit**

```bash
git add services/backup/ docker-compose.yml .env.example bootstrapper/tests/fixtures/rendered_config_baseline.yml
git commit -m "Add backup service (manifest + fragment + docs)"
```

---

### 1.3. Task 3: Live smoke test + runbook scheduling note

**Files:**
- Modify: `services/backup/README.md` (usage), `docs/deployment/` runbook (cron)

- [ ] **Step 1: Smoke-test against a running stack** (manual; requires the stack up with `BACKUP_SOURCE=container`):
```bash
docker compose run --rm backup /scripts/backup-all.sh
# verify objects landed:
docker compose run --rm backup sh -c 'mc alias set s3 http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" && mc ls --recursive s3/atlas-backups/'
```
Expected: a `postgres.dump` + volume tarballs under a timestamp prefix.

- [ ] **Step 2: Restore drill** (against a scratch DB or staging):
```bash
docker compose run --rm -e BACKUP_TIMESTAMP=<ts> backup /scripts/restore-postgres.sh
```
Confirm row counts / a known record post-restore.

- [ ] **Step 3: Document host-cron scheduling in the runbook** (e.g. daily):
```cron
15 3 * * * cd /opt/atlas && docker compose run --rm backup /scripts/backup-all.sh >> /var/log/atlas-backup.log 2>&1
```
And document offsite: set `BACKUP_S3_ALIAS_URL` + creds to an external S3 (Backblaze B2 / Wasabi / S3) instead of on-box MinIO so a host loss doesn't take the backups with it.

- [ ] **Step 4: Commit**

```bash
git add services/backup/README.md docs/deployment/
git commit -m "docs: backup usage, restore drill, cron + offsite"
```

---

## 2. Self-Review

- **Spec coverage:** Implements P0-6 (backups + offsite + schedule + a restore drill). Neo4j already has its own `backup.sh`; this complements it for Postgres + the other volumes (the plan snapshots `graph-db-data` raw too, which is a belt-and-suspenders volume copy).
- **Placeholders:** none — both scripts are complete and runnable; the one genuine validation gate (does `apk add minio-client` give the real `mc`?) is an explicit Step with a fallback, not a placeholder.
- **Type consistency:** env var names (`BACKUP_BUCKET`, `BACKUP_S3_ALIAS_URL`, `SUPABASE_DB_*`, `MINIO_ROOT_*`) identical across manifest, fragment, and scripts. Volume names use the `${PROJECT_NAME}-...` convention.
- **Offsite caveat surfaced:** default target is on-box MinIO (convenient but co-located); the runbook step explicitly directs setting an external S3 for real DR.
