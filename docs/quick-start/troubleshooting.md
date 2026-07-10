# Troubleshooting Guide

This guide covers common issues and their solutions when using Atlas.

## 1. .env Migration (LiteLLM rollout)

If you're upgrading from a pre-LiteLLM `.env` you may see startup errors about missing variables. Apply these changes:

- Rename `LLM_PROVIDER_PORT` to `LITELLM_PORT` (default is now `63040` under the current topology layout — the slot belongs to the LiteLLM gateway, not Ollama).
- Remove `OLLAMA_ENDPOINT` and any `OLLAMA_BASE_URL` lines — consumers now read `LITELLM_BASE_URL` and `LITELLM_API_KEY` (where `LITELLM_API_KEY=$LITELLM_MASTER_KEY`).
- If you previously set `LLM_PROVIDER_SOURCE=api` or `LLM_PROVIDER_SOURCE=disabled`, change it to `LLM_PROVIDER_SOURCE=none` and enable at least one of `CLOUD_OPENAI_SOURCE`, `CLOUD_ANTHROPIC_SOURCE`, `CLOUD_OPENROUTER_SOURCE`.

The simplest reset is `cp .env.example .env` followed by `./start.sh --cold` — keys are regenerated and every variable is in its current form.

## 2. Session Log

When `./start.sh` runs the Textual TUI, every line is tee'd to a timestamped file — both wizard-time diagnostic events (cloud `/v1/models` fetch failures, Ollama upstream discovery warnings, etc.) and the entire launch phase (build, port verification, `docker compose up`, per-service `logs --tail` on failure):

```
/tmp/atlas-launch-<YYYYMMDDTHHMMSS>.log
```

The most recent log is always:

```bash
ls -t /tmp/atlas-launch-*.log | head -1
```

Inspect it after a failed launch — it captures everything the log pane showed, plus a few sources the pane filters out (e.g. cloud-fetch fallback warnings: `[warn/openai-fetch] live /v1/models returned 0 models — falling back to catalog (cause: HTTP 401)`). The file persists across reboots until your OS rotates `/tmp`; copy it elsewhere if you need to keep it.

## 3. Quick Fixes

### 3.1 Port Conflicts
```bash
# Error: "bind: address already in use"
./start.sh --base-port 64000  # Use different port range

# Find what's using the port
lsof -i :63096

# Kill process using the port (if safe)
kill -9 $(lsof -t -i:63096)
```

### 3.2 Memory Issues
```bash
# Error: Containers crashing with exit code 137 (OOM kill)
# Solution: Increase Docker memory allocation

# Docker Desktop: Settings → Resources → Memory (set to 10-12GB)
# Colima users:
colima stop
colima start --memory 12 --cpu 6
```

### 3.3 Access Issues
```bash
# Can't access *.localhost URLs?
./start.sh --setup-hosts  # Configure hosts file

# Want to skip hosts setup?
./start.sh --skip-hosts   # Access via direct ports only

# Fresh start needed?
./stop.sh --cold && ./start.sh --cold
```

### 3.4 Platform Issues
```bash
# Windows/WSL issues?
python3 bootstrapper/start.py --help  # Use Python directly

# Shell script permissions?
chmod +x start.sh stop.sh
```

## 4. Service-Specific Issues

### 4.1 LLM Issues (LiteLLM gateway + Ollama upstream)

**LiteLLM not responding / consumers can't reach LLMs:**
```bash
# Liveness check (no auth required)
curl http://localhost:63040/health/liveliness

# List registered models (auth required)
curl -H "Authorization: Bearer $LITELLM_MASTER_KEY" http://localhost:63040/v1/models

# Inspect LiteLLM logs
docker logs ${PROJECT_NAME}-litellm -f
```

**Ollama models not downloading:**
```bash
# Check the ollama-pull init container
docker logs ${PROJECT_NAME}-ollama-pull -f

# Or the Ollama container itself
docker logs ${PROJECT_NAME}-ollama -f

# For localhost setup, pre-download on the host:
ollama serve &
ollama pull qwen3.6:latest
ollama pull qwen3-embedding:0.6b
```

Reminder: Ollama no longer has a host port mapping. Reach it via LiteLLM (`http://localhost:63040/v1`) or via `docker exec` for direct `/api/*` calls.

**Out of memory during model loading:**
```bash
# Use a localhost Ollama upstream to free up Docker memory
./start.sh --llm-provider-source ollama-localhost
ollama pull qwen3:1.7b  # Smaller model
```

### 4.2 ComfyUI Issues

**Models downloading slowly or missing:**
```bash
# The bootstrapper resolves COMFYUI_USER_MODELS at start and writes
# volumes/comfyui/active-models.tsv. comfyui-init downloads each entry
# via wget. If models you picked never show up, check these logs:
docker logs ${PROJECT_NAME}-comfyui-init -f

# Verify the manifest was written at start (check volumes/comfyui/):
ls volumes/comfyui/

# Check ComfyUI service status
docker logs ${PROJECT_NAME}-comfyui -f
```

**Can't access ComfyUI interface:**
```bash
# Check if hosts are configured
./start.sh --setup-hosts

# Access via direct URL
curl http://localhost:63053  # Direct port access (COMFYUI_PORT)
```

### 4.3 n8n Issues

**n8n not accessible:**
```bash
# Check n8n service status
docker logs ${PROJECT_NAME}-n8n -f

# Try direct access
curl http://localhost:63075

# Check Kong routing
curl -H "Host: n8n.localhost" http://localhost:63000/
```

**Workflow execution fails:**
```bash
# Check n8n worker logs
docker logs ${PROJECT_NAME}-n8n-worker -f

# Check Redis connection
docker logs ${PROJECT_NAME}-redis -f
```

### 4.4 Database Issues

**Supabase services not starting:**
```bash
# Check individual service logs
docker logs ${PROJECT_NAME}-supabase-db -f
docker logs ${PROJECT_NAME}-supabase-auth -f
docker logs ${PROJECT_NAME}-supabase-api -f

# Check if database initialization completed
docker logs ${PROJECT_NAME}-supabase-db-init -f
```

**Database connection errors:**
```bash
# Verify database is running
docker exec ${PROJECT_NAME}-supabase-db pg_isready

# Check connection from another service
docker exec ${PROJECT_NAME}-backend python -c "import psycopg2; print('DB OK')"
```

**`password authentication failed for user "supabase_admin"`:** the `supabase_admin` role password is baked into the `supabase-db-data` volume **once**, at first init, and is never re-synced. `SUPABASE_DB_PASSWORD` ships as the placeholder `password` and auto-rotates to a random value on the first `./start.sh`. If the volume later persists across a `.env` password change (e.g. `.env` regenerated from `.env.example` while an old volume is still around — `./stop.sh` without `--cold` keeps volumes), every client authenticates with the new value while the role still holds the old → this error. The bootstrapper now **skips** rotation and warns when it detects an existing `${PROJECT_NAME}-supabase-db-data` volume, so it won't silently drift `.env`. To recover:
```bash
# Option A — start fresh (removes volumes, reinitializes role + .env together)
./stop.sh --cold && ./start.sh
# Option B — keep your data: set SUPABASE_DB_PASSWORD in .env back to the
#            value the volume was created with, then restart.
```
See `services/supabase/README.md` §2.1 for the full explanation.

### 4.5 Kong Gateway Issues

**404 errors for services:**
```bash
# Kong config is dynamically generated at startup — inspect the generator
# (and the KONG_* env vars it consumes) rather than the emitted file:
cat bootstrapper/utils/kong_config_generator.py

# Verify Kong is running
docker logs ${PROJECT_NAME}-kong-api-gateway -f

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

### 5.1 Docker Resource Monitoring

```bash
# Check overall resource usage
docker stats

# Check disk usage
docker system df

# Clean up unused resources
docker system prune -f
docker volume prune -f  # BE CAREFUL - removes unused volumes
```

### 5.2 Memory Optimization

```bash
# Disable memory-heavy services
./start.sh --n8n-source disabled --weaviate-source disabled --minio-source disabled

# Use localhost services to reduce container overhead
./start.sh --llm-provider-source ollama-localhost --comfyui-source localhost
```

## 6. Network Issues

### 6.1 DNS Resolution

```bash
# Check hosts file entries
cat /etc/hosts | grep localhost

# Manually add entries if needed
echo "127.0.0.1 n8n.localhost comfyui.localhost search.localhost api.localhost chat.localhost" | sudo tee -a /etc/hosts
```

### 6.2 Firewall Issues

```bash
# Check if ports are accessible
telnet localhost 63096
nc -zv localhost 63096

# For localhost services, check host firewall
sudo ufw status  # Ubuntu/Debian
```

## 7. Startup Issues

### 7.1 Service Dependencies

```bash
# Some services depend on others - check startup order
docker compose ps

# If services are failing, check dependency services first
docker logs ${PROJECT_NAME}-redis -f      # Many services need Redis
docker logs ${PROJECT_NAME}-supabase-db -f # Backend needs database
```

### 7.2 Environment Issues

```bash
# Check if .env file exists and is valid
ls -la .env
cat .env | head -20

# Regenerate if corrupted
cp .env.example .env
./start.sh --cold  # Regenerate keys
```

## 8. Debug Commands

### 8.1 System Status Check

```bash
# Overall system health
docker compose ps

# Service logs (most recent)
docker compose logs --tail=50

# Specific service investigation
docker logs ${PROJECT_NAME}-ollama --tail=100 -f
docker logs ${PROJECT_NAME}-backend --tail=100 -f
```

### 8.2 Configuration Verification

```bash
# Inspect the SOURCE values currently written to .env
grep -E '^[A-Z_]+_SOURCE=' .env

# List all available CLI flags (Click-generated help is the source of truth)
python3 bootstrapper/start.py --help

# Inspect the dynamic Kong configuration generator (kong.yml is rebuilt
# on every startup — don't edit by hand; instead trace the inputs):
cat bootstrapper/utils/kong_config_generator.py | head -80
env | grep ^KONG_

# Check live environment variables in your shell
env | grep -E "(OLLAMA|COMFYUI|N8N|WEAVIATE|CLOUD|MINIO)_SOURCE"
```

### 8.3 Network Testing

```bash
# Test internal service connectivity (LLM goes through LiteLLM, not Ollama directly)
docker exec ${PROJECT_NAME}-backend curl http://${PROJECT_NAME}-litellm:4000/health/liveliness
docker exec ${PROJECT_NAME}-litellm curl http://${PROJECT_NAME}-ollama:11434/api/tags
docker exec ${PROJECT_NAME}-kong-api-gateway curl http://${PROJECT_NAME}-supabase-api:3000/health

# Test external access
curl http://localhost:63096
curl -H "Host: n8n.localhost" http://localhost:63000/
```

## 9. Getting Help

### 9.1 Log Collection

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
cp .env config_backup.env  # Remove sensitive data before sharing
```

### 9.2 Common Support Information

1. **Platform**: macOS/Linux/Windows + version
2. **Docker memory allocation**: Settings → Resources in Docker Desktop
3. **Services enabled**: Which SOURCE values you're using
4. **Error messages**: Exact error text and which service
5. **Steps to reproduce**: What you did before the error occurred

### 9.3 Community Resources

- [GitHub Issues](https://github.com/thekaveh/atlas/issues) - Bug reports and feature requests
- [GitHub Discussions](https://github.com/thekaveh/atlas/discussions) - Questions and community support
- [Documentation](https://github.com/thekaveh/atlas/blob/main/docs/README.md) - Complete documentation index

## 10. Recovery Procedures

### 10.1 Complete Reset

```bash
# Nuclear option - removes all data
./stop.sh --cold
docker system prune -af  # Remove all containers, networks, images
docker volume prune -f   # BE CAREFUL - removes ALL unused volumes

# Start fresh
./start.sh --cold --base-port 64000
```

### 10.2 Partial Reset

```bash
# Reset just environment
./stop.sh
rm .env
cp .env.example .env
./start.sh --cold  # Regenerate keys only

# Reset specific service data
docker volume rm ${PROJECT_NAME}-supabase-db-data  # Database only
docker volume rm ${PROJECT_NAME}-n8n-data          # n8n workflows only
```

### 10.3 Backup Before Reset

```bash
# Backup important data before reset
mkdir -p backup/$(date +%Y%m%d_%H%M%S)
docker run --rm -v ${PROJECT_NAME}-supabase-db-data:/data -v $(pwd)/backup/$(date +%Y%m%d_%H%M%S):/backup alpine cp -r /data /backup/supabase_db
docker run --rm -v ${PROJECT_NAME}-n8n-data:/data -v $(pwd)/backup/$(date +%Y%m%d_%H%M%S):/backup alpine cp -r /data /backup/n8n
```

Remember: Most issues can be resolved without losing data. Try targeted solutions before doing a complete reset!
