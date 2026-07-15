# Design: Per-service seed partition + move the LLM model source-of-truth out of Supabase

- **Date:** 2026-06-25
- **Status:** Approved (design); pending implementation plan
- **Branch:** `model-sot-decoupling`
- **Scope of this spec:** Part A + Part B in depth. Part C (ComfyUI) captured as a roadmap follow-on.

---

## 1. Summary

Atlas seeds a single shared Supabase Postgres database from one monolithic init
container, and uses a `public.llms` table as a cross-service coordination bus for
which LLM models exist and which are active. This spec does two things:

- **Part A — Partition the seed scripts per owning service**, in place, with strong
  graceful-degradation guards and a proof that behavior does not change.
- **Part B — Move the LLM model source-of-truth out of Supabase** into human-readable,
  service-owned YAML files (`services/ollama/models.yaml`, `services/litellm/models.yaml`),
  deleting `public.llms` and the database coupling of the entire model-provisioning flow.

Part C applies the same YAML-SoT pattern to the ComfyUI model catalog once Part B proves out.

## 2. Motivation

Two coupling problems were identified during investigation:

1. **The seed is a monolith with split-brain ownership.** `services/supabase/db/scripts/`
   runs `01..12.sql` alphabetically in one `supabase-db-init` container. `05-public-tables.sql`
   alone bundles four services' tables (`users`, `llms`, `comfyui_models/workflows/generations`);
   the ComfyUI concern is further smeared across `08-seed-data.sql` and `12-extend-comfyui-models.sql`;
   `07-functions.sql` hides an `update_llms_updated_at` trigger. Research (`09`) and langmem (`10`/`10a`)
   are already cleanly per-service — so the partition is half-done and inconsistent.

2. **`public.llms` is a database used as a build-time cache.** It is *re-derived from scratch
   on every `./start.sh`* from `bootstrapper/utils/llm_catalog.py` + `.env`, so it is the
   source-of-truth for nothing. Four of its five consumers read it exactly once at boot
   (init containers that render a file/env and exit); the fifth (backend) already prefers the
   `LITELLM_DEFAULT_MODEL` env var and falls through to the table only as a last resort.
   The "live `psql UPDATE`" affordance is largely illusory because nothing re-reads the table
   until the next `./start.sh`, which overwrites it. This makes Supabase a hard dependency of
   the whole LLM subsystem for no load-bearing reason.

## 3. Goals & non-goals

**Goals**
- Make the Supabase seed legible: exactly one owning service per script, with enforced ownership.
- Behavior-preserving partition (Part A): zero schema/seed difference, proven by a byte-identical dump.
- Remove the LLM-provisioning dependency on Postgres (Part B): the model SoT becomes human-readable
  YAML owned by the service that uses it; consumers read env vars they already fall back to.
- Comprehensive automated tests covering both the SQL execution/order/idempotency and the
  YAML assembly/resolution.

**Non-goals (explicit honesty)**
- This does **not** make Supabase unlocked. n8n (workflows), Open WebUI (chat), Airflow
  (DAG metadata), JupyterHub (sessions), LiteLLM (its own logging/keys DB), and the backend's
  memory/research tables still own primary state in Postgres. Part B removes the
  *LLM-provisioning* coupling specifically — a prerequisite for an eventual DB-optional minimal
  stack, not the whole thing.
- No change to Redis couplings (n8n BullMQ queue, Kong rate-limit, Open WebUI websockets,
  LiteLLM cache, JupyterHub sessions). These are load-bearing and correctly locked.
- No new code-style linter/formatter/type-checker is introduced (per repo convention). The
  "static lints" in the test plan are domain audit checks in the spirit of the existing
  `scripts/check-*.py` and pytest suite — not a new style tool.

## 4. Background — current architecture

### 4.1. The LLM model flow (definition → seed → read → pull)

```
SOURCE OF TRUTH                          SEED STEP                    CONSUMERS (read)
bootstrapper/utils/llm_catalog.py  ──┐
.env (OPENAI_USER_MODELS,            ├─► llm-catalog-init ──► public.llms ──┬─► litellm-init  → config.yaml
      OLLAMA_USER_MODELS, …)         │   (sync-catalog.py:                  ├─► ollama-pull   → /api/pull
live discovery (cloud /v1/models,  ──┘    UPSERT + activate +              ├─► weaviate-init → embedding model
      ollama /api/tags)                    live-discover)                   ├─► local-deep-researcher → content model
                                                                           └─► backend (runtime) → extraction model
```

Key files:
- `bootstrapper/utils/llm_catalog.py` — curated catalog (cloud + Ollama defaults). Today the SoT.
- `services/litellm/catalog-init/scripts/sync-catalog.py` — UPSERTs catalog into `public.llms`,
  applies `.env` activation, **does a second startup `/api/tags` re-import** (redundant with the wizard).
- `services/litellm/init/scripts/init.py` — `SELECT … WHERE active=true` → renders `config.yaml`;
  guesses `ollama` vs `ollama_chat` adapter from the model **name**.
- `services/ollama/pull/scripts/pull.sh` — `SELECT name WHERE provider='ollama' AND active=true` → `/api/pull`.
- `services/weaviate/init/scripts/init-weaviate.sh` — `SELECT … embeddings>0 ORDER BY embeddings DESC LIMIT 1`;
  already falls back to `LITELLM_EMBEDDING_MODEL`, hard-fallback `nomic-embed-text`.
- `services/local-deep-researcher/build/scripts/init-config.py` — `SELECT … content>0 …`; falls back to `LITELLM_DEFAULT_MODEL`.
- `services/backend/app/app/memory_service.py::_get_extraction_model()` — resolution order
  is explicit arg → `LITELLM_DEFAULT_MODEL` env → DB query `… content>0 ORDER BY content DESC …`.
- Wizard discovery: `bootstrapper/utils/ollama_discovery.py`, `ollama_library.py`,
  `bootstrapper/ui/textual/widgets/prompt_panel.py` (live `/api/tags`, already-pulled checkmarks).

### 4.2. Capability-metadata usage (what's actually consumed)

Traced field-by-field across consumers (reads, not writes):
- `content` — read by backend + local-deep-researcher (filter `>0`, rank `ORDER BY DESC`).
- `embeddings` — read by weaviate-init (filter `>0`, rank `ORDER BY DESC`).
- `vision`, `structured_content` — **only written** by `sync-catalog.py`; no consumer routes on them.
- `context_window` — only written; defaulted to `0` for Ollama (not even populated).
- `size_gb` — `None` for Ollama; only ComfyUI uses its own `size_gb` (disk warning).
- `api_endpoint` — zero references; legacy. `api_key` for cloud is read from `.env`/`os.environ`, not the table.

Conclusion: the numeric scores did two jobs — **classify** (`>0`) and **rank** (`ORDER BY`). Dedicated
YAML role-sections + list order do both, more legibly, and drop the unused fields.

### 4.3. Why `public.llms` can leave

It is a derived build-time artifact, not a database of record. Removing it requires repointing
five consumers; four already have env-var fallbacks that become the primary (only) path, and the
fifth (litellm-init) re-renders a file either way. The capability-ranked selection becomes a
convention over an ordered list, computed once in the bootstrapper.

---

## 5. Part A — Per-service seed partition (behavior-preserving, guarded)

### 5.1. Layout

Keep everything in `services/supabase/db/scripts/`, same alphabetical execution model, reorganized
into two tiers.

**Tier 1 — core/Supabase (`0x`), stays centralized:** extensions, schemas, auth types, gotrue
migration sync, storage, base role/permission scaffolding, shared `health()` function.

**Tier 2 — per-service vertical slices (`1x+`), each fully owning its objects** in correct
intra-file order (table → grants → triggers → seed → migrations):

| New file | Owner | Absorbs from today |
|---|---|---|
| `10-litellm.sql` | litellm | `llms` table + `update_llms_updated_at` trigger (from `07`) + `05a` llms migrations + its grants |
| `11-comfyui.sql` | comfyui | `comfyui_models/workflows/generations` (from `05`) + workflow seeds (`08`) + extend columns (`12`) + its grants |
| `12-backend-research.sql` | backend / local-deep-researcher | today's `09-research-tables.sql` |
| `13-backend-memory.sql` | backend | today's `10-langmem-tables.sql` + `10a-langmem-migrations.sql` |
| `14-users.sql` | auth / backend | `users` table (from `05`) + its grants |

### 5.2. Guards (required uniformly)

Every object in a Tier-2 slice must be idempotent and defensive:
- `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE … ADD COLUMN IF NOT EXISTS`.
- Triggers/constraints wrapped in `DO $$ … IF NOT EXISTS / IF EXISTS … $$`.
- Grants wrapped in existence checks where the target may be absent.
- Every seed `INSERT … ON CONFLICT DO …`.
- A required header banner per slice: `-- OWNER: <service> — only this service's objects belong here`.

### 5.3. Ordering invariant

Alphabetical execution must yield the same net DDL as today: Tier-1 (`0x`) before Tier-2 (`1x+`);
within a slice, dependencies precede dependents. Vertical slices make this self-evident (each file
is internally ordered), and the `0x`-before-`1x` numbering guarantees infra-before-app.

### 5.4. Sequencing note

Part A partitions **all** current tables (including `llms` and `comfyui_*`) for immediate clean
ownership and standalone value. Part B then deletes the `10-litellm.sql` slice; Part C reworks
`11-comfyui.sql`. If B lands directly behind A, we may skip creating `10-litellm.sql` — decided at
plan time.

---

## 6. Part B — `public.llms` → per-service YAML SoT

### 6.1. The SoT files

`services/ollama/models.yaml` (lives next to the `pull/` init that consumes it):

```yaml
# SoT for Ollama models. Add/remove freely (e.g. bump qwen3.5 → qwen3.6).
# Section = role. Order = priority (first ACTIVE one wins). default: pre-selected/pulled.
content:
  - {name: qwen3.6:latest, default: true}
  - {name: llama3.3:latest}
embeddings:
  - {name: nomic-embed-text, default: true}
  - {name: bge-small-en-v1.5}
vision:
  - {name: llava:latest}
```

`services/litellm/models.yaml` (cloud providers — same shape with a provider layer):

```yaml
openai:
  content:    [{name: gpt-5, default: true}]
  embeddings: [{name: text-embedding-3-large, default: true}]
  vision:     [{name: gpt-5}]
anthropic:
  content:    [{name: claude-opus-4-7, default: true}]
  vision:     [{name: claude-opus-4-7}]
openrouter:
  content:    [{name: meta-llama/llama-3.3-70b-instruct, default: true}]
```

Notes:
- A model may appear in multiple sections (e.g. `gpt-5` is both `content` and `vision`) — explicit and intentional.
- No `vision`/`structured_content` scores, no `context_window`, no `size_gb`, no `api_endpoint` — dropped
  (unused or live-derivable). Size / already-pulled is fetched at wizard time from `ollama_library.py` /
  `ollama_discovery.py` / live `/api/tags`.

### 6.2. Assembly & resolution (bootstrapper, at `./start.sh`)

- `bootstrapper/utils/llm_catalog.py` becomes a **thin loader** that reads both YAMLs and yields the
  dataclasses the rest of the bootstrapper already expects (inverts the SoT: the `.py` reads the YAML).
- `services/env_assembler.py` stitches model env vars into `.env.example`/`.env` like every other manifest
  field: `OLLAMA_USER_MODELS` / `OPENAI_USER_MODELS` / … (defaults from `default: true`).
- A **resolver** emits the three authoritative defaults as plain env vars:
  - `LITELLM_DEFAULT_MODEL` ← best **content**
  - `LITELLM_EMBEDDING_MODEL` ← best **embeddings**
  - `LITELLM_VISION_MODEL` ← best **vision** (may be empty)

  Convention: **"best" = the first *active* model reading the category top-to-bottom across the unified
  candidate list** (Ollama entries, then each enabled cloud provider's entries, in catalog order).
  "Active" = the deployment's `.env` selection, else entries marked `default: true`. This is the floor
  used by non-interactive paths (CLI flags, `--no-tui`, CI).

### 6.3. Wizard

- Model picker options are sourced from the SoT (not `llm_catalog.py`). The Ollama picker keeps live
  `/api/tags` discovery with already-pulled checkmarks.
- **New final consolidation step** (single screen): lists the union of active models across all providers
  and asks the user to choose the default **content**, **embeddings**, and **vision** model. Each is a
  single-select pre-filled with the convention winner; **vision carries an explicit "— none / skip —"**
  option that writes an empty `LITELLM_VISION_MODEL`. Single-candidate categories auto-fill; zero-candidate
  categories surface a warning instead of a picker.
- The step writes `LITELLM_DEFAULT_MODEL` / `LITELLM_EMBEDDING_MODEL` / `LITELLM_VISION_MODEL` to `.env`.
  These **override** the convention (they are the slot backend already honors first). Interactive users
  choose; CLI users leave them empty and get the convention.
- **Validation gate:** if a RAG/embedding consumer (Weaviate, LightRAG, …) is enabled but no embedding
  model is active, the step warns (or requires a pick) instead of failing silently at boot.

### 6.4. Consumer changes (mostly deletions)

| Consumer | Change |
|---|---|
| `litellm-init` (`init.py`) | Render `config.yaml` from the SoT + selection. Role sections decide `ollama` vs `ollama_chat` adapter **deterministically** (removes the name-guess). No DB query. |
| `ollama-pull` (`pull.sh`) | Read `OLLAMA_USER_MODELS` from env; no DB query. |
| `weaviate-init` | Use `LITELLM_EMBEDDING_MODEL` (already its fallback), hard-fallback `nomic-embed-text`; delete DB query. |
| `local-deep-researcher` (`init-config.py`) | Use `LITELLM_DEFAULT_MODEL`/`LOCAL_LLM`; delete DB query. |
| `backend` (`memory_service.py`) | Use `LITELLM_DEFAULT_MODEL`; delete DB query and the asyncpg connection used for model selection. (Backend keeps the DB for memory/research tables.) |

### 6.5. Removals & decommission

- Remove the `llm-catalog-init` service and `services/litellm/catalog-init/scripts/sync-catalog.py`.
- Remove the startup `/api/tags` re-import (the redundant "pass #2") and the host-Ollama drift-reconcile.
- Remove the cloud `/v1/models` init-probe; cloud models come from `litellm/models.yaml` (+ optional
  wizard-time probe, symmetric with Ollama).
- Add a guarded decommission migration: `DROP TABLE IF EXISTS public.llms;` (idempotent) for existing
  volumes. Remove the `10-litellm.sql` slice created in Part A.
- Remove all `depends_on` references to `llm-catalog-init` across compose fragments + topology.

---

## 7. Part C — ComfyUI (roadmap; only if Part B proves out)

Apply the identical pattern to the ComfyUI model catalog:
- New SoT `services/comfyui/models.yaml` (the service already ships a `custom-models.yaml` sidecar,
  so the precedent and the merge logic exist).
- Drop `comfyui-catalog-init`'s DB writes; repoint `comfyui-init` to read the YAML.
- Decommission `public.comfyui_models` (and revisit `comfyui_workflows`) with guarded migrations.
- Note: ComfyUI's `size_gb` *is* used (disk warning) and is retained in its YAML.

This becomes its own spec → plan → PR cycle.

---

## 8. Explicitly NOT touched (load-bearing, correctly locked)

- **Postgres state owners:** n8n (workflows), Open WebUI (chat history), Airflow (DAG metadata),
  JupyterHub (sessions), LiteLLM (logging/keys DB), backend (memory/research tables).
- **Redis state owners:** n8n (BullMQ queue), Kong (rate-limit state), Open WebUI (websockets),
  LiteLLM (cache), JupyterHub (sessions).

These remain exactly as they are. Supabase stays in the locked tier because of the above, not because
of model provisioning.

---

## 9. Test plan

### 9.1. Part A — seed partition

**Execution tests (real Postgres, dockerized fixture — proves they run, in order):**
- **Order + clean-run:** run the full `services/supabase/db/scripts/*.sql` set in alphabetical order;
  assert exit 0 on every script and the ordering invariant (Tier-1 before Tier-2; within a slice,
  table → grants → triggers → seed).
- **Schema-equivalence (golden):** `pg_dump --schema-only` + seed-row `SELECT` snapshot after the new
  scripts must be **byte-identical** to a committed golden captured from the old scripts.
- **Idempotency:** run every script **twice**; assert no error and an identical dump (proves guards work).

**Static lints (fast, no DB):**
- **Ownership lint:** each app table's DDL lives in exactly one owned `1x` file; each slice touches only
  its owner's objects; required header banner present.
- **Guard lint:** every `CREATE TABLE` / `ADD COLUMN` / trigger / grant / seed carries its guard clause.
- **Completeness lint:** the set of objects (tables, columns, constraints, triggers, grants, seed rows)
  across the new files equals the old set — nothing silently dropped or added.

### 9.2. Part B — YAML SoT + assembler

- **Schema validation:** both `models.yaml` files validated against a new JSON schema (sections ∈
  {content, embeddings, vision}; entries require `name`, optional `default`) via the existing jsonschema tooling.
- **Loader:** the rewritten `llm_catalog.py` parses both YAMLs into the expected dataclasses.
- **Assembler-merge:** `env_assembler` reads the Ollama **and** LiteLLM YAMLs together and produces the
  correct `.env.example` lines (`*_USER_MODELS` defaults), byte-equivalence enforced — including the
  combined Ollama+cloud case.
- **Resolver unit tests:** single active → chosen; multiple → first-in-catalog-order; cross-provider union
  ordering; explicit `LITELLM_DEFAULT_MODEL` overrides convention; empty set → empty → consumer fallback;
  per category (content/embeddings/vision); vision skipped → empty.
- **Wizard final-step tests:** candidate list = union of active across providers classified by role;
  pre-selected to convention winner; vision skippable → empty; single candidate auto-filled; "RAG enabled
  but no embedding model" validation fires; writes the three `LITELLM_*_MODEL` vars; selections survive a
  wizard re-run.
- **`litellm-init` render test:** render `config.yaml` from a fixture SoT + selection against the existing
  `bootstrapper/tests/fixtures/rendered_config_baseline.yml`; assert role sections drive `ollama` vs
  `ollama_chat` deterministically.
- **Consumer tests:** backend returns `LITELLM_DEFAULT_MODEL` and opens **no Postgres connection for model
  selection** (unset → raises); weaviate-init uses `LITELLM_EMBEDDING_MODEL` with hard-fallback
  `nomic-embed-text`; ollama-pull pulls exactly `OLLAMA_USER_MODELS`; LDR uses the env path.
- **Decommission tests:** `DROP TABLE IF EXISTS public.llms` guarded + idempotent; manifest/compose
  validators confirm no dangling refs to `llm-catalog-init`; grep-style test asserts zero remaining
  `public.llms` references in consumer code.

### 9.3. Existing tests to REWRITE (not leave failing)

These cover behavior being removed and must be retargeted to the new YAML+env flow:
- `bootstrapper/tests/test_catalog_init_auto_import.py`
- `bootstrapper/tests/test_live_catalog_sync.py`
- `bootstrapper/tests/test_user_model_selections_seam_parity.py`
- `bootstrapper/tests/test_degraded_multiselect_keeps_saved_models.py`
- `bootstrapper/tests/test_wizard_ollama_options.py` (review for DB assumptions)

### 9.4. Mapping to the CI gate (`services-lint`)

- **Manifest lint + unit tests** ← loader, schema, resolver, wizard, consumer, static lints.
- **Compose merge + byte-equivalence + source-permutation matrix** ← assembler byte-equivalence,
  compose/topology integrity post-removal, dockerized seed execution/order/idempotency, source-permutation
  across the new env vars.
- **Docs drift + audit scripts** ← drift gate, new ownership/guard lints, README updates for the partition
  + model SoT.

---

## 10. Sequencing & delivery

Two PRs (Part C is a later, separate cycle), each landing via the standard flow (branch → push →
`gh pr create --base main` → 3 green checks → squash-merge):

1. **PR 1 — Part A:** seed partition + guards + dockerized execution/equivalence tests + static lints.
   Standalone value; behavior-preserving.
2. **PR 2 — Part B:** YAML SoT + loader + assembler/resolver + wizard final step + consumer repointing +
   removals + decommission migration + test rewrites.

The work proceeds on the `model-sot-decoupling` branch (worktrees per sub-task as needed).

## 11. Risks & rollback

- **Part A risk:** an ordering or guard regression silently changes the schema. *Mitigation:* the
  byte-identical golden dump + idempotency (double-run) tests are hard gates; rollback is reverting the PR.
- **Part B risk:** a consumer loses its model because the env var wasn't set. *Mitigation:* the resolver
  always sets the convention default in non-interactive paths; consumer hard-fallbacks (e.g.
  `nomic-embed-text`) remain; the wizard validation gate catches the RAG-without-embeddings case.
- **Existing-volume risk:** `public.llms` lingering on upgraded deployments. *Mitigation:* guarded
  idempotent `DROP TABLE IF EXISTS`.

## 12. Decisions resolved during design

- Partition stays **inside** `services/supabase/db/scripts/`, split per service (not moved into each
  service's `init/`).
- Part A is **behavior-preserving**, proven by a byte-identical schema dump, with uniform guards and
  preserved execution order.
- `public.llms` **leaves** Supabase; SoT becomes per-service YAML (Ollama owns Ollama models; LiteLLM
  owns cloud-provider models). `llm_catalog.py` becomes a YAML loader.
- Metadata is **lean**: role-sections (`content`/`embeddings`/`vision`) + list-order priority +
  `default:` flag. No numeric scores, `context_window`, `size_gb`, or `api_endpoint`.
- **No persisted "discovered" set.** Discovery is transient (wizard-time). The redundant startup
  re-import and host-Ollama drift-reconcile are removed.
- "Best model" = **first active in catalog order** (convention), overridable by the wizard's final
  consolidation step writing explicit `LITELLM_*_MODEL` env vars.
- Selection decides the **set**; catalog order decides **priority**. Selection order is not used.
- **Vision** included as a skippable category; `LITELLM_VISION_MODEL` wired forward-looking (no current consumer).
- ComfyUI (Part C) gets the same treatment after Part B proves out.
