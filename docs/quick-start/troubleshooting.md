# 2.3. Quick Start Troubleshooting

This page lists common Atlas problems and their fixes. For a launch or stop that ran under `sudo`, see [Sudo Recovery](../TROUBLESHOOTING.md).

Run these commands from the repository root. First, set the Compose project name once per shell. Plain `docker compose` uses the checkout folder name (or `COMPOSE_PROJECT_NAME`), not Atlas's `PROJECT_NAME`. Without this line, the commands miss the stack in a submodule checkout (such as `infra/`) or after `--project`:

```bash
export COMPOSE_PROJECT_NAME="$(sed -n 's/^PROJECT_NAME=["'\'']\{0,1\}\([A-Za-z0-9_-]*\).*/\1/p' .env | tail -n1)"
```

The commands then use the checked-out `docker-compose.yml` and your `.env`. Authenticated examples read values from `.env` without printing them. Ports assume the default `BASE_PORT=63000`. For a custom base port, get your endpoints from `./start.sh endpoints export --format env`.

## 1. Missing or renamed variables after an upgrade

Run `./start.sh env backfill`. It keeps every existing value, appends new keys from `.env.example` and reports what changed. It never touches data. `./start.sh --cold` is **not** a configuration repair. It is for an intentional full reset only (§10).

## 2. Session Log

When `./start.sh` runs the Textual TUI, it copies every line to a timestamped file. The file holds wizard diagnostics (for example cloud `/v1/models` fetch failures and Ollama upstream warnings). It also holds the full launch phase: build, port check, `docker compose up` and per-service `logs --tail` on failure:

```
${TMPDIR:-/tmp}/atlas-launch-<YYYYMMDDTHHMMSS>-<unique>.log
```

The most recent log is always:

```bash
ls -t "${TMPDIR:-/tmp}"/atlas-launch-*.log | head -1   # macOS: TMPDIR is under /var/folders
```

Inspect it after a failed launch. It holds everything the log pane showed, plus diagnostics the pane hides. An example is `[warn/openai-fetch] live /v1/models returned 0 models — falling back to catalog (cause: HTTP 401)`. A failed step also logs its reason (port conflict, auto-disabled dependency, key or config error), not only `<step> failed`.

Each session keeps up to 3 segments of 32 MiB. The first segment is always kept. Later output rotates into `.log.N` files with truncation markers. Atlas keeps the 5 newest sessions and prunes older `atlas-launch-*` files at the next launch. Copies you make elsewhere are never pruned. Every segment is created owner-only (`0600`), never through an existing file or symlink.

## 3. Quick Fixes

### 3.1. Port Conflicts
```bash
# Error: "bind: address already in use"
./start.sh --base-port 64000  # Use a different port block

# Find what holds the port
lsof -i :63096

# If lsof shows a Docker process, find the container that publishes the port
docker ps --filter publish=63096
```

Stop the owner normally: `docker stop` for a container, or the program's own stop command. Do not `kill -9` a Docker process. On Docker Desktop and Colima, one backend process holds the published ports of every container.

The start-up port check probes only ports that a container will publish. It skips services set to `disabled`, the LLM provider's `none` (no Ollama) and host-run `localhost` variants. A port held only by a closing connection (`TIME_WAIT`) is not a conflict, because Docker can still bind it.

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

To start over completely, run `./stop.sh --cold && ./start.sh`. This **deletes every named project volume** (databases, n8n workflows, downloaded models). The start then re-initializes them with the credentials in your kept `.env`. It is a full data reset, not a fix. Back up first (§10.3) and read §10.1 before you use it.

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

# For ollama-localhost, Atlas pulls the selected models onto the host
# daemon at start. If a pull fails, pull it on the host:
ollama pull qwen3.8:latest
```

The Ollama container publishes no host port. Reach it through LiteLLM (`http://localhost:63040/v1`), or with `docker compose exec ollama` for direct `/api/*` calls.

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

**`password authentication failed for user "supabase_admin"`:** `.env` holds a different `SUPABASE_DB_PASSWORD` than the one the `supabase-db-data` volume was created with. Set it back to that value and restart, or run `./stop.sh --cold && ./start.sh`, which deletes all of this project's volumes. Cause and details: [Supabase §2.2](../../services/supabase/README.md#22-supabase_admin-password-drift).

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

To see what this project stores, run `./start.sh storage inventory`. It reports:

- each volume named for the project, with its size from `docker system df -v`. A volume that no Atlas compose fragment declares is marked `unknown`. Examples are a leftover volume, or another project whose name starts the same way.
- the size of the declared host model directories (`COMFYUI_LOCAL_MODELS_PATH`, `COMFYUI_MPS_MODELS_PATH`).
- while the stack runs, the models in the Atlas Ollama container (`ollama list`) and the files in the ComfyUI models volume, each labelled as below.

| Label | Meaning |
|---|---|
| `retained` | In use, so never removed. An Ollama model is retained in three cases. `OLLAMA_USER_MODELS` or `OLLAMA_CUSTOM_MODELS` selects it. Or `LITELLM_DEFAULT_MODEL`, `LITELLM_VISION_MODEL`, `LITELLM_EMBEDDING_MODEL` or `LANGMEM_EMBEDDING_MODEL` names it as `ollama/<name>` or `ollama_chat/<name>`. Or the rendered `volumes/litellm/config.yaml` routes it. A ComfyUI file is retained when `volumes/comfyui/active-models.tsv` lists it. |
| `removable` | A catalog model that nothing selects or routes. |
| `unknown` | Not in the catalog. Removed only when you name it. |

Ollama sizes count blobs shared between models once per model, so they can add up to more than the volume holds.

To free model disk without a reset, run `./start.sh storage clean`:

- It removes `removable` items, plus the `unknown` items you name with `--name`, one at a time. It never removes a `retained` item.
- It prints each item and the total bytes, then asks before it removes anything. `--yes` skips the question.
- It removes single items through `docker exec` into the Atlas Ollama and ComfyUI containers. It never removes a volume, so no database, workflow or other stateful volume can be part of a cleanup.
- It never touches a host Ollama daemon or a host ComfyUI directory. Models are itemized only when `LLM_PROVIDER_SOURCE` is `ollama-container-*` or `COMFYUI_SOURCE` is `container-*`. ComfyUI files are itemized only after a start has written `volumes/comfyui/active-models.tsv`.
- If a removal fails, the cleanup stops, exits 1 and leaves every remaining item as it was. Run `storage inventory` again to see the true state.

To reclaim all disk from **this project only**, use the project-scoped reset (`./stop.sh --cold`, §10.1). It removes only Atlas's own containers, network and named volumes. Other Compose projects on the host are not touched.

Atlas recovery never uses daemon-wide cleanup (`docker system prune`, `docker volume prune`). Those commands affect every project on the host, including other applications' containers, images, build caches and volumes. If the host needs it, check `docker system df -v` first. Run it only when you can account for everything it removes.

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

If `.env` is corrupted beyond repair, rebuilding it is **destructive**. New secrets do not match the passwords already set in your database volumes (§4.4). A new `.env` therefore works only with a full project reset that deletes those volumes. Back up first (§10.3), then follow §10.1.

**A service rejects its password or encryption key after an upgrade.** Docker Compose expands a backslash or a bare `$` in an unquoted value. Atlas therefore single-quotes such values from `.env.user`, `ATLAS_ENV_USER_FILE`, a consumer manifest's `env.values` or a wizard API key. Older releases wrote them unquoted, so Compose passed a truncated secret (`ab$cd` became `ab`). A service that stored it at first boot keeps it truncated (an n8n encryption key, a database password).

To fix it, set the value to what the service stored, or rotate the secret in the service. Generated secrets never contain `$` or a backslash and are not affected.

### 7.3. An image fails to build

Atlas builds local images before `docker compose up` in two cases. A cold start (`./start.sh --cold` or the wizard option) builds every enabled image without cache. A normal start builds only stale images. Images are stale on the first start, after an Atlas source or build-setting change, or after a change to the enabled services. Otherwise a normal start builds nothing.

The build is one `docker compose build` of all enabled services. If it fails, Atlas first rebuilds the required images together. These are the images another enabled service names in `depends_on`, plus the core (Supabase, Kong, Redis, LiteLLM and Backend). Then it rebuilds each other image on its own. A cold start keeps `--no-cache`, so this can take as long as the first build. A normal start reuses the build cache.

- **A required image fails:** the launch stops with `Failed to build some services`. Managed host processes (for example a host ComfyUI) are rolled back, and `./start.sh` exits nonzero.
- **Only other images fail** (for example the `jupyterhub` notebook image): Atlas names them, leaves them out of `docker compose up` and starts everything else. Managed host processes keep running. The launch result reads `degraded` and names each image, so the start still succeeds. The next start tries the failed build again until it is fixed or the service is disabled:

  ```bash
  # Skip the service until its build is fixed (or set JUPYTERHUB_SOURCE=disabled in .env)
  ./start.sh --jupyterhub-source disabled
  ```

- **Every image builds on its own:** the first failure was transient and the whole stack starts.

**`At least one invalid signature was encountered` while building `jupyterhub`**. The notebook image runs `apt-get update` for the JDK that its Scala kernel needs, so this step cannot be skipped. On arm64 hosts such as Apple Silicon, the mirror is `ports.ubuntu.com`. The error usually means the Docker VM is out of disk space or its clock is wrong. Check the space Docker reports, free some or raise Docker Desktop's disk limit, then build again:

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

Attach a redacted support bundle to the issue. Raw logs and a raw `.env` hold the LiteLLM master key, database passwords and provider API keys, so do not attach them.

```bash
# Write the bundle after it shows you its full contents
./start.sh doctor --bundle ./atlas-support.tar.gz

# Or re-run the failing start; a failed launch writes the bundle
./start.sh --support-bundle ./atlas-support.tar.gz
```

Redaction is best-effort, so read the preview ([Operations §4.1](../operations/index.md#41-support-bundle)). Also include your versions:

```bash
docker --version
docker compose version
python3 --version
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

Atlas recovery is **project-scoped**. Everything Atlas creates (containers, the network, named volumes) carries your `PROJECT_NAME` prefix (`atlas` by default). The commands below remove only that. Other Compose projects on the host, and their volumes, images and caches, are never touched.

### 10.1. Complete reset (deletes this project's data)

`./stop.sh --cold` stops the stack and **deletes every named Atlas project volume**: databases, n8n workflows, downloaded models and generated artifacts. There is no undo.

- If you started the stack with `--consumer <manifest>`, stop it with the same `--consumer`. Otherwise the volumes of its compose overlays are unknown to the stop. The cold stop then names them and exits non-zero, and does not report a full wipe. `./start.sh --cold` checks the same way and stops before it rotates any secret.
- Take a backup first (§10.3). With the default `BACKUP_S3_MODE=local`, the backup is in this project's MinIO volume and the Neo4j and Weaviate snapshot volumes, so `--cold` deletes it too. Use `BACKUP_S3_MODE=external`, or copy the bucket off the host before the reset.
- Keep your own copy of `BACKUP_MANIFEST_HMAC_KEY` and `BACKUP_DEPLOYMENT_ID`. A backup cannot be restored without them.

```bash
# Full project reset: removes THIS project's containers, network and
# volumes; every other project on the host is left untouched
./stop.sh --cold

# Re-initialize the empty volumes with the kept .env
./start.sh
```

Use `./start.sh --cold` only to also rebuild `.env` from `.env.example`. That resets `BASE_PORT`, sources and typed keys. The previous file is saved next to it as `.env.backup.cold.<YYYYmmddTHHMMSS>.<random>` (owner-only). The five newest cold copies are kept, apart from the routine `.env.backup.*` copies.

### 10.2. Partial Reset

```bash
# Repair environment configuration WITHOUT touching data — preserves
# existing values, appends newly introduced keys, reports what changed:
./start.sh env backfill

# Reset one volume's data (destructive). Stop the stack first: Docker
# refuses to remove a volume a container (even a stopped one) still uses.
# Volume names carry the PROJECT_NAME prefix from .env:
./stop.sh
# Uses COMPOSE_PROJECT_NAME from the setup line at the top of this page.
docker volume rm "${COMPOSE_PROJECT_NAME}-supabase-db-data"  # the shared Postgres: every service database
docker volume rm "${COMPOSE_PROJECT_NAME}-n8n-data"          # n8n's user folder only
```

`supabase-db-data` holds every database on the shared Postgres. These are Supabase, backend (memory, media ledger), n8n, Open WebUI, LiteLLM, LightRAG, Airflow, Langfuse, MLflow, Label Studio, Iceberg, JupyterHub, Zeppelin and TrueForge. Removing it resets all of them. n8n keeps its workflows and credentials in that Postgres (`DB_TYPE=postgresdb`). `n8n-data` holds only its user folder (`/home/node/.n8n`).

Rebuilding `.env` from `.env.example` is **not** a partial reset. New secrets do not match the credentials already set in the existing volumes. A new `.env` therefore needs the full reset in §10.1, which deletes those volumes and re-initializes both together.

### 10.3. Backup Before Reset

Do **not** copy a running database's data directory as a backup. A file-by-file copy of a live PostgreSQL data directory is torn mid-write and is not known to restore. Use the consistency-safe backup service. It captures a `pg_dump -Fc` Postgres dump, bounded offline Neo4j dumps, a native Weaviate snapshot and a Supabase Storage archive. It pushes authenticated artifacts to the stack's S3 bucket.

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

Coverage and limits:

- Captured: the main Supabase database (`SUPABASE_DB_NAME`), Neo4j, Weaviate and Supabase Storage.
- **Not** captured: the other databases on the same Postgres server (LiteLLM, Airflow, Langfuse, MLflow, Label Studio, the Iceberg catalog, Supavisor and similar). A reset loses their keys, spend, traces, registries and metadata.
- **Not** captured: `.env`. Keep your own copy; it holds the keys that decrypt what the databases store.
- In local mode, artifacts are in this project's MinIO volume and do not survive `./stop.sh --cold` (§10.1).
- Postgres and Storage are archived at slightly different times. Storage has no restore procedure yet (it is not in the signed manifest). After a Postgres restore, `storage.objects` rows may not match the files on disk.
- Only a restore proves a backup. Before you rely on one, and before you delete anything, rehearse the restore on a disposable project. Follow [Backup README](../../services/backup/README.md) (`run-database-restore.sh` / `restore-postgres.sh`). A guided restore rehearsal is tracked in [#1034](https://github.com/thekaveh/atlas/issues/1034).

Most issues can be fixed without data loss. Try a targeted fix before a complete reset.
