# Interactive Setup Wizard

Atlas includes an interactive Textual TUI wizard that guides you through configuring all services step by step. It launches automatically when you run `./start.sh` with no arguments.

## 1. Quick Start

```bash
./start.sh
```

That's it. The wizard handles everything from there.

## 2. Step Order

The wizard's question order isn't fixed — service-source steps are sorted by each service's resolved port (so the wizard's order matches the stack-overview panel beside it), with the LLM cluster spliced in immediately after the LLM Engine step. The shape is roughly:

```
first  Base port
       Project name (Docker Compose namespace / container family → PROJECT_NAME)
…      Service-source steps, sorted by resolved port
       (ComfyUI, LLM Engine, ollama-related, Weaviate, …)
…      LLM cluster (spliced right after the LLM Engine step):
         Ollama  ·  models               (single unified multiselect)
         Ollama  ·  additional models    (free-text, container only)
         OpenAI key + models
         Anthropic key + models
         OpenRouter key + models
         LLM defaults  ·  chat model      (single-select)
         LLM defaults  ·  embedding model (single-select, dimension-sensitive)
         LLM defaults  ·  vision model    (single-select, skippable)
…      Remaining service-source steps
near-end  Cold start
near-end  Hosts file
last   Confirm — Launch the stack with this configuration?
```

Steps gated by `skip_if_prev` predicates simply vanish from the flow when their precondition isn't met (e.g. each cloud key/model pair only renders when its `CLOUD_*_SOURCE` is `enabled` after the prior secret step; Ollama variant steps only render when `LLM_PROVIDER_SOURCE` is an `ollama-*` value).

## 3. Prompt Kinds

Each wizard step renders one of five prompt widgets, picked based on the question type:

| Kind | Used for | UX |
|---|---|---|
| `options` | Single-select with a small fixed option set — every `*_SOURCE`, the `Cold start` toggle, the `Hosts file` choice, and the three **LLM defaults** pickers (chat / embedding / vision, see §4.6). | Up/Down arrows + Enter; the current `.env` value is pre-highlighted. |
| `number` | Numeric prompts (`Base port`). | Single-line input restricted to digits; range-validated. |
| `secret` | API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`). | Masked password Input + a live char-count hint as you paste. When a key is already set, the hint shows the source-aware action: press Enter to keep the saved key, type a new key to replace, type `clear` + Enter to remove. No sentinel rows are rendered — the input field IS the prompt. |
| `multiselect` | Cloud and Ollama model lists. | `[✓]` / `[ ]` rows in a scrollable viewport (capped height; the cursor follows the selection so a 230-row library scrape stays usable). Space toggles, Enter confirms. **Cloud** multiselect: default-active set (intersected with what your account actually returns) is pre-checked on first visit. **Ollama** multiselect: source-aware — container shows the library only, localhost shows a merged `[pulled]` + `[library]` view. Purely additive; the default-active baseline is baked into `services/ollama/models.yaml` with `default: true` and resolved by `model_resolver` on every `docker compose up`. |
| `text` | Free-text entries — the **Project name** step (Docker Compose namespace, persisted to `PROJECT_NAME`; lower-cased + validated) and the Ollama "additional models to pull" step. | Single-line input; trimmed. The project-name step pre-fills with the current `PROJECT_NAME` and a bare Enter keeps it. |

Throughout: `Up/Down` to move, `Enter` to confirm, `Space` to toggle multiselect rows, `Esc` returns to the previous step, `Ctrl+C` (or `Ctrl+Q`) quits.

## 4. LLM Cluster Steps in Detail

### 4.1 LLM Engine (single-select)

`LLM_PROVIDER_SOURCE` choice — `ollama-container-cpu`, `ollama-container-gpu`, `ollama-localhost`, or `none` (cloud-only). LiteLLM is locked / always-on and is **not** a separate prompt — it's the mandatory front door for every LLM consumer.

The wizard refuses to launch when **LLM Engine = `none`** **and** every cloud provider is `disabled` — that combination would leave LiteLLM with nothing to route to.

### 4.2 Ollama  ·  models (multiselect)

A single unified multi-select shown for every `ollama-*` source. The option list is **source-aware**:

- **`ollama-container-*`** — only the live scrape of `https://ollama.com/library` (~230 entries). Nothing is pulled yet (the in-stack container isn't running at wizard time), so the library is the primary discovery surface. The `ollama-pull` init container fetches checked entries at startup.
- **`ollama-localhost`** — the upstream's `/api/tags` (already-pulled models) merged with the library scrape. Each row carries a status badge: `[pulled]` (on disk on the upstream — checking activates it immediately) or `[library]` (catalog-only — checking saves the name to `OLLAMA_USER_MODELS` in `.env` but you must `ollama pull <name>` on the host yourself so it's available when LiteLLM routes to it).

Each row is 2 cells tall and surfaces:

**Line 1** (cursor + expand-glyph + checkbox + label + capability columns + pull count):
- **Capability badges** — `[embedding]`, `[thinking]`, `[vision]`, `[tools]`, `[audio]`, `[mlx]`. Pulled from each model card's `x-test-capability` spans (and the MLX chip on each model's detail page for the `[mlx]` slot); a row may carry zero, one, or several. Rendered in a fixed canonical column order with reserved per-slot widths so the same tag lands at the same horizontal column across rows — absent capabilities still reserve their slot. Narrow terminals (< 100 cells for parents, < 130 for leaves) fall back to inline variable-width tags so the pull-count column doesn't get pushed off-screen.
- **Status badges** — `[pulled]` / `[library]` / `[default]` plus the `[legacy]` marker for models updated > 365 days ago. Rendered after the capability block with variable width.
- **Pull count** — far right, muted, in `K`/`M`/`B` format (e.g. `114.2M`). Right-aligned to the row width.

**Line 2** (muted, indented):
- **Size variants** — each tag in the form `<param-count> (<approx-GB>)`, joined with `·`. E.g. `llama3.1` → `8b (4.8GB) · 70b (42GB) · 405b (243GB)`. The parameter count is Ollama's canonical tag namespace (what `ollama pull qwen3:8b` matches); the GB figure is approximate Q4 disk footprint via `params × 0.6 bytes/param` (Q4_K_M rule of thumb), real downloads ±10–15%. Once you expand a parent (see below) the detail-page fetcher replaces the approximation with the real per-variant disk size and adds the context window.
- **Hint** — curated description (if the catalog has one for this model) joined with `updated X ago`.

Line 2 wraps to multiple visual rows on narrow terminals when a model has many variants (e.g. `qwen3` has 8 sizes).

**Search box** above the chips: a single-line `Input` (placeholder `Tab or /  to filter models by name…`) that narrows the visible list by case-insensitive substring against the model name. Press **`Tab`**, click into it with the mouse, or press **`/`** to focus it; once focused, type to filter live. The input lights up bold cyan on a tinted background so you can tell at a glance that keystrokes are landing in search and not in the option list. **`Tab`** again, **`Enter`**, or **`Esc`** returns focus to the option list. Up/down still walk the visible rows while you're typing, so you can preview matches without losing your cursor. Every wizard keybinding except those four exits and the arrow keys is temporarily suppressed while the search box has focus, so letters and spaces land in the input as text.

**Filter chips** appear directly below the search box: `Filter  [ALL]  embedding  thinking  vision  tools  audio`. Click a chip — or press **`f`** to cycle them from the keyboard — to narrow the list to that capability. Single-select; click `ALL` (or keep pressing `f` to wrap) to reset. The chip filter and the search box **stack**: a row must match both the active chip AND the search substring to render. Filtering is a view operation only — rows you've already checked stay checked when hidden and reappear when the filter is cleared.

**Ollama Cloud-exclusive entries excluded** — the live listing-page scrape flags models carrying the `cloud` chip that publish no pullable variants (`glm-5`, `minimax-m2`, `kimi-k2`, `deepseek-v4-pro`, …). Those can't be `ollama pull`-ed, so the wizard drops them from the multiselect before render and writes `[info/ollama-fetch] excluded N cloud-only Ollama Cloud model(s)` to the session log. Hybrid entries that publish both cloud and pullable local variants (`gemma3`, `gpt-oss`, `qwen3-coder`, `deepseek-v3.1`, …) stay in the list with their local variants intact.

**Variant picker (in-place tree)** — multi-variant Ollama rows show a `▶` indicator on the left. Press **`Space`** on the parent to expand the tree in place; the variants appear as indented leaves with `└─` connectors directly below. Press `Space` again to collapse. Press `Space` on a leaf to toggle that specific tag. Single-variant rows (`nomic-embed-text`, custom local builds) toggle directly on `Space`. Selections persist to `OLLAMA_USER_MODELS` as `qwen3:8b,qwen3:14b` — `ollama-pull` will fetch each one. The parent's `[✓]` is the aggregate state — green when any leaf is checked. Arrows, Enter, and Esc all keep working naturally; cursor and focus stay in the prompt panel throughout (no popup).

**Rich per-variant data via the detail page** — on the first expand of any parent, the wizard fires an async fetch of `ollama.com/library/{model}` (the model's detail page) and parses its per-variant table. Subsequent expansions of the same parent use the in-memory cache (sub-millisecond). The detail page is much richer than the listing:

- **Real variants** — beyond the param-count tags (`8b`, `70b`) the listing exposes, the detail page lists every Ollama tag the model maker publishes: quantization variants like `27b-coding-mxfp8` and `35b-a3b-mlx-bf16`.
- **Real sizes** — actual disk footprint (`5.2GB`, `523MB`) rather than the Q4 approximation we compute from the param count.
- **Context window** — `40K` / `128K` / `256K` per variant.
- **Per-variant capability tags** — derived from the detail page's `Input` column. A variant whose Input is `Text, Image` gets a `[vision]` badge; `Audio` gets `[audio]`. This means leaves of the same parent can carry different capabilities (e.g., `gemma3:4b` has `[vision]` while `gemma3:270m` doesn't).

While the fetch is in flight, the parent's expansion shows a single `⏳ Fetching variants from ollama.com/library …` splash leaf. On parse failure (network down, upstream HTML changed), the wizard falls back to the listing-page sizes (synthetic `:latest` + param-count tags). The fetch never blocks navigation — arrows, filter chips, and other steps remain responsive throughout.

**Bare ↔ tagged invariant**: per row, `_checked_values` contains either the bare model name (`qwen3` → pulls `:latest`) OR one+ tagged forms (`qwen3:8b`), never both. The synthetic `latest` leaf at the top of every expansion lets you pick the model-maker default explicitly. Toggling a leaf auto-clears any pre-existing bare entry for that parent.

**Sort order**: two buckets, recent first.
1. Models updated within the last 365 days, sorted descending by total pull count.
2. Models older than 365 days (the `[legacy]` bucket), same sort.

This pushes year-old hits like `llama3.1` (114M pulls but updated a year ago) below newer-but-popular models like `deepseek-r1`, `gemma3`, and `qwen3`. The bucket boundary is signalled visually by the `[legacy]` badge and the `updated X ago` annotation in the hint line.

Selections persist as `OLLAMA_USER_MODELS`.

When the library scrape fails (rare), the wizard falls back to the curated default-active baseline in `bootstrapper/utils/llm_catalog.py` (qwen3.6:latest, qwen3-embedding:0.6b, nomic-embed-text). Capability tags and sizes aren't recoverable in fallback (the catalog only carries `embedding` / `vision` flags); the `[legacy]` badge is suppressed because age data is unavailable. When `/api/tags` fails for a localhost source, the merge degrades to library-only with a warning in the session log.

The default-active baseline is baked into `services/ollama/models.yaml` with `default: true` and is always included by `model_resolver` when `OLLAMA_USER_MODELS` is empty, so checking items here is **purely additive** — leaving everything unchecked still leaves the baseline active. Pre-checking behaviour:

- **First visit** (`OLLAMA_USER_MODELS` empty): the wizard pre-checks the default-active baseline (`default_active_names("ollama")` → `qwen3.6:latest`, `qwen3-embedding:0.6b`, `nomic-embed-text`). The user sees the baseline already ticked.
- **Subsequent visit** (`OLLAMA_USER_MODELS` set): the saved selection is restored, intersected with the visible options. Names no longer in the merged list are dropped silently.

### 4.3 Ollama  ·  additional models to pull (text)

Shown only for `ollama-container-*` sources. Free-text comma-separated list, e.g. `mistral:7b,phi4:latest`. Used when an entry isn't surfaced by the library scrape but you still want it pulled at startup. Persists as `OLLAMA_CUSTOM_MODELS`.

### 4.4 Cloud key + model pairs (secret + multiselect)

Each enabled cloud provider gets two consecutive steps:

1. **API key** (`secret` kind). The widget is a masked password Input — no sentinel rows are rendered. When a key is already saved: press **Enter** to keep it, type a new key + Enter to replace it, or type `clear` + Enter to remove it. When no key is saved: type a key + Enter to enable, or press Enter (empty) to leave the provider disabled. The hint line below the input always tells you which action Enter will take.
2. **Models** (`multiselect`). Live fetch from the provider's models endpoint:
   - **OpenAI** — `GET /v1/models` (filtered to the chat / o-series / `text-embedding-3-*` set).
   - **Anthropic** — `GET /v1/models` (Anthropic's documented endpoint).
   - **OpenRouter** — `GET /api/v1/models` (no auth required for the listing — anyone can browse the model catalog). **Enabling OpenRouter as a usable LiteLLM provider still requires `OPENROUTER_API_KEY`** for actual request routing; the listing is a convenience, not a green light to skip the key step.

   The default-active subset of `bootstrapper/utils/llm_catalog.py` is intersected with what your account actually returns; the result is pre-checked. Selections persist as `OPENAI_USER_MODELS`, `ANTHROPIC_USER_MODELS`, `OPENROUTER_USER_MODELS`.

If the live fetch fails (network outage, key rejected, 5xx), the wizard falls back to the curated catalog so you can still proceed; the failure reason appears in the launch log (see [Troubleshooting](troubleshooting.md)).

### 4.5 Splash + cache + back-invalidation

Live fetches run in a background worker so the wizard stays responsive (Esc still works). While the request is in flight, the multiselect renders a single `⏳ Fetching <provider> models…` row (the **fetch splash**). Once data arrives, the splash is replaced with the real options. The fetched list is cached for the lifetime of the wizard process and re-used if you navigate forward and back. Pressing **Esc** to return to a prior step **invalidates** the cache for any provider step at or after the new position AND bumps a generation counter so any in-flight worker that has since become stale silently drops its result instead of polluting the now-empty cache. Re-entry triggers a fresh fetch with the (possibly updated) key.

### 4.6 LLM defaults · chat / embedding / vision (single-select)

After the cloud key/model pairs, the wizard asks you to pick the **default model per role** from everything you just selected (Ollama + cloud). Three consecutive `options` steps, each pre-highlighting the current `.env` value:

1. **Chat / content** → `LITELLM_DEFAULT_MODEL`. The fallback the backend and Open WebUI use when no model is named. Pre-selected to the highest-priority content-capable model in your selection.
2. **Embedding** → `LITELLM_EMBEDDING_MODEL`. ⚠ **Dimension-sensitive.** The backend's `memory_facts` table is a pgvector `vector(768)` column. The picker **auto-matches**: it pre-selects whichever curated embedding model's declared `dim:` (in `services/*/models.yaml`) equals that required 768 — `ollama/nomic-embed-text` by default — so correctness comes from the declared dimension, not list order. Choosing a different-dimension model — e.g. the 1536-dim `qwen3-embedding:0.6b`, or OpenAI's 1536/3072-dim embedders — breaks memory writes unless you migrate that column; the bootstrapper prints a warning at write time when it detects a non-768-dim pick.
3. **Vision** → `LITELLM_VISION_MODEL`. The first option is **— none / skip —**; vision routing is optional, and the step is skipped entirely when no vision-capable model is selected.

All three persist to `.env` and are consumed by `litellm-init` (via `model_resolver`) on the next `docker compose up`. The whole trio is skipped when no LLM provider is active.

## 5. ComfyUI Model Picker

`ComfyUI  ·  models` — a multiselect step parallel to the Ollama
models step, shown for every non-`disabled` `COMFYUI_SOURCE`
(`container-cpu` / `container-gpu` / `localhost`).
The wizard's catalog is sourced from `bootstrapper/utils/comfyui_library.py`,
which merges a live Hugging Face scrape (per-category filters
covering Image, Image-edit, Video, Audio, and 3D models) +
anonymous civitai LoRAs + a curated allowlist + the optional
`services/comfyui/custom-models.yaml` sidecar. The typical assembled
catalog is ~150 entries.

Each row carries:

- **Category chip** — `[image]` / `[image-edit]` / `[video]` /
  `[audio]` / `[3d]` / `[Custom]` for sidecar-YAML entries.
  This is the display-group chip used by the filter row above;
  the actual family-grouping mechanism is the **variant tree**
  described below.
- **Status badges** — `[pulled]` (model file already on disk under
  `services/comfyui/data/<target_dir>/`, only meaningful for
  container modes), `[library]` (catalog-only — not yet downloaded).
- **Capability hints** — `[gpu]` if `min_vram_gb > 0`, `⚠ <node>`
  if the model requires a ComfyUI custom node. For container sources,
  the bootstrapper maps those node names through
  `services/comfyui/custom-nodes.yaml` and writes a pinned
  `active-custom-nodes.tsv` install plan; unknown or unallowlisted
  nodes warn and are not cloned automatically.

**Filter chips** below the search box: `Filter  [ALL]  image  image-edit  video  audio  3d`.
Press **`f`** to cycle the chips from the keyboard (or click). The
chip filter and the search box stack — a row must match both the
active chip AND the search substring to render.

**Search box** above the chips, behaving identically to the
Ollama picker's search: **`Tab`** or **`/`** to focus, type to
narrow, **`Tab`** / **`Enter`** / **`Esc`** to return focus to the
option list.

**Variant picker (in-place tree)** — Hugging Face entries sharing a
leading-letters family root collapse into one expandable parent row
mirroring Ollama's `qwen3 · 8b / 14b / 32b` UX. A row like
`▶ TRELLIS  ·  6 variants` represents all `microsoft--TRELLIS-*`
plus `gqk--TRELLIS-*` repositories. Press **`Space`** on the parent
to expand the tree in place; variants appear as indented leaves
with `└─` connectors directly below, each toggleable independently
via **`Space`** on the leaf. Press **`Space`** on the parent again
to collapse. The parent's checkbox is an aggregate — green when any
leaf is checked. Selections persist as full repository names in
`COMFYUI_USER_MODELS`; the synthetic family-root token (e.g.
`family:TRELLIS`) never leaves the wizard. Families of one HF
entry stay flat, as do civitai numeric IDs, the curated allowlist,
and sidecar entries.

**Catalog load latency** — Hugging Face's list endpoint returns
each model card without sibling file sizes, so the wizard fires
one per-repo `/api/models/{id}?blobs=true` follow-up call to
populate real `size_gb` values. These calls fan out across a
ThreadPoolExecutor of 10 workers (HF rate-limits at higher
concurrency); total cost is **~10–15 s** of extra wizard load on
first open for the ~130-entry HF tier. Per-repo failures are
silent; the affected row keeps `0.00 GB` and the rest of the
catalog still loads.

**Source-aware behaviour** — the picker fires for all non-`disabled`
ComfyUI sources, but the downstream init pipeline branches:

- **`container-cpu` / `container-gpu`** — at bootstrapper start,
  `comfyui_resolver` computes the active set from `COMFYUI_USER_MODELS`
  + `services/comfyui/custom-models.yaml` and writes
  `volumes/comfyui/selected-models.yaml` (manifest) and
  `volumes/comfyui/active-models.tsv`. It also maps required custom
  nodes through `services/comfyui/custom-nodes.yaml` and writes
  `volumes/comfyui/active-custom-nodes.tsv` (all three are gitignored
  runtime artifacts — regenerated every start, never committed).
  `comfyui-init` then
  downloads each model in the model TSV into the `comfyui-models`
  volume via wget. The AI-Dock provisioning hook in the main ComfyUI
  container clones each allowlisted custom-node row into the
  `comfyui-custom-nodes` volume at the pinned commit SHA and installs
  its requirements when enabled.
  Selections persist to `COMFYUI_USER_MODELS` in `.env`.
- **`localhost`** — `comfyui-init` is scaled to 0 (the download
  container would write into a path the host ComfyUI doesn't read), but
  the bootstrapper still writes the manifest so the backend
  `/comfyui/db/models` endpoint that Open WebUI and n8n consume can
  serve the active set. You populate your host ComfyUI install's
  `models/<target_dir>/` directory yourself, same as
  `ollama pull <name>` for an Ollama localhost upstream.

Selection persists as `COMFYUI_USER_MODELS` (comma-separated
catalog names) in `.env`. CLI flag `--comfyui-models` accepts the
same CSV. CLI flag `--comfyui-custom-models-file PATH` overrides
the default sidecar YAML location.

**Multi-file bundles** — a single catalog name can represent a bundle
declared with `files:` in `services/comfyui/models.yaml` or
`services/comfyui/custom-models.yaml`. Selecting that one logical row writes
one manifest entry per file and one TSV download row per file. Each file can
declare its own category, filename, SHA256, target directory, precision, and
variant, so Krea-style model sets can stage diffusion weights into
`diffusion_models/`, text encoders into `text_encoders/`, and VAEs into
`vae/` without asking the user to select three separate rows. Mesh/3D bundles
can also route loader-specific weights to `checkpoints/` while remaining
grouped under one catalog selection.

When the upstream HF / civitai scrape fails (rare), the wizard
falls back to the bundled allowlist via
`bootstrapper/utils/comfyui_library.py::list_fallback()`. The
fallback path emits a session-log warning but the wizard remains
usable. Note: the fallback only triggers when BOTH scrapers raise
network exceptions; an HF response of `200 OK` with zero parseable
entries is not treated as a fallback trigger.

## 6. Inline secondary numeric inputs

Three service rows mount an inline numeric input alongside the source prompt
via the `SecondaryNumberInput` widget (see `ui.textual.widgets.prompt_panel`).
Selections persist as a sibling env var:

| Row | Env var | Default | Range | Visible when |
|---|---|---|---|---|
| Ray | `RAY_WORKER_COUNT` | `2` | 0..(no upper cap) | `ray-container-cpu`, `ray-container-gpu` |
| Spark | `SPARK_WORKER_COUNT` | `2` | 1..8 | `container` |
| Prometheus | `PROMETHEUS_RETENTION_DAYS` | `7` | 1..365 | `container` |

The input renders directly on the source step — no follow-up cascade — so
the user picks both a source and a numeric refinement in one keystroke
sequence. Adding a Prometheus-style manifest-driven inline input requires only
a `secondary_number` block on the relevant `rows[]` entry in `service.yml` (the
schema field is documented in `docs/CONTRIBUTING-services.md`); the Ray and
Spark worker-count inputs are wired directly in the wizard code
(`bootstrapper/ui/textual/integration.py`).

## 7. Stack Options

The wizard also collects these stack-level (non-service-source) options — **base port first**, before any service-source prompts; the cold-start and hosts-file options come last:

- **Base port** for all services (default: 63000) — collected at the very start of the wizard so all subsequent port displays reflect the chosen base.
- **Cold start** option to remove volumes and rebuild from scratch.
- **Hosts file configuration** to enable friendly URLs like `chat.localhost` and `n8n.localhost`.

## 8. Pre-Launch Summary

Before launching, a configuration summary inside the same anchored info-box shows:

- Every service with its selected source, alias (when hosts are configured), and direct port.
- Hosted endpoints (e.g., `chat.localhost:63000`) if hosts file entries are configured.
- A separate **Cloud APIs** sub-section lists OpenAI / Anthropic / OpenRouter status (`enabled · key set ✓`, `disabled`, `enabled · key MISSING ⚠`). Cloud providers don't run as containers, so they render below the services grid rather than alongside real services.
- Color-coded source choices (container = green, localhost / cloud = cyan, off = slate).

You confirm to launch (the **Launch the stack with this configuration?** step is the wizard's final question), or cancel to exit without changes.

## 9. Streaming Logs

After confirmation, the wizard transitions in-place from prompts to the launch phase:

- The brand panel and pre-launch summary stay **pinned** at the top — they never move while logs flow.
- Below them, a bordered **Logs** pane streams `docker compose` build / up / port-verify / `logs -f` output, line-by-line.
- Per-service container names (e.g. `atlas-supabase-db`, `atlas-ollama-pull`) are **color-coded** based on `bootstrapper/ui/textual/palette.py::SOURCE_COLORS`. Unknown service names get a stable hue from a small md5-based palette so every service in the stack remains visually distinguishable.
- The full launch-phase output is also tee'd to `/tmp/atlas-launch-<timestamp>.log` for post-mortem inspection. See [Troubleshooting](troubleshooting.md#2-session-log).
- Press `Ctrl+Q` to detach cleanly from the wizard UI. `Ctrl+C` sends SIGINT — fine after services are up (already-detached compose containers keep running) but during the launch pipeline it may interrupt a compose step mid-flight, leaving the stack in a partial state. Either way, services that have finished starting keep running; resume log streaming with `docker compose logs -f <service>`.

## 10. Navigation

| Key | Action |
|-----|--------|
| `Up/Down` | Navigate between options or rows |
| `Space` | Toggle a row in a multiselect |
| `Enter` | Confirm the current selection |
| `Esc` | Return to the previous step (and from the first step, exit) |
| `Ctrl+Q` | Quit the wizard |

## 11. Progress Tracking

A progress bar at the top of each screen shows how far you are through the configuration process. It starts at 0% and reaches 100% after all steps (services + stack options) are completed.

## 12. When to Use the Wizard vs CLI Flags

| Scenario | Approach |
|----------|----------|
| First time setting up the stack | Wizard (`./start.sh`) |
| Exploring available service options | Wizard |
| Changing one or two services | CLI flags (`./start.sh --llm-provider-source ollama-localhost`) |
| CI/CD or scripted deployments | CLI flags or `.env` file |
| Repeating a previous configuration | CLI flags (copy from wizard's command preview) |

## 13. Relationship to .env and CLI Flags

The wizard reads your current `.env` values as defaults and produces the same `--*-source` overrides that CLI flags would. After confirmation, these overrides are applied to `.env` and the stack launches normally.

- **Wizard selections are persistent** in `.env` and carry over to future runs
- **CLI flags always skip the wizard** and apply directly
- **Any flag** (including `--cold`, `--base-port`, etc.) skips the wizard

## 14. Requirements

The TUI uses two Python libraries — both included in `bootstrapper/pyproject.toml`:

- **textual** — owns the wizard prompts and the post-confirm launch phase (pinned summary + log pane + filter chips), all hosted in a single Textual app.
- **rich** — used for styled spans inside Textual widgets and for the `--no-tui` linear pre-launch summary table.

Python ≥ 3.10 is required (see `bootstrapper/pyproject.toml`). The wizard automatically falls back to the linear stdout flow when `stdin` isn't a TTY, when the terminal is too small to host the Textual app, or when the user passes `--no-tui`. In that mode `./start.sh` prints a pre-launch summary table and streams docker compose output directly.

## 15. Brand Customization

The metadata on the pinned info-box's border (brand name, tagline, version, author, author email, license, repo URL) is overridable via `BRAND_*` environment variables. Defaults are Atlas's identity; forks can rebrand the wizard by editing the `BRAND_*` block in `.env`:

```
BRAND_NAME=Atlas
BRAND_TAGLINE=A self-hosted, source-configurable, multi-disciplinary engineering platform — gen-AI, ML, and data.
BRAND_VERSION=0.1.0
BRAND_AUTHOR=Kaveh Razavi
BRAND_AUTHOR_EMAIL=kaveh.razavi@gmail.com
BRAND_LICENSE=Apache License 2.0
BRAND_REPO_URL=https://github.com/thekaveh/atlas
BRAND_LOGO_FILE=
```

Empty values fall back to the canonical defaults (encoded in `bootstrapper/ui/state.py::AppState`). See `.env.example` for the latest documented block.

### 15.1 Block-art logo (`BRAND_LOGO_FILE`)

The big ASCII block-art lockup — shown in the wizard's brand panel and the `--no-tui` startup banner — defaults to the built-in **ATLAS** art (an [ANSI-Shadow](https://patorjk.com/software/taag/#p=display&f=ANSI%20Shadow) figlet lockup). Point `BRAND_LOGO_FILE` at a text file to override it; leave it empty to keep ATLAS. Both render surfaces read the same file (`bootstrapper/utils/brand_logo.py`), so the override stays in parity across the TUI and the linear banner.

**File format** — the *wide* lockup rows, an optional `---` line on its own, then the *narrow* (compact) fallback used on terminals too small for the wide one:

```
 ██████╗  █████╗  ██████╗
 ██╔══██╗██╔══██╗██╔════╝
 ██████╔╝███████║██║  ███╗
 ██╔══██╗██╔══██║██║   ██║
 ██║  ██║██║  ██║╚██████╔╝
 ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝
---
(optional narrower variant here)
```

- Keep the art **6 rows tall** (figlet's ANSI-Shadow output already is, regardless of word length) so it fits the brand panel.
- Provide the compact section for long names — without a `---` separator the single block is reused at every width and will clip on narrow terminals.
- Generate matching art with any figlet tool, e.g. `figlet -f "ANSI Shadow" "My Brand"` (or [patorjk.com/software/taag](https://patorjk.com/software/taag/)).
- Leading/trailing blank lines are trimmed; everything else renders verbatim with the same top→bottom blue gradient as ATLAS.

> The richer image-derived **splash** (the globe hero in `atlas_hero.py`, generated from a source image by `scripts/generate_logo.py`) is a separate asset and is **not** covered by `BRAND_LOGO_FILE` — it stays the Atlas hero unless you regenerate those grids.

## 16. Configurable Services

The wizard automatically discovers all configurable services from each `services/<name>/service.yml` manifest. The table below is a representative subset (it does not enumerate every track service — e.g. MLflow, Label Studio, Verba, Langfuse, LLM Graph Builder, Jenkins, Celery, MCP Servers, Iceberg REST, Trino, Redpanda, Tika are also wizard-prompted per `bootstrapper/tracks.yml`); run `./start.sh --list-tracks` or see [Source configuration](../deployment/source-configuration.md) for the complete, current set. Representative entries:

| Service | Options |
|---------|---------|
| LiteLLM Gateway | locked / always-on (no choice; mandatory front door for every LLM consumer) |
| LLM Engine (Ollama upstream) | ollama-container-cpu, ollama-container-gpu, ollama-localhost, none |
| ComfyUI | container-cpu, container-gpu, localhost, disabled |
| Weaviate | container, localhost, disabled |
| Multi2Vec CLIP | container-cpu, container-gpu, disabled |
| Neo4j Graph DB | container, localhost, disabled |
| STT Provider | speaches-container-cpu, speaches-container-gpu, parakeet-container-gpu, parakeet-localhost, whisper-cpp-localhost, disabled |
| TTS Provider | speaches-container-cpu, speaches-container-gpu, chatterbox-container-gpu, chatterbox-localhost, disabled |
| Document Processor (Docling) | docling-container-gpu, docling-localhost, disabled |
| OpenClaw | container, localhost, disabled |
| Hermes Agent | container, localhost, disabled |
| n8n | container, disabled |
| SearxNG | container, disabled |
| Crawl4AI | container, disabled |
| JupyterHub | container, disabled |
| LightRAG | container, localhost, disabled |
| Ray | ray-container-cpu, ray-container-gpu, disabled (with inline `RAY_WORKER_COUNT` input on container variants) |
| Spark cluster | container, disabled (with inline `SPARK_WORKER_COUNT` input on `container`, default 2, range 1..8) |
| Zeppelin | container, disabled (requires Spark — `ZEPPELIN_SOURCE=container` with `SPARK_SOURCE=disabled` errors at bootstrap) |
| Airflow | container, disabled |
| TEI Reranker | container-cpu, container-gpu, localhost, disabled |
| Prometheus | container, disabled (with inline `PROMETHEUS_RETENTION_DAYS` input on `container`, default 7, range 1..365) |
| Grafana | container, disabled |
| Open WebUI | container, disabled |
| MinIO Console | container, disabled |
| Local Deep Researcher | container, disabled |

### 16.1 Cloud LLM providers (not auto-discovered)

OpenAI, Anthropic, and OpenRouter are **not** regular services — they don't run as containers (`scale: 0` in the `services/cloud-providers/service.yml` virtual manifest). Instead, the wizard injects them via `bootstrapper/wizard/llm_steps.py:build_cloud_steps` as bespoke (secret + multiselect) pairs spliced after the LLM Engine step:

| API | Key var | Wizard step |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `OpenAI Cloud  ·  API key` then `OpenAI Cloud  ·  models` |
| Anthropic | `ANTHROPIC_API_KEY` | `Anthropic Cloud  ·  API key` then `Anthropic Cloud  ·  models` |
| OpenRouter | `OPENROUTER_API_KEY` | `OpenRouter Cloud  ·  API key` then `OpenRouter Cloud  ·  models` |

Source toggles are persisted as `CLOUD_OPENAI_SOURCE` / `CLOUD_ANTHROPIC_SOURCE` / `CLOUD_OPENROUTER_SOURCE` (`enabled` / `disabled`). They render in the **Cloud APIs** sub-section of the stack overview, separate from the services grid.

New services added under `services/<name>/` with a `service.yml` manifest (and included in `docker-compose.yml`'s `include:` list) are automatically picked up by the wizard.

## 17. Dependency Validation

The wizard validates service dependencies in real time. For example, if you enable n8n but disable Weaviate (which n8n requires), the wizard warns you and offers to either enable the dependency or disable the dependent service. The same machinery enforces the "LiteLLM must have an upstream" rule (LLM Engine != `none`, or at least one cloud provider is `enabled`).

## 18. Hosts File Setup

The hosts file configuration step enables friendly URLs routed through Kong API Gateway:

| Option | Behavior |
|--------|----------|
| **Default** | Checks `/etc/hosts` for required entries, warns if missing |
| **Setup hosts now** | Adds entries to `/etc/hosts` (requires `sudo`) |
| **Skip** | No hosts check, use `localhost:PORT` URLs only |

When hosts are configured, the pre-launch summary table shows both the direct `localhost:PORT` URL and the friendly `service.localhost:KONG_PORT` URL for applicable services.
