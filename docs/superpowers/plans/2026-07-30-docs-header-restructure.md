# Docs Header Restructure (nativ-grade) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the opening of all three documentation surfaces (root `README.md`, the generated `.io` landing, and the generated GitHub wiki `Home`) into a nativ-grade centered header, and add a new ~130-word lead-in executive summary at the top.

**Architecture:** Hand-author exactly two files. `README.md` gets a centered header block (poster, centered title, bold tagline, subtitle, stack badges) followed by the lead-in executive summary, with the wizard screenshot kept as the single anchor visual and the architecture diagram moved into the Service Topology section. `docs/index.md` gets its hero re-pointed to the same tagline and summary; because `docs/index.md` is the Layer-B source projected into both the `.io` landing and the wiki `Home` via `_render_pages`, that one edit updates both derived surfaces. One new pytest module locks the canonical tagline and summary anchor into both hand-authored files so they cannot drift.

**Tech Stack:** Markdown with GitHub HTML blocks (`<p align="center">`, `<h1 align="center">`), MkDocs Material `md_in_html` (for the `docs/index.md` hero), pytest, and the Atlas docs pipeline (`make docs-build` / `make docs-check`, `scripts/number-markdown-headings.py`, `scripts/check-docs-drift.py`, `scripts/check_doc_links.py`).

**Spec:** `docs/superpowers/specs/2026-07-30-docs-header-restructure-design.md`. Single PR off `develop`.

## 1. Global Constraints

- **Two files only.** Hand-author `README.md` and `docs/index.md`. Do NOT edit `bootstrapper/docs/sitegen/wiki.py:107` or `bootstrapper/docs/sitegen/pages.py:428` — both are dead legacy Home templates not used by the live surfaces (follow-up cleanup, out of scope here).
- **One source feeds two surfaces.** `docs/index.md` is projected into `generated/site/index.md` (the `.io` landing) and `generated/wiki/Home.md` (the wiki `Home`) by `scripts/docs/build_docs.py:_render_pages`. Editing `docs/index.md` and running `make docs-build` updates both. No direct wiki edit.
- **Do not commit generated output.** `generated/site/`, `generated/wiki/`, and `mkdocs.yml` are gitignored (`.gitignore` lines 8-10) and rebuilt by CI. The PR commits only `README.md`, `docs/index.md`, and `bootstrapper/tests/test_doc_header_parity.py`.
- **Keep the visual-contract test green.** `bootstrapper/tests/test_docs_site_platform.py::test_home_and_theme_preserve_the_atlas_clean_systems_visual_contract` (lines 245-255) asserts `docs/index.md` contains `atlas-home`, `assets/atlas-poster-blue.png`, `screenshots/wizard-running.png`, and that `.atlas-home__hero` has a CSS rule, and that `assets/atlas-poster-gold.png` is absent. Keep all of these true: do not remove the hero `div`, the poster-blue figure, or the wizard screenshot (the screenshot lives in the `## 5. Setup Surface` section of `docs/index.md`, untouched by this work).
- **content_quality gate.** `docs/index.md` is scanned by `scripts/docs/content_quality.py` (diagram-narration + production-style findings). Copy must be clinical and factual, not marketing hype: no "powerful", "revolutionary", "seamless", "cutting-edge". No "the diagram shows" narration. State what Atlas does and the real numbers.
- **Strict `## N.` heading numbering.** `scripts/docs/heading_quality.py:documentation_paths` covers every git-tracked `.md` (including `README.md`). After removing `## 2. What is Atlas`, run `scripts/number-markdown-headings.py` so the remaining README H2s renumber contiguously (Quick start, Service topology, Documentation, Contributing, License, Support).
- **TOPOLOGY block is a separate CI gate.** `README.md` contains a `<!-- TOPOLOGY:BEGIN -->` ... `<!-- TOPOLOGY:END -->` table. Do not touch it. Only the surrounding section headings renumber.
- **Every internal link resolves.** `scripts/check_doc_links.py` must exit 0. Do not break existing README links; the architecture-diagram image move keeps the same `./docs/diagrams/architecture.svg` target.
- **PNG-drift trap.** `make docs-build` can regenerate `docs/diagrams/img/*.png` non-deterministically. Run `git restore docs/diagrams/img/` before staging, and use targeted `git add <file>` — NEVER `git add -A`.
- **Gitflow.** Work in a worktree under `.claude/worktrees/<name>` branched off `develop`. `gh pr create --base main`, wait for the three required `services-lint` checks, then `gh pr merge --squash --delete-branch`. Never `git push origin main` (the ruleset rejects it).
- **Environment noise.** Every shell command prints a harmless first line about `/Users/kaveh/.zshenv` / `vmx-cargo` — ignore it.

---

### 1.1. Task 1: `README.md` header restructure (TDD)

**Files:**
- Create: `bootstrapper/tests/test_doc_header_parity.py`
- Modify: `README.md` (replace lines 1-16, the opening through the architecture-diagram caption; remove `## 2. What is Atlas` at line 26; move the architecture diagram into the Service Topology section around line 98)

**Interfaces:**
- Produces: module-level constants `CANONICAL_TAGLINE` and `CANONICAL_SUMMARY_ANCHOR` in `test_doc_header_parity.py`, plus `test_readme_header_matches_canonical()`. Task 2 reuses the same constants for the `docs/index.md` assertion.

- [ ] **Step 1: Write the failing parity test (README half)**

Create `bootstrapper/tests/test_doc_header_parity.py`:

```python
"""Header parity: README.md and docs/index.md must share the canonical header copy.

README.md is hand-authored. docs/index.md is hand-authored and is the Layer-B
source projected into both the .io landing and the GitHub wiki Home, so locking
these two files locks every surface against drift.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CANONICAL_TAGLINE = "One Docker Compose stack for self-hosted gen-AI, ML, and data engineering"
CANONICAL_SUMMARY_ANCHOR = "Atlas is a self-hosted engineering platform that bundles 30+ services"

README = ROOT / "README.md"
INDEX = ROOT / "docs" / "index.md"


def test_readme_header_matches_canonical() -> None:
    text = README.read_text(encoding="utf-8")
    assert CANONICAL_TAGLINE in text, "README.md is missing the canonical tagline"
    assert CANONICAL_SUMMARY_ANCHOR in text, "README.md is missing the canonical summary anchor"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd bootstrapper && uv run pytest tests/test_doc_header_parity.py -q`
Expected: FAIL — README.md does not yet contain the tagline or the summary anchor.

- [ ] **Step 3: Replace the README opening with the centered header + lead-in summary**

In `README.md`, replace everything from line 1 through the architecture-diagram caption (the poster `<p>`, `# Atlas`, the one-liner, the wizard screenshot block, and the architecture-diagram block — i.e. the current lines 1-15) with the block below. Keep `## 1. Quick start` immediately after it.

```markdown
<p align="center">
  <img src="./assets/atlas-poster-blue.png" alt="Atlas — the Titan holding the globe, with the ATLAS-PLATFORM wordmark" width="100%">
</p>

<h1 align="center">Atlas</h1>

<p align="center">
  <strong>One Docker Compose stack for self-hosted gen-AI, ML, and data engineering.</strong>
</p>

<p align="center">
  Spin up chat, RAG, agents, distributed compute, and a full data platform — every service switchable between container, localhost, or off.
</p>

<p align="center">
  <img alt="Docker Compose" src="https://img.shields.io/badge/Docker%20Compose-orchestration-2496ED?logo=docker&logoColor=white">
  <img alt="Ollama" src="https://img.shields.io/badge/Ollama-local%20LLMs-000000?logo=ollama&logoColor=white">
  <img alt="LiteLLM" src="https://img.shields.io/badge/LiteLLM-LLM%20gateway-2563EB">
  <img alt="Kong" src="https://img.shields.io/badge/Kong-API%20gateway-003459?logo=kong&logoColor=white">
</p>

Atlas is a self-hosted engineering platform that bundles 30+ services — an LLM gateway and inference, vector and graph databases, workflow and DAG automation, distributed compute, object storage, notebooks, and observability — behind a Kong gateway and an adaptive FastAPI backend.

Every service is independently switchable between `container`, `localhost`, and `disabled` through its own SOURCE variable, so the same stack scales from a CPU starter to a multi-GPU lab. A tracks system (`gen-ai-rag`, `gen-ai-eng`, `gen-ai-creative`, `ml-eng`, `data-eng`, `trading`, `all`) preselects a working subset per workflow; the always-on core is Supabase, Redis, LiteLLM, and the Backend API.

- **30+ services across 7 tracks**, all ports derived from one `BASE_PORT`
- **Always-on core:** Supabase, Redis, LiteLLM, Backend
- **Per-service SOURCE:** `container` / `localhost` / `disabled`
- **One command:** `./start.sh` runs the interactive setup wizard

[![Atlas — interactive setup wizard streaming the launch phase, with the ASCII brand banner pinned at the top of the terminal](./docs/screenshots/wizard-running.png)](./docs/screenshots/wizard-running.png)

*The Textual TUI wizard streaming a live `./start.sh` launch — one view for stack status and logs.*
```

Badge note: logos come from simple-icons via shields.io. Docker, Ollama, and Kong have icons; LiteLLM does not (plain colored badge). If a logo fails to render, drop the `?logo=...` query for that badge. Ollama is an optional service; if the owner prefers always-on fabric, swap the Ollama badge for `<img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-backend-009688?logo=fastapi&logoColor=white">`.

- [ ] **Step 4: Remove `## 2. What is Atlas` and move the architecture diagram**

Delete the entire `## 2. What is Atlas` section (the heading and its single paragraph — its facts are now in the lead-in summary). Then, inside the Service Topology section, paste the architecture-diagram block immediately after the `<!-- TOPOLOGY:END -->` line and before the `Full port + Kong-route detail:` paragraph:

```markdown
[![Atlas — topologically-ordered architecture diagram](./docs/diagrams/architecture.svg)](./docs/diagrams/architecture.svg)

*How a request reaches a service: clients → Kong → apps/agents → shared LLM + data layers. Per-service diagrams under `services/<name>/architecture.svg` derive from each manifest's `data_flow.calls`.*
```

- [ ] **Step 5: Renumber the README headings**

Run: `uv run --project bootstrapper python scripts/number-markdown-headings.py`
Then open `README.md` and confirm the H2s are contiguous 1 through 6: `## 1. Quick start`, `## 2. Service topology`, `## 3. Documentation`, `## 4. Contributing`, `## 5. License`, `## 6. Support`. The `<!-- TOPOLOGY:BEGIN/END -->` block content is unchanged.

- [ ] **Step 6: Run the parity test to verify it passes**

Run: `cd bootstrapper && uv run pytest tests/test_doc_header_parity.py -q`
Expected: PASS (1 passed).

- [ ] **Step 7: Run the README-side gates**

Run: `uv run --project bootstrapper python scripts/check_doc_links.py` — exit 0 (no broken links).
Run: `uv run --project bootstrapper python scripts/check-docs-drift.py` — PASS (heading numbering contiguous, no decorative symbols, README structure clean).

- [ ] **Step 8: Commit**

```bash
git restore docs/diagrams/img/ 2>/dev/null
git add README.md bootstrapper/tests/test_doc_header_parity.py
git commit -m "docs(readme): nativ-grade centered header + lead-in executive summary

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### 1.2. Task 2: `docs/index.md` hero re-sync (TDD)

**Files:**
- Modify: `bootstrapper/tests/test_doc_header_parity.py` (add the index assertion)
- Modify: `docs/index.md` (the `atlas-home__hero` block, lines 5-20)

**Interfaces:**
- Consumes: `CANONICAL_TAGLINE` and `CANONICAL_SUMMARY_ANCHOR` from Task 1.
- Produces: `test_index_header_matches_canonical()`. Together with Task 1's test, this locks all three surfaces (the `.io` and wiki surfaces derive from `docs/index.md`).

- [ ] **Step 1: Add the failing parity test (index half)**

Append to `bootstrapper/tests/test_doc_header_parity.py`:

```python
def test_index_header_matches_canonical() -> None:
    text = INDEX.read_text(encoding="utf-8")
    assert CANONICAL_TAGLINE in text, "docs/index.md is missing the canonical tagline"
    assert CANONICAL_SUMMARY_ANCHOR in text, "docs/index.md is missing the canonical summary anchor"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd bootstrapper && uv run pytest tests/test_doc_header_parity.py::test_index_header_matches_canonical -q`
Expected: FAIL — `docs/index.md` still has the old kicker and hero paragraph.

- [ ] **Step 3: Re-point the `docs/index.md` hero to the canonical copy**

In `docs/index.md`, replace the hero block (the `<div class="atlas-home"> ... </div>` opening hero, currently the kicker + one paragraph + actions + poster figure) with:

```html
<div class="atlas-home">
  <section class="atlas-home__hero">
    <div class="atlas-home__copy">
      <p class="atlas-kicker">One Docker Compose stack for self-hosted gen-AI, ML, and data engineering</p>
      <p>Spin up chat, RAG, agents, distributed compute, and a full data platform — every service switchable between container, localhost, or off.</p>
      <p>Atlas is a self-hosted engineering platform that bundles 30+ services — an LLM gateway and inference, vector and graph databases, workflow and DAG automation, distributed compute, object storage, notebooks, and observability — behind a Kong gateway and an adaptive FastAPI backend.</p>
      <div class="atlas-home__actions">
        <a href="quick-start/">Quick Start</a>
        <a href="services/">Service Catalog</a>
        <a href="architecture/">Architecture</a>
      </div>
    </div>
    <figure class="atlas-home__media">
      <img src="assets/atlas-poster-blue.png" alt="Atlas platform poster">
    </figure>
  </section>
</div>
```

This preserves `atlas-home`, `atlas-home__hero`, `atlas-home__media`, `assets/atlas-poster-blue.png`, and the action links. The `# 1. Atlas Documentation` H1, the Capabilities grid, and the `## 5. Setup Surface` wizard screenshot are unchanged. No new CSS class is introduced (the subtitle renders as a normal paragraph); styling it is an optional follow-up, out of scope.

- [ ] **Step 4: Run the parity test to verify it passes**

Run: `cd bootstrapper && uv run pytest tests/test_doc_header_parity.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Build the site and run the landing-side gates**

Run: `make docs-build && make docs-check`
Expected: strict build with 0 warnings, exit 0.
Run: `uv run --project bootstrapper python scripts/number-markdown-headings.py` (idempotent; `git status --short docs/index.md` shows no change).
Run: `uv run --project bootstrapper python scripts/check-docs-drift.py` — PASS (content_quality clean, no diagram-narration or production-style findings in the new copy).
Run: `uv run --project bootstrapper python scripts/check_doc_links.py` — exit 0.

- [ ] **Step 6: Verify the visual-contract test and wiki propagation**

Run: `cd bootstrapper && uv run pytest tests/test_docs_site_platform.py::test_home_and_theme_preserve_the_atlas_clean_systems_visual_contract -q`
Expected: PASS (hero structure, poster-blue, wizard screenshot, and `.atlas-home__hero` CSS all intact).
Then confirm the wiki `Home` inherited the change: `grep "One Docker Compose stack" generated/wiki/Home.md` — expected: one match. If empty, `docs/index.md` is not projecting to the wiki; stop and investigate `_render_pages` before committing.

- [ ] **Step 7: Commit**

```bash
git restore docs/diagrams/img/ 2>/dev/null
git add docs/index.md bootstrapper/tests/test_doc_header_parity.py
git commit -m "docs(landing): sync hero to canonical tagline + executive summary

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### 1.3. Task 3: Full verification and PR

**Files:** none (verification and merge).

- [ ] **Step 1: Run the complete bootstrapper test suite**

Run: `cd bootstrapper && uv run pytest -q`
Expected: all green, including `test_doc_header_parity.py` (2 passed) and `test_docs_site_platform.py::test_home_and_theme_preserve_the_atlas_clean_systems_visual_contract`.

- [ ] **Step 2: Run the full docs gate set**

Run: `make docs-build && make docs-check` — strict, 0 warnings.
Run: `uv run --project bootstrapper python scripts/check-docs-drift.py` — PASS.
Run: `uv run --project bootstrapper python scripts/check_doc_links.py` — exit 0.

- [ ] **Step 3: Confirm the committed diff is minimal**

Run: `git restore docs/diagrams/img/ 2>/dev/null`
Run: `git status --short`
Expected: only `README.md`, `docs/index.md`, and `bootstrapper/tests/test_doc_header_parity.py` (plus this plan and the spec, already on the branch). No `generated/`, no `mkdocs.yml`, no `docs/diagrams/img/` PNGs.

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin HEAD
gh pr create --base main --title "docs: nativ-grade header + lead-in executive summary" --body "Restructures the opening of all three doc surfaces (README, .io landing, wiki Home) to a centered header (poster, title, tagline, subtitle, stack badges) plus a new ~130-word lead-in executive summary. Hand-authored in README.md + docs/index.md (wiki/.io derive from docs/index.md); one parity test guards drift. Spec: docs/superpowers/specs/2026-07-30-docs-header-restructure-design.md.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 5: Wait for the required checks and merge**

Wait for the three required `services-lint` checks to go green (Manifest lint + unit tests; Compose merge + byte-equivalence + source-permutation matrix; Docs drift + audit scripts). Then:

```bash
gh pr merge --squash --delete-branch
```

If merging from a worktree, the local branch-delete may error though the remote merge succeeds; finish cleanup from the main checkout (delete the local branch and the worktree).
