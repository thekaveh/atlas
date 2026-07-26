# Documentation Overhaul & .io Site Reskin — Design

- **Date:** 2026-07-26
- **Status:** Draft (awaiting review)
- **Owner:** Kaveh Razavi
- **Supersedes / builds on:** `2026-07-04-atlas-docs-site-wiki-revamp-design.md`, `2026-07-13-three-surface-docs-remediation-design.md` (the three-surface pipeline they established is sound and is kept; this effort layers information-architecture, content-quality, and visual work on top).

---

## 1. Summary

Atlas already has a disciplined, manifest-driven three-surface documentation system (in-repo Markdown · `.io` MkDocs site · GitHub wiki) whose *synchronisation is enforced by construction*. The problems are not in the plumbing — they are in **information architecture** (a 659-line root README acting as a competing mini-site, the same facts told in 3–4 places, folders that no longer map to nav sections, ~40% of files unnavigated), in **content quality** (diagram-narrating prose, a copy-pasted block on 9 pages, three marketing-tone READMEs, a few ungrounded facts), and most visibly in the **`.io` site's look** (stock Material for MkDocs, dark-first, no logo/favicon/custom font — it reads as lackluster next to the reference the owner set, `https://dspy.ai`).

This effort is a single, comprehensive overhaul delivered in two phases — **Foundation first (structure · content · sync), then the visual reskin** — with every change routed through the existing single source of truth so all three surfaces stay in sync automatically.

## 2. Goals

1. A coherent, non-duplicated information architecture with one canonical home per fact.
2. A content corpus free of the owner's named defects (stating-the-obvious, diagram-narration, filler, redundancy, stale/ungrounded claims) — verified, not asserted — and an automated gate so it cannot regress.
3. All three surfaces provably in sync from one source (verify the existing mechanism covers everything; close residual content drift).
4. A `.io` site reskinned to a clean, professional, dspy.ai-level standard: light-first "Clean Systems" identity, dual light/dark themes given equal care, refined typography, brand mark/favicon, and a real landing page.

## 3. Non-goals

- Rebuilding the generation pipeline or sync mechanism (it works — see §4.1). No new SSG, no migration off MkDocs Material.
- Rewriting the already-clinical service READMEs that pass the audit (the newer corpus is strong; we touch only what's defective).
- Changing per-service `service.yml` manifests, compose, or any runtime behaviour. This is documentation + site theming only.
- Publishing the archival trees (research/ · superpowers/ · strategy/) to the public site.

## 4. Current state (grounded)

Findings below are from a four-part read of the `develop` tree; file references are exact.

### 4.1. Pipeline & single source of truth — **healthy, keep it**
- **Single source:** committed Markdown enumerated by `docs/manifest.yaml` (110 canonical pages) — `docs/**`, root `README.md`, and `services/*/README.md`. Per-service content itself derives from `services/*/service.yml`.
- **Two generation layers:** Layer A (`scripts/docs/canonical_references.py` + `bootstrapper/docs/sitegen/{model,pages,services}.py`) back-generates manifest-derived pages into the committed `docs/` tree; Layer B (`scripts/docs/build_docs.py`) projects the committed source into `generated/site/` (MkDocs input) and `generated/wiki/`, and emits `mkdocs.yml`. Chained by the `Makefile` (`docs-build`, `docs-check`).
- **`mkdocs.yml` is generated + gitignored** (`.gitignore:8-10`); nav is a pure function of the manifest (`build_docs._nav_entries`). Structure changes happen in `docs/manifest.yaml`; theme changes in `build_docs.render_mkdocs_yml`.
- **Sync enforced by construction:** SHA-256 determinism check (`build_docs._assert_dirs_equal`), canonical-drift gate (`canonical_references.sync_canonical_references`, surfaced by `check_docs`), cross-surface link forbiddance / self-containment (`links.is_forbidden`, `check_docs.check_self_containment`), completeness + placeholder gates, and diagram-PNG fingerprint staleness (`render_diagrams`, PNG `tEXt AtlasSourceSHA256`).
- **Deploy:** `.github/workflows/docs-pages.yml` — push to `main` on doc paths → `make docs-check` → Pages artifact deploy; a follow-on job pushes the wiki to branch `master` via `push_wiki.py`.

### 4.2. Information architecture — disciplined but three real problems
The manifest imposes a clean 10-bucket taxonomy (Overview · Quick Start · Core Concepts · Tracks · Services · Architecture · Configuration · Operations · Development · Reference). Problems:
1. **Root `README.md` (659 lines) is a parallel mini-site** with its own 1–12 numbering, re-telling quick-start, overview, the SOURCE system, service access, and reuse. Largest single duplication surface. Contains a generated `<!-- TOPOLOGY:BEGIN/END -->` table (`README.md:259-325`).
2. **Four-way fact duplication:** ports/routes live in the README TOPOLOGY block, `docs/reference/ports-routes.md`, `docs/deployment/ports-and-routes.md`, and each service README §2; SOURCE values in README §3.4, `docs/reference/source-values.md`, `docs/architecture/source-configuration-model.md`, `docs/core-concepts.md`; tracks in `docs/tracks.md`, `docs/reference/tracks.md`, README §1.5.
3. **Folder ↔ section drift + thin pages + oversized files:** `docs/deployment/` files are scattered across nav §7 and §8; stub pages (`docs/tracks.md` 13 lines, `docs/configuration.md` 13 lines, `docs/reference/index.md` 22 lines); `docs/CHANGELOG.md` (2831 lines) and `docs/ROADMAP.md` (1416) sit inside Development; ~100 markdown files under research/ (64) · superpowers/ (33) · strategy/ (7) are outside the nav entirely (exempted in `check_docs`).

### 4.3. Content quality — strong corpus, localized defects
The newer service READMEs are clinical and fact-checked. Defects concentrate in:
- **Diagram-narration / stating-the-obvious:** the 11 architecture pages' "How To Read This View" sections restate the SVG (e.g. `docs/architecture/platform-overview.md:11`, `network-routing-topology.md:11`); two root-README image captions narrate pixel-by-pixel (`README.md:11`, `README.md:15`); `docs/diagrams/README.md:36-40` documents cosmetic production detail ("same JetBrains Mono, same slate-950 background").
- **Redundancy:** an identical "Source Files" block pasted across ~9–11 architecture pages (e.g. `docs/architecture/platform-overview.md:15-18`) — redundant and partly ungrounded (claims unrelated views derive from the same two files); a config sentence duplicated across weaviate/ollama READMEs; the Ollama picker explanation told three times.
- **Ungrounded / stale:** `services/ollama/README.md:85` says `default_active: true` but the real manifest key is `default:` (`services/ollama/models.yaml:12`); `README.md:528` "2,800+ tests" vs. an actual `def test_` count ~2,351 (unverified/rounded); Ollama library size drifts "a few hundred" (`README.md:108`) vs "~230" (`services/ollama/README.md:56`, `docs/quick-start/interactive-setup-wizard.md:65`).
- **Tone:** kong / supabase / doc-processor READMEs use marketing register ("intelligent API gateway", "AI-powered") against an otherwise clinical corpus — a house-style consistency gap.
- **Cross-surface leakage:** essentially clean (only the standard Material `repo_url` header link; no genuine in-repo→site/wiki leaks in user-facing pages).

### 4.4. The `.io` site — stock Material, big headroom
- Theme: Material for MkDocs, **dark-first** (`slate` + `default` schemes), `primary`/`accent: custom` driven by CSS variables in `docs/assets/stylesheets/atlas.css` (~3 KB "space" palette). **No logo, no favicon, no custom font, no `overrides/` template dir.** Homepage is a bespoke `docs/index.md` hero (poster + wizard screenshot) using `.atlas-home*` CSS.
- Reskin levers, all in three places: `build_docs.render_mkdocs_yml` (theme config), `docs/assets/stylesheets/atlas.css` (the visual system), and `docs/index.md` (+ a new `overrides/` dir + brand assets).
- Legacy to avoid disturbing: `bootstrapper/docs/sitegen/{theme,wiki,rendering}.py` and `scripts/*-docs-*.py` shims are dormant; the active pipeline is `scripts/docs/*` + `make`.

## 5. Decisions (with rationale)

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Foundation first, then reskin.** One spec, two phases. | Reskin styles a stable structure once; avoids re-styling pages that later move/merge. |
| D2 | **Root README → ~150–200-line landing.** | Kills the biggest duplication surface; keeps a strong GitHub front door (pitch + quick-start + generated topology + links). |
| D3 | **Archival trees stay internal + get an index.** | research/ is living reference, superpowers/ & strategy/ are historical; they don't belong on the public site but need a reader path. |
| D4 | **Full content pass + an automated lint gate.** | Fix everything now and make the defect classes un-reintroducible via CI. |
| D5 | **Diagram prose → non-obvious insight only.** | Directly implements the owner's rule: never restate in prose what the image shows. |
| D6 | **Reskin = "Clean Systems", light-first, pure.** | The straight dspy.ai-level treatment: airy, neutral, one calm blue accent, minimal chrome; best for long-lived reference docs. |
| D7 | **Rich, dspy-style landing page.** | The landing is the signature page and the biggest visible win. |
| D8 | **Full brand polish:** refined self-hosted font pairing + Atlas logomark + favicon. | Required to genuinely reach the reference's level; the site currently ships none of these. |

## 6. Phase 1 — Foundation

### 6.1. Information architecture
- **Shrink `README.md`** to a landing: one-paragraph pitch, a 30-second quick-start, the generated `TOPOLOGY` table, a short "what's inside / where to go" link block into the docs. Remove the 1–12 numbering and every section that merely restates a canonical page. Move any genuinely unique prose into the matching canonical page first (no information loss).
- **One home per duplicated fact:** designate the generated `docs/reference/*` pages as canonical for ports/routes, SOURCE values, tracks, service-dependencies, env-vars, manifest-fields. Replace narrative copies elsewhere with a one-line summary + link. Keep only generated tables (they can't drift); delete hand-maintained twins.
- **Folder ↔ section coherence:** re-home `docs/deployment/*` so the physical folder predicts its nav section, or split into `docs/configuration/` + `docs/operations/` to match §7/§8. Enrich or merge the stub pages. Relocate `CHANGELOG.md`/`ROADMAP.md` out of the reader-facing Development bucket (e.g. to repo root or a clearly-marked "Project" area) so they stop dominating the nav.
- **Archival index:** add an in-repo `docs/internal/README.md` (or extend `docs/README.md`) that indexes research/ · superpowers/ · strategy/ with one line each; keep them `check_docs`-exempt and out of the site nav.
- All of the above is expressed by editing `docs/manifest.yaml` (nav/numbering) + moving/rewriting the source files; `make docs-build` re-projects all surfaces.

### 6.2. Content-quality full pass
- **Diagram-narration:** rewrite each architecture page's "How To Read This View" to keep only what the diagram cannot convey (rationale, constraints, gotchas, cross-refs); drop the section where nothing non-obvious remains. Delete the two narrating README captions and the cosmetic production prose in `docs/diagrams/README.md`.
- **De-duplication:** replace the pasted "Source Files" block with either a per-view accurate short list or a generated block; collapse the tripled Ollama-picker explanation to one canonical location + links; remove the duplicated config sentence.
- **Tone:** rewrite kong / supabase / doc-processor READMEs into the clinical house style used by backend/litellm/comfyui.
- **Ungrounded facts:** `default_active`→`default`; reconcile the Ollama count to one verified number; replace "2,800+ tests" with a verified figure or a non-numeric phrasing.
- **Method:** every change is fact-checked against the tree; the pass is exhaustive (all nav pages + all service READMEs), not sampled.

### 6.3. Content-lint gate
- Extend `scripts/docs/heading_quality.py` (or a sibling `content_quality.py`) into a gate wired into `make docs-check` and `docs-pages.yml`. Rules (all with an allowlist/inline-suppress for rare legitimate cases):
  - Ban diagram-narration phrases near an image/diagram embed: "the diagram (above|below) shows", "as you can see", "the image shows", etc.
  - Ban production/style narration: "(dark|light) (background|accent)", "same .* font", "per the .* style", orientation/color descriptions.
  - Ban marketing adjectives in service READMEs: "intelligent", "powerful", "seamless", "cutting-edge", "AI-powered" (as unearned qualifiers).
  - Flag copy-pasted blocks: identical ≥N-line Markdown blocks appearing on >K pages (catches the "Source Files" class).
  - Keep the existing TODO/TBD/FIXME/XXX placeholder gate.
- Ship the gate with the cleanup in the same change so CI is green on introduction.

### 6.4. Sync verification
- Confirm the determinism, canonical-drift, self-containment, completeness, and diagram-fingerprint gates cover every new/moved page; run `make docs-check` to prove green; close any residual content drift the audit surfaced.

## 7. Phase 2 — Reskin ("Clean Systems", light-first)

### 7.1. Design tokens (pinned; final hex confirmed during implementation)
- **Light:** bg `#ffffff`, surface `#f8fafc`, text `#0f172a`, muted `#5b6472`, border `#e6e9ef` (slight cool bias — a *chosen* neutral, not pure grey), accent `#2563eb`, accent-hover `#1d4ed8`, code-bg `#0f172a`/code-fg `#e2e8f0`.
- **Dark (true equal, not an inversion):** bg `#0d1117`, surface `#161b22`, text `#e6edf3`, muted `#8b949e`, border `#21262d`, accent `#5b8bff`.
- Semantic admonition colors (note/tip/warning/danger) kept separate from the blue accent.

### 7.2. Typography (proposed; confirm faces in the plan)
- Body/headings: a clean, legible docs sans with a little character — proposed **Public Sans** (neutral, high-legibility, non-cliché) or **IBM Plex Sans**; deliberately avoiding the Inter/Space-Grotesk default.
- Code/labels: **JetBrains Mono** or **IBM Plex Mono**.
- Self-hosted via Material's `theme.font` (Material bundles/self-hosts, satisfying privacy + the site's offline-ish build). Set a type scale; headings `text-wrap: balance`; body near 65–72ch.

### 7.3. Theme config (`build_docs.render_mkdocs_yml`)
- Flip to **light-first** palette order; keep the dark toggle. `primary`/`accent: custom` (tokens from CSS).
- Add `theme.logo`, `theme.favicon`, `theme.font`, and `custom_dir: overrides`.
- Nav: top **tabs** (`navigation.tabs`) + sections + index pages + TOC follow + code copy + search suggest/highlight (mostly already set); consider `navigation.instant` for SPA-like feel.

### 7.4. Stylesheet (`docs/assets/stylesheets/atlas.css` — rewrite)
- Replace the dark-space system with the token set above; hairline borders, generous spacing, refined code/table/admonition styling; both themes via `[data-theme]`/`prefers-color-scheme` at the token level.
- Rebuild the `.atlas-home*` landing components for the new identity.

### 7.5. Brand assets
- A simple, geometric **Atlas logomark** (SVG, works at favicon size, both themes) + wordmark in the nav; generate `favicon.ico`/PNG set. Store under `docs/assets/` and wire via theme config + `render_site` copy.

### 7.6. Rich landing (`docs/index.md` + `overrides/home.html` if needed + CSS)
- Hero: tagline + subhead + primary CTAs (Quick Start · Service Catalog · Architecture).
- Capability grid: the tracks (gen-ai-eng / rag / creative / ml-eng / data-eng / trading) or headline services as cards.
- A quick-start code moment (`./start.sh …` with tabbed variants).
- The topology visual (existing platform diagram, restyled).
- Optional: a compact "what runs" stat line grounded in real numbers.

## 8. Verification & acceptance criteria

- `make docs-check` green: strict MkDocs build (0 warnings), determinism, canonical-drift, self-containment, completeness, placeholder, **new content-lint**, wiki dry-run, built-HTML link check.
- `bootstrapper` suite green where it touches docs (`test_docs_drift`, diagram fingerprints).
- Manual visual QA: every page type (landing, concept, service, architecture, reference) reviewed in **both** light and dark; contrast legible; dark is a true equal.
- Grounding: a re-audit pass finds zero instances of the D5 defect classes; every remaining documented fact/command/number is anchored in the tree.
- No diagram-PNG churn committed unintentionally (see §9).
- Surface parity: the same content is correct on all three surfaces (spot-check in-repo vs site vs wiki).

## 9. Risks & mitigations

- **Diagram-PNG drift trap.** Running the docs/bootstrapper builds rewrites `docs/diagrams/img/*.png` with non-deterministic bytes. *Mitigation:* always targeted `git add <files>`, never `git add -A`; restore `docs/diagrams/img/` before committing unless a diagram genuinely changed.
- **Webfont/CSP in previews.** Font CDNs are blocked in artifact previews; the real site self-hosts via Material, so this only affects mockups, not production.
- **Scope creep across ~110 pages.** *Mitigation:* the content pass is bounded by the audit's defect classes + the lint gate; already-clean pages are left alone.
- **Determinism/gate breakage from restructure.** *Mitigation:* every manifest edit is followed by `make docs-check`; move-then-verify in small batches.
- **Large single PRs.** *Mitigation:* land in staged PRs (see §10), each green on the three required `services-lint` checks + the docs gate, via gitflow (develop→main) per the standing rule.

## 10. Rollout / PR plan

Delivered as a sequence of gitflow PRs off the `docs-overhaul` branch (each develop→main, targeted `git add`):
1. **Content-lint gate + content pass** (§6.2–6.3) — introduce the gate green, fix all defects.
2. **IA restructure** (§6.1) — README shrink, dedup, re-home, archival index; manifest + moves.
3. **Reskin core** (§7.1–7.5) — tokens, theme config, CSS rewrite, brand assets.
4. **Rich landing** (§7.6).

Each PR is independently green and shippable; the order preserves "foundation before skin."

## 11. Open items to finalize in the implementation plan

- Exact font faces (Public Sans vs IBM Plex Sans; mono choice) and final palette hex after a contrast check.
- Final home for `CHANGELOG`/`ROADMAP` (repo root vs a "Project" nav area).
- Whether the "Source Files" block becomes generated or hand-curated per view.
- Logomark concept (geometric "A" / atlas motif) — 2–3 quick options before committing.
- Whether to adopt `navigation.tabs` vs the current `navigation.sections` top-level treatment.
