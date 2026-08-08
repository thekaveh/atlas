# Tabbed wizard/launch screens + selectable content

**Date:** 2026-08-07
**Status:** Approved design, ready for planning
**Scope:** `bootstrapper/ui/textual/` — layout only. No pipeline, compose-streaming, service-resolution, or wizard-step logic changes.

## 1. Problem

The wizard stacks every pane on one screen: brand panel, stack overview, prompt, command summary, shortcuts. During launch the log pane is added to that same stack while the overview stays mounted.

Atlas now ships 61 source-configurable services, and the overview grows with the service count. Measured on a 44-row terminal at the current service count:

| Element | Rows |
|---|---:|
| Brand panel | 9 |
| Stack overview (61 services, 2 columns) | 31 |
| Filter chips | 4 |
| Shortcuts | 3 |
| Gutters | 3 |
| **Total fixed chrome** | **50** |
| **Terminal budget** | **44** |
| **Left for logs** | **−6** |

The chrome over-subscribes the screen by 6 rows. The log pane — the whole point of the launch phase — is squeezed to nothing, and the overview itself clips. Every service added makes it worse.

## 2. Goals

1. Give the log pane a usable share of the screen at launch (target: ≥20 rows on a 44-row terminal).
2. Keep the existing design language: the logo pane and its border stay on both views; the bottom-docked shortcuts pane stays on both views, with contents that change per view.
3. Scale to more services without re-triggering the squeeze.
4. Make the command summary and logs copyable.

## 3. Non-goals

- Redesigning the panels themselves (colors, borders, titles).
- Changing what the wizard asks or how the pipeline runs.
- Replacing `RichLog` (see §7.3 — the cost is not justified).

## 4. Architecture

**One screen, two tab bodies.** `WizardScreen` retains all state (`_selections`, `_services`, the pipeline worker, the log tee). Only the middle of the layout swaps.

```
#wizard-body
├── BrandPanel          always visible; bottom border carries tabs (left) + byline (right)
├── #tab-setup          InfoPanel + PromptPanel + CommandSummary
├── #tab-logs           LogFilterChips + LogPane
└── FooterBar           always visible; hint set swaps per tab
```

Switching toggles `display` on `#tab-setup` / `#tab-logs`. Both bodies stay mounted, so:

- the log pane keeps streaming while Setup is visible;
- the overview keeps updating live while Logs is visible;
- no widget is unmounted, no worker restarts, no output is lost.

### 4.1. Why not `TabbedContent`

Textual's `TabbedContent` ships its own tab-bar chrome (underline indicators, its own palette) that conflicts with the rounded-border language, and it consumes a row. The border-subtitle approach spends **zero rows** and is native by construction.

### 4.2. Tab affordance

Tabs render on the brand panel's **bottom border**, left-aligned; the existing `by <author> · <license> · v<version> · <repo>` byline stays on the same border, right-aligned. Implemented with one `border_subtitle` string:

```python
left  = r" \[ Setup ] \[ Logs ] "     # brackets MUST be escaped (see §7.1)
right = " by Kaveh Razavi · Apache-2.0 · v0.1.0 · github.com/thekaveh/atlas "
pad   = max(1, inner_width - len(left) - len(right))
panel.border_subtitle = left + "─" * pad + right
panel.styles.border_subtitle_align = "left"
```

The padding is recomputed on resize. Verified rendering (90 cols):

```
╭─  tagline  ─────────────────────────────────────────────────────────────╮
│                          ATLAS-PLATFORM                                 │
╰─  [ Setup ] [ Logs ] ───────────── by Kaveh Razavi · Apache-2.0 · v0.1… ─╯
```

## 5. Tab mechanics

| Concern | Behavior |
|---|---|
| Before launch | Setup active. Logs visible but dimmed/disabled — discoverable, not activatable. |
| On launch | Auto-switch to Logs. Setup remains reachable. |
| Switching | `1` / `2`, `tab` / `shift+tab` cycling, or mouse click on the border row. |
| Footer | Hint set swaps per tab via the existing `FooterBar.update_hints()`. |
| Activity marker | While Setup is active during launch, the Logs label gains a subtle marker when new errors arrive, so failures are not missed behind a hidden tab. |

## 6. State and data flow

No state moves; tabs only change visibility. New state on `WizardScreen`: `_active_tab: Literal["setup", "logs"]` and `_logs_enabled: bool`.

| Stream | Writer | Behavior when its tab is hidden |
|---|---|---|
| Service status (`_refresh_info_panel` → `InfoPanel.update_state`) | pipeline worker | Keeps updating into the hidden container; Tab 1 is correct on switch. |
| Log lines (`_write_status` / `_safe_log` → `LogPane.write_log`) | compose stream + pipeline | Keeps appending. `LogPane` is already bounded (`RichLog(max_lines=10_000)` + `_records` cap), so a long hidden run cannot grow unbounded. |
| Filter chips (`add_source`) | `LogPane.set_on_new_source` | Unchanged; new sources register regardless of visibility. |

**Consequence — corrected during implementation.** Log widgets are created up front (hidden) rather than at transition time, so `_log_pane` is non-`None` for the screen's whole life. An earlier draft of this spec claimed that as a side benefit ("wizard-time warnings will land in the log pane"). That was wrong, and the Task 2 review caught it: `on_worker_state_changed` used `_log_pane is None` as a sentinel for "still in setup", and its toast fallback became unreachable dead code. A setup-phase worker crash would have written into the hidden, not-yet-reachable Logs tab — invisible until the user finished the whole wizard. That branch must be gated on `self._phase` instead, so setup-phase failures still surface as a toast. Any other behavior keyed off `_log_pane`'s None-ness must be re-checked for the same reason.

## 7. Selectable / copyable content

Textual 6.2.1 already provides selection: `Widget.ALLOW_SELECT`, `Widget.allow_select`, `Widget.get_selection`, `Widget.text_selection`, `App.copy_to_clipboard`. **No version change required.**

The governing rule is `Widget.allow_select == ALLOW_SELECT and not is_container`, and `Widget.get_selection` extracts only from a `Text` / `Content` render.

### 7.1. Measured behavior

| Widget | `is_container` | `allow_select` | Result |
|---|---|---|---|
| `CommandSummary` (panel) | True | False | Panel not selectable… |
| ↳ inner `Static` | False | True | …but its **content is** — already works today |
| `InfoPanel` (panel) | True | False | Panel not selectable… |
| ↳ inner `Static` | False | True | …but its **content is** — already works today |
| `LogPane` → `RichLog` | True | False | Not drag-selectable; `get_selection` returns `None` |

Verified round-trip on the command summary: a drag selection extracted
`'./start.sh --base-port 63000 --comfyui-source container-gpu'`, and `App.copy_to_clipboard(text)` emits OSC-52 (works over SSH).

### 7.2. Decision

- **Command summary and stack overview:** already selectable. No code change. Confirm in a real terminal and document.
- **Logs:** two complementary affordances —
  1. **Shift-drag**: terminal-native selection bypasses Textual's mouse capture (iTerm2, Terminal.app, most emulators). Document it in the shortcuts bar and README.
  2. **Copy shortcuts**: `y` copies the visible log buffer; `Y` copies the full session-log contents. Both route through `App.copy_to_clipboard`. Every line is already teed to `/tmp/atlas-launch-<ts>-<unique>.log`, so `Y` has a durable source.

### 7.3. Rejected: replacing `RichLog`

Swapping `RichLog` for a selectable widget would forfeit the bounded 10k-line buffer, per-source/level filtering, and streaming performance — significant work and regression risk for a capability Shift-drag already provides.

## 8. Error handling

- **Launch failure:** unchanged. `_mark_launch_failed()` still sets the exit code and frees Ctrl+Q; the failure is written to the log pane, which is the active tab by default after launch.
- **Failure while on Setup:** the Logs activity marker (§5) surfaces it.
- **Tab switch during teardown:** switching only toggles `display`; it never touches workers or the process tree, so it is safe at any point, including mid-cancel.
- **Short terminals:** the `is_tui_capable` floor (≥20 rows) still applies. Tabs improve this case since each tab holds roughly half of today's stacked content. The pre-existing ≤30-row overlap between the command summary and the footer is fixed as part of this work, since it lives in the same layout.

## 9. Testing

All headless via `App.run_test()`; no real terminal required.

1. **Border render:** the bottom border row contains both tab labels and the byline; escaped brackets survive; on narrow widths the byline elides while tab labels never truncate.
2. **Switching:** `1` / `2`, `tab` / `shift+tab`, and a click within the tab label's x-range each activate the correct body; the inactive body has `display: none`.
3. **Gating:** Logs is not activatable before launch; launch auto-switches to Logs; Setup stays reachable afterwards.
4. **Footer:** the hint set matches the active tab.
5. **Streaming while hidden:** write log lines with Setup active, switch to Logs, assert the lines are present (proves no loss).
6. **Layout budget:** on a 44-row terminal with 61 services, the Logs tab gives the log pane ≥20 rows. Regression guard against re-introducing the squeeze.
7. **Selection:** `CommandSummary`'s inner `Static` returns the expected text from `get_selection`; the `y` / `Y` copy actions invoke `copy_to_clipboard` with the expected payload.

## 10. Migration path

1. `BrandPanel` gains `set_tabs(active, enabled)` — builds the padded subtitle (with `\[` escaping), recomputes on resize.
2. `compose()` wraps existing children in `#tab-setup` / `#tab-logs`; log widgets are created hidden instead of at transition time.
3. `_transition_to_launch()` drops `await lower.remove_children()`; it enables and activates the Logs tab instead.
4. Add `action_show_setup` / `action_show_logs` / cycling / border-click handling; wire the footer hint swap.
5. Add `y` / `Y` copy actions; document Shift-drag.
6. Tests (§9) + CHANGELOG.

## 11. Feasibility evidence

Probed against Textual 6.2.1 in this repo's venv, not assumed:

| Question | Result |
|---|---|
| Tabs left + byline right on one border row | Renders natively via padded `border_subtitle` + `border-subtitle-align: left` |
| Do `[ Setup ]` labels survive? | Only with `\[` escaping — Textual consumes bare brackets as console markup |
| Narrow terminal (<135 cols) | Textual auto-elides the byline (`· v0.1…`); tab labels never truncate |
| Mouse click on the border row | Click events arrive with x/y, so x maps to a tab |
| Custom border painter required? | No — stock Textual |
| Selection available without a version bump? | Yes — `ALLOW_SELECT` / `get_selection` / `copy_to_clipboard` all present in 6.2.1 |
