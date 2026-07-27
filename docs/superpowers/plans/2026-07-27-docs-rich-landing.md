# Docs .io Rich Landing Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn the `.io` site homepage (`docs/index.md`) from a hero + link list into a rich, professional dspy.ai-style landing — hero, a capability/feature grid (the tracks), a quick-start code moment, and the topology visual — styled to match the Clean Systems reskin from PR #3.

**Architecture:** Two coupled, hand-authored levers: `docs/index.md` (the landing markup + factual copy, using `.atlas-home*` component classes) and additions to `docs/assets/stylesheets/atlas.css` (the new landing components — feature-grid cards, code moment, section rhythm — for both themes, on top of the Clean Systems tokens PR #3 established). `docs/index.md` is hand-authored (not generated), so it is edited directly.

**Tech Stack:** MkDocs Material (`md_in_html` extension already enabled, so HTML blocks in Markdown work), CSS custom properties (the PR #3 token system), the Atlas docs pipeline (`make docs-build` / `make docs-check` → `mkdocs build --strict`).

**This is PR #4 of 4** — the final PR — in `docs/superpowers/specs/2026-07-26-docs-overhaul-and-reskin-design.md` §7.6. Independently shippable.

## 1. Global Constraints

- **Clean Systems continuity:** reuse the PR #3 token system (accent `#2563eb` light / `#5b8bff` dark, borders, surfaces) via the existing `--atlas-*` / `--md-*` variables; do NOT introduce a new palette. Both themes first-class.
- **Professional, factual copy — not marketing hype.** The landing must read like dspy.ai: confident and clean, but grounded. `docs/index.md` is scanned by the content-quality gate: `diagram_narration_findings` + `production_style_findings` apply (no "the diagram shows…", no styling narration). Marketing-adjective checks only apply to service READMEs, but STILL keep the copy clinical/professional — no "powerful/revolutionary/seamless" hype. State what Atlas does and the real numbers.
- **Ground every number/claim:** service-family count, track count/names, SOURCE count — pull from the tree (`bootstrapper/tracks.yml`, `services/`, topology) so the landing matches reality. Track descriptions come from `bootstrapper/tracks.yml`.
- **Contiguous numbering:** `docs/index.md` uses a baked `# 1.` H1 and `## 1./2./…` H2s. Keep H2 numbering contiguous — run `scripts/number-markdown-headings.py`. (H2 section headings can be minimal since the landing is visual; but they must stay numbered + contiguous for the audit.)
- **Every link resolves** (`check_doc_links`): verify each `href`/link target with `ls` before writing. Relative links from the site root (e.g. `quick-start/`, `services/`, `architecture/`).
- **Keep the existing hero + assets working:** the hero, `assets/atlas-poster-blue.png`, `screenshots/wizard-running.png`, and the `.md-content--atlas-wide`/`.atlas-home` classes already exist and are styled — build ON them.
- **The visual-contract test** (`bootstrapper/tests/test_docs_site_platform.py::test_home_and_theme_preserve_the_atlas_clean_systems_visual_contract`) asserts `atlas-home` in the home, poster-blue present, poster-gold absent, wizard-running present, and the 2-column hero grid — keep all those true (don't remove the hero, poster, or wizard screenshot; don't add poster-gold).
- **Strict build 0 warnings** (`make docs-check`). **PNG-drift:** `git restore docs/diagrams/img/` before committing. Targeted `git add`; NEVER `git add -A`.
- ENVIRONMENT NOISE: every shell command prints a harmless first line about `/Users/kaveh/.zshenv` / `vmx-cargo` — ignore it.

---

### 1.1. Task 1: Rich landing — markup, copy, and CSS components

**Files:**
- Modify: `docs/index.md` (the landing markup + copy)
- Modify: `docs/assets/stylesheets/atlas.css` (new `.atlas-home*` landing components)

**Interfaces:** none (page + styles). Verified by the gates + the visual-contract test.

- [ ] **Step 1: Gather the grounded content**

`ls docs/quick-start/ docs/architecture/ docs/reference/`; read `bootstrapper/tracks.yml` for the track keys + descriptions (the capability grid); confirm the service-family count and SOURCE count that the current `docs/index.md` states are still accurate (`ls services/ | wc -l` etc.) — update the numbers if they drifted. Read the current `docs/index.md` + the `.atlas-home*` rules in `docs/assets/stylesheets/atlas.css` to see what components already exist.

- [ ] **Step 2: Rewrite `docs/index.md` as a rich landing**

Keep the existing hero section (kicker + copy + CTA buttons + poster). Below it, add (all as `md_in_html` HTML blocks using new `.atlas-home__*` classes):
- A **capability / feature grid** (`.atlas-home__grid` of `.atlas-card`) — one card per track from `tracks.yml` (gen-ai-eng, gen-ai-rag, gen-ai-creative, ml-eng, data-eng, trading), each with the track name + its one-line factual purpose + a link to the tracks page. Factual, no hype.
- A **quick-start code moment** (`.atlas-home__quickstart`) — a short fenced code block showing `./start.sh` (and one or two representative `--*-source` variants), plus a one-line pointer to the Quick Start page. (Fenced code is exempt from the prose gates.)
- A **topology strip** (`.atlas-home__topology`) — embed the existing platform architecture SVG/diagram (the one the root README/architecture index uses, e.g. `assets/img/atlas-platform.svg` if present, else link to `architecture/`) with a one-line functional caption (NOT diagram-narration).
- Keep the wizard screenshot (`.atlas-screenshot`, `screenshots/wizard-running.png`).
- Replace the plain "Start Here / Documentation Scope / Organization" prose sections with tighter, still-numbered H2s (or fold them into a compact `.atlas-home__grid` of doc-area cards linking Quick Start / Concepts / Services / Architecture / Reference). Keep the grounded scope numbers (service families / tracks / SOURCE surfaces) as a factual one-liner.
Copy: professional, clean, grounded — dspy.ai register. Contiguous `## N.` H2s for any remaining Markdown headings.

- [ ] **Step 3: Add the landing CSS components**

In `docs/assets/stylesheets/atlas.css`, add (both themes, via the existing tokens): `.atlas-home__grid` (responsive card grid — `repeat(auto-fit, minmax(...))`, gap), `.atlas-card` (surface bg, 1px border token, radius, padding, hover lift/accent-border, card title + body + link), `.atlas-home__quickstart` (framed code moment), `.atlas-home__topology` (centered diagram with subtle border/radius), and section spacing rhythm. Use `--md-*`/`--atlas-*` tokens — no new hardcoded palette. Ensure cards + links have adequate contrast in both themes (light: text `#0f172a` on `#f8fafc`; dark: `#e6edf3` on `#161b22`); accent used for card hover-border + links.

- [ ] **Step 4: Verify build + gates + visual-contract test**

`make docs-build` then `make docs-check` → exit 0 (strict, 0 warnings). `uv run --project bootstrapper python scripts/number-markdown-headings.py`; `uv run --project bootstrapper python scripts/check-docs-drift.py` → all PASS (content_quality clean — no diagram-narration/production-style in the new copy); `uv run --project bootstrapper python scripts/check_doc_links.py` → exit 0 (every landing link resolves); `uv run --project bootstrapper python -m pytest bootstrapper/tests/test_docs_site_platform.py bootstrapper/tests/test_mkdocs_theme.py -q` → pass (visual-contract test still green). `git restore docs/diagrams/img/`. Confirm the built `site/index.html` contains the new grid/cards.

- [ ] **Step 5: Commit**

```bash
git add docs/index.md docs/assets/stylesheets/atlas.css
git commit -m "docs(landing): rich Clean Systems homepage — capability grid, quick-start, topology"
```

---

### 1.2. Task 2: Full verification

**Files:** none (verification).

- [ ] **Step 1: Complete gate set (all green)**

- `make docs-check` → exit 0 (mkdocs build --strict, 0 warnings)
- `uv run --project bootstrapper python scripts/check-docs-drift.py` → all PASS
- `uv run --project bootstrapper python scripts/check_doc_links.py` → exit 0
- `uv run --project bootstrapper python -m tools.validate_fragments` → pass
- `cd bootstrapper && uv run pytest -q` → full suite green (report count; ~5-6 min, FOREGROUND, WAIT for it — do not background-and-return)
- `git restore docs/diagrams/img/` from repo root.

- [ ] **Step 2: Record maintainer visual-QA items**

In the report, list what needs the maintainer's eyes on the deployed site: the landing's overall look in light + dark, card grid responsiveness, the quick-start code block styling, topology strip, and hero — final aesthetic sign-off is the maintainer's.

- [ ] **Step 3: Commit (only if a verification fix was needed; else no-op).**

## 2. 1.3. Self-Review

**Spec coverage (§7.6):** hero (kept) + capability/feature grid + quick-start code moment + topology visual → Task 1. Both-theme CSS → Task 1 Step 3. Verification → Task 2.

**Placeholder scan:** none — grounded content sources (tracks.yml, service count), exact classes, exact gate commands.

**Honest limitation:** as with PR #3, headless verification proves the build is clean, links resolve, copy passes the content gates, and the visual-contract test holds; the RENDERED look of the landing (light/dark, responsive cards) is the maintainer's post-deploy sign-off — surfaced in Task 2 Step 2, not a silent gap.
