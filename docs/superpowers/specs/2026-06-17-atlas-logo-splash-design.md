# Atlas Logo, Setup-Wizard Splash, and GitHub Identity — Design

Date: 2026-06-17
Status: Approved for planning

## 1. Overview

Add a sci-fi "Atlas holding the globe" logo to the project, rendered as colored
terminal block-art, and use it in two places:

1. **In the setup wizard** as an opening splash that fills the content region,
   holds briefly, then dissolves to reveal the live wizard.
2. **As the GitHub repository identity** — a README hero banner plus derived
   social-preview and avatar assets.

The art is derived from a single source image (a stylized pixel-art Atlas in
blue holding a gold grid-globe over a deep-navy starfield). The image already
aligns with the project palette: the blue figure echoes the blue
`ATLAS-PLATFORM` title gradient, and the gold globe is the warm accent.

### 1.1. Goals

- One reusable rendering pipeline that turns the source image into colored
  terminal art, used by both the app and the GitHub assets.
- A polished, skippable opening splash in the Textual TUI with a graceful
  linear/`--no-tui`/CI fallback.
- A complete, accessible GitHub README hero (image + markdown About covering the
  full service roster).

### 1.2. Non-goals

- No change to the existing `ATLAS-PLATFORM` block-letter lockup itself
  (`block_logo.py` / `banner.py`); the wordmark is reused as-is. The parity test
  (`test_banner_block_logo_parity.py`) stays green.
- No runtime dependency on `chafa` for end users — art is pre-rendered and
  shipped (see §4).
- Uploading the social-preview image and repo avatar in GitHub settings is a
  manual maintainer step (the API/git cannot set these from this work).

## 2. Locked creative decisions

- **Subject / source:** the landscape pixel-art Atlas image (referred to here as
  the master source). Copied into the repo at `assets/atlas-source.png`.
- **Technique:** `chafa` symbol mode using block-element glyphs (quadrant /
  eighth / half blocks) with truecolor foreground+background per cell. This is
  far sharper than naive half-block and reads cleanly because the source is
  stylized with a dark background.
- **Color:** full color from the source (gold globe, blue Atlas, deep-navy
  background). No recolor — it already matches the brand.
- **Crop ("w84"):** sides-only crop, full height retained (globe top through the
  figure's legs/base). Horizontal window `x ∈ [0.101, 0.941]` of the source,
  centered on the globe (globe center measured at `x = 0.521`), full height
  `y ∈ [0.0, 1.0]`. Result is a clean ~3:2 landscape with equal margins around
  the globe. The full figure must never be vertically cropped.
- **Enhancement (pre-chafa):** shadow-deepening gamma curve `1.12` at brightness
  `1.04`, saturation `1.22`, contrast `1.16`. Tuned to punch the gold/navy while
  staying natural.
- **chafa invocation (reference):**
  `chafa -f symbols -c full --symbols block+space --fill block --dither none -s {cols}x{rows}`

## 3. Two hero variants

Both variants derive from the identical chafa render of the w84 crop.

- **App splash hero — image only, no wordmark.** The wizard already renders the
  `ATLAS-PLATFORM` title header above the content region, so the splash hero
  carries no title text.
- **GitHub banner — image + wordmark + one line.** A composed PNG: the hero
  image, the blue `ATLAS-PLATFORM` block wordmark beneath it, and a single
  descriptor line. Two text tiers only (wordmark + body); no redundant
  "platform" repetition.

## 4. Rendering pipeline (shared)

### 4.1. Components

- `assets/atlas-source.png` — committed master source image.
- A maintainer-run **generator** (proposed: `bootstrapper/assets/generate_logo.py`)
  that:
  1. Loads the source, applies the w84 crop and enhancement (§2).
  2. Runs `chafa` at each target width breakpoint.
  3. Parses chafa's ANSI (SGR truecolor fg/bg + block glyph per cell) into a
     compact **cell-grid data file** committed in-repo (proposed:
     `bootstrapper/assets/atlas_hero.<width>.json` or a single packed module).
     Each cell = `(glyph, fg_hex, bg_hex)`.
- An app-side **loader + renderer** that reads the cell-grid for the nearest
  breakpoint ≤ terminal width and paints it.

Rationale for pre-rendering: `chafa` is not guaranteed on end-user machines or
in CI, and runtime image processing is fragile. Pre-rendering to committed data
follows the repo's established "generated artifact in-repo" convention. The
generator is run by maintainers when the source or parameters change.

### 4.2. Width breakpoints

Pre-render at approximately **80 / 100 / 120 / 160** columns. At render time pick
the largest breakpoint that fits the available width; below 80, fall back to the
existing compact title only (no hero) to avoid clipping. Exact breakpoints
finalized during implementation against real terminal sizes.

### 4.3. Textual rendering

A dedicated widget (proposed: `AtlasHero`, sibling to `BlockLogo` in
`bootstrapper/ui/textual/widgets/`) builds one Rich `Text` per grid row, each
cell styled with `Style(color=fg, bgcolor=bg)`, mirroring how `BlockLogo`
already paints gradient rows. Height equals the chosen breakpoint's row count.

## 5. In-app splash + transition

### 5.1. Layout and lifecycle

- The wizard's fixed chrome — the `ATLAS-PLATFORM` title header and the footer
  bar — remain visible throughout.
- On launch, the splash `AtlasHero` fills the **content region** (the same area
  that later hosts wizard prompts, the service-summary table, and the live log
  pane), from under the header to above the footer.
- It holds for **~3 seconds**, then runs the transition (§5.2) into the live
  wizard content.

### 5.2. Transition — pixel dissolve

- The hero overlays the wizard content. A deterministic per-cell dissolve order
  is precomputed; over the animation the overlay progressively replaces hero
  cells with the wizard beneath until fully revealed, then the overlay is
  removed. Coarse frame count (~12–16 steps) to stay smooth in a TUI.
- **Skip:** any key press or mouse press immediately completes the dissolve and
  hands focus to the wizard.
- **Frequency:** plays on every launch.

### 5.3. Accessibility and environment fallbacks

- Reduced-motion preference, non-TTY, CI, or `--no-tui`: **no animation**.
- `--no-splash` CLI flag disables the splash entirely (straight to wizard).
- These compose with the existing `is_tui_capable()` gate in `ui/term_caps.py`.

### 5.4. `--no-tui` linear path

Print the pre-rendered hero (nearest width breakpoint; compact title only below
80 cols) once to stdout, then proceed with the existing linear flow. No hold, no
animation. Mirrors the existing FULL/COMPACT width threshold behavior.

## 6. GitHub identity

### 6.1. Assets (committed under `assets/`)

A productionized compose script (proposed: `bootstrapper/assets/generate_banner.py`)
emits:

- `assets/banner.png` — hero (terminal-art) + `ATLAS-PLATFORM` wordmark + one
  descriptor line, on the dark background. Seam-free wordmark (block rows tiled
  by ink height so the hyphen reads as one solid bar, matching the in-app
  lockup).
- `assets/social-preview.png` — 1280×640, derived from the same hero (centered /
  letterboxed on the dark background).
- `assets/avatar.png` — square, tight crop of the hero for the repo/org avatar.

### 6.2. README hero section

Markdown, placed at the top of `README.md`:

1. Centered `banner.png`.
2. `# Atlas` heading.
3. Centered shields.io badges (License Apache 2.0, Docker Compose, Python 3.10+,
   Services 30+), tinted to the brand blues/green.
4. A bold one-paragraph value proposition.
5. A comprehensive capability table (§6.3).

All About copy lives as **markdown text**, never baked into an image (selectable,
searchable, translatable, screen-reader-friendly).

**Naming constraint:** no third-party project may be credited as the inspiration
for Atlas's name or logo anywhere in public docs, the banner, or commit
messages. The `hermes` service that ships in the stack IS listed normally as a
service wherever applicable — this constraint concerns inspiration credit only,
not the shipped service.

### 6.3. Comprehensive capability table

Covers the full roster (infra / data / llm / media / agents / apps), including
items previously omitted (LightRAG, multi2vec-CLIP, ComfyUI, TTS/STT):

| Capability | Services |
|---|---|
| LLM inference + gateway | Ollama (local, CPU/GPU), LiteLLM (unified gateway), cloud-provider passthroughs, TEI reranker |
| RAG, knowledge + multimodal | Weaviate (vectors), Neo4j (graph), LightRAG, multi2vec-CLIP embeddings |
| Creative AI | ComfyUI (image generation, CPU/GPU) |
| Speech + documents | TTS (Chatterbox), STT (Parakeet / Speaches), Docling (document processing) |
| Agents + automation | n8n (workflows), Airflow (DAGs), Hermes (agent), OpenClaw |
| Chat + apps | Open WebUI, Backend (FastAPI orchestration), Local Deep Researcher |
| Compute + notebooks | Ray, Spark; JupyterHub, Zeppelin |
| Search | SearXNG |
| Data + storage | Supabase (Postgres / auth / storage), MinIO (S3-compatible), Redis |
| Gateway + observability | Kong (API gateway), Prometheus + Grafana |

(Virtual/doc-only roles — cloud-providers, tts-provider, stt-provider,
doc-processor, multi2vec-clip — are represented through the capabilities above.)

## 7. Testing

- **No CI drift gate for the art.** The committed pre-rendered cell-grid and
  banner are the source of truth. They regenerate only when a maintainer
  deliberately changes the source image or the crop/enhance/chafa params, and
  the refreshed output is committed in that same change — there is no realistic
  drift for routine PRs to catch, so CI does not run the generators and `chafa`
  is NOT added to CI. Regeneration is a manual maintainer step.
- **Loader/renderer unit tests (no `chafa`):** breakpoint selection by width;
  grid parses to
  the expected dimensions; the `<80` fallback returns the compact title.
- **Splash logic tests:** skip-on-input completes immediately; reduced-motion /
  non-TTY / `--no-tui` / `--no-splash` paths skip animation; splash never blocks
  a non-interactive run.
- **No regressions:** existing banner/block-logo parity and the broader
  bootstrapper suite stay green.

## 8. Implementation phases

- **Phase A — App:** source asset + rendering pipeline + `AtlasHero` widget +
  splash/pixel-dissolve + `--no-tui` hero + flags + tests.
- **Phase B — GitHub:** banner/social/avatar generator + committed assets +
  README hero section and comprehensive About.

Phases share the rendering pipeline (Phase A builds it; Phase B's banner compose
reuses the same crop/enhance/chafa step). They can be separate implementation
plans, with Phase A first.

## 9. Open items to resolve during planning

- Exact width breakpoints and the `<80`-column fallback details.
- Cell-grid storage format (per-width JSON vs. one packed module) and size.
- Final wording of the README value-proposition paragraph and descriptor line.
- Animation frame count / duration constants, tuned in a real terminal.
