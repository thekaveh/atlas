# 2.3. Troubleshooting Guide

This guide covers common issues and their solutions when using Atlas.

Every copy block below works from a **fresh shell at the repository root** — no exported variables are assumed. `docker compose` commands resolve the project name and configuration from the checked-out `docker-compose.yml` plus your `.env` automatically, and the few authenticated examples read the needed value from `.env` inline without printing it. Examples use the default `BASE_PORT=63000` port block; if you started with a custom `--base-port`, get your stack's real endpoints from `./start.sh endpoints export --format env` instead of translating port numbers by hand.

## 1. .env Migration (LiteLLM rollout)

If you're upgrading from a pre-LiteLLM `.env` you may see startup errors about missing variables. Apply these changes:

- Rename `LLM_PROVIDER_PORT` to `LITELLM_PORT` (default is now `63040` under the current topology layout — the slot belongs to the LiteLLM gateway, not Ollama).
- Remove `OLLAMA_ENDPOINT` and any `OLLAMA_BASE_URL` lines — consumers now read `LITELLM_BASE_URL` and `LITELLM_API_KEY` (where `LITELLM_API_KEY=$LITELLM_MASTER_KEY`).
- If you previously set `LLM_PROVIDER_SOURCE=api` or `LLM_PROVIDER_SOURCE=disabled`, change it to `LLM_PROVIDER_SOURCE=none` and enable at least one of `CLOUD_OPENAI_SOURCE`, `CLOUD_ANTHROPIC_SOURCE`, `CLOUD_OPENROUTER_SOURCE`.

The simplest repair is `./start.sh env backfill` — it preserves every value you already have, appends newly introduced keys from `.env.example`, and reports what it changed. It never touches your data. (`./start.sh --cold` is **not** a configuration repair: it deletes every named project volume — databases, workflows, models — and exists only for an intentional full reset; see §10.)

## 2. Session Log

When `./start.sh` runs the Textual TUI, every line is tee'd to a timestamped file — both wizard-time diagnostic events (cloud `/v1/models` fetch failures, Ollama upstream discovery warnings, etc.) and the entire launch phase (build, port verification, `docker compose up`, per-service `logs --tail` on failure):

```
/tmp/atlas-launch-<YYYYMMDDTHHMMSS>-<unique>.log
```

The most recent log is always:

```bash
ls -t /tmp/atlas-launch-*.log | head -1
```

Inspect it after a failed launch — it captures everything the log pane showed, plus a few sources the pane filters out (e.g. cloud-fetch fallback warnings: `[warn/openai-fetch] live /v1/models returned 0 models — falling back to catalog (cause: HTTP 401)`). Session logs are bounded: 3 segments of 32 MiB per session (the first segment — session start and earliest diagnostics — is always kept; overflow rotates into numbered `.log.N` segments with truncation markers), and the 5 newest sessions are retained while older `atlas-launch-*` files are pruned at the next launch. Copy a log elsewhere if you need to keep it longer; exported copies are never touched by the pruning.

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
./start.sh --setup-hosts  # Configure hosts file

# Want to skip hosts setup?
./start.sh --skip-hosts   # Access via direct ports only
```

Truly starting over? `./stop.sh --cold && ./start.sh --cold` **deletes every named project volume** (databases, n8n workflows, downloaded models) before rebuilding — it is a full reset, not a fix. Back up first (§10.3) and see §10.1 before reaching for it.

### 3.4. Platform Issues
```bash
# Windows/WSL issues?
python3 bootstrapper/start.py --help  # Use Python directly

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

To reclaim disk from **this project only**, use the project-scoped reset (`./stop.sh --cold`, §10.1) — it removes only Atlas's own containers, network, and named volumes and leaves every other Compose project on the host untouched.

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
python3 bootstrapper/start.py --help

# Inspect the dynamic Kong configuration generator (kong.yml is rebuilt
# on every startup — don't edit by hand; instead trace the inputs):
cat bootstrapper/utils/kong_config_generator.py | head -80

# Inspect the KONG_* values the generator consumes
grep -E '^KONG_' .env

# Inspect the SOURCE values the stack was configured with
grep -E "(OLLAMA|COMFYUI|N8N|WEAVIATE|CLOUD|MINIO)[A-Z_]*_SOURCE" .env
```

### 8.3. Network Testing

```bash
# Test internal service connectivity (LLM goes through LiteLLM, not Ollama
# directly). Inside the Compose network, services resolve by service name:
docker compose exec backend curl -sf http://litellm:4000/health/liveliness
docker compose exec litellm curl -sf http://ollama:11434/api/tags
docker compose exec kong-api-gateway curl -sf http://supabase-api:3000/health

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

`./stop.sh --cold` stops the stack and **deletes every named Atlas project volume**: databases, n8n workflows, downloaded models, generated artifacts. There is no undo. Take a backup first (§10.3).

```bash
# Full project reset — removes THIS project's containers, network, and
# volumes; every other project on the host is left untouched
./stop.sh --cold

# Start fresh (also destructive: --cold clears any surviving volumes
# before rebuilding configuration and data from scratch)
./start.sh --cold --base-port 64000
```

### 10.2. Partial Reset

```bash
# Repair environment configuration WITHOUT touching data — preserves
# existing values, appends newly introduced keys, reports what changed:
./start.sh env backfill

# Reset specific service data (destructive for that service only).
# Volume names carry the PROJECT_NAME prefix from .env:
docker volume rm $(grep '^PROJECT_NAME=' .env | cut -d= -f2-)-supabase-db-data  # Database only
docker volume rm $(grep '^PROJECT_NAME=' .env | cut -d= -f2-)-n8n-data          # n8n workflows only
```

Rebuilding `.env` from `.env.example` is **not** a partial reset: freshly generated secrets no longer match the credentials baked into existing volumes, so a from-scratch `.env` requires the full destructive reset in §10.1 (which deletes those volumes and reinitializes both together).

### 10.3. Backup Before Reset

Do **not** copy a running database's data directory as a "backup" — a live PostgreSQL data dir copied file-by-file is torn mid-write and is not established as restorable. Use the stack's consistency-safe backup service instead: it captures a `pg_dump -Fc` Postgres dump, bounded offline Neo4j dumps, a native Weaviate snapshot, and a Supabase Storage archive, and pushes authenticated artifacts to the stack's S3 bucket.

```bash
# One-time prerequisites: the backup runner and MinIO must be enabled
# (BACKUP_SOURCE=container, MINIO_SOURCE=container in .env — or:)
./start.sh --backup-source container --detach

# Run a full consistency-safe backup (host entry point; quiesces Neo4j
# and writes a completion marker per timestamp)
services/backup/run-consistent-backup.sh
```

Coverage and limits: the backup captures the databases and Supabase Storage listed above — it does not capture `.env` (keep your own copy of it; it holds the keys that decrypt what the databases store) and Postgres and Storage are archived at slightly different instants. A backup is only proven by restoring it: before you rely on one — and before deleting anything — follow the restore procedure in [`services/backup/README.md`](../../services/backup/README.md) (`run-database-restore.sh` / `restore-postgres.sh`) on a disposable project. A guided restore rehearsal is tracked in [#1034](https://github.com/thekaveh/atlas/issues/1034).

Remember: Most issues can be resolved without losing data. Try targeted solutions before doing a complete reset!
