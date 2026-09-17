# Wizard Recovery (#1029) Implementation Plan

> **Status: archived plan** — dated 2026-09-13; a point-in-time record kept for archaeology, not current guidance. The landed outcome is recorded in the [CHANGELOG](../../CHANGELOG.md).

> **For agentic workers:** Execute sequentially with executing-plans and a separate code review. The campaign directive authorizes implementation and both PR merges without another approval checkpoint.

**Goal:** Replace unsafe, speculative recovery advice with non-destructive diagnosis and explicit, time-bounded destructive confirmation.

**Architecture:** Retain `WizardScreen` actions and log rendering. Use fixed prose and narrow classification in the existing hint method; record a monotonic confirmation deadline. Share no new mutable domain registry.

**Tech Stack:** Python >=3.10, Textual, pytest, canonical three-surface Markdown pipeline.

## 1. Constraints

- Preserve colors, outputs, progress, navigation and large-screen layout.
- No permission changes, credential changes or real volume deletion in tests.
- No changes to image pins, dependencies, headless cold semantics or VMx layers.
- New commits use the public GitHub no-reply identity; protected branches receive PRs only.

## 2. Acceptance matrix

| Criterion | Change | Evidence |
|---|---|---|
| No recursive chmod 777 advice | Replace permission branch | Synthetic permission logs produce no mutating command |
| Known path or explicit incomplete diagnosis | Fixed known-path hints; unknown UID/GID disclosure | Known LiteLLM/Kong and arbitrary-path fixtures |
| Distinguish auth causes before deletion | Availability/configuration/stored-credential guidance | Supabase, generic auth, unavailable and combined fixtures |
| Explicit reset scope and confirmation; diagnosis/retry retained | Detailed cold action warnings, matching keys and deadline | Fake stopper, expired/mixed-key cases, rendered log, cold-start prompt |

## 3. Tasks

### 3.1. Regression tests and recovery implementation

Files: `bootstrapper/tests/test_wizard_recovery_1029.py`,
`bootstrapper/ui/textual/screens/wizard_screen.py`.

- [x] Add synthetic log fixtures calling the actual `_emit_failure_hints` method and asserting no `chmod`, `chown` or `--cold` recovery command, honest unknown UID/GID, and configuration/stored-credential distinctions.
- [x] Run `uv run --project bootstrapper pytest bootstrapper/tests/test_wizard_recovery_1029.py -q`; observe semantic assertion failures before implementation.
- [x] Replace the speculative branches with independent permission/authentication/unavailable classifications. Preserve unrelated-log silence and avoid copying arbitrary error content into generated guidance.
- [x] Rerun the new tests and existing launch-log tests.

### 3.2. Explicit destructive-action semantics

Files: the same test/screen files, `bootstrapper/ui/textual/integration.py`,
`bootstrapper/ui/textual/widgets/prompt_panel.py`.

- [x] Add tests asserting target project and affected data categories appear before the second cold-stop press; use a fake stopper to prove no first-press side effect.
- [x] Set `_pending_teardown_deadline = time.monotonic() + 8` when arming; require both matching action and unexpired deadline to commit. Test expiry by controlling monotonic time, not sleeping.
- [x] Expand cold-start prompt copy to name project volume loss and env/key regeneration; keep its non-destructive default and explicit selection.
- [x] Run `uv run --project bootstrapper pytest bootstrapper/tests/test_stack_stop_actions.py bootstrapper/tests/test_wizard_recovery_1029.py -q`.

### 3.3. Documentation, visual verification and delivery

Files: `docs/quick-start/interactive-setup-wizard.md` and this plan.

- [x] Document recovery order, cold-stop scope, eight-second confirmation and safe retry in the canonical guide. No generated page edits.
- [x] Capture before/after Textual recovery screenshots using synthetic failures. Confirm text is visible and existing palette, tabs and filters remain.
- [x] Run relevant wizard/launch regressions and `make docs-check`.
- [ ] Pass required CI checks on the final PR revision.
- [x] Review complete diff and AC evidence; fix any issue-scoped findings and rerun affected tests.
- [ ] Commit/push, create and squash the develop PR after checks; repeat with develop-to-main promotion. Verify relevant post-merge/publication workflows.
- [ ] Comment/close issue, update board, remove campaign branch and test artifacts, and checkpoint the campaign ledger before #1031.

## 4. Baseline and research

Existing targeted baseline: `test_stack_stop_actions.py` and
`test_tui_launch_log.py`: **10 passed**. Live rules require four services-lint
checks, current base, and resolved review conversations, with zero required
approvals. No open PRs at campaign start. Upstream scope reference:
https://docs.docker.com/reference/cli/docker/compose/down/ .

## 5. Verification and review evidence

- Regression-first run: 16 failed, 1 passed before production changes.
- Final recovery suite: 18 passed, including actual composed-screen warning
  visibility and restoration of ordinary prompt subtitle height.
- Wizard/TUI/stop/post-launch regression run: 256 passed, 1 skipped.
- Prompt option/group suite: 42 passed.
- `make docs-check`: passed canonical contracts, strict build, built-site links
  and wiki dry run; the standard generator refreshed the plan archive date.
- Independent review found the expanded cold-start warning clipped by the
  existing one-line subtitle. Opt-in wrapping and reduced decorative spacing
  fixed it. The full warning and both choices render at 120×40. Re-review
  found no unresolved actionable findings. Smaller terminal layout limitations
  also occur without this change and remain tracked separately in #1030.
- Tests use synthetic logs and fake teardown; no user volumes were removed.
- Required CI, both merges and post-merge publication remain pending.

## 6. CI review follow-up

The first required CI run reported 6,075 passed, 11 skipped and two complexity
failures. Local reproduction confirmed recovery classification crossed the C
threshold and caption wrapping added a branch to the reviewed `load_step`
symbol. Permission diagnosis and caption rendering now have focused helpers;
independent review confirmed preserved behavior. All code-complexity ceilings
remain unchanged.

Once those failures were fixed, the inventory gate exposed the three expected
new files: this plan, its design spec and the recovery regression suite. A
separate inventory maintenance review attributed exactly 1,545 → 1,548 files to
`7a8e777b..f7bf572e`. The durable `.maintenance.json` review lists each path and
purpose. Only that inventory snapshot and its pinned expectation are refreshed;
counting rules, immutable-snapshot assertions and review deadlines remain.

The separate non-required image scan reports existing Blender vendored Python
debt tracked in #1002. No image or scanner configuration is changed here.

Follow-up verification: maintenance plus recovery **27 passed**; wizard/TUI/stop/
post-launch **256 passed, 1 skipped**; `make docs-check` and `git diff --check`
passed. The existing full-warning compositor test confirms unchanged rendering.
