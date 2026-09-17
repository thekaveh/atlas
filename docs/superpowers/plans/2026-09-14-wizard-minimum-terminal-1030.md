# Wizard Minimum-Terminal Usability (#1030) Implementation Plan

> **Status: archived plan** — dated 2026-09-14; a point-in-time record kept for archaeology, not current guidance. The landed outcome is recorded in the [CHANGELOG](../../CHANGELOG.md).

> **For agentic workers:** Execute sequentially in this session with TDD and a separate code review. The campaign directive authorizes implementation and both PR merges without another approval checkpoint.

**Goal:** Make every wizard prompt and its essential actions usable at the accepted 60×20 terminal floor while preserving state through live resize and leaving the normal layout unchanged.

**Architecture:** `WizardScreen` owns one compact-height breakpoint and toggles presentation classes on the permanently mounted widget tree. `BrandPanel` renders the configured identity in either its existing block lockup or a compact one-row form. `FooterBar` condenses only the hint presentation. No prompt or input is remounted on resize.

**Tech stack:** Python >=3.10, Textual 8.2.8, Rich, pytest, canonical three-surface Markdown pipeline.

## 1. Verified baseline and scope boundaries

- Current branch started from `origin/develop` `3d6d0ab9426c817f349af08560c3f80efaab4456` and was pushed before repository edits.
- Textual compositor reproduction at 60×20 places `PromptPanel` at y=23 and `CommandSummary` at y=21, with zero visible rows for both.
- The prompt becomes fully visible only at 27 rows in the minimal two-option fixture; existing short-screen coverage checked the summary from 30 rows upward.
- `is_tui_capable` accepts 60×20 and rejects 59×20 and 60×19. That contract stays unchanged.
- Textual 8.2.8 exposes `Pilot.resize_terminal(width, height)` by posting a real `Resize` message; tests use that public seam.
- Preserve all selection, search, input, command, launch, palette, output, progress, tab and large-screen behavior.
- Do not change Docker images, dependencies, manifests, service architecture, VMx boundaries or the linear-flow implementation.

## 2. Acceptance matrix

| Acceptance criterion | Planned change | Verification evidence |
|---|---|---|
| Every prompt type exposes focus, relevant input/options and navigation at 60×20 | Compact brand strip; hide setup overview; collapse gutters; reserve rows for prompt, summary and action footer; cap long subtitle | Parameterized compositor/keyboard tests for options, multiselect, number, secret and text, including long/wrapped descriptions |
| Normal → minimum → normal keeps selection and focus with no blank prompt | Toggle CSS/display state on mounted widgets; never call `load_step` or rebuild on resize | `Pilot.resize_terminal` object-identity, focus, input value, selected/check state and rendered-row assertions |
| Floor and one-row/column-below behavior agree | Keep 60×20 gate; assert 59×20 and 60×19 reject | Existing gate test plus issue-specific boundary contract |
| Screenshots retain Atlas identity and normal behavior | Compact configured-brand wordmark; unchanged normal CSS path | Captured 60×20 and 120×44 setup screenshots, rendered to SVG/PNG and referenced by canonical wizard guide |
| Keyboard focus has no traps; contrast/status accessible | Compact action hints; keep focus styles, cursor/checkbox/icon/text status cues and current palette | Keyboard pilots, 4.5:1 palette tests, rendered text assertions; record no screen-reader claim |
| Review and launch states stay useful | Final confirm keeps prompt/summary; launch Setup reveals overview; Logs remains visible with applicable exit action | Compositor tests at 60×20 for final prompt, Setup tab, Logs tab and resize |

## 3. Task 1: Add failing minimum-size and resize regressions

**Files:**
- Create `bootstrapper/tests/test_wizard_minimum_terminal_1030.py`.
- Reuse public fixtures/contracts from `ui.textual.screens.wizard_screen`, `PromptPanel`, `BrandPanel`, `FooterBar` and `ui.term_caps`.

- [x] Add a 60×20 render helper that computes actual viewport intersection, rather than trusting `Widget.visible` for off-screen regions.
- [x] Parameterize all five prompt kinds and assert the focused input/option, prompt border, command summary, next/back action text and Atlas identity have visible compositor rows.
- [x] Include a wrapped long-description prompt and a long multiselect list; move and toggle via keys to prove cursor-following scroll.
- [x] Resize 120×44 → 60×20 → 120×44 after editing an input and selecting/toggling an option. Assert the same widget holds focus and all state/rendering survives.
- [x] Cover the final review prompt, compact launch Logs and compact launch Setup overview.
- [x] Pin 60×20 acceptance and 59×20 / 60×19 fallback in the same issue suite.
- [x] Run the new file and retain the RED output proving current layout failure (11 failures, 4 passes before the compact implementation).

## 4. Task 2: Implement in-place compact presentation

**Files:**
- Modify `bootstrapper/ui/textual/screens/wizard_screen.py`.
- Modify `bootstrapper/ui/textual/widgets/block_logo.py`.
- Modify `bootstrapper/ui/textual/widgets/footer_bar.py`.

- [x] Add a named short-height breakpoint below 30 rows and an idempotent resize handler.
- [x] Toggle `compact-height` on the screen and `launch-phase` when launch begins; refresh footer hints without touching prompt state.
- [x] Add a configured-brand compact rendering path to `BlockLogo`/`BrandPanel`; preserve the existing block art and border/tab logic at normal height.
- [x] In compact setup, hide the overview, reduce brand/prompt/gutter budgets, constrain descriptive text and keep prompt, summary and footer visible.
- [x] In compact launch, expose the overview on Setup and preserve the Logs body; show condensed applicable action hints.
- [x] Rerun the issue suite until all minimum-size, keyboard and resize contracts pass (27 passed with warnings as errors after review-driven width, prompt-transition and screenshot checks).

## 5. Task 3: Documentation and visual evidence

**Files:**
- Modify `docs/quick-start/interactive-setup-wizard.md`.
- Add `docs/screenshots/wizard-minimum-terminal.svg`.
- Add `docs/screenshots/wizard-normal-terminal.svg`.
- Modify `scripts/docs/build_docs.py`.
- Modify `bootstrapper/tests/test_three_surface_build.py`.
- Update this plan with final evidence.

- [x] Document the 60×20 floor, compact behavior, 59×20/60×19 linear fallback, resize state preservation, keyboard controls and WCAG 2.2 AA 4.5:1 target.
- [x] State that keyboard/compositor checks do not establish screen-reader compatibility.
- [x] Capture deterministic Textual SVG screenshots at 60×20 and 120×44 from the same representative prompt, inspect their compositor geometry/text and reference them from the canonical guide.
- [x] Map copied `docs/assets` and `docs/screenshots` paths through the existing
  surface rewriter. The first docs run proved that a nested canonical page's
  `../screenshots/...` path remains correct on the nested site page but breaks
  after the wiki projection flattens that page to its root.
- [x] Add a nested-page projection regression proving the site keeps
  `../screenshots/...`, the wiki rewrites it to `screenshots/...`, and both
  copied assets exist. This is the smallest complete three-surface correction;
  it does not change page URLs, HTML-link rules, diagram handling or publishing.
- [x] Run the three-surface drift/audit pipeline and verify the built site/wiki copies contain both images and the updated prose (`make docs-check` passed).

## 6. Task 4: Review, regression and delivery

- [x] Run focused prompt, tab, footer, palette, terminal-gate and issue tests with warnings as errors.
- [x] Run the broader wizard/TUI suite, required docs checks, `git diff --check`, and repository-required validation appropriate to the final diff (561 passed, 3 skipped; docs gate passed).
- [x] Inspect the complete diff for resize races, focus/state loss, clipped actions, custom-brand regressions, tab click geometry, normal-layout drift, color-only status and generated-doc drift.
- [x] Obtain an independent issue-scoped review, present concrete findings, fix every valid finding and rerun affected checks. The review found height-only footer density, stale/dead prompt hints, missing named quit affordance and grayscale evidence; all were fixed, and final re-review reported no unresolved findings.
- [ ] Commit and push; open the issue PR to `develop` with the AC matrix, screenshots and exact validation.
- [ ] Wait for current required checks and conversations, resolve findings, squash-merge and verify the develop revision and post-merge workflows.
- [ ] Inspect and merge the protected `develop` → `main` promotion, then verify main contents, required post-merge CI and documentation publication.
- [ ] Comment on and close #1030, mark its project item Done, remove only campaign-owned branches/artifacts, prune refs and checkpoint the ledger before #1032.
