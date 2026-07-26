# Docs Content-Quality Gate + Content Pass — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every named content-quality defect across the Atlas docs corpus and add an automated content-lint gate so those defect classes cannot be reintroduced.

**Architecture:** A new pure-function rules module `scripts/docs/content_quality.py` (mirroring the existing `scripts/docs/heading_quality.py`) provides fence-aware findings for diagram-narration prose, production/style narration, marketing adjectives, and cross-file duplicate blocks. The content is cleaned first (so every commit is green), then the gate is registered in `scripts/check-docs-drift.py`'s `checks` registry — the audit script already run by the required "Docs drift + audit scripts" CI job.

**Tech Stack:** Python 3.12, `re`, `pathlib`; pytest via `uv run --project bootstrapper`; MkDocs Material docs pipeline (`make docs-build` / `make docs-check`).

**This is PR #1 of 4** in `docs/superpowers/specs/2026-07-26-docs-overhaul-and-reskin-design.md` §10. It is independently shippable and green on its own.

## Global Constraints

- **Single source of truth:** all page content lives in committed Markdown enumerated by `docs/manifest.yaml`; never edit `generated/**`, `site/**`, or `mkdocs.yml` (gitignored build artifacts).
- **PNG-drift trap:** docs/bootstrapper builds rewrite `docs/diagrams/img/*.png` with non-deterministic bytes. ALWAYS `git add <explicit files>`; NEVER `git add -A`; restore `docs/diagrams/img/` before committing unless a diagram master genuinely changed.
- **Fence-aware:** all lint rules must skip fenced code blocks (` ``` ` / `~~~`) — reuse the `_structural_lines` iterator pattern from `heading_quality.py`.
- **House style:** clinical, factual, active voice. No marketing adjectives ("intelligent", "powerful", "seamless", "AI-powered", "cutting-edge") as unearned qualifiers. Never restate in prose what an adjacent diagram/image already shows.
- **Grounding:** every retained factual claim, command, port, and number must be verifiable against the tree.
- **Commands run from repo root** unless noted. Python entrypoints: `uv run --project bootstrapper python -m <module>`.
- **Do not** change `service.yml`, compose, or runtime behaviour — docs only.

---

### Task 1: Content-lint rules module — fence-aware finders

**Files:**
- Create: `scripts/docs/content_quality.py`
- Test: `bootstrapper/tests/test_content_quality.py`

**Interfaces:**
- Consumes: nothing (pure functions over text).
- Produces:
  - `diagram_narration_findings(text: str) -> list[tuple[int, str]]`
  - `production_style_findings(text: str) -> list[tuple[int, str]]`
  - `marketing_adjective_findings(text: str, *, is_service_readme: bool) -> list[tuple[int, str]]`
  - `_structural_lines(text: str)` — re-exported fence-aware iterator (yields `(line_number, line, in_fence)`), identical semantics to `heading_quality._structural_lines`.
  - Suppression: a line containing the literal comment `<!-- lint-ok -->` is exempt from all three finders.

- [ ] **Step 1: Write the failing test**

```python
# bootstrapper/tests/test_content_quality.py
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.docs.content_quality import (  # noqa: E402
    diagram_narration_findings,
    production_style_findings,
    marketing_adjective_findings,
)


def test_diagram_narration_flagged():
    text = "See the figure.\n\nThe diagram above shows how requests flow.\n"
    findings = diagram_narration_findings(text)
    assert [ln for ln, _ in findings] == [3]


def test_diagram_narration_ignores_code_fence():
    text = "```\nthe diagram above shows x\n```\n"
    assert diagram_narration_findings(text) == []


def test_diagram_narration_suppressed_inline():
    text = "The diagram above shows the flow. <!-- lint-ok -->\n"
    assert diagram_narration_findings(text) == []


def test_production_style_narration_flagged():
    text = "Rendered on a slate-950 background with the same JetBrains Mono.\n"
    findings = production_style_findings(text)
    assert findings and findings[0][0] == 1


def test_marketing_adjectives_flagged_in_service_readme():
    text = "Kong is the intelligent, powerful API gateway.\n"
    findings = marketing_adjective_findings(text, is_service_readme=True)
    flagged = {word for _, word in findings}
    assert "intelligent" in flagged and "powerful" in flagged


def test_marketing_adjectives_allowlisted_phrases_ok():
    # "powerful" inside a quoted CLI example or non-service doc is not flagged here
    text = "The optimizer is powerful.\n"
    assert marketing_adjective_findings(text, is_service_readme=False) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kaveh/repos/atlas/.claude/worktrees/docs-overhaul && uv run --project bootstrapper python -m pytest bootstrapper/tests/test_content_quality.py -q`
Expected: FAIL — `ModuleNotFoundError: scripts.docs.content_quality`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/docs/content_quality.py
"""Fence-aware content-quality lint rules for Atlas docs.

Companion to heading_quality.py. Each finder returns (line_number, message)
tuples for lines that violate a rule. Fenced code blocks are always skipped,
and any line containing the literal `<!-- lint-ok -->` marker is exempt.
"""
from __future__ import annotations

import re

_SUPPRESS = "<!-- lint-ok -->"

# Rule 1 — prose that narrates an adjacent diagram/image.
_DIAGRAM_NARRATION = re.compile(
    r"\b("
    r"the (?:diagram|figure|image|chart|graph)\s+(?:above|below)\s+(?:shows|depicts|illustrates)"
    r"|as (?:you can|we can) see (?:above|below|in the (?:diagram|figure))"
    r"|(?:this|the) (?:diagram|figure|image) (?:shows|depicts|illustrates)"
    r"|in the (?:diagram|figure) (?:above|below)"
    r")\b",
    re.IGNORECASE,
)

# Rule 2 — narration of how a doc/diagram was produced or styled.
_PRODUCTION_STYLE = re.compile(
    r"\b("
    r"(?:dark|light|slate-\d+|navy|gray|grey)\s+background"
    r"|same\s+(?:font|palette|typeface|colou?rs?)"
    r"|per the .{0,30}style (?:guide|guidelines)"
    r"|landscape-orient|portrait-orient"
    r"|JetBrains Mono|slate-950"
    r")\b",
    re.IGNORECASE,
)

# Rule 3 — unearned marketing adjectives (only enforced in service READMEs).
_MARKETING_WORDS = (
    "intelligent",
    "powerful",
    "seamless",
    "seamlessly",
    "cutting-edge",
    "state-of-the-art",
    "ai-powered",
    "blazing",
    "world-class",
    "next-generation",
    "revolutionary",
)
_MARKETING_RE = re.compile(
    r"\b(" + "|".join(re.escape(word) for word in _MARKETING_WORDS) + r")\b",
    re.IGNORECASE,
)


def _structural_lines(text: str):
    in_fence = False
    fence_marker = ""
    for line_number, line in enumerate(text.splitlines(keepends=True), 1):
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence, fence_marker = False, ""
            yield line_number, line, True
            continue
        yield line_number, line, in_fence


def _scan(text: str, pattern: re.Pattern) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for line_number, line, in_fence in _structural_lines(text):
        if in_fence or _SUPPRESS in line:
            continue
        match = pattern.search(line)
        if match:
            findings.append((line_number, match.group(0).strip()))
    return findings


def diagram_narration_findings(text: str) -> list[tuple[int, str]]:
    return _scan(text, _DIAGRAM_NARRATION)


def production_style_findings(text: str) -> list[tuple[int, str]]:
    return _scan(text, _PRODUCTION_STYLE)


def marketing_adjective_findings(
    text: str, *, is_service_readme: bool
) -> list[tuple[int, str]]:
    if not is_service_readme:
        return []
    return _scan(text, _MARKETING_RE)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --project bootstrapper python -m pytest bootstrapper/tests/test_content_quality.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/docs/content_quality.py bootstrapper/tests/test_content_quality.py
git commit -m "feat(docs-lint): fence-aware content-quality rules module"
```

---

### Task 2: Cross-file duplicate-block finder

**Files:**
- Modify: `scripts/docs/content_quality.py`
- Test: `bootstrapper/tests/test_content_quality.py`

**Interfaces:**
- Produces: `duplicate_block_findings(docs: dict[str, str], *, min_lines: int = 4, min_pages: int = 4) -> list[tuple[str, str]]` — takes `{relative_path: text}`, returns `(relative_path, message)` for each page carrying a normalized ≥`min_lines`-line block that also appears on ≥`min_pages` pages total. Blocks inside fences are ignored. Message names the block's first line and the duplicate count.

- [ ] **Step 1: Write the failing test**

```python
from scripts.docs.content_quality import duplicate_block_findings  # noqa: E402


def test_duplicate_block_across_pages_flagged():
    block = "- `a.yml`\n- `b.py`\n- `c.md`\n- `d.txt`\n"
    docs = {f"p{i}.md": f"# Page {i}\n\n{block}\ntail {i}\n" for i in range(5)}
    findings = duplicate_block_findings(docs, min_lines=4, min_pages=4)
    assert len(findings) == 5  # every page carrying the shared block


def test_unique_blocks_not_flagged():
    docs = {f"p{i}.md": f"# Page {i}\n\nunique line {i}\nother {i}\n" for i in range(5)}
    assert duplicate_block_findings(docs, min_lines=4, min_pages=4) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project bootstrapper python -m pytest bootstrapper/tests/test_content_quality.py -q -k duplicate`
Expected: FAIL — `ImportError: cannot import name 'duplicate_block_findings'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to scripts/docs/content_quality.py
def _content_lines(text: str) -> list[tuple[int, str]]:
    """Non-fence, non-blank lines as (line_number, stripped_text)."""
    out = []
    for line_number, line, in_fence in _structural_lines(text):
        if in_fence:
            continue
        stripped = line.strip()
        if stripped:
            out.append((line_number, stripped))
    return out


def duplicate_block_findings(
    docs: dict[str, str], *, min_lines: int = 4, min_pages: int = 4
) -> list[tuple[str, str]]:
    # Map each normalized window -> set of pages it appears on.
    window_pages: dict[tuple[str, ...], set[str]] = {}
    per_page_windows: dict[str, list[tuple[str, ...]]] = {}
    for path, text in docs.items():
        lines = [text for _, text in _content_lines(text)]
        windows = []
        for i in range(0, max(0, len(lines) - min_lines + 1)):
            window = tuple(lines[i : i + min_lines])
            windows.append(window)
            window_pages.setdefault(window, set()).add(path)
        per_page_windows[path] = windows
    findings: list[tuple[str, str]] = []
    for path in docs:
        seen: set[tuple[str, ...]] = set()
        for window in per_page_windows[path]:
            if window in seen:
                continue
            pages = window_pages[window]
            if len(pages) >= min_pages:
                seen.add(window)
                findings.append(
                    (
                        path,
                        f"block starting {window[0]!r} is duplicated across "
                        f"{len(pages)} pages",
                    )
                )
        # de-dup to one finding per page for the first offending block
        if any(p == path for p, _ in findings):
            first = next((f for f in findings if f[0] == path), None)
            findings = [f for f in findings if f[0] != path]
            if first:
                findings.append(first)
    return sorted(findings)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --project bootstrapper python -m pytest bootstrapper/tests/test_content_quality.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/docs/content_quality.py bootstrapper/tests/test_content_quality.py
git commit -m "feat(docs-lint): cross-file duplicate-block finder"
```

---

### Task 3: Fix diagram-narration prose (architecture pages + README captions + diagrams README)

**Files:**
- Modify (GENERATOR — the architecture `.md` are NOT hand-authored): `bootstrapper/docs/sitegen/pages.py`. The per-slug "How To Read This View" prose for all 11 architecture pages lives in the `ARCHITECTURE_INTERPRETATIONS: dict[str, str]` mapping (~line 251); the section heading is emitted by the `architecture_pages()` template (~line 781, `## 2. How To Read This View`). The committed `docs/architecture/*.md` are regenerated from this by `make docs-build` (Layer A, `scripts/docs/canonical_references.py`) — editing the `.md` directly is reverted by the build and fails the `check_docs` drift gate. Fix the generator, then run `make docs-build` and commit the regenerated `.md` alongside `pages.py`.
- Modify (hand-authored — edit directly): `README.md:11`, `README.md:15` (the two image captions); `docs/diagrams/README.md:36-40`, `:19`, `:11-14` (cosmetic production prose). These are NOT generated.

**Interfaces:** none (editorial). Verified by `diagram_narration_findings` + `production_style_findings` returning empty over the regenerated committed `.md`, and `make docs-check` green.

- [ ] **Step 1: Establish the failing verification**

Run:
```bash
uv run --project bootstrapper python - <<'PY'
from pathlib import Path
from scripts.docs.content_quality import diagram_narration_findings, production_style_findings
root = Path(".")
hits = 0
for p in list(root.glob("docs/architecture/*.md")) + [root/"README.md", root/"docs/diagrams/README.md"]:
    t = p.read_text()
    for ln, m in diagram_narration_findings(t) + production_style_findings(t):
        print(f"{p}:{ln}: {m}"); hits += 1
print("HITS", hits)
PY
```
Expected: non-zero HITS (the current narration/style prose).

- [ ] **Step 2: Rewrite each interpretation in the generator + rename the section heading**

In `bootstrapper/docs/sitegen/pages.py`, rewrite each value of `ARCHITECTURE_INTERPRETATIONS` (one string per slug) to **only non-obvious insight** — rationale, constraints, gotchas, cross-references — dropping any sentence a reader could derive from the diagram itself. Rules:
- Delete node-name narration and "X routes to Y" / "clients enter through Kong" restatement.
- Keep only: *why* it's arranged this way, edge cases, where a boundary is enforced, and links to the authoritative page.
- Example — the `platform-overview` interpretation currently reads *"Clients enter through Kong or a deliberately published direct port. Application and agent services consume the shared LLM and data layers; LiteLLM keeps local inference and cloud-provider credentials behind one OpenAI-compatible boundary."* → keep only the non-obvious half, e.g.: *"Direct published ports bypass Kong deliberately, for host tools that can't use the `*.localhost` gateway. All model traffic — local and cloud — is funneled through LiteLLM so credentials and routing live in exactly one place (see [LLM provider flow](./llm-provider-flow.md))."*
- Since the section is no longer "how to read," rename the template heading at pages.py ~781 from `## 2. How To Read This View` to `## 2. Notes` (one edit, applies to all pages). Keep the baked `## N.` numbering contiguous — do not remove the section (removing one section from the template would renumber `## 3. Source Files`; `check_numbered_headings` enforces contiguity). If an interpretation has genuinely nothing non-obvious, use a single terse pointer sentence rather than deleting the section.
- If a bootstrapper unit test snapshots architecture-page or interpretation content, update that fixture to match; `cd bootstrapper && uv run pytest -q -k "docs or architecture or sitegen or pages"` surfaces such tests.

- [ ] **Step 3: Fix the two README captions and the diagrams-README production prose**

- `README.md:11` and `README.md:15`: replace the pixel-by-pixel image narration with a one-line functional caption (what the reader should take away, not what the pixels are). E.g. the topology caption → *"How a request reaches a service: clients → Kong → apps/agents → shared LLM + data layers."* Keep it under one line; it must not enumerate what the SVG already labels.
- `docs/diagrams/README.md`: delete the styling/production narration at `:36-40` ("same palette, same JetBrains Mono, same slate-950 background"), `:19` (palette-mirroring detail), and reduce `:11-14` to the actionable fact only (*"The top-level diagram is hand-authored; edit `docs/diagrams/architecture.html` and re-run `make docs-build`."*). Cosmetic "how it looks" belongs nowhere in prose.

- [ ] **Step 4: Regenerate, then verify finders clean + docs build green**

Run `make docs-build` FIRST (regenerates `docs/architecture/*.md` from the edited generator), THEN the Step-1 finder snippet over the regenerated `.md` → Expected: `HITS 0`.
Run: `make docs-check` → Expected: exit 0 (strict build + numbering + canonical-drift all green — proves the committed `.md` match the generator).
Restore diagram PNGs: `git restore docs/diagrams/img/` (no diagram master changed).

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/docs/sitegen/pages.py docs/architecture/ README.md docs/diagrams/README.md
# include any updated bootstrapper test fixture from Step 2 if applicable
git commit -m "docs: remove diagram-narration and production-style prose"
```

---

### Task 4: De-duplicate the pasted "Source Files" block across architecture pages

**Files:**
- Modify (GENERATOR): `bootstrapper/docs/sitegen/pages.py`. The "Source Files" block is HARDCODED as the same four bullets in the `architecture_pages()` template (~lines 785-790) for every page. Add a new `ARCHITECTURE_SOURCE_FILES: dict[str, list[str]]` mapping (one list per slug, beside `ARCHITECTURE_INTERPRETATIONS`) and change the template to render `ARCHITECTURE_SOURCE_FILES[slug]` instead of the literal list. Then `make docs-build` regenerates the committed `.md`.

**Interfaces:** Verified by `duplicate_block_findings` returning empty over the regenerated `docs/architecture/*.md`, and `make docs-check` green.

- [ ] **Step 1: Establish the failing verification**

Run:
```bash
uv run --project bootstrapper python - <<'PY'
from pathlib import Path
from scripts.docs.content_quality import duplicate_block_findings
docs = {str(p): p.read_text() for p in Path("docs/architecture").glob("*.md")}
for path, msg in duplicate_block_findings(docs, min_lines=4, min_pages=4):
    print(path, "-", msg)
PY
```
Expected: the identical 4-bullet Source Files block flagged on ~9–11 pages.

- [ ] **Step 2: Add the per-slug source-file mapping + wire the template**

In `bootstrapper/docs/sitegen/pages.py`, add `ARCHITECTURE_SOURCE_FILES: dict[str, list[str]]` with one entry per slug (same keys as `ARCHITECTURE_PERSPECTIVES`), each listing the files that **actually** drive that view — grounded against the tree:
- `network-routing-topology` → `bootstrapper/utils/kong_config_generator.py`, `bootstrapper/services/topology.py`, `services/kong/service.yml`.
- `observability-flow` → `services/{prometheus,grafana,loki,tempo,otel-collector}/service.yml` (not `tracks.yml`).
- `security-auth-secrets-boundary` → `services/kong/service.yml`, `services/supabase/service.yml`, `bootstrapper/generate_supabase_keys.py`.
- `source-configuration-model` / `track-selection-matrix` → `bootstrapper/tracks.yml`, `bootstrapper/services/topology.py` (these legitimately use them).
- `bootstrapper-lifecycle` → `bootstrapper/start.py`, `bootstrapper/core/docker_manager.py`.
- `llm-provider-flow` → `services/litellm/*`, `services/ollama/service.yml`.
- `data-rag-flow` → `services/{weaviate,backend,lightrag}/service.yml`.
- `data-engineering-lakehouse-flow` → `services/{minio,trino,iceberg-rest,spark}/service.yml`.
- `service-admission-workflow` → `bootstrapper/services/manifest_validator.py`, `bootstrapper/services/source_validator.py`.
- `platform-overview` → keep a short, genuinely-cross-cutting set (`services/*/service.yml`, `bootstrapper/services/topology.py`).
Change the template's `## 3. Source Files` block (pages.py ~785-790) to render the bullets from `ARCHITECTURE_SOURCE_FILES[slug]`. Verify each referenced path exists (`ls`); do not list a file that isn't real.

- [ ] **Step 3: Regenerate + verify no duplicate block + build green**

Run `make docs-build` (regenerates the `.md`), then the Step-1 snippet over the regenerated pages → Expected: no output (empty; per-view lists are now distinct enough that no 4-line block repeats across ≥4 pages).
Run: `make docs-check` → Expected: exit 0. Restore PNGs: `git restore docs/diagrams/img/`.

- [ ] **Step 4: Commit**

```bash
git add bootstrapper/docs/sitegen/pages.py docs/architecture/
git commit -m "docs: replace copy-pasted Source Files block with per-view sources"
```

---

### Task 5: Rewrite marketing-tone READMEs to house style

**Files:**
- Modify: `services/kong/README.md`, `services/supabase/README.md`, `services/doc-processor/README.md`

**Interfaces:** Verified by `marketing_adjective_findings(..., is_service_readme=True)` empty for these files, and `make docs-check` green.

- [ ] **Step 1: Establish the failing verification**

Run:
```bash
uv run --project bootstrapper python - <<'PY'
from pathlib import Path
from scripts.docs.content_quality import marketing_adjective_findings
for name in ["kong","supabase","doc-processor"]:
    p = Path(f"services/{name}/README.md")
    for ln, w in marketing_adjective_findings(p.read_text(), is_service_readme=True):
        print(f"{p}:{ln}: {w}")
PY
```
Expected: hits for "intelligent" (kong:7, doc-processor:7), "AI-powered" (doc-processor:3), etc.

- [ ] **Step 2: Rewrite to clinical, factual prose**

Match the register of `services/backend/README.md` / `services/litellm/README.md`. Replace claims-of-quality with statements-of-fact:
- kong:3 *"intelligent API gateway"* → *"API gateway. Routes `*.localhost` requests to services using configuration generated at startup."*
- kong:7 / kong:12 — delete the "Unlike traditional…" promotional framing; state what it does.
- supabase:3 *"core database infrastructure"* → *"Provides Postgres, Auth, Storage, and the REST/Realtime APIs Atlas builds on."*
- doc-processor:3/:7 — drop "AI-powered"/"intelligent"; describe the conversion/extraction pipeline factually.
- Remove filler lead-ins ("consists of multiple integrated services:", "offers … with:") that restate the H1.

- [ ] **Step 3: Verify finder clean + build green**

Run the Step-1 snippet → Expected: empty.
Run: `make docs-check` → Expected: exit 0. Restore PNGs.

- [ ] **Step 4: Commit**

```bash
git add services/kong/README.md services/supabase/README.md services/doc-processor/README.md
git commit -m "docs: rewrite kong/supabase/doc-processor READMEs to house style"
```

---

### Task 6: Fix ungrounded / drifting facts

**Files:**
- Modify: `services/ollama/README.md:85` (`default_active` → `default`)
- Modify: `README.md:108` and `services/ollama/README.md:56` / `docs/quick-start/interactive-setup-wizard.md:65` (reconcile the library count)
- Modify: `README.md:528` (the "2,800+ tests" figure)

**Interfaces:** none. Verified by grep + a grounded recount.

- [ ] **Step 1: Correct the manifest key name**

In `services/ollama/README.md:85`, change `default_active: true` to `default: true` (the real key per `services/ollama/models.yaml:12`). Verify:
`grep -n "default_active" services/ollama/README.md` → Expected: no output.

- [ ] **Step 2: Reconcile the Ollama library-count figure**

Determine the true number and use it everywhere. Recount:
`grep -c "^- name:" services/ollama/models.yaml` (or the actual scrape source the docs describe). Replace `README.md:108` "a few hundred entries" and confirm `services/ollama/README.md:56` / `interactive-setup-wizard.md:65` all state the SAME verified figure (e.g. "~230"). If the exact number is volatile, use one hedged phrasing ("~230") consistently, not two different ones.

- [ ] **Step 3: Correct or de-numeric the test-count claim**

Recount: `grep -rhoE "def test_[A-Za-z0-9_]+" bootstrapper/tests | wc -l`. Set `README.md:528` to a grounded statement — either the real order-of-magnitude ("2,300+ tests") or a non-numeric phrasing ("an extensive pytest suite") rather than an unverifiable "2,800+".

- [ ] **Step 4: Verify + build green**

Run: `grep -rn "default_active\|2,800+\|a few hundred" README.md services/ollama/README.md docs/quick-start/interactive-setup-wizard.md` → Expected: no stale hits.
Run: `make docs-check` → Expected: exit 0. Restore PNGs.

- [ ] **Step 5: Commit**

```bash
git add README.md services/ollama/README.md docs/quick-start/interactive-setup-wizard.md
git commit -m "docs: ground ollama model-key, library count, and test-count claims"
```

---

### Task 7: Register the gate in the docs-drift audit + prove full green

**Files:**
- Modify: `scripts/check-docs-drift.py` (add `check_content_quality()` + register in the `checks` dict)

**Interfaces:**
- Consumes: `content_quality.{diagram_narration_findings, production_style_findings, marketing_adjective_findings, duplicate_block_findings}` from Tasks 1–2; `heading_quality.documentation_paths`.
- Produces: a new `content_quality` entry in the audit's `checks` registry.

- [ ] **Step 1: Write the check function + register it**

Add to `scripts/check-docs-drift.py`, mirroring `check_numbered_headings()` (which loops `documentation_paths(ROOT)`):

```python
from scripts.docs.content_quality import (
    diagram_narration_findings,
    production_style_findings,
    marketing_adjective_findings,
    duplicate_block_findings,
)

def _is_service_readme(path):
    parts = path.relative_to(ROOT).parts
    return len(parts) >= 3 and parts[0] == "services" and parts[-1] == "README.md"

def check_content_quality():
    hits = []
    docs = {}
    for path in documentation_paths(ROOT):
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(ROOT).as_posix()
        docs[rel] = text
        for line_number, message in diagram_narration_findings(text):
            hits.append(f"{rel}:{line_number}: diagram-narration: {message!r}")
        for line_number, message in production_style_findings(text):
            hits.append(f"{rel}:{line_number}: production-style prose: {message!r}")
        for line_number, word in marketing_adjective_findings(
            text, is_service_readme=_is_service_readme(path)
        ):
            hits.append(f"{rel}:{line_number}: marketing adjective {word!r}")
    for rel, message in duplicate_block_findings(docs):
        hits.append(f"{rel}: {message}")
    return hits
```
Then add `'content_quality': check_content_quality(),` to the `checks` dict in `main()`.

- [ ] **Step 2: Run the audit — expect PASS now that content is clean**

Run: `uv run --project bootstrapper python scripts/check-docs-drift.py`
Expected: `PASS content_quality` among the results, exit 0. (If any hit remains, fix that page and re-run — the content tasks above must have cleared them all.)

- [ ] **Step 3: Run the full unit suite + docs gate**

Run: `uv run --project bootstrapper python -m pytest bootstrapper/tests/test_content_quality.py -q` → PASS.
Run: `make docs-check` → exit 0.
Run: `cd bootstrapper && uv run pytest -q` → all pass (confirms nothing else broke); then `git restore docs/diagrams/img/` from repo root.

- [ ] **Step 4: Commit**

```bash
git add scripts/check-docs-drift.py
git commit -m "feat(docs-lint): enforce content-quality gate in docs-drift audit"
```

---

## Self-Review

**Spec coverage (against §6.2–6.3 of the design):**
- Content-lint gate wired into the docs audit → Tasks 1, 2, 7. ✅
- Diagram-narration removal (11 pages + 2 captions + diagrams README) → Task 3. ✅
- "Source Files" block de-dup → Task 4. ✅
- Tone READMEs (kong/supabase/doc-processor) → Task 5. ✅
- Ungrounded facts (default_active, Ollama count, test count) → Task 6. ✅
- Green-on-introduction ordering (content fixed before gate enforces) → Tasks 3–6 precede Task 7. ✅

**Placeholder scan:** No "TBD/handle edge cases/similar to Task N". Editorial tasks cite exact files/lines + the specific defect + the corrected text + a machine verification (finder snippet / grep / `make docs-check`). ✅

**Type consistency:** finder signatures defined in Tasks 1–2 (`diagram_narration_findings`, `production_style_findings`, `marketing_adjective_findings(text, *, is_service_readme)`, `duplicate_block_findings(docs, *, min_lines, min_pages)`) are consumed with those exact names/kwargs in Task 7. ✅

**Note on the exhaustive sweep:** the gate in Task 7 runs over `documentation_paths(ROOT)` (every tracked `.md`), so if any *unsampled* page carries a defect class, Step 2 fails and that page must be fixed before commit — this is what makes the pass exhaustive rather than limited to the audit's samples.
