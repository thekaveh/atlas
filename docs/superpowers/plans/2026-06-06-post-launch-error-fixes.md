# Post-Launch Error Fix Plan

**Date:** 2026-06-06
**Scope:** All errors surfaced in the first successful launch of the stack with `LIGHTRAG_SOURCE=container` + `TEI_RERANKER_SOURCE=container-cpu` after the LightRAG + TEI Reranker integration shipped.
**Base SHA:** `36a6347` (most recent main).

## 1. Issue inventory (grouped by severity)

### 1.1. P0 — Crash loops; block end-to-end testing

| # | Service | Symptom | Owner |
|---|---|---|---|
| 1 | `lightrag` | `ValueError: Unsupported rerank binding: openai` at `lightrag_server.py:1910` | LightRAG integration (our work) |
| 2 | `tei-reranker` | ORT backend fails: `BAAI/bge-reranker-v2-m3` has no ONNX files; `Could not start backend` | LightRAG integration (our work) |
| 3 | `backend` | `ModuleNotFoundError: No module named 'prometheus_fastapi_instrumentator'` at `main.py:67` | Pre-existing (image stale since 2026-05-15; predates the import) |

### 1.2. P1 — Non-fatal but user-visible (warnings on every launch)

| # | Service | Symptom | Owner |
|---|---|---|---|
| 4 | `lightrag-init` | `Neo4j migration HTTP call failed (curl exit 6)` — couldn't resolve host | LightRAG integration (our work; wrong hostname) |
| 5 | `lightrag` | `WARNING: TOKEN_SECRET not set... Falling back to default guest-mode JWT secret` | LightRAG integration (our work) |
| 6 | `lightrag` | `Warning: Startup directory must contain .env file for multi-instance support.` | LightRAG integration (our work; spec I2 concern) |

### 1.3. P2 — Pre-existing, non-blocking, unrelated to our work

| # | Service | Symptom | Owner |
|---|---|---|---|
| 7 | `postgres-exporter` | `pq: column "checkpoints_timed" does not exist` (Supabase upgraded to Postgres 18; exporter expects ≤17 schema) | Pre-existing |
| 8 | `postgres-exporter` | `Error opening config file "postgres_exporter.yml": no such file` | Pre-existing (config-file path mismatch; exporter works without it) |
| 9 | `cadvisor` | `Failed to get system UUID: /etc/machine-id`, podman/containerd/crio socket not found | Pre-existing (cadvisor expects Linux paths; harmless on macOS) |
| 10 | `node-exporter` | `Failed to open /run/udev/data, disabling udev device properties` | Pre-existing (Linux-only path; harmless on macOS) |
| 11 | `n8n-worker` | `Failed to start Python task runner... Python 3 is missing` | Pre-existing (informational; n8n falls back to internal mode) |
| 12 | `weaviate` | `Multiple vector spaces are present, GraphQL Explore... has been disabled` | Pre-existing (by design when CLIP vectorizer is enabled) |
| 13 | `ray-head` / `ray-worker` | `/dev/shm has only 4 GB available... This will harm performance` | Pre-existing (Docker Desktop default; perf only) |

---

## 2. Root cause + fix per issue

### 2.1. #1 LightRAG `RERANK_BINDING=openai` rejected

**Root cause:** LightRAG v1.5.0 only accepts these `RERANK_BINDING` values: `null`, `cohere`, `jina`, `aliyun`. Our compose fragment sets it to literal `openai` whenever `LIGHTRAG_RERANK_BINDING_HOST` is set:

```yaml
# services/lightrag/compose.yml (current, line ~45)
RERANK_BINDING: ${LIGHTRAG_RERANK_BINDING_HOST:+openai}
```

TEI's `/rerank` endpoint is wire-compatible with the Jina/Cohere standard schema (`{query, documents[], top_n, return_documents}`). LightRAG's `cohere` and `jina` adapters both POST the same payload — TEI accepts it.

**Fix:** Use `jina` (slightly cleaner; sets `return_documents=false` explicitly).

```yaml
# services/lightrag/compose.yml
RERANK_BINDING: ${LIGHTRAG_RERANK_BINDING_HOST:+jina}
RERANK_BINDING_HOST: ${LIGHTRAG_RERANK_BINDING_HOST}/rerank  # append /rerank path
RERANK_MODEL: BAAI/bge-reranker-v2-m3
```

Also need to update `services/lightrag/service.yml::runtime_adaptive.lightrag.environment_adaptation`:

```yaml
# pass the FULL rerank URL including /rerank — LightRAG sends to this exact URL
LIGHTRAG_RERANK_BINDING_HOST: ${TEI_RERANKER_ENDPOINT}
```

(Keep the var name `LIGHTRAG_RERANK_BINDING_HOST` even though it now resolves to a URL; the compose env-var passthrough appends `/rerank`.)

Update LightRAG README §4 and CHANGELOG accordingly.

### 2.2. #2 TEI Reranker model loading

**Root cause:** `ghcr.io/huggingface/text-embeddings-inference:cpu-1.9` is compiled with the **ORT (ONNX Runtime) backend** for x86_64. `BAAI/bge-reranker-v2-m3` has no ONNX files on HuggingFace (only `model.safetensors`). The arm64 image `cpu-arm64-1.9`/`cpu-arm64-latest` uses the **candle** backend which loads safetensors natively.

Two fix paths:

**Fix A (recommended) — switch to arm64 native image with platform pin removal:**
- The platform pin we added (`platform: linux/amd64`) for TEI Reranker was based on `cpu-1.9` being amd64-only. Switching to `cpu-arm64-latest` makes the pin moot.
- Add a host-arch auto-detection: bootstrapper picks `cpu-1.9` (amd64) OR `cpu-arm64-latest` (arm64) based on `platform.machine()`.

**Fix B (simpler) — swap model to one with ONNX files:**
- `mixedbread-ai/mxbai-rerank-base-v1` ships ONNX out of the box. Quality is comparable to bge-reranker-v2-m3 for retrieval tasks.
- Change `TEI_RERANKER_MODEL_ID=mixedbread-ai/mxbai-rerank-base-v1` and the existing `cpu-1.9` image works.

**Recommended approach:** Fix A. Reasoning:
- Keeps the model promise (`bge-reranker-v2-m3` is widely-known, what's referenced in docs/READMEs).
- Removes the Rosetta emulation cost on Apple Silicon (large CPU latency reduction).
- Leverages an existing tag that's actively maintained by HF.

Implementation:
1. Update `services/tei-reranker/service.yml::images`: add a third image var `TEI_RERANKER_ARM64_IMAGE`, default `ghcr.io/huggingface/text-embeddings-inference:cpu-arm64-latest`.
2. Update `bootstrapper/services/service_config.py::_generate_tei_reranker_config`: when source is `container-cpu`, pick the arm64 image if host is arm64, else the amd64 image.
3. Update `services/tei-reranker/compose.yml`: REMOVE the `platform: linux/amd64` pin (no longer needed when the arch-native image is used).
4. Update `.env.example` regen.
5. Update README §1/§3 to document the arch-aware image selection.
6. Refresh fragment-equivalence baseline.

**Bonus:** add a `test_tei_reranker_image_arch_selection` unit test that mocks `platform.machine()` and asserts the right image is selected.

### 2.3. #3 Backend missing `prometheus-fastapi-instrumentator`

**Root cause:** The dependency IS listed in `services/backend/app/app/requirements.txt:8`:
```
prometheus-fastapi-instrumentator>=7.0.0
```
But the image `atlas-backend:latest` was built on 2026-05-15, **before** the import was added (`main.py:67`). Docker has been reusing the stale image.

**Fix:** Rebuild backend image.

```bash
cd /Users/kaveh/repos/atlas
docker compose -p atlas build --no-cache backend
docker compose -p atlas up -d backend
```

This is a one-shot operation, not a code change. Optionally, also add `--build` to the `start.sh` invocation for the backend stage, or add the backend to a "force-rebuild on launch" list in the bootstrapper for cases where requirements.txt has changed.

### 2.4. #4 lightrag-init Neo4j migration: wrong hostname

**Root cause:** `services/lightrag/service.yml::runtime_adaptive.lightrag.environment_adaptation` uses `bolt://neo4j:7687`, and `services/lightrag/init/scripts/init-lightrag.sh` calls `http://neo4j:7474/...`. The actual Neo4j compose service id in this stack is `neo4j-graph-db` (per `services/neo4j/compose.yml:3`). Memory `reference_kong_compose_service_id` already documents this same pattern for Kong.

**Fix:** Replace all `neo4j` host references with `neo4j-graph-db` in:
- `services/lightrag/service.yml::runtime_adaptive.lightrag.environment_adaptation.LIGHTRAG_NEO4J_URI` → `bolt://neo4j-graph-db:7687`
- `services/lightrag/init/scripts/init-lightrag.sh` → `http://neo4j-graph-db:7474/db/neo4j/tx/commit`

(`bolt://neo4j-graph-db:7687` URI matches `NEO4J_server_default__advertised__address: "neo4j-graph-db"` in Neo4j's own compose env.)

Refresh fragment-equivalence baseline.

### 2.5. #5 LightRAG `TOKEN_SECRET` warning

**Root cause:** LightRAG ships a JWT auth path (login → token) gated on `TOKEN_SECRET` env. We never set it, so LightRAG falls back to a hardcoded default key — security risk in any non-trivial deployment.

**Fix:** Generate a fresh `LIGHTRAG_TOKEN_SECRET` in `bootstrapper/utils/key_generator.py` similar to `generate_lightrag_api_key`, write to `.env`, and pass into the compose env block.

Add to `services/lightrag/service.yml::env`:
```yaml
  - name: LIGHTRAG_TOKEN_SECRET
    default: ""
    secret: true
    description: "Auto-generated. JWT signing key for LightRAG /login flows."
```

Update `services/lightrag/compose.yml`:
```yaml
TOKEN_SECRET: ${LIGHTRAG_TOKEN_SECRET}
```

Update `bootstrapper/utils/key_generator.py::generate_missing_keys` (gated on `LIGHTRAG_SOURCE != disabled`).

### 2.6. #6 LightRAG `.env` warning — missing startup file

**Root cause:** LightRAG looks for `/app/.env` (the working directory `.env`) at startup for multi-instance worker support. Our `lightrag-init` writes resolved values to `/app/data/.lightrag-resolved.env` (a different path that LightRAG doesn't auto-load).

**Fix:** Have `lightrag-init` write its output to `/app/data/.env` instead, AND mount that file (or the whole `data/`) so the lightrag container reads it on startup. The simplest path:

1. In `services/lightrag/init/scripts/init-lightrag.sh`, change the resolved-env output path:
   ```bash
   python3 /scripts/resolve-models.py > /app/data/.env
   ```
2. In `services/lightrag/compose.yml::services.lightrag`, ensure the working dir is `/app/data/` OR add `env_file: /app/data/.env`. Inspect LightRAG's Dockerfile to confirm its WORKDIR; if `/app`, add a symlink or env_file directive.

Verify with `docker exec atlas-lightrag env | grep LIGHTRAG_LLM_MODEL` after boot — should show the resolved model.

### 2.7. #7 postgres-exporter Postgres 18 schema mismatch

**Root cause:** Supabase shipped Postgres 18 (current `supabase-db` image). Postgres 17+ renamed `pg_stat_bgwriter.checkpoints_timed` → split into `pg_stat_checkpointer.num_timed`. Current `postgres-exporter` image (`v0.16.0`) queries the old column.

**Fix:** Bump postgres-exporter to v0.17.0 or newer (released after PG18 support added). Image: `prometheuscommunity/postgres-exporter:v0.18.1` (current).

Edit `services/supabase/service.yml::images` (or wherever postgres-exporter image is pinned). Refresh fragment-equivalence baseline.

### 2.8. #8 postgres-exporter missing config file

**Root cause:** The exporter container looks for `/etc/postgres_exporter.yml` (or pwd-relative `postgres_exporter.yml`) but no config volume is mounted. This is informational — the exporter works fine without it (uses defaults).

**Fix:** Either:
- (Suppress) Mount an empty `postgres_exporter.yml` to silence the warning, OR
- (Document) Add a comment in `services/supabase/compose.yml` noting the warning is expected and harmless.

Lowest-effort option: (Document). Not worth a code change.

### 2.9. #9–12 Pre-existing macOS-specific warnings (cadvisor, node-exporter, n8n-worker, weaviate)

All informational, all surface on macOS Docker Desktop because container expects Linux host paths. Document in `docs/deployment/expected-startup-warnings.md` (file exists) under a new "Container-level expected warnings on macOS" section.

### 2.10. #13 Ray /dev/shm size

**Root cause:** Docker Desktop on macOS provisions `/dev/shm` at 4 GB by default; Ray's object store uses it. The warning recommends 10 GB.

**Fix:** Add `shm_size: "8gb"` to Ray's compose service entries. Tests against `test_fragment_equivalence` may need baseline refresh.

Lower priority — only matters for non-trivial Ray workloads.

---

## 3. Recommended execution order

### 3.1. Phase A — Land LightRAG fully (P0 + P1)
1. **Fix #4** (Neo4j hostname) — quick, unblocks the only init-time warning.
2. **Fix #1** (RERANK_BINDING=jina) — unblocks the lightrag main container.
3. **Fix #2** (TEI Reranker arch-aware image) — unblocks tei-reranker, removes emulation cost.
4. **Fix #5** (TOKEN_SECRET) — security; quick.
5. **Fix #6** (lightrag .env path) — quietens warning; verifies I2 concern from spec.

Verification gate: re-launch the stack, confirm lightrag + tei-reranker + lightrag-init all reach healthy state. Run the live smoke test from spec §12d.

### 3.2. Phase B — Pre-existing backend bug (P0)
6. **Fix #3** (rebuild backend image). One command.

Verification gate: `curl http://localhost:$BACKEND_PORT/health` returns 200.

### 3.3. Phase C — Hygiene + Polish (P2)
7. **Fix #7** (postgres-exporter v0.18.1 bump) — removes the most-frequent error line.
8. **Fix #8** (postgres-exporter config docs) — note in expected-startup-warnings.md.
9. **Fix #9–12** — document in expected-startup-warnings.md as a single batch.
10. **Fix #13** (Ray shm_size) — if Ray workloads are imminent.

### 3.4. Phase D — Verification (after all)
- Full pytest suite passes.
- `docker compose config` clean.
- All audit scripts pass.
- Live smoke: LightRAG WebUI loads at `lightrag.localhost:$KONG_HTTP_PORT/webui`; LiteLLM `/v1/chat/completions` with `model=lightrag` returns a response; ingest a PDF; verify KG in Neo4j Browser; verify vectors in Supabase Studio.

---

## 4. Risks / open questions

| Risk | Mitigation |
|---|---|
| `cpu-arm64-latest` is unversioned — tag could shift under us | Pin a digest after the next boot if stability matters; document in CHANGELOG |
| Postgres-exporter v0.18.x might require a different config schema | Test the bump against the running Supabase v18 before committing |
| Switching `RERANK_BINDING` to `jina` may differ from `cohere` in subtle ways (e.g., `top_n` defaulting) | Smoke-test a rerank request; both should work with TEI but `jina` is more conservative |
| LightRAG's `/app/data/.env` write may conflict with LightRAG's own `.env` template behavior | Inspect the official LightRAG Dockerfile + entrypoint to confirm `.env` lookup path |
| Stale baseline regen could mask other drift | Use the same surgical-edit pattern from Task 24 of the original plan, not a full regen |

---

## 5. Out of scope

- Adding GPU support for TEI Reranker (separate enhancement).
- Migrating from `mc` to a different MinIO client image.
- Refactoring the postgres-exporter image selection across services.
- Updating Ray to use anonymous mmap volume instead of /dev/shm.
- The init container could be smarter about retries on Neo4j unavailability — current pattern is fine.

---

## 6. Estimated effort

| Phase | Tasks | Time |
|---|---|---|
| A — LightRAG P0+P1 | 5 fixes + baseline refresh + tests | ~60 min |
| B — Backend rebuild | 1 command | ~5 min |
| C — Hygiene | 4 fixes + docs | ~30 min |
| D — Verification | Full smoke test | ~15 min |
| **Total** | | **~110 min** |
