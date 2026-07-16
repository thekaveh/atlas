# Model SoT Migration — Part C (ComfyUI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Mirror Part B for ComfyUI — move the ComfyUI model catalog SoT out of `public.comfyui_models` into per-service YAML (retaining the download metadata `comfyui-init` needs), delete `comfyui-catalog-init`, repoint `comfyui-init` + the backend GET route to a generated manifest, drop the table. Keep `comfyui_workflows` + `comfyui_generations` (runtime app state).

**Architecture:** `services/comfyui/models.yaml` = checked-in curated catalog (replaces `comfyui_library.py`'s hardcoded curated + fallback). The live HF/CivitAI scrape stays at WIZARD time. The wizard persists the user's SELECTED entries (with full download metadata) to a generated manifest (`volumes/comfyui/selected-models.yaml`); for CLI/no-wizard runs the bootstrapper resolves `COMFYUI_USER_MODELS` against the YAML + sidecar into the same manifest. `comfyui-init` (download_models.sh) reads the manifest (not the DB); the backend `GET /comfyui/db/models` reads the manifest (not the DB); the unused `POST/PUT/DELETE` routes are removed. `public.comfyui_models` is dropped.

**Tech Stack:** Python, PyYAML, jsonschema, pytest, Docker Postgres (golden), shell (download_models.sh), FastAPI (backend).

## 1. Global Constraints
- **Keep `comfyui_workflows` + `comfyui_generations`** (runtime state) — only the MODEL catalog moves. `12-comfyui.sql` keeps those two tables; only `comfyui_models` (+ its indexes + the extend block) leaves it.
- **Retain download metadata** in the YAML/manifest: `name, type/category, filename, url/download_url, sha256, target_dir, size_gb, family, requires_custom_node, cpu_supported, min_vram_gb, popularity, source, notes`. `comfyui-init` uses name/category→target_dir/filename/url/sha256; the rest feed the wizard + the backend GET.
- **Only the GET /comfyui/db/models is consumed** (Open WebUI tool `comfyui_image_generation_tool.py`, n8n `comfyui-image-generation.json`) — preserve its response shape. POST/PUT/DELETE have ZERO callers — remove them.
- Live HF/CivitAI scrape stays at wizard time (`assemble_wizard_catalog`).
- Byte-equivalence (`.env.example`), docs-drift, and the Part A seed golden gates stay green (the golden regen in C6 is comfyui_models-only).
- Branch `model-sot-decoupling`, PR #150. NO worktrees; verify branch after each task.

## 2. Task sequence
- **C1** — `services/comfyui/models.yaml` (curated catalog) + `comfyui_library.py` reads it (wizard-preserving). ← detailed below
- **C2** — `comfyui_resolver`: resolve active comfyui entries (YAML + COMFYUI_USER_MODELS + sidecar) → write the generated manifest `volumes/comfyui/selected-models.yaml`; wire the wizard to write the selected entries (with metadata) to it.
- **C3** — repoint `comfyui-init` (download_models.sh) to read the manifest instead of `SELECT ... public.comfyui_models`.
- **C4** — repoint backend `GET /comfyui/db/models` to read the manifest; remove POST/PUT/DELETE routes + dead request models.
- **C5** — delete `comfyui-catalog-init` (service + sync-catalog.py) + compose depends_on + manifest containers + diagrams.
- **C6** — drop `public.comfyui_models`: remove it (+ its indexes + the catalog-extend block + its seed-less parts) from `12-comfyui.sql` (KEEP workflows/generations); add a guarded `DROP TABLE IF EXISTS public.comfyui_models;` decommission migration; regenerate the Part A golden (comfyui_models-only diff); update layout lints.
- **C7** — rewrite/adjust affected tests (`test_comfyui_catalog_*`, `test_comfyui_sidecar`, `test_comfyui_scrapers`, `test_comfyui_curated_and_fallback`).
- **C8** — docs sweep (comfyui README + any comfyui-catalog-init/public.comfyui_models references).

(Each later task fleshed out at dispatch time, grounded in the actual post-previous state.)

---

## 3. Task C1: `services/comfyui/models.yaml` (curated catalog) + loader

**Files:**
- Create: `services/comfyui/models.yaml`
- Create: `bootstrapper/schemas/comfyui-models.schema.json`
- Create: `bootstrapper/tests/fixtures/comfyui_curated_snapshot.json` (characterization snapshot of the CURRENT curated+fallback catalog)
- Create: `bootstrapper/tests/test_comfyui_models_yaml.py`
- Modify: `bootstrapper/utils/comfyui_library.py` (`list_curated()` / `list_fallback()` read the YAML)

**Interface preserved:** `assemble_wizard_catalog()`, `load_custom_models()`, `list_curated()`, `list_fallback()`, the `ComfyUILibraryEntry` dataclass — all keep their signatures. The wizard + (until C5) `comfyui-catalog-init` keep working unchanged.

- [ ] **Step 1: Snapshot the CURRENT curated+fallback catalog (before edits)**
Write a one-off generator importing the current `comfyui_library`, dumping `list_curated() + list_fallback()` entries (every field) to `bootstrapper/tests/fixtures/comfyui_curated_snapshot.json`. Run it, commit the snapshot, delete the generator. (Pins the current catalog so the YAML translation is provably faithful.)

- [ ] **Step 2: Author `bootstrapper/schemas/comfyui-models.schema.json`**
Validate a `{models: [ {name (req), category (req, enum from VALID_CATEGORIES), url|download_url (req), filename, family, size_gb, sha256, target_dir, min_vram_gb, cpu_supported, requires_custom_node:[str], popularity, source, notes} ]}` shape. `additionalProperties:false` on entries.

- [ ] **Step 3: Author `services/comfyui/models.yaml`**
Translate EVERY entry from the current `_CURATED_ENTRIES` + the bundled fallback (`bootstrapper/utils/data/comfyui_catalog_fallback.json`) into the YAML (flat `models:` list), preserving all fields. Header comment explaining: curated catalog SoT; the wizard ALSO live-scrapes HF/CivitAI; user additions go in `custom-models.yaml`.

- [ ] **Step 4: Make `comfyui_library.py` load the YAML**
`list_curated()` (and `list_fallback()` if you fold the fallback JSON into the YAML — or keep the JSON, your call but document it) parse `services/comfyui/models.yaml` into `ComfyUILibraryEntry` objects. Resolve the YAML path host+container (mirror `llm_catalog.py`'s `ATLAS_MODELS_DIR` + candidate paths — the comfyui-catalog-init container bind-mounts `bootstrapper/utils` as `/catalog`, so add the comfyui yaml to that mount in C5/now if catalog-init must see it). Keep `assemble_wizard_catalog()` merging curated(YAML)+scrape+fallback identically.

- [ ] **Step 5: Tests (`test_comfyui_models_yaml.py`)**
- schema validation of `services/comfyui/models.yaml`.
- loader reproduces the snapshot: the YAML-loaded curated+fallback entries equal `comfyui_curated_snapshot.json` (every field).
- `assemble_wizard_catalog()` still returns a non-empty merged catalog (scrape may be empty offline → falls back to curated/fallback).

- [ ] **Step 6: Run + commit**
```bash
cd bootstrapper && uv run pytest tests/test_comfyui_models_yaml.py tests/test_comfyui_curated_and_fallback.py -v
cd bootstrapper && uv run pytest -q
git add services/comfyui/models.yaml bootstrapper/schemas/comfyui-models.schema.json \
        bootstrapper/utils/comfyui_library.py bootstrapper/tests/test_comfyui_models_yaml.py \
        bootstrapper/tests/fixtures/comfyui_curated_snapshot.json
git commit -m "feat(comfyui): curated catalog YAML + comfyui_library loader (Part C1)"
```

## 4. Documentation requirement
`services/comfyui/models.yaml` header + the `comfyui_library.py` module docstring must accurately describe the YAML-as-curated-SoT + the still-live scrape. Per-task: keep docstrings accurate; the full comfyui README sweep is C8.

## 5. Branch hygiene
Commit on `model-sot-decoupling` in the main checkout. No worktrees. `git worktree list` after each commit.
