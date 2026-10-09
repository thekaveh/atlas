# 2.3. Troubleshooting Guide

This guide covers common issues and their solutions when using Atlas.

Every copy block below works from a shell at the repository root after one setup line. Plain `docker compose` names the project after the checkout folder (or `COMPOSE_PROJECT_NAME`), not after Atlas's `PROJECT_NAME`, so run this once per shell; without it the commands only find the stack when the folder is named like the project (the default `atlas` checkout with `PROJECT_NAME=atlas`), never in a submodule checkout such as `infra/` or after `--project`:

```bash
export COMPOSE_PROJECT_NAME="$(sed -n 's/^PROJECT_NAME=["'\'']\{0,1\}\([A-Za-z0-9_-]*\).*/\1/p' .env | tail -n1)"
```

Configuration then comes from the checked-out `docker-compose.yml` plus your `.env`, and the few authenticated examples read the needed value from `.env` inline without printing it. Examples use the default `BASE_PORT=63000` port block; if you started with a custom `--base-port`, get your stack's real endpoints from `./start.sh endpoints export --format env` instead of translating port numbers by hand.

## 1. .env Migration (LiteLLM rollout)

If you're upgrading from a pre-LiteLLM `.env` you may see startup errors about missing variables. Apply these changes:

- Rename `LLM_PROVIDER_PORT` to `LITELLM_PORT` (default is now `63040` under the current topology layout — the slot belongs to the LiteLLM gateway, not Ollama).
- Remove `OLLAMA_ENDPOINT` and any `OLLAMA_BASE_URL` lines — consumers now read `LITELLM_BASE_URL` and `LITELLM_API_KEY` (where `LITELLM_API_KEY=$LITELLM_MASTER_KEY`).
- If you previously set `LLM_PROVIDER_SOURCE=api` or `LLM_PROVIDER_SOURCE=disabled`, change it to `LLM_PROVIDER_SOURCE=none` and enable at least one of `CLOUD_OPENAI_SOURCE`, `CLOUD_ANTHROPIC_SOURCE`, `CLOUD_OPENROUTER_SOURCE`.

The simplest repair is `./start.sh env backfill` — it preserves every value you already have, appends newly introduced keys from `.env.example`, and reports what it changed. It never touches your data. (`./start.sh --cold` is **not** a configuration repair: it deletes every named project volume — databases, workflows, models — and exists only for an intentional full reset; see §10.)

## 2. Session Log

When `./start.sh` runs the Textual TUI, every line is tee'd to a timestamped file — both wizard-time diagnostic events (cloud `/v1/models` fetch failures, Ollama upstream discovery warnings, etc.) and the entire launch phase (build, port verification, `docker compose up`, per-service `logs --tail` on failure):

```
${TMPDIR:-/tmp}/atlas-launch-<YYYYMMDDTHHMMSS>-<unique>.log
```

The most recent log is always:

```bash
ls -t "${TMPDIR:-/tmp}"/atlas-launch-*.log | head -1   # macOS: TMPDIR is under /var/folders
```

Inspect it after a failed launch — it captures everything the log pane showed, plus a few sources the pane filters out (e.g. cloud-fetch fallback warnings: `[warn/openai-fetch] live /v1/models returned 0 models — falling back to catalog (cause: HTTP 401)`). Session logs are bounded: 3 segments of 32 MiB per session (the first segment — session start and earliest diagnostics — is always kept; overflow rotates into numbered `.log.N` segments with truncation markers), and the 5 newest sessions are retained while older `atlas-launch-*` files are pruned at the next launch. Copy a log elsewhere if you need to keep it longer; exported copies are never touched by the pruning. Every segment is created owner-only (`0600`) and never through an existing file or symlink. A failed launch step's reason (port conflicts, auto-disabled dependencies, key or config errors) is written to the log pane and this file, not only the generic `<step> failed` line.

## 3. Quick Fixes

### 3.1. Port Conflicts
```bash
# Error: "bind: address already in use"
./start.sh --base-port 64000  # Use different port range

# Find what's using the port
lsof -i :63096

# Kill process using the port (if safe)
kill -9 $(lsof -t -i:63096)
```

The start-up port check only probes ports a container will actually publish: services set to `disabled`, the LLM provider's cloud-only `none` (Ollama not run) and host-run `localhost` variants are skipped, and a port held only by a closing connection (`TIME_WAIT`) is not treated as a conflict, since Docker can bind it anyway.

### 3.2. Memory Issues
```bash
# Error: Containers crashing with exit code 137 (OOM kill)
# Solution: Increase Docker memory allocation

# Docker Desktop: Settings → Resources → Memory (set to 10-12GB)
# Colima users:
colima stop
colima start --memory 12 --cpu 6
```

### 3.3. Access Issues
```bash
# Can't access *.localhost URLs?
./start.sh --setup-hosts  # Configure hosts file, then start the stack (no wizard)

# Want to skip hosts setup?
./start.sh --skip-hosts   # Access via direct ports only
```

Truly starting over? `./stop.sh --cold && ./start.sh --cold` **deletes every named project volume** (databases, n8n workflows, downloaded models) before rebuilding — it is a full reset, not a fix. Back up first (§10.3) and see §10.1 before reaching for it.

### 3.4. Platform Issues
```bash
# Windows/WSL issues?
uv run --project bootstrapper python bootstrapper/start.py --help  # bypasses the sh wrapper

# Shell script permissions?
chmod +x start.sh stop.sh
```

## 4. Service-Specific Issues

### 4.1. LLM Issues (LiteLLM gateway + Ollama upstream)

**LiteLLM not responding / consumers can't reach LLMs:**
```bash
# Liveness check (no auth required)
curl http://localhost:63040/health/liveliness

# List registered models (reads the key from .env without printing it)
curl -H "Authorization: Bearer $(grep '^LITELLM_MASTER_KEY=' .env | cut -d= -f2-)" http://localhost:63040/v1/models

# Inspect LiteLLM logs
docker compose logs --tail=100 -f litellm
```

**Ollama models not downloading:**
```bash
# Check the ollama-pull init container
docker compose logs --tail=100 -f ollama-pull

# Or the Ollama container itself
docker compose logs --tail=100 -f ollama

# For localhost setup, pre-download on the host:
ollama serve &
ollama pull qwen3.8:latest
ollama pull qwen3-embedding:0.6b
```

Reminder: Ollama no longer has a host port mapping. Reach it via LiteLLM (`http://localhost:63040/v1`) or via `docker compose exec ollama` for direct `/api/*` calls.

**Out of memory during model loading:**
```bash
# Use a localhost Ollama upstream to free up Docker memory
./start.sh --llm-provider-source ollama-localhost
ollama pull qwen3:1.7b  # Smaller model
```

### 4.2. ComfyUI Issues

**Models downloading slowly or missing:**
```bash
# The bootstrapper resolves COMFYUI_USER_MODELS at start and writes
# volumes/comfyui/active-models.tsv. comfyui-init downloads each entry
# via wget. If models you picked never show up, check these logs:
docker compose logs --tail=100 -f comfyui-init

# Verify the manifest was written at start (check volumes/comfyui/).
# The manifests there are gitignored runtime artifacts — regenerated
# on every non-disabled start, so a start never dirties the checkout:
ls volumes/comfyui/

# Check ComfyUI service status
docker compose logs --tail=100 -f comfyui
```

**Can't access ComfyUI interface:**
```bash
# Check if hosts are configured
./start.sh --setup-hosts

# Access via direct URL
curl http://localhost:63054  # Direct port access (COMFYUI_PORT)
```

### 4.3. n8n Issues

**n8n not accessible:**
```bash
# Check n8n service status
docker compose logs --tail=100 -f n8n

# Try direct access
curl http://localhost:63075

# Check Kong routing
curl -H "Host: n8n.localhost" http://localhost:63000/
```

**Workflow execution fails:**
```bash
# Check n8n worker logs
docker compose logs --tail=100 -f n8n-worker

# Check Redis connection
docker compose logs --tail=100 -f redis
```

### 4.4. Database Issues

**Supabase services not starting:**
```bash
# Check individual service logs
docker compose logs --tail=100 -f supabase-db
docker compose logs --tail=100 -f supabase-auth
docker compose logs --tail=100 -f supabase-api

# Check if database initialization completed
docker compose logs --tail=100 supabase-db-init
```

**Database connection errors:**
```bash
# Verify the server accepts connections (liveness only — proves nothing
# about credentials or a specific database)
docker compose exec supabase-db pg_isready

# Run a real query through the Backend's own configured connection.
# This proves the backend's credentials authenticate against supabase-db
# and a read-only query returns — nothing more (not migrations, not app
# routes). Bounded to 5 seconds per step; never prints the DSN.
docker compose exec backend python -c "
import asyncio, os, asyncpg
async def main():
    conn = await asyncio.wait_for(asyncpg.connect(os.environ['DATABASE_URL']), 5)
    assert await asyncio.wait_for(conn.fetchval('SELECT 1'), 5) == 1
    await conn.close()
    print('database query succeeded')
asyncio.run(main())"
```

**`password authentication failed for user "supabase_admin"`:** the `supabase_admin` role password is baked into the `supabase-db-data` volume **once**, at first init, and is never re-synced. `SUPABASE_DB_PASSWORD` ships as the placeholder `password` and auto-rotates to a random value on the first `./start.sh`. If the volume later persists across a `.env` password change (e.g. `.env` regenerated from `.env.example` while an old volume is still around — `./stop.sh` without `--cold` keeps volumes), every client authenticates with the new value while the role still holds the old → this error. The bootstrapper now **skips** rotation and warns when it detects an existing `${PROJECT_NAME}-supabase-db-data` volume, so it won't silently drift `.env`. To recover:
```bash
# Option A — start fresh (DELETES this project's volumes, then
#            reinitializes role + .env together)
./stop.sh --cold && ./start.sh
# Option B — keep your data: set SUPABASE_DB_PASSWORD in .env back to the
#            value the volume was created with, then restart.
```
See `services/supabase/README.md` §2.1 for the full explanation.

### 4.5. Kong Gateway Issues

**404 errors for services:**
```bash
# Kong config is dynamically generated at startup — inspect the generator
# (and the KONG_* env vars it consumes) rather than the emitted file:
cat bootstrapper/utils/kong_config_generator.py

# Verify Kong is running
docker compose logs --tail=100 -f kong-api-gateway

# Test Kong routing end-to-end (proxies SearXNG's /healthz through Kong)
curl -H 'Host: search.localhost' http://localhost:63000/healthz
```

**Service routing not working:**
```bash
# Check if service is enabled in configuration
grep -i "COMFYUI_SOURCE" .env
grep -i "N8N_SOURCE" .env

# Verify service is running
docker compose ps | grep -E "(comfyui|n8n)"
```

## 5. Resource Issues

### 5.1. Docker Resource Monitoring

```bash
# Check overall resource usage
docker stats

# Check disk usage (read-only; -v breaks usage down per volume/image)
docker system df
docker system df -v
```

To see what this project stores, run `./start.sh storage inventory` (#1194). It reports:

- each volume named for the project, with its size from `docker system df -v`. A volume no Atlas compose fragment declares (a leftover, or another project whose name starts the same way) is marked `unknown`.
- the size of the declared host model directories (`COMFYUI_LOCAL_MODELS_PATH`, `COMFYUI_MPS_MODELS_PATH`).
- while the stack runs, the models in the Atlas Ollama container (`ollama list`) and the files in the ComfyUI models volume, each labelled as below.

| Label | Meaning |
|---|---|
| `retained` | In use, so never removed. An Ollama model is retained when `OLLAMA_USER_MODELS` or `OLLAMA_CUSTOM_MODELS` selects it, when `LITELLM_DEFAULT_MODEL`, `LITELLM_VISION_MODEL`, `LITELLM_EMBEDDING_MODEL` or `LANGMEM_EMBEDDING_MODEL` names it as `ollama/<name>` or `ollama_chat/<name>`, or when the rendered `volumes/litellm/config.yaml` routes it. A ComfyUI file is retained when `volumes/comfyui/active-models.tsv` lists it. |
| `removable` | A catalog model that nothing selects or routes. |
| `unknown` | Not in the catalog. Removed only when you name it. |

Ollama sizes count blobs shared between models once per model, so they can add up to more than the volume holds.

To free model disk without a reset, run `./start.sh storage clean`:

- It removes `removable` items, plus the `unknown` items you name with `--name`, one at a time. It never removes a `retained` item.
- It prints each item and the total bytes, then asks before it removes anything. `--yes` skips the question.
- It removes single items through `docker exec` into the Atlas Ollama and ComfyUI containers. It never removes a volume, so no database, workflow or other stateful volume can be part of a cleanup.
- It never touches a host Ollama daemon or a host ComfyUI directory. With `LLM_PROVIDER_SOURCE` not `ollama-container-*`, or `COMFYUI_SOURCE` not `container-*`, that service's models are not itemized. ComfyUI files are not itemized until a start has written `volumes/comfyui/active-models.tsv`.
- If a removal fails, the cleanup stops, exits 1 and leaves every remaining item as it was. Run `storage inventory` again to see the true state.

To reclaim all disk from **this project only**, use the project-scoped reset (`./stop.sh --cold`, §10.1) — it removes only Atlas's own containers, network, and named volumes and leaves every other Compose project on the host untouched.

Daemon-wide cleanup (`docker system prune`, `docker volume prune`) is deliberately **not** part of Atlas recovery: those commands operate on every project on the host and can delete other applications' stopped containers, images, build caches, and unused volumes. If your host needs that kind of housekeeping, treat it as a separate operator task — inspect what would be affected with `docker system df -v` first, and run it only when you can account for everything it will remove.

### 5.2. Memory Optimization

```bash
# Disable memory-heavy services
./start.sh --n8n-source disabled --weaviate-source disabled --minio-source disabled

# Use localhost services to reduce container overhead
./start.sh --llm-provider-source ollama-localhost --comfyui-source localhost
```

## 6. Network Issues

### 6.1. DNS Resolution

```bash
# Check hosts file entries
cat /etc/hosts | grep localhost

# Manually add entries if needed
echo "127.0.0.1 n8n.localhost comfyui.localhost search.localhost api.localhost chat.localhost" | sudo tee -a /etc/hosts
```

### 6.2. Firewall Issues

```bash
# Check if ports are accessible
telnet localhost 63096
nc -zv localhost 63096

# For localhost services, check host firewall
sudo ufw status  # Ubuntu/Debian
```

## 7. Startup Issues

### 7.1. Service Dependencies

```bash
# Some services depend on others - check startup order
docker compose ps

# If services are failing, check dependency services first
docker compose logs --tail=100 -f redis        # Many services need Redis
docker compose logs --tail=100 -f supabase-db  # Backend needs database
```

If Redis restart-loops with `Bad file format reading the append only file`, its append-only file is corrupt and nothing that depends on Redis will start. Run `./start.sh doctor`: the `redis-aof` check confirms it and prints the backup-first repair, documented in [Redis troubleshooting](../../services/redis/README.md#7-troubleshooting).

### 7.2. Environment Issues

```bash
# Check if .env file exists and is valid
ls -la .env
cat .env | head -20

# Missing or blank variables after an upgrade? Backfill nondestructively —
# existing values are preserved, new keys are appended, data is untouched:
./start.sh env backfill
```

If `.env` is corrupted beyond repair, rebuilding it from scratch is a **destructive** path: regenerated secrets no longer match the passwords baked into your existing database volumes (see §4.4), so a from-scratch `.env` only works together with a full project reset that deletes those volumes. Back up first (§10.3), then follow §10.1.

**A service rejects its password or encryption key after an upgrade.** Atlas now writes a value from `.env.user`, `ATLAS_ENV_USER_FILE`, a consumer manifest's `env.values` or a wizard API key in single quotes when it contains a backslash or a bare `$`, because Docker Compose expands those in unquoted values. Older releases wrote such a value unquoted, so Compose passed a truncated secret (`ab$cd` reached containers as `ab`), and a service that stored it at first boot (an n8n encryption key, a database password) still holds the truncated form. Either set the value to what the service actually stored, or rotate it in the service. Generated secrets never contain `$` or a backslash and are unaffected.

### 7.3. An image fails to build

Atlas builds local images before `docker compose up` in two cases. A cold start (`./start.sh --cold`, or the wizard's cold-start option) builds every enabled service's image without cache. A normal start builds only when the images are stale: on a fresh clone's first start, after the Atlas source or a build setting changed, or when the set of enabled services changed. A normal start whose images are current builds nothing and runs a single `docker compose up`, as before.

Either build is one `docker compose build` of every enabled service. When it fails, Atlas works out which image failed before deciding whether to stop. It rebuilds the required images together: the ones another enabled service lists in `depends_on`, and those of the always-running core (Supabase, Kong, Redis, LiteLLM and Backend). It then rebuilds every other image on its own. A cold start keeps `--no-cache` for this pass, so it can take about as long as the first build; a normal start reuses the build cache.

- **A required image fails:** the launch stops as before with `Failed to build some services`, managed host processes (for example a host ComfyUI) are rolled back, and `./start.sh` exits nonzero.
- **Only other images fail** (the `jupyterhub` notebook image, for example): Atlas names them, leaves them out of `docker compose up`, starts everything else and keeps managed host processes running. The launch result reads `degraded` and names each one, the way a failed post-start check does, so the start itself still succeeds. Atlas does not record the images as built, so the next start tries the failed build again until it is fixed or the service is disabled:

  ```bash
  # Skip the service until its build is fixed (or set JUPYTERHUB_SOURCE=disabled in .env)
  ./start.sh --jupyterhub-source disabled
  ```

- **Every image builds on its own:** the first failure was transient and the whole stack starts.

**`At least one invalid signature was encountered` while building `jupyterhub`.** The notebook image runs `apt-get update` against the Ubuntu mirror (`ports.ubuntu.com` on arm64 hosts such as Apple Silicon) for the JDK its Scala kernel needs, so this step cannot be skipped. The error commonly means the Docker VM is out of disk space or its clock is wrong, not that the package list is bad. Check the space Docker reports, free some or raise Docker Desktop's disk limit, then build again:

```bash
docker system df
```

## 8. Debug Commands

### 8.1. System Status Check

```bash
# Overall system health
docker compose ps

# Service logs (most recent)
docker compose logs --tail=50

# Specific service investigation
docker compose logs --tail=100 -f ollama
docker compose logs --tail=100 -f backend
```

### 8.2. Configuration Verification

```bash
# Inspect the SOURCE values currently written to .env
grep -E '^[A-Z_]+_SOURCE=' .env

# List all available CLI flags (Click-generated help is the source of truth)
./start.sh --help

# Inspect the dynamic Kong configuration generator (kong.yml is rebuilt
# on every startup — don't edit by hand; instead trace the inputs):
cat bootstrapper/utils/kong_config_generator.py | head -80

# Inspect the KONG_* values the generator consumes
grep -E '^KONG_' .env

# Inspect the SOURCE values the stack was configured with
grep -E "(LLM_PROVIDER|COMFYUI|N8N|WEAVIATE|CLOUD|MINIO)[A-Z_]*_SOURCE" .env
```

### 8.3. Network Testing

```bash
# Test internal service connectivity (LLM goes through LiteLLM, not Ollama
# directly). Inside the Compose network, services resolve by service name:
docker compose exec backend curl -sf http://litellm:4000/health/liveliness
docker compose exec litellm python -c "import urllib.request; print(urllib.request.urlopen('http://ollama:11434/api/tags', timeout=5).status)"   # the LiteLLM image has no curl
docker compose exec backend curl -sf http://supabase-api:3000/   # PostgREST answers its OpenAPI root; /health is not a route (the Kong image has no curl)

# Test external access
curl http://localhost:63096
curl -H "Host: n8n.localhost" http://localhost:63000/
```

## 9. Getting Help

### 9.1. Log Collection

When reporting issues, include:

```bash
# System information
docker --version
docker compose version
python3 --version

# Service status
docker compose ps > service_status.txt

# Recent logs
docker compose logs --tail=100 > stack_logs.txt

# Configuration
cp .env .env.backup.support  # matches .gitignore's .env.backup.* — redact secrets before sharing
```

### 9.2. Common Support Information

1. **Platform**: macOS/Linux/Windows + version
2. **Docker memory allocation**: Settings → Resources in Docker Desktop
3. **Services enabled**: Which SOURCE values you're using
4. **Error messages**: Exact error text and which service
5. **Steps to reproduce**: What you did before the error occurred

### 9.3. Community Resources

- [GitHub Issues](https://github.com/thekaveh/atlas/issues) - Bug reports and feature requests
- [Ask a question](https://github.com/thekaveh/atlas/issues/new?labels=question) - Open an issue with the `question` label; Discussions is not enabled on this repository
- [Security policy](../../SECURITY.md) - Report security-sensitive findings privately, never as a public issue
- [Documentation](https://github.com/thekaveh/atlas/blob/main/docs/README.md) - Complete documentation index

## 10. Recovery Procedures

Atlas recovery is **project-scoped by design**: everything Atlas creates — containers, the network, named volumes — belongs to this project (names carry your `PROJECT_NAME` prefix, `atlas` by default), and the commands below remove only that. Other Compose projects on the same host, their volumes, images, and caches are never touched.

### 10.1. Complete Reset (destructive — deletes this project's data)

`./stop.sh --cold` stops the stack and **deletes every named Atlas project volume**: databases, n8n workflows, downloaded models, generated artifacts. There is no undo.

- Stop a stack started with `--consumer <manifest>` with the same `--consumer`. Otherwise the volumes its compose overlays declare are outside the base model: the cold stop names them and exits non-zero instead of reporting a full wipe. `./start.sh --cold` checks the same way and stops before it rotates any secret.
- Take a backup first (§10.3). With the default `BACKUP_S3_MODE=local` the backup lives in this project's MinIO volume (and the Neo4j and Weaviate snapshot volumes), so `--cold` deletes it too. Use `BACKUP_S3_MODE=external`, or copy the bucket off the host before the reset.
- `./start.sh --cold` rebuilds `.env` from `.env.example`. It saves the previous file next to it as `.env.backup.cold.<YYYYmmddTHHMMSS>.<random>` (owner-only; the five most recent cold copies are kept, separately from the routine `.env.backup.*` copies).
- Keep your own copy of `BACKUP_MANIFEST_HMAC_KEY` and `BACKUP_DEPLOYMENT_ID`: a backup cannot be restored without them.

```bash
# Full project reset — removes THIS project's containers, network, and
# volumes; every other project on the host is left untouched
./stop.sh --cold

# Start fresh (also destructive: --cold clears any surviving volumes
# before rebuilding configuration and data from scratch)
./start.sh --cold
```

### 10.2. Partial Reset

```bash
# Repair environment configuration WITHOUT touching data — preserves
# existing values, appends newly introduced keys, reports what changed:
./start.sh env backfill

# Reset one volume's data (destructive). Stop the stack first: Docker
# refuses to remove a volume a container (even a stopped one) still uses.
# Volume names carry the PROJECT_NAME prefix from .env:
./stop.sh
docker volume rm $(grep '^PROJECT_NAME=' .env | cut -d= -f2-)-supabase-db-data  # the shared Postgres: every service database
docker volume rm $(grep '^PROJECT_NAME=' .env | cut -d= -f2-)-n8n-data          # n8n's user folder only
```

`supabase-db-data` is not one service's data: the same Postgres holds the Supabase, backend (memory, media ledger), n8n, Open WebUI, LiteLLM, LightRAG, Airflow, Langfuse, MLflow, Label Studio, Iceberg, JupyterHub, Zeppelin and TrueForge databases, so removing it resets all of them. n8n stores its workflows and credentials in that Postgres (`DB_TYPE=postgresdb`), not in `n8n-data`, which holds only its user folder (`/home/node/.n8n`).

Rebuilding `.env` from `.env.example` is **not** a partial reset: freshly generated secrets no longer match the credentials baked into existing volumes, so a from-scratch `.env` requires the full destructive reset in §10.1 (which deletes those volumes and reinitializes both together).

### 10.3. Backup Before Reset

Do **not** copy a running database's data directory as a "backup" — a live PostgreSQL data dir copied file-by-file is torn mid-write and is not established as restorable. Use the stack's consistency-safe backup service instead: it captures a `pg_dump -Fc` Postgres dump, bounded offline Neo4j dumps, a native Weaviate snapshot, and a Supabase Storage archive, and pushes authenticated artifacts to the stack's S3 bucket.

```bash
# One-time prerequisites: the backup runner and MinIO must be enabled, and
# the manifest signing key and deployment id must be set in .env (Atlas does
# not generate them; keep a copy outside the bucket, a restore needs them).
# Run `openssl rand -hex 32` and paste its output into the
# BACKUP_MANIFEST_HMAC_KEY= line (present after the first ./start.sh; add it
# otherwise; .env does not run commands), and set
# BACKUP_DEPLOYMENT_ID= to a stable name (letters, digits, . _ -; max 128).
./start.sh --backup-source container --minio-source container --detach

# Run a full consistency-safe backup (host entry point; quiesces Neo4j
# and writes a completion marker per timestamp)
services/backup/run-consistent-backup.sh
```

Coverage and limits: the backup captures the main Supabase database (`SUPABASE_DB_NAME`), Neo4j, Weaviate and Supabase Storage listed above. The per-service databases on the same Postgres server (LiteLLM, Airflow, Langfuse, MLflow, Label Studio, the Iceberg catalog, Supavisor and similar) are **not** dumped, so their keys, spend, traces, registries and metadata are lost by a reset. Local-mode artifacts live in this project's MinIO volume and do not survive `./stop.sh --cold` (§10.1). The backup does not capture `.env` (keep your own copy of it; it holds the keys that decrypt what the databases store) and Postgres and Storage are archived at slightly different instants. The Storage archive has no restore procedure yet (it is not in the signed manifest), so after a Postgres restore `storage.objects` rows may not match the files on disk. A backup is only proven by restoring it: before you rely on one — and before deleting anything — follow the restore procedure in [`services/backup/README.md`](../../services/backup/README.md) (`run-database-restore.sh` / `restore-postgres.sh`) on a disposable project. A guided restore rehearsal is tracked in [#1034](https://github.com/thekaveh/atlas/issues/1034).

Remember: Most issues can be resolved without losing data. Try targeted solutions before doing a complete reset!
