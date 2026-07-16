# Post-Re-run Error Triage + Fix Plan

**Date:** 2026-06-07
**Scope:** All errors surfaced by the third stack launch (post-PR-#64) against `LIGHTRAG_SOURCE=container` + `TEI_RERANKER_SOURCE=container-cpu` + `--base-port 64000` on Apple Silicon arm64.

## 1. Issue inventory

| # | Severity | Service | Symptom | Classification |
|---|---|---|---|---|
| 1 | Critical | `tei-reranker` | Restart loop (RestartCount 25+); reaches "Warming up model" then exits 0; never `Ready` | NEW BUG |
| 2 | Important | `lightrag` | 8 `ERROR: relation "lightrag_doc_full" does not exist` lines at first boot, each immediately followed by `Creation success` | UNDOCUMENTED EXPECTED NOISE |
| 3 | Minor | `lightrag` | `/health` shows `"embedding_model":""` — workspace-isolation suffix missing | CONFIGURATION GAP |
| 4 | Minor | `kong-api-gateway` | nginx "user directive" warns × 2 at boot | UNDOCUMENTED EXPECTED NOISE |

Everything else in live logs matched `docs/deployment/expected-startup-warnings.md`. No regressions surfaced.

---

## 2. Root cause + fix per issue

### 2.1. #1 — TEI Reranker restart loop on arm64

**What's happening:** `cpu-arm64-latest` (only arm64 tag HF publishes) ships the candle backend. `BAAI/bge-reranker-v2-m3` doesn't ship ONNX, so the ORT path 404s (expected) and the code falls back to candle. Candle downloads `model.safetensors`, starts the BERT model on CPU, logs "Warming up model" — then the process exits cleanly (exit code 0, no OOM kill, no panic). No `Ready` line is ever emitted. Container is restarted by Docker every ~15 s.

**Why the model choice matters:** BGE-reranker-v2-m3 (~560 M params, ~2.2 GB FP32) is one of the heaviest cross-encoders. The combination of (candle + arm64 + this model + 4 GB limit) appears to crash silently during warmup inference. Bumping the limit to 8 GB doesn't help (no OOMKilled flag). The crash is likely a candle-arm64 bug specific to this model's `hidden_act=gelu` + dense module path (the WARN line `GeLU + tanh approximation will be used` hints at a substitution path that may misbehave).

**Fix:** Switch the default reranker model from `BAAI/bge-reranker-v2-m3` to `mixedbread-ai/mxbai-rerank-base-v1`. Reasons:
- Ships ONNX (`onnx/model.onnx` present in HF repo) → ORT backend works on amd64.
- Smaller (~184 M params) and simpler architecture → candle warmup completes on arm64.
- Comparable retrieval quality (mxbai-rerank-base is a strong commercial baseline; outperforms BGE-base, slightly behind BGE-large-v2 on BEIR).
- Cross-arch consistent: same model on amd64 and arm64.

Files to change:
- `services/tei-reranker/service.yml` — `TEI_RERANKER_MODEL_ID.default` → `mixedbread-ai/mxbai-rerank-base-v1`
- `services/lightrag/compose.yml` — `RERANK_MODEL` constant → same
- `services/tei-reranker/README.md` — model reference in §1/§3
- `bootstrapper/tests/fixtures/rendered_config_baseline.yml` — `RERANK_MODEL` env value drift
- `.env.example` — `TEI_RERANKER_MODEL_ID` regen

### 2.2. #2 — LightRAG `relation does not exist` startup noise

**What's happening:** LightRAG's `PGVectorStorage` uses a SELECT-then-CREATE pattern (it queries the table to verify schema, and on the first ever boot the table doesn't exist, so the SELECT errors). The code catches the error and runs CREATE TABLE, which succeeds. Net effect: 8 ERROR + 8 "Creation success" pairs scrolling past on every fresh-DB boot.

**Fix:** Document in `docs/deployment/expected-startup-warnings.md` under "Unavoidable startup races" — same shape as the LiteLLM view-create race entry. No code change needed; the pattern is self-resolving and only fires on first ingestion-startup.

### 2.3. #3 — `LIGHTRAG_EMBEDDING_MODEL` empty in live container

**What's happening:** `services/lightrag/init/scripts/resolve-models.py` correctly resolves the model name from LiteLLM `/v1/models` and writes `LIGHTRAG_EMBEDDING_MODEL=<resolved>` to `/app/data/.env`. But:
- LightRAG's WORKDIR is `/app`, not `/app/data`. Its dotenv loader looks at `/app/.env`, not `/app/data/.env`.
- AND: compose passes `EMBEDDING_MODEL=${LIGHTRAG_EMBEDDING_MODEL}` which expands to empty (.env's static default). Even if dotenv loaded `/app/data/.env`, dotenv doesn't override values already in `os.environ` — and compose puts EMBEDDING_MODEL="" in os.environ.

Net effect: LightRAG sees `embedding_model=""` and emits `WARNING: PostgreSQL table: LIGHTRAG_VDB_ENTITY missing suffix` (workspace isolation degrades).

**Fix:** Change the init script's output path from `/app/data/.env` to `/app/.env` (LightRAG's actual WORKDIR), AND change the lightrag compose env block to NOT pass empty `LLM_MODEL` / `EMBEDDING_MODEL` (let dotenv populate them). The cleanest is to drop the two `<var>: ${LIGHTRAG_<var>}` lines from compose entirely when the values are auto-resolved at init time.

Files to change:
- `services/lightrag/init/scripts/init-lightrag.sh` — `python3 /scripts/resolve-models.py > /app/data/.env` → `/app/.env`
- `services/lightrag/compose.yml` — drop `LLM_MODEL` and `EMBEDDING_MODEL` from the env block (init script populates via .env)

### 2.4. #4 — Kong nginx "user directive" warns

**What's happening:** Kong's bundled nginx config sets a `user` directive while running as a non-root user, triggering 2 warnings at boot. Cosmetic; Kong's image owner has chosen not to silence this.

**Fix:** Document in `expected-startup-warnings.md` under "Library-internal" alongside the existing Kong rate-limiting deprecation entry.

---

## 3. Execution order

1. **Doc-add** #2 and #4 to `expected-startup-warnings.md` — risk-free, immediate visibility win.
2. **Model swap** #1 — biggest unlock; restores rerank functionality.
3. **Config fix** #3 — eliminates the embedding_model workspace-isolation warning; latent issue today but real before scale-out.
4. **Baseline refresh** + tests.
5. **Verify**: tear down `atlas-tei-reranker` + `atlas-lightrag`, recreate, observe healthy + Ready logs.
6. **Commit + PR**.

## 4. Test plan

- `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/` → 717+ pass (1 baseline test needs re-baseline)
- `docker compose -p atlas up -d --force-recreate tei-reranker lightrag-init lightrag` → both reach healthy within `start_period`
- `curl $TEI_RERANKER_PORT/health` → 200 OK
- `curl -X POST $TEI_RERANKER_PORT/rerank -d '{"query":"X","texts":["A","B"]}'` → JSON ranked array
- LightRAG `/health` → `embedding_model` non-empty + `pg_*` workspace suffixes present

## 5. Out of scope

- Pinning a specific known-working arm64 TEI digest (no versioned arm64 tags exist; pinning floating `latest` digest is fragile).
- Switching to a different reranker stack entirely (Infinity, vLLM rerank). Reasonable enhancement but not blocking.
- Silencing the macOS-specific noise patterns (cadvisor, node-exporter, ray /dev/shm); already documented as expected.

## 6. Memory ties

- `project_post_merge_env_staleness` — the LIGHTRAG_EMBEDDING_MODEL gap is the same shape as Bug A there (declarative/imperative drift between init script and compose env block).
- `reference_litellm_quirks` — ollama_chat adapter pattern, relevant when verifying LiteLLM still proxies `lightrag` model after restart.
- `feedback_regression_test_structural_over_resolved_state` — the embedding_model test should assert it's non-empty (intent), not assert a specific value (implementation-dependent).
