# Wizard UX Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix seven wizard/launch-screen defects reported from a live run — inert tab styling, a stale prompt left on the Setup tab after launch, no way to stop the stack, no `auto` base port, hint text that spills its indentation, and "profile" meaning two different things.

**Architecture:** All changes live in `bootstrapper/ui/textual/` plus the wizard step builder in `ui/textual/integration.py`. One change reaches into `bootstrapper/stop.py`'s existing `AtlasStopper` API rather than shelling out to `./stop.sh`. No pipeline, compose, or service-resolution logic changes.

**Tech Stack:** Python ≥3.10, Textual 6.2.1 (pinned `textual>=0.85`), Rich, pytest + headless `App.run_test()`.

## 1. Global Constraints

- Textual is pinned `textual>=0.85`; installed version is **6.2.1**. Do not add or bump dependencies.
- **Never launch Atlas, run `./start.sh`/`./stop.sh`, or touch the GPU.** Another instance may be running and the GPU may be in use. All verification is `pytest` + headless `run_test()`.
- Run tests from `bootstrapper/`: `cd bootstrapper && uv run pytest ...`
- Bare `[` in a `border_subtitle` is consumed as console markup and must be escaped `\[`. Console markup **does** render there (probed: `[#7dcfff bold]` produced `style=bold #7dcfff`).
- **The byline-only border path must stay byte-identical to its pre-tabs output.** That property cost three fix rounds to establish (stray `╰─ ─` corner artifact + premature byline ellipsis). Any change to `_render_border` must re-verify it at widths 60/90/140/200.
- New/changed functions must stay **under 60 physical lines** — `tests/test_maintenance_baseline.py` asserts `functions_over_60_physical_lines <= 189` and separately `== 189`. Do not touch `.maintenance.json`; hoist docstrings to module level if a function grows.
- Preserve all existing output, colors, borders, and panel titles (repo editing rule).
- Headings in `docs/` must be hierarchically numbered — the structural docs audit in `make docs-check` fails otherwise.
- Docs are required with every change: CHANGELOG entry + any affected spec/README, never deferred.

## 2. Decisions Already Made

Confirmed by the repo owner before planning; treat as fixed requirements, not open questions.

| Topic | Decision |
|---|---|
| Stop action | Offer **both** a normal stop (keep volumes) and a clearly-marked **cold** stop (removes volumes, data loss) |
| Tabs before launch | **Keep hidden** until launch; correct the spec text instead of changing behavior |
| Base port `auto` | **Accept the literal string `auto`** in the existing step, matching `--base-port auto` and manifest `BASE_PORT: auto` |
| "profile" collision | **Reserve "profile" for deployment hardening only**; reword the track picker off that word |

## 3. Findings → Task Map

| # | Finding | Evidence | Task |
|---|---|---|---|
| 1 | Tabs inert — no accent, no hover | `block_logo.py:178` marker-only; no palette token | 4.6 |
| 2 | Stale `67/67` prompt on Setup tab after launch | pre-tabs did `await lower.remove_children()`; today `grep remove_children` = 0 hits | 4.4 |
| 3 | No stop/shutdown | `action_interrupt` exits w/ 130; cleanup SIGTERMs subprocesses only, not containers | 4.5 |
| 4 | Base port cannot be `auto` | step is `kind="number"`, `number_min=1024` (`integration.py:388`) | 4.2 |
| 5 | Hint text spills its indentation | `option_row.py:630-665` — leading space run, wrapped lines restart at col 0 | 4.1 |
| 6 | prod/dev "missing" | step exists + works; track step at `integration.py:95,336` owns the word "profile" | 4.3 |
| 7 | Tabs invisible pre-launch | `on_mount` → `set_tabs(enabled=False)` (`wizard_screen.py:890`); spec §5 promises otherwise | 4.7 (docs) |

## 4. Tasks

### 4.1. Task 1: OptionRow hint respects its hanging indent

**Files:**
- Modify: `bootstrapper/ui/textual/widgets/option_row.py:630-665`
- Test: `bootstrapper/tests/test_option_row_indent.py` (create)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing other tasks rely on.

`OptionRow.render()` builds line 2 as a single `Text` starting with `" " * label_col`. Rich wraps that as one logical line, so only the FIRST visual row carries the indent and every continuation restarts at column 0. The track picker's hints enumerate all services (~300 chars), so it is the most visible case. `OptionRowWithInput` (same file, `:874-881`) already solves this by putting the hint in its own `Static` with `padding-left`; this task brings `OptionRow` in line.

- [ ] **Step 1: Write the failing test**

```python
def test_long_hint_continuation_lines_keep_the_label_indent():
    row = OptionRow("Generative AI · RAG", hint="word " * 60)
    row._size = Size(60, 3)          # narrow enough to force wrapping
    text = row.render()
    lines = text.plain.split("\n")
    assert len(lines) >= 3, "hint must wrap for this test to mean anything"
    indent = len(lines[1]) - len(lines[1].lstrip())
    for ln in lines[2:]:
        assert ln.startswith(" " * indent), f"continuation not indented: {ln[:20]!r}"
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd bootstrapper && uv run pytest tests/test_option_row_indent.py -v`
Expected: FAIL — continuation lines start at column 0.

- [ ] **Step 3: Implement**

Wrap the hint manually to `available_width - label_col` and join with `"\n" + " " * label_col`, so each visual line carries the indent. Use the widget's own width (`self.size.width`, falling back to a sane default when size is unknown pre-layout). Keep the `sizes · hint` composition and all existing styles (`P.TEXT_FAINT`, per-variant accent) exactly as they are.

- [ ] **Step 4: Verify + regression**

Run: `uv run pytest tests/test_option_row_indent.py -v` (PASS) then `uv run pytest -k "option_row or prompt or wizard" -q` (no regressions).

- [ ] **Step 5: Commit** — `fix(tui): keep wrapped option hints inside their hanging indent`

### 4.2. Task 2: Base-port step accepts `auto`

**Files:**
- Modify: `bootstrapper/ui/textual/integration.py:388-398` (step definition)
- Modify: `bootstrapper/ui/textual/widgets/prompt_panel.py` (number-kind validation path)
- Test: `bootstrapper/tests/test_base_port_auto.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: the base-port selection may now be the string `"auto"` — Task 4.3's command summary must quote it unchanged, and any consumer doing `int(selection)` must tolerate it.

`auto` already exists as a first-class concept (`--base-port auto`; manifest `BASE_PORT: auto`; `start.py:783` `_resolve_auto_base_port_override`; `port_manager.auto_base_port()`). Only the wizard lacks it, because the step is `kind="number"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_base_port_step_accepts_the_literal_auto():
    steps = _build_steps(...)                      # helper mirroring tests/test_wizard_tabs.py
    step = next(s for s in steps if s.title.startswith("Base port"))
    assert step.accepts_auto is True
    assert "auto" in (step.subtitle or "").lower()

def test_base_port_auto_survives_to_stack_options():
    opts = _resolve_stack_options({"Base port  ·  range": "auto"})
    assert opts["base_port"] == "auto"             # NOT coerced to int
```

- [ ] **Step 2: Run to confirm failure** — `uv run pytest tests/test_base_port_auto.py -v`

- [ ] **Step 3: Implement**

Widen the number step with an opt-in `accepts_auto` flag so the literal `auto` validates alongside 1024–65000; mention it in the subtitle. Then make the resolver at `integration.py:1034` stop unconditionally `int()`-ing the value — pass `"auto"` through so the existing `start.py` resolution handles it. **Audit every `int(...)` on the base-port selection** before changing the type; a missed one is a crash on launch, not a lint error.

- [ ] **Step 4: Verify** — new tests PASS; `uv run pytest -k "base_port or integration or wizard" -q` clean.

- [ ] **Step 5: Commit** — `feat(tui): let the wizard's base-port step accept auto`

### 4.3. Task 3: Reserve "profile" for deployment hardening

**Files:**
- Modify: `bootstrapper/ui/textual/integration.py:95` (`PICKER_STEP_TITLE`), `:336` (heading)
- Modify: `bootstrapper/ui/textual/screens/wizard_screen.py` (command-summary flag emission)
- Test: `bootstrapper/tests/test_wizard_vocabulary.py` (create)

**Interfaces:**
- Consumes: Task 4.2's possible `"auto"` base-port value (must be emitted unquoted-safe via the existing `_quote_csv`/`shlex.quote` path).
- Produces: `PICKER_STEP_TITLE` changes value — it is a **selections-dict key**. Grep every use before editing (`_make_track_skip`, `starter.active_track`, prefill paths at `:1184`, `:1367`) and update them together, or the track selection silently stops resolving.

The dev/prod feature works — the step exists at index 2 and its choice is honored via `_visible_source_options` reading `selections[PROFILE_STEP_TITLE]` at step-load time. The problem is vocabulary: the track picker is titled `"Track  ·  pick your profile"` with heading `"Which profile fits what you're building?"`, spending the word before the real profile step arrives.

- [ ] **Step 1: Write the failing tests**

```python
def test_only_the_hardening_step_uses_the_word_profile():
    steps = _build_steps(...)
    track = next(s for s in steps if s.title.startswith("Track"))
    assert "profile" not in track.title.lower()
    assert "profile" not in (track.heading or "").lower()

def test_command_summary_emits_the_selected_profile():
    summary = _summary_for({PROFILE_STEP_TITLE: "prod"})
    assert "--profile prod" in summary
```

- [ ] **Step 2: Run to confirm failure**

- [ ] **Step 3: Implement**

Reword the track step to workload language (e.g. title `"Track  ·  pick your workload"`, heading `"Which workload are you building?"`), keeping its subtitle and option hints unchanged. Update every reference to the renamed constant in the same commit. Separately, emit `--profile <value>` into the command summary when the selection is not the default, so the pasted command reproduces the run.

- [ ] **Step 4: Verify** — `uv run pytest -k "vocabulary or track or profile or command_summary" -q`

- [ ] **Step 5: Commit** — `fix(tui): stop the track picker from calling itself a profile`

### 4.4. Task 4: Retire the prompt and command summary at launch

**Files:**
- Modify: `bootstrapper/ui/textual/screens/wizard_screen.py` (`_transition_to_launch`, ~`:1682`)
- Test: `bootstrapper/tests/test_wizard_tabs.py` (extend)

**Interfaces:**
- Consumes: the `#tab-setup` / `#tab-logs` structure from the tabs work.
- Produces: nothing.

**This is a regression introduced by the tabs change (#911).** Pre-tabs, `_transition_to_launch` called `await lower.remove_children()`, which took the prompt panel and command summary down; the old code comments say so explicitly. The tab swap replaced that teardown and nothing assumed the job, so the Setup tab permanently shows the last answered question (`67/67`) under the stack overview. The stack overview **must stay live** — that was the whole point of keeping both bodies mounted — so hide/retire only the prompt and the command summary.

- [ ] **Step 1: Write the failing test**

```python
async def test_setup_tab_has_no_stale_prompt_after_launch():
    scr = _screen()                                  # helper in this file
    async with scr.app.run_test():
        scr._logs_enabled = True
        await scr._transition_to_launch()
        scr.show_tab(BrandPanel.TAB_SETUP)
        assert scr._prompt.display is False, "answered prompt still visible on Setup"
        # the overview must NOT have been torn down with it
        assert scr.query_one("#info-panel").display is True
```

- [ ] **Step 2: Run to confirm it fails** — the prompt is still displayed.

- [ ] **Step 3: Implement**

In `_transition_to_launch`, after flipping `_phase`, hide the prompt panel and the command summary (set `display = False`; do **not** unmount — `action_back` and other paths still hold references, and unmounting reintroduces the null-sentinel class of bug the tabs work already had to fix once). Leave `InfoPanel` untouched.

- [ ] **Step 4: Verify** — new test PASSES; `uv run pytest -k "wizard or tabs or launch" -q` clean.

- [ ] **Step 5: Commit** — `fix(tui): retire the answered prompt when the launch begins`

### 4.5. Task 5: Stop and cold-stop the stack from the TUI

**Files:**
- Modify: `bootstrapper/ui/textual/screens/wizard_screen.py` (bindings, actions, hint sets)
- Test: `bootstrapper/tests/test_stack_stop_actions.py` (create)

**Interfaces:**
- Consumes: `_footer_hints()` from the tabs work; `bootstrapper/stop.py::AtlasStopper.stop_services(cold_stop, project_name)`.
- Produces: two new actions and two new hint entries.

Today nothing in the TUI stops containers: `ctrl+c` → `action_interrupt` sets exit 130 and exits; `_run_app_with_process_cleanup` only SIGTERMs bounded subprocesses. `ctrl+q` detaches. The user must run `./stop.sh` separately.

**Safety requirements — the cold path destroys data:**
1. Reuse `AtlasStopper`; do not shell out to `./stop.sh`.
2. Normal stop is confirm-then-go. **Cold stop requires an explicit, distinct confirmation** naming what is destroyed ("removes this project's named volumes — data will be lost"), not a reflexive y/n on the same key.
3. Bind them so neither is adjacent to, or a near-miss of, `ctrl+q`/`ctrl+c`.
4. Run the teardown in a worker with its own `group=` so `exclusive=True` cannot cancel the pipeline (same hazard the log-copy worker had).
5. **Managed hosts are deliberately left running** by `./stop.sh` unless explicitly asked — `AtlasStopper` has `stop_managed_comfyui_mps` / `_blender_mcp` / `_vllm_metal` and `report_managed_hosts_left_running`. Mirror the CLI's default (leave them) and say so in the confirmation, so a user is not told the stack is stopped when a GPU-holding host process is still up.

- [ ] **Step 1: Write the failing tests**

```python
async def test_stop_action_is_inert_during_setup()          # phase gate
async def test_stop_invokes_stopper_without_cold()          # cold_stop is False
async def test_cold_stop_requires_its_own_confirmation()    # no teardown before confirm
async def test_cold_stop_invokes_stopper_with_cold()        # cold_stop is True
async def test_stop_worker_uses_its_own_group()             # cannot cancel the pipeline
```

Stub `AtlasStopper` — the tests must never run a real teardown.

- [ ] **Step 2: Run to confirm failure**

- [ ] **Step 3: Implement** the two actions, the confirmation, the worker, and the hint entries via `_footer_hints()`.

- [ ] **Step 4: Verify** — `uv run pytest tests/test_stack_stop_actions.py -v`, then `-k "wizard or footer or hints"`.

- [ ] **Step 5: Commit** — `feat(tui): stop or cold-stop the running stack from the launch screen`

### 4.6. Task 6: Accent + hover styling for the tabs

**Files:**
- Modify: `bootstrapper/ui/textual/widgets/block_logo.py` (`_tab_segment`, `_render_border`, new mouse handlers)
- Test: `bootstrapper/tests/test_brand_panel_tabs.py` (extend)

**Interfaces:**
- Consumes: `tab_spans()` (already used for click routing).
- Produces: `_hovered_tab` state; `tab_spans()` semantics unchanged.

Both mechanisms are **probed and confirmed** against Textual 6.2.1:
- Markup renders in `border_subtitle` — `[#7dcfff bold]\[ Setup ][/]` produced `style=bold #7dcfff`.
- `on_mouse_move` reaches the panel over its border row with usable coordinates, alongside `on_enter`/`on_leave`.

Palette tokens already exist: `ACCENT = "#7dcfff"`, `ACCENT_HOVER = "#a8d4e6"`, plus a muted token for inactive.

**The crux:** markup makes source length ≠ rendered length. Both the padding math (`inner = size.width - 4`, `len(left)`) and `tab_spans` currently measure the raw string, with an ad-hoc correction for escaped brackets. Replace that with a clean split — build a **plain** string for all measurement (spans, padding) and a **markup** string for display — so no future style change silently breaks click routing.

- [ ] **Step 1: Write the failing tests**

```python
def test_active_tab_renders_in_the_accent_color()      # segment style == bold ACCENT
def test_inactive_tab_is_muted()
def test_hovered_inactive_tab_uses_the_hover_color()
def test_hover_does_not_move_the_tab_spans()           # styling must not shift click targets
def test_byline_only_path_is_unchanged_at_60_90_140_200()   # the byte-identical guard
```

- [ ] **Step 2: Run to confirm failure**

- [ ] **Step 3: Implement** the plain/markup split, the three style states, and `on_mouse_move`/`on_leave` handlers that re-render **only when the hovered tab changes** (a re-render per mouse pixel is wasteful).

- [ ] **Step 4: Verify** — new tests PASS; re-verify the byline-only path is byte-identical at 60/90/140/200; `uv run pytest -k "brand_panel or tabs" -q` clean.

- [ ] **Step 5: Commit** — `feat(tui): highlight the active tab and respond to hover`

### 4.7. Task 7: Docs + full validation

**Files:**
- Modify: `docs/superpowers/specs/2026-08-07-wizard-tabbed-screens-design.md` (§5, §8)
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: Correct the spec.** §5 still claims "Before launch | Setup active. Logs visible but dimmed/disabled — discoverable, not activatable." That is not what ships and, per §2, is not what we are building. Rewrite it to state that the tab strip appears at launch, and note the deliberate reason (the byline-only border stays byte-identical pre-launch). This is the same class of falsified claim already corrected twice in this spec — do not soften, state what ships.

- [ ] **Step 2: Add the CHANGELOG entry** at the end of the `## 1. [Unreleased]` block, continuing the numbering (the next free number after §1.155 — verify with `grep -n "^### 1\.15[0-9]\." docs/CHANGELOG.md`, since parallel work may have taken it).

- [ ] **Step 3: Full validation**

```bash
cd bootstrapper && uv run pytest -q                     # expect 0 failed
cd /Users/kaveh/repos/atlas
uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check
make docs-check
python scripts/check_doc_links.py
uv run --project bootstrapper python -m scripts.audit_runtime_locks
```

All must exit 0.

- [ ] **Step 4: Commit** — `docs(changelog): record the wizard UX fixes`

## 5. Sequencing and Risk

| Order | Task | Risk | Why here |
|---|---|---|---|
| 1 | 4.1 OptionRow indent | Low | Fully independent, single render method |
| 2 | 4.2 Base port `auto` | Medium | Type widening — audit every `int()` on the value |
| 3 | 4.3 Naming | Medium | Renames a **selections-dict key**; all uses must move together |
| 4 | 4.4 Stale prompt | Low | Small, isolated, closes a shipped regression |
| 5 | 4.5 Stop / cold stop | **High** | Destructive; new subprocess surface; confirm UX must be right |
| 6 | 4.6 Tab styling | Medium | Touches the border geometry that took three rounds to stabilize |
| 7 | 4.7 Docs | Low | Rides along |

4.4, 4.5, and 4.6 all touch `wizard_screen.py` / `block_logo.py`; run them serially, never in parallel worktrees.

**Two tasks need their own regression guards specifically because their absence is what let the bug ship:** 4.4 (nothing asserted on post-launch Setup-tab content) and 4.1 (nothing asserted on wrapped hint geometry).

## 6. Self-Review

**6.1. Coverage.** All seven reported issues map to a task (§3). Issue 7 resolves as a docs correction per the owner's decision, not code.

**6.2. Placeholders.** None — every task names exact files, line anchors, test names, and commit messages.

**6.3. Type consistency.** The one cross-task interface is the base-port value becoming `str | int` (4.2), consumed by 4.3's command summary. Flagged in both tasks' Interfaces blocks.

**6.4. Known risk not designed away.** 4.5's cold stop is genuinely destructive; the plan specifies a distinct confirmation and mirrors the CLI's leave-managed-hosts-running default, but the exact confirmation widget is left to the implementer to match existing TUI patterns.
