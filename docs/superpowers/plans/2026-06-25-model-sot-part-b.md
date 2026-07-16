# Model SoT Migration (Part B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the LLM model source-of-truth out of the Postgres `public.llms` table into per-service YAML catalogs (`services/ollama/models.yaml`, `services/litellm/models.yaml`), resolved by a shared module into env vars; delete `llm-catalog-init` + `public.llms`.

**Architecture:** `llm_catalog.py` becomes a loader over the section-based YAML (keeping its exact public contract). A new shared `model_resolver` computes the active model set + ranked content/embeddings/vision picks from the YAML + `.env`; `litellm-init` renders `config.yaml` from it instead of the DB; the other consumers read the resolved `LITELLM_DEFAULT_MODEL` / `LITELLM_EMBEDDING_MODEL` / `LITELLM_VISION_MODEL` env vars (which several already fall back to). A new final wizard step writes those three vars.

**Tech Stack:** Python 3.10+, PyYAML, jsonschema, pytest (`uv run pytest`), Textual (wizard), Docker Postgres (Part A golden regen).

## 1. Global Constraints

- **YAML shape (user-approved):** section-based — top-level role sections `content:` / `embeddings:` / `vision:` (cloud file adds a provider layer first). Each entry: `name` (required), optional `default: true`, optional `description`, optional `badges: [..]`. NO numeric capability scores, NO `context_window`, NO `size_gb` in the YAML. A model may appear in multiple sections (multi-role); the loader merges by `name`.
- **`llm_catalog.py` public contract is preserved exactly:** `CatalogEntry` dataclass (same field names), `CLOUD_CATALOG`, `OLLAMA_DEFAULT_CATALOG`, `cloud_entries(provider)`, `ollama_entries()`, `default_active_names(provider)`, `all_catalog_entries()`. Importers (`wizard/llm_steps.py`, `service_config.py`, container-side dynamic import) must not need changes for the loader swap.
- **Wizard picker stays visually identical** (names, descriptions, badges, default-checks). `CLAUDE.md`: preserve TUI aesthetics; describe any visual change.
- **Cross-context YAML access:** the YAML must be readable both on the host (wizard) and inside the init containers (which bind-mount `bootstrapper/utils` as `/catalog`). The loader resolves the YAML via candidate paths + an `ATLAS_MODELS_DIR` env override; the init-container compose blocks bind-mount the YAML files.
- **`.env.example` byte-equivalence** is enforced by `test_env_assembler.py`; regenerate with `cd bootstrapper && uv run python -m services.env_assembler` whenever a manifest env var changes.
- **Part A golden interaction:** dropping `public.llms` (Task B7) changes the schema, so the Part A `seed_schema_golden.sql` is regenerated and the `test_seed_partition_layout.py` `EXPECTED_OWNER` loses `llms`/`11-litellm.sql`. This is expected (Part B is NOT behavior-preserving for the `llms` table).
- **Branch:** `model-sot-decoupling` (same PR #150). Part C is a separate plan.

---

## 2. Task sequence (each its own implement→review→fix loop)

- **B1** — YAML catalogs + JSON schema + `llm_catalog.py` loader (wizard-preserving). ← detailed below
- **B2** — shared `model_resolver` (active set + best content/embeddings/vision) + emit `LITELLM_DEFAULT_MODEL`/`_EMBEDDING_MODEL`/`_VISION_MODEL` at assembly into `.env.example`.
- **B3** — wizard final consolidation step (content/embeddings/vision defaults; vision skippable) → writes the 3 env vars.
- **B4** — repoint `litellm-init` to render `config.yaml` from the resolver (YAML + `.env`) instead of `public.llms`; keep the rendered config byte-equivalent to `rendered_config_baseline.yml` for the baseline scenario.
- **B5** — repoint `ollama-pull`, `weaviate-init`, `local-deep-researcher`, `backend` off `public.llms` to the resolved env vars / resolver.
- **B6** — delete `llm-catalog-init` (service + `sync-catalog.py`) + the startup `/api/tags` re-import + the host-drift reconcile; remove compose `depends_on` references.
- **B7** — drop `public.llms`: remove `11-litellm.sql`, add a guarded `DROP TABLE IF EXISTS` migration, regenerate the Part A golden, update `EXPECTED_OWNER` in the layout lints.
- **B8** — rewrite the affected tests (`test_catalog_init_auto_import.py`, `test_live_catalog_sync.py`, etc.) to target the YAML+resolver flow.
- **B9** — docs: service READMEs (ollama, litellm), `.env.example` comments, the seed README; drift gates green.

(Each later task is fleshed out to full no-placeholder detail immediately before it is dispatched, grounded in the actual post-previous-task state.)

---

## 3. Task B1: YAML catalogs + loader (wizard-preserving)

**Files:**
- Create: `services/ollama/models.yaml`
- Create: `services/litellm/models.yaml`
- Create: `bootstrapper/schemas/models.schema.json`
- Create: `bootstrapper/tests/fixtures/llm_catalog_snapshot.json` (characterization snapshot of the CURRENT catalog)
- Create: `bootstrapper/tests/test_models_yaml.py` (schema validation + loader equivalence)
- Modify: `bootstrapper/utils/llm_catalog.py` (becomes a YAML loader)
- Modify: `services/litellm/compose.yml` (bind-mount the two YAML files into `llm-catalog-init` and `litellm-init` under `/catalog`)

**Interfaces:**
- Produces (unchanged public contract): `CatalogEntry` (fields: `provider, name, content, structured_content, vision, embeddings, context_window, size_gb, description, default_active, badges`), `CLOUD_CATALOG: list[CatalogEntry]`, `OLLAMA_DEFAULT_CATALOG: list[CatalogEntry]`, `cloud_entries(provider) -> list[CatalogEntry]`, `ollama_entries() -> list[CatalogEntry]`, `default_active_names(provider) -> list[str]`, `all_catalog_entries() -> list[CatalogEntry]`.
- New internal: `load_models_yaml(path) -> list[CatalogEntry]` and a candidate-path resolver honoring `ATLAS_MODELS_DIR`.

- [ ] **Step 1: Capture a characterization snapshot of the CURRENT catalog (before any edit)**

Write a one-off generator that imports the CURRENT `bootstrapper/utils/llm_catalog.py` and dumps, for every entry, the wizard-facing + classification data to `bootstrapper/tests/fixtures/llm_catalog_snapshot.json`:

```python
# run from bootstrapper/:  uv run python -c "exec(open('tests/_gen_catalog_snapshot.py').read())"
# tests/_gen_catalog_snapshot.py
import json, pathlib
from utils import llm_catalog as c

def cap(e):
    # classification only — which capabilities are > 0 (scores themselves are dropped)
    return sorted(k for k in ("content", "embeddings", "vision")
                  if getattr(e, k) > 0)

snap = [
    {"provider": e.provider, "name": e.name, "description": e.description,
     "badges": list(e.badges), "default_active": e.default_active,
     "capabilities": cap(e)}
    for e in c.all_catalog_entries()
]
pathlib.Path("tests/fixtures/llm_catalog_snapshot.json").write_text(
    json.dumps(snap, indent=2, sort_keys=False) + "\n", encoding="utf-8")
print(f"wrote {len(snap)} entries")
```

Run it, commit the snapshot. (This pins the current wizard-facing data so the loader rewrite is provably faithful. Delete `tests/_gen_catalog_snapshot.py` after — it's a one-off; the snapshot is the artifact.)

- [ ] **Step 2: Author the JSON schema**

Create `bootstrapper/schemas/models.schema.json` validating the section shape. The Ollama file is sections at top level; the LiteLLM file is `{provider: {section: [...]}}`. Use a `oneOf` or two schema files — author ONE schema with a top-level `oneOf` (flat-sections vs provider-keyed). Each entry: `{name: str (required), default: bool, description: str, badges: [str]}`, `additionalProperties: false`. Sections ∈ `{content, embeddings, vision}`.

- [ ] **Step 3: Author `services/ollama/models.yaml` and `services/litellm/models.yaml`**

Translate EVERY entry in the current `OLLAMA_DEFAULT_CATALOG` → `services/ollama/models.yaml`, and EVERY entry in the current `CLOUD_CATALOG` → `services/litellm/models.yaml` (grouped by provider). Placement rule: put each model in the section(s) for which its current score is `> 0` (content / embeddings / vision). Carry `name`, `description`, `badges`, and `default: true` (only when `default_active` is true). A multi-role model (e.g. `qwen3.6:latest` with content>0 AND vision>0) appears under both `content:` and `vision:` — put the full `description`/`badges` on its FIRST (content) occurrence; later occurrences may carry just `name`. Validate both files against the schema.

Ollama file skeleton:
```yaml
# services/ollama/models.yaml
# Curated Ollama default catalog (maintainer-editable). Section = role,
# order = priority, default: pre-selected. The wizard also live-scrapes
# ollama.com/library for the full picker; this file is the default trio.
content:
  - name: qwen3.6:latest
    default: true
    description: "Qwen3.6 multimodal — strong content + vision, default content model"
    badges: [default]
embeddings:
  - name: qwen3-embedding:0.6b
    default: true
    description: "Qwen3 embeddings, 0.6B — top of MTEB multilingual leaderboard"
    badges: [default, embeddings]
  - name: nomic-embed-text
    default: true
    description: "Nomic text embeddings — small, fast"
    badges: [default, embeddings]
vision:
  - name: qwen3.6:latest
```

LiteLLM file skeleton:
```yaml
# services/litellm/models.yaml
# Curated cloud-provider catalog. provider → role section → entries.
openai:
  content:
    - name: gpt-5
      default: true
      description: "OpenAI flagship multimodal — strongest content + vision + structured output"
      badges: [flagship]
    # ... every other openai content model from CLOUD_CATALOG ...
  embeddings:
    - name: text-embedding-3-large
      default: true
      description: "OpenAI flagship embedding model — 3072 dims"
      badges: [embeddings]
  vision:
    - name: gpt-5
anthropic:
  content:
    - name: claude-sonnet-4-6
      default: true
      description: "Anthropic mid-tier — 1M context, balanced cost/quality"
  vision:
    - name: claude-sonnet-4-6
openrouter:
  content:
    - name: openrouter/auto
      default: true
      description: "OpenRouter auto-router — picks a backend per request"
      badges: [router]
```

- [ ] **Step 4: Rewrite `bootstrapper/utils/llm_catalog.py` as a loader**

Keep the `CatalogEntry` dataclass EXACTLY as-is (all fields). Replace the hardcoded `CLOUD_CATALOG` / `OLLAMA_DEFAULT_CATALOG` literals with values loaded from the YAML. Loader behavior:
- Resolve the models dir: `os.environ.get("ATLAS_MODELS_DIR")` → else first existing of `[<repo>/services, /catalog]`. Ollama file at `<dir>/ollama/models.yaml` OR `<dir>/ollama-models.yaml` (container-mounted name); LiteLLM at `<dir>/litellm/models.yaml` OR `<dir>/litellm-models.yaml`. Try both names.
- Parse each file; for each section build entries; merge by `(provider, name)` across sections: capability field for a section = a positive integer derived from reverse position within that section (first entry in a section → highest, so list order = priority), so `content`/`embeddings`/`vision` are set per section membership. Set `structured_content = content`. `context_window = 0`, `size_gb = None`. `description`/`badges`/`default_active` from the YAML (first non-empty occurrence). Provider for the ollama file is `"ollama"`; for the litellm file it is the provider key.
- Populate `OLLAMA_DEFAULT_CATALOG` (ollama entries) and `CLOUD_CATALOG` (cloud entries) module globals; keep `cloud_entries`, `ollama_entries`, `default_active_names`, `all_catalog_entries` with identical signatures, now filtering the loaded lists.
- Loud failure if a YAML is missing/unparseable (mirror the current bind-mount error style).

- [ ] **Step 5: Write the loader-equivalence + schema tests**

Create `bootstrapper/tests/test_models_yaml.py`:
- `test_yaml_files_validate_against_schema()` — both YAML files validate against `models.schema.json` (uses the existing jsonschema dep).
- `test_loader_reproduces_catalog_snapshot()` — for every entry the loader yields, the `(provider, name, description, sorted(badges), default_active, sorted(capabilities>0))` tuple set equals the committed `llm_catalog_snapshot.json`. (This proves the wizard-facing data + capability classification is byte-faithful; the dropped numeric scores are intentionally NOT compared.)
- `test_public_functions_intact()` — `default_active_names("ollama")`, `cloud_entries("openai")`, `ollama_entries()`, `all_catalog_entries()` return non-empty and typed `CatalogEntry` with the expected field names.

- [ ] **Step 6: Bind-mount the YAMLs into the init containers**

In `services/litellm/compose.yml`, add to BOTH `llm-catalog-init` and `litellm-init` `volumes:`:
```yaml
      - ../../services/ollama/models.yaml:/catalog/ollama-models.yaml:ro
      - ../../services/litellm/models.yaml:/catalog/litellm-models.yaml:ro
```
The container-side dynamic import of `/catalog/llm_catalog.py` will resolve the YAML via the `/catalog/*-models.yaml` candidate path (loader Step 4). (This wiring is removed for `llm-catalog-init` in B6 and adjusted for `litellm-init` in B4; it exists now so B1 stays behavior-preserving.) NOTE: the rendered-compose baseline (`rendered_config_baseline.yml`, `test_fragment_equivalence.py`) may need regeneration — run that test; if it fails on the new mounts, regenerate the baseline per its instructions and include it.

- [ ] **Step 7: Run the tests**

```bash
cd bootstrapper && uv run pytest tests/test_models_yaml.py -v
cd bootstrapper && uv run pytest -q          # full suite — catch any catalog importer that regressed
```
Expected: new tests pass; full suite green (regenerate `.env.example` / compose baseline only if a test instructs it, and re-run).

- [ ] **Step 8: Commit**

```bash
git add services/ollama/models.yaml services/litellm/models.yaml \
        bootstrapper/schemas/models.schema.json \
        bootstrapper/utils/llm_catalog.py \
        bootstrapper/tests/test_models_yaml.py \
        bootstrapper/tests/fixtures/llm_catalog_snapshot.json \
        services/litellm/compose.yml
git commit -m "feat(litellm): YAML model catalogs + llm_catalog.py loader (Part B1)"
```

---

## 4. Self-Review (B1)
- Snapshot captured from the CURRENT catalog BEFORE the rewrite → faithful equivalence target.
- Loader keeps the exact public contract → no importer changes.
- Wizard-facing data (names/descriptions/badges/default) preserved; only the (about-to-be-removed) numeric scores change.
- YAML readable in both host + container contexts.
