# Documentation Header Restructure (nativ-grade) — Design

- **Date:** 2026-07-30
- **Status:** Draft (awaiting review)
- **Owner:** Kaveh Razavi
- **Builds on:** `2026-07-26-docs-overhaul-and-reskin-design.md`. PR #4 already delivered the rich `.io` landing and the Clean Systems reskin. This effort sharpens the *opening* of every surface to a nativ-grade header and adds the missing lead-in executive summary. It is documentation + one parity test only; no runtime, manifest, or compose change.

---

## 1. Summary

The owner set `https://github.com/Blaizzy/nativ` as the reference for how a project should *open*. `nativ`'s README header is a centered block: full-width poster, centered title, a bold one-line tagline, a one-line subtitle, a centered badge row, then a two-paragraph executive summary written as lead-in prose with no heading. Atlas already has the poster, the name, and a one-line summary short enough for GitHub's About box, but its root `README.md` opens with a left-aligned `# Atlas`, an unstyled one-liner, no badges, no lead-in summary, and jumps straight into two images. The "detailed" summary the project already has is buried at `## 2. What is Atlas`.

This design restructures the opening of all three documentation surfaces (in-repo `README.md`, the generated `.io` landing, and the generated GitHub wiki `Home`) to match that professional layout, and introduces one new artifact: a structured, roughly 130-word executive summary that sits at the top as lead-in prose. A single parity test keeps the two hand-authored sources (`README.md` and `docs/index.md`) telling the same story.

The pipeline finding that shapes this design: `docs/index.md` is hand-authored and is the single Layer-B source projected into *both* the `.io` landing (`generated/site/index.md`) and the wiki home (`generated/wiki/Home.md`). So only two files are hand-edited; the third surface is derived automatically.

## 2. Goals

1. A nativ-grade opening on every surface: centered poster, centered title, bold tagline, subtitle, badge row, then a lead-in executive summary.
2. One new structured executive summary (~130 words, two to three short paragraphs plus a compact at-a-glance bullet list) that is comprehensive yet concise, placed as lead-in prose at the top.
3. All three surfaces open identically in spirit (README hand-authored; `.io` and wiki both derived from `docs/index.md`).
4. No drift: a parity test asserts the canonical tagline and the summary's opening sentence are identical across `README.md` and `docs/index.md`.
5. Every repo gate stays green: the three required `services-lint` checks, `content_quality`, `check-docs-drift`, and strict `## N.` heading numbering.

## 3. Non-goals

- No change to the docs generation pipeline, the manifest, `service.yml`, compose, or any runtime behavior.
- No reskin of the `.io` theme (already done in PR #3). This work touches hero *copy and ordering* on `docs/index.md`, not the CSS or palette.
- No re-authoring of the body of any surface beyond the opening region (poster through the first content section).
- No removal of the GitHub About one-liner; it stays as the repo's short description.
- No edit to the dead legacy Home templates (`pages.py:428` `static_pages` home, `wiki.py:107` simple Home). They are flagged for a separate cleanup, not touched here.

## 4. Current state (grounded)

Findings below are from a read of the `develop` tree; references are exact.

### 4.1. The reference (`nativ`)

`nativ`'s `README.md` opens, in order: a full-width poster banner inside `<p align="center">`; a centered `<h1 align="center">Nativ</h1>`; a centered bold tagline (`Local AI, native to your Mac.`); a centered one-line subtitle; a centered badge row of four shields; then a two-paragraph executive summary as plain lead-in prose with no heading; then the first `##` section. Notably the badges are *tech* badges (macOS, Apple silicon, Swift, MLX), not CI/license status badges.

### 4.2. Atlas today, per surface

Root `README.md` (hand-authored, 127 lines):

1. Full-width poster inside `<p align="center">` (already nativ-style).
2. `# Atlas` left-aligned (not centered).
3. A one-line summary as plain left-aligned text (this is the About text).
4. No subtitle, no badge row.
5. No lead-in executive summary; the document jumps straight to two images (the wizard screenshot and the architecture diagram) each with a caption.
6. `## 1. Quick start`, then `## 2. What is Atlas` (the verbose summary, buried at section 2).

`docs/index.md` (hand-authored; the `.io` landing source): a `# 1. Atlas Documentation` H1, then an `atlas-home` hero block with a kicker (`Source-configurable local AI, data, and engineering stack`), one summary paragraph, three action links, and the poster figure. Below it sit a Capabilities grid, a Quick Start block, a Platform Topology block, a Documentation Map grid, and a Setup Surface screenshot.

`generated/wiki/Home.md` (generated): currently mirrors the rich `docs/index.md` landing (same hero and `## 1. Capabilities` grid), with wiki-style link targets.

### 4.3. Pipeline map (decisive)

Layer A (`scripts/docs/canonical_references.py`) regenerates only `docs/tracks.md`, `docs/services.md`, `docs/reference/index.md`, the `docs/reference/*` pages, and the `docs/architecture/*` pages. It does **not** write `docs/index.md` or `README.md`. The `home` template inside `static_pages` (`bootstrapper/docs/sitegen/pages.py:428`) is fetched but never consumed, i.e. dead code.

Layer B (`scripts/docs/build_docs.py`) projects committed manifest pages via `_render_pages` into both `generated/site/` (the `.io` input) and `generated/wiki/`. `render_wiki` and `render_site` both call `_render_pages`, so `docs/index.md` is the single source for *both* the `.io` landing and the wiki `Home`. The simple `wiki / "Home.md"` template at `bootstrapper/docs/sitegen/wiki.py:107` is also dead code; the live wiki Home is the projected `docs/index.md`.

`generated/site/`, `generated/wiki/`, and `mkdocs.yml` are gitignored (`.gitignore` lines 8 to 10) and rebuilt by CI, so they are not committed.

Consequences for this design:

- Hand-edit exactly two files: `README.md` and `docs/index.md`.
- Editing `docs/index.md` automatically updates the `.io` landing and the wiki Home after `make docs-build`.
- The drift surface narrows to `README.md` versus `docs/index.md`; the `.io` and wiki cannot drift from each other because they share a source.

## 5. Design

### 5.1. Authoring approach (source of truth)

Hand-author the opening in `README.md` and `docs/index.md`. Add one parity test (`bootstrapper/tests/test_doc_header_parity.py`) asserting the canonical tagline and the executive summary's opening sentence are byte-identical in both files. Because the `.io` and wiki derive from `docs/index.md`, that two-way check effectively covers all three surfaces.

This matches the repo's established pattern (drift tests as the enforcement mechanism, e.g. `test_env_assembler`, `test_docs_drift`, `content_quality`) and introduces no new generation step. A canonical-content module plus generator (the DRY alternative) is not justified for two hand-authored copies and is rejected for this effort.

### 5.2. Canonical content

The short About one-liner stays as GitHub's About box. At the top of the docs the opening becomes:

- **Title (centered):** `Atlas`
- **Tagline (centered, bold):** One Docker Compose stack for self-hosted gen-AI, ML, and data engineering.
- **Subtitle (centered):** Spin up chat, RAG, agents, distributed compute, and a full data platform, with every service switchable between container, localhost, or off.
- **Badges (centered):** Docker Compose, Ollama, LiteLLM, Kong (shields.io static badges; logos via simple-icons where available, verified at implementation time).
- **Executive summary (lead-in prose, no heading, ~130 words):**

> Atlas is a self-hosted engineering platform that bundles 30+ services, an LLM gateway and inference, vector and graph databases, workflow and DAG automation, distributed compute, object storage, notebooks, and observability, behind a Kong gateway and an adaptive FastAPI backend.
>
> Every service is independently switchable between `container`, `localhost`, and `disabled` through its own SOURCE variable, so the same stack scales from a CPU starter to a multi-GPU lab. A tracks system (`gen-ai-rag`, `gen-ai-eng`, `gen-ai-creative`, `ml-eng`, `data-eng`, `trading`, `all`) preselects a working subset per workflow; the always-on core is Supabase, Redis, LiteLLM, and the Backend API.
>
> - 30+ services across 7 tracks, all ports derived from one `BASE_PORT`
> - Always-on core: Supabase, Redis, LiteLLM, Backend
> - Per-service SOURCE: `container` / `localhost` / `disabled`
> - One command: `./start.sh` runs the interactive setup wizard

Badge caveat: Ollama is an optional service (the platform also runs cloud-only through LiteLLM). If the owner prefers always-on fabric over the headline LLM runtime, FastAPI or Supabase are honest replacements; the decision is deferred to implementation.

### 5.3. `README.md` layout (before and after)

The opening block becomes, in order:

1. `<p align="center"><img src="./assets/atlas-poster-blue.png" ... width="100%"></p>` (kept).
2. `<h1 align="center">Atlas</h1>` (replaces the left-aligned `# Atlas`).
3. `<p align="center"><strong>tagline</strong></p>`.
4. `<p align="center">subtitle</p>`.
5. `<p align="center">badges</p>`.
6. The lead-in executive summary (two paragraphs plus the at-a-glance bullet list), no heading.
7. The wizard screenshot with its caption (kept as the single anchor visual).
8. `## 1. Quick start` (unchanged).

The architecture diagram moves out of the opening and into the Service Topology section (its conceptual home), with its caption and a pointer to the full diagram set. The old `## 2. What is Atlas` is removed; its content is absorbed by the lead-in summary, and subsequent sections renumber (Service Topology becomes section 2, Documentation section 3, and so on). Section numbers are re-stamped with `scripts/number-markdown-headings.py`.

### 5.4. `docs/index.md` hero

The hero block is re-pointed to the new messaging: the kicker becomes the tagline, a subtitle line is added, and the hero paragraph becomes the first sentence of the executive summary. The action links and the poster figure are kept. The existing Capabilities grid below already serves as the at-a-glance layer, so the bullet list is not duplicated on the landing. Badges are README-only (a GitHub convention); the landing keeps its action buttons. The `# 1. Atlas Documentation` H1 and the rest of the landing are unchanged.

### 5.5. Wiki home

No direct edit. After `docs/index.md` is updated, `make docs-build` regenerates `generated/wiki/Home.md` from it. Implementation verifies the hero change propagated; if it did not, the wiki projection path is investigated before commit.

## 6. Tests and gates

1. **New** `bootstrapper/tests/test_doc_header_parity.py`: asserts the canonical tagline string and the sentence `Atlas is a self-hosted engineering platform that bundles 30+ services` appear identically in `README.md` and `docs/index.md`. This is the real anti-drift safeguard.
2. **Update** any test pinning the current hero copy. Grep the test tree for the current kicker string `Source-configurable local AI, data, and engineering stack` and re-point any assertion to the new tagline.
3. Re-run `content_quality` and `check-docs-drift` after the edits and the section renumber.
4. Re-stamp `README.md` section numbers with `scripts/number-markdown-headings.py` after removing section 2.
5. Confirm the three required `services-lint` checks stay green: Manifest lint + unit tests, Compose merge + byte-equivalence + source-permutation matrix, and Docs drift + audit scripts.

## 7. Rollout

Single PR off `develop`, typically from a `.claude/worktrees/<name>` worktree. Edit `README.md` and `docs/index.md`, add the parity test, re-stamp heading numbers, run the gates locally, then `gh pr create --base main`, wait for the three required checks, and `gh pr merge --squash --delete-branch`. The diff is small and reviewable.

## 8. Risks and follow-ups

- **Dead templates.** `static_pages` home (`pages.py:428`) and the wiki simple Home (`wiki.py:107`) are legacy and misleading. Out of scope here; a follow-up ticket should remove them so future editors are not tricked into editing a dead template.
- **Heading renumber ripple.** Removing `## 2. What is Atlas` renumbers every subsequent README section. The TOPOLOGY block is a separate CI gate and must not be touched; only the surrounding section headings renumber. Verify no inbound anchor links break.
- **Wiki projection assumption.** The design relies on `docs/index.md` projecting to `generated/wiki/Home.md` via `_render_pages`. Implementation confirms propagation after the first build.
- **Badge logo availability.** simple-icons coverage for LiteLLM and Kong is verified at implementation; fallback is a colored badge without a logo.
