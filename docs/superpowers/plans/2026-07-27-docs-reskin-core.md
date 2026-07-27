# Docs .io Reskin Core ("Clean Systems", light-first) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Reskin the Atlas `.io` MkDocs-Material site from its stock dark-space look to the "Clean Systems" identity chosen from the direction mockup — light-first, airy, one calm blue accent, hairline borders, refined typography, with a proper Atlas logomark + favicon — and a dark theme given equal care.

**Architecture:** Three coupled levers, all committed source (the site is regenerated from them): the theme config dict in `scripts/docs/build_docs.py::render_mkdocs_yml` (palette order, fonts, logo, favicon, features), the stylesheet `docs/assets/stylesheets/atlas.css` (the token system + component styling, copied verbatim into the built site by `render_site`), and new brand assets under `docs/assets/` (logomark SVG + favicon, auto-copied by `render_site`). The rich landing page is deferred to PR #4; this PR keeps the existing `docs/index.md` hero working under the new theme.

**Tech Stack:** MkDocs + Material for MkDocs (theme.font self-hosts Google Fonts at build), CSS custom properties, SVG; the Atlas docs pipeline (`make docs-build` / `make docs-check` → `mkdocs build --strict`).

**This is PR #3 of 4** in `docs/superpowers/specs/2026-07-26-docs-overhaul-and-reskin-design.md` §7. Independently shippable.

## 1. Global Constraints

- **Direction is fixed:** "Clean Systems", light-first, pure (per the spec + the user's mockup choice). Do NOT reintroduce the dark-space "Orbit" look. Dark mode is a true equal, not an afterthought.
- **Pinned tokens (spec §7.1 — final hex confirmed here):**
  - Light: bg `#ffffff`, surface `#f8fafc`, text `#0f172a`, muted `#5b6472`, border `#e6e9ef` (cool-biased neutral, a chosen grey), accent `#2563eb`, accent-hover `#1d4ed8`, link `#1d4ed8`, code-bg `#0f172a` / code-fg `#e2e8f0`.
  - Dark (true equal): bg `#0d1117`, surface `#161b22`, text `#e6edf3`, muted `#8b949e`, border `#21262d`, accent `#5b8bff`, link `#79a6ff`.
  - Semantic admonition colors stay separate from the blue accent.
- **Typography:** self-host via Material `theme.font` — text `Public Sans`, code `JetBrains Mono` (deliberately not the Inter/Space-Grotesk cliché; Public Sans is neutral + legible). Set a type scale; body near 65–72ch; headings `text-wrap: balance`.
- **Map tokens to Material's variables:** style through Material's `--md-*` custom properties per `[data-md-color-scheme]` (`default` = light, `slate` = dark), not by hard-coding component colors. Palette order in `render_mkdocs_yml` must be light-first (`default` scheme first so the site defaults to light; `slate` second).
- **Strict build must stay clean:** `make docs-check` runs `mkdocs build --strict` — 0 warnings. Any CSS referencing a missing asset, or a broken nav/link, fails it.
- **Generated artifacts:** `mkdocs.yml`, `site/`, `generated/` are gitignored build outputs — never commit them; change `render_mkdocs_yml` instead. `render_site` copies `docs/assets/**` into the built site, so brand assets go under `docs/assets/`.
- **No content changes:** this PR is theme/CSS/assets only. Don't edit page prose (the content-quality gate still applies to any prose you do touch — no marketing adjectives).
- **PNG-drift trap:** `make docs-build` rewrites `docs/diagrams/img/*.png`; `git restore docs/diagrams/img/` before committing. Targeted `git add`; NEVER `git add -A`.
- **Visual acceptance is post-deploy:** the true look is the built site in a browser (light + dark). These tasks verify the build is clean and the CSS is coherent/valid; final pixel sign-off is the maintainer's on the deployed `.io` site.
- ENVIRONMENT NOISE: every shell command prints a harmless first line about `/Users/kaveh/.zshenv` / `vmx-cargo` — ignore it.

---

### 1.1. Task 1: Atlas logomark + favicon

**Files:**
- Create: `docs/assets/brand/atlas-logo.svg` (nav wordmark/mark), `docs/assets/brand/favicon.svg`
- (favicon PNG fallback optional: `docs/assets/brand/favicon.png`)

**Interfaces:** the asset paths are consumed by Task 2's theme config (`theme.logo`, `theme.favicon`). Produces: the two asset paths under `docs/assets/brand/`.

- [ ] **Step 1: Design a simple geometric Atlas mark (SVG)**

Create `docs/assets/brand/atlas-logo.svg` — a clean, geometric monochrome mark that works at ~24px nav height and reads on both light and dark headers. Use `currentColor` (or a token) so it inherits the header foreground in both themes, OR provide a mark that works on the accent. Keep it simple (a geometric "A" / layered-map/atlas motif) — hand-authored SVG, valid XML, no external refs, viewBox set. Keep it small (< ~2KB).

- [ ] **Step 2: Favicon**

Create `docs/assets/brand/favicon.svg` (a compact square version of the mark, fixed colors so it renders as a tab icon). Validate it's well-formed XML (`python -c "import xml.dom.minidom,sys; xml.dom.minidom.parse('docs/assets/brand/favicon.svg')"`).

- [ ] **Step 3: Verify assets are copyable + valid**

Run `python -c "import xml.dom.minidom; [xml.dom.minidom.parse(p) for p in ['docs/assets/brand/atlas-logo.svg','docs/assets/brand/favicon.svg']]; print('svg ok')"`. Confirm the files are under `docs/assets/` (so `render_site` copies them).

- [ ] **Step 4: Commit**

```bash
git add docs/assets/brand/
git commit -m "docs(reskin): add Atlas logomark + favicon assets"
```

---

### 1.2. Task 2: Theme config — light-first palette, fonts, logo, favicon, features

**Files:**
- Modify: `scripts/docs/build_docs.py` (`render_mkdocs_yml`, ~line 138)
- Test: `bootstrapper/tests/` — add/extend a test asserting the rendered `mkdocs.yml` config (see Step 1)

**Interfaces:**
- Consumes: the brand asset paths from Task 1 (`assets/brand/atlas-logo.svg`, `assets/brand/favicon.svg` — relative to `docs_dir`/site root, which is where `render_site` places `assets/`).
- Produces: the updated theme dict.

- [ ] **Step 1: Write/extend a failing test for the config**

Add `bootstrapper/tests/test_mkdocs_theme.py` (sys.path shim to repo root like `test_content_quality.py`) asserting `render_mkdocs_yml(manifest)` output:

```python
import sys, yaml
from pathlib import Path
_R = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_R))
from scripts.docs.build_docs import render_mkdocs_yml  # noqa: E402
from scripts.docs.manifest import load_manifest  # noqa: E402

def _cfg():
    m = load_manifest(_R / "docs/manifest.yaml", _R)
    return yaml.safe_load(render_mkdocs_yml(m))

def test_palette_is_light_first():
    schemes = [p["scheme"] for p in _cfg()["theme"]["palette"]]
    assert schemes[0] == "default" and schemes[1] == "slate"

def test_theme_declares_font_logo_favicon():
    t = _cfg()["theme"]
    assert t["font"]["text"] == "Public Sans"
    assert t["font"]["code"] == "JetBrains Mono"
    assert t["logo"] == "assets/brand/atlas-logo.svg"
    assert t["favicon"] == "assets/brand/favicon.svg"
```

Run: `uv run --project bootstrapper python -m pytest bootstrapper/tests/test_mkdocs_theme.py -q` → FAIL (palette dark-first, no font/logo/favicon).

- [ ] **Step 2: Update `render_mkdocs_yml`**

In the `theme` dict: reorder `palette` so `{"scheme": "default", ...}` (light) is FIRST and `{"scheme": "slate", ...}` (dark) is second (swap the toggle icons/names accordingly — light shows the moon "switch to dark", dark shows the sun "switch to light"). Add `"font": {"text": "Public Sans", "code": "JetBrains Mono"}`, `"logo": "assets/brand/atlas-logo.svg"`, `"favicon": "assets/brand/favicon.svg"`. Add features `"navigation.tabs"`, `"navigation.tabs.sticky"`, `"navigation.footer"`, `"content.tabs.link"` to the existing list (keep the current ones). Keep `primary: custom` / `accent: custom`.

- [ ] **Step 3: Run test + strict build**

`uv run --project bootstrapper python -m pytest bootstrapper/tests/test_mkdocs_theme.py -q` → PASS.
`make docs-build && make docs-check` → exit 0 (mkdocs build --strict must fetch Public Sans/JetBrains Mono and succeed with 0 warnings; if the logo/favicon path 404s, the build warns — fix the path). `git restore docs/diagrams/img/`.

- [ ] **Step 4: Commit**

```bash
git add scripts/docs/build_docs.py bootstrapper/tests/test_mkdocs_theme.py
git commit -m "docs(reskin): light-first palette, Public Sans/JetBrains Mono fonts, logo + favicon"
```

---

### 1.3. Task 3: Rewrite `atlas.css` to the Clean Systems system

**Files:**
- Modify (rewrite): `docs/assets/stylesheets/atlas.css`

**Interfaces:** consumes the palette schemes set in Task 2 (`[data-md-color-scheme="default"]` light, `[="slate"]` dark). Produces the visual system.

- [ ] **Step 1: Replace the token block**

Rewrite the `:root` + `[data-md-color-scheme="default"]` + `[data-md-color-scheme="slate"]` blocks to map the pinned Clean Systems tokens (Global Constraints) onto Material's variables: `--md-default-bg-color`, `--md-default-fg-color`, `--md-primary-fg-color`, `--md-accent-fg-color`, `--md-typeset-a-color`, `--md-code-bg-color`, `--md-code-fg-color`, `--md-default-fg-color--light/--lighter` (muted), and border via `--md-typeset-table-color` / custom `--atlas-border`. Light = `default` scheme (bg #ffffff, text #0f172a, accent #2563eb, border #e6e9ef); dark = `slate` (bg #0d1117, text #e6edf3, accent #5b8bff, border #21262d). Remove the dark-space `--atlas-void` gradient system.

- [ ] **Step 2: Component polish (hairlines + airy)**

Replace the translucent dark header + gradient `.md-main` with: a clean header (light: white with a 1px `--atlas-border` bottom, subtle; dark: `#0d1117` with `#21262d` border), no big gradients. Hairline borders + generous spacing on `.md-typeset table`, `.md-typeset code`/`pre` (rounded, `--md-code-bg`), admonitions (thin left rule in the semantic color, calm background), and a comfortable content measure. Style nav-tabs (from Task 2's `navigation.tabs`) with the accent underline on the active tab. `text-wrap: balance` on `h1,h2`.

- [ ] **Step 3: Restyle the existing `.atlas-home*` hero for Clean Systems**

Keep the `docs/index.md` hero working (PR #4 rebuilds the full landing) but restyle `.atlas-home`, `.atlas-home__hero`, `.atlas-home__actions`, `.atlas-kicker`, `.atlas-home__media` for the light-first clean look — accent CTA buttons (`#2563eb`, white text, hover `#1d4ed8`), calm kicker, airy grid, poster with a subtle border/radius. Ensure it works in both themes.

- [ ] **Step 4: Verify build + both themes**

`make docs-build` (copies the new CSS into the built site) then `make docs-check` → exit 0 (strict build clean). Open `site/index.html` region / grep the built CSS to confirm `atlas.css` was copied and references resolve. `git restore docs/diagrams/img/`. (Visual light/dark QA is the maintainer's post-deploy — note anything you couldn't verify.)

- [ ] **Step 5: Commit**

```bash
git add docs/assets/stylesheets/atlas.css
git commit -m "docs(reskin): rewrite atlas.css to the Clean Systems light-first system"
```

---

### 1.4. Task 4: Full verification

**Files:** none (verification).

- [ ] **Step 1: Complete gate set (all green)**

- `make docs-check` → exit 0 (mkdocs build --strict, 0 warnings; link + wiki checks pass)
- `uv run --project bootstrapper python -m pytest bootstrapper/tests/test_mkdocs_theme.py -q` → pass
- `uv run --project bootstrapper python scripts/check-docs-drift.py` → all PASS
- `uv run --project bootstrapper python scripts/check_doc_links.py` → exit 0
- `cd bootstrapper && uv run pytest -q` → full suite green (report count; ~5-6 min, foreground, WAIT)
- Confirm the built `site/` uses the new palette: grep `site/assets/stylesheets/atlas.css` for `#2563eb` (accent present) and confirm no `#020617` void remains; confirm `site/assets/brand/atlas-logo.svg` + `favicon.svg` were copied.
- `git restore docs/diagrams/img/` from repo root.

- [ ] **Step 2: Note visual-QA items for the maintainer**

In the report, list what could NOT be verified headlessly (actual rendered appearance in light/dark, font loading, logo legibility at nav size) so the maintainer can sign off on the deployed site.

- [ ] **Step 3: Commit (if any verification tweak was needed; else no-op)**

Only commit if Step 1 required a fix; otherwise report clean.

## 2. 1.5. Self-Review

**Spec coverage (§7.1-7.5):** tokens + light-first palette → Tasks 2-3. Fonts → Task 2. Logo/favicon → Tasks 1-2. CSS rewrite / component polish → Task 3. (Rich landing §7.6 is PR #4.) Verification → Task 4.

**Placeholder scan:** none — pinned hex + exact Material variables + exact config keys given.

**Honest limitation:** headless verification proves the build is clean and the CSS/config are coherent and reference real assets; it does NOT prove the rendered look. Task 4 Step 2 surfaces that for maintainer sign-off — not a silent gap.
