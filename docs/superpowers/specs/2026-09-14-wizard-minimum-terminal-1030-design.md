# Wizard minimum-terminal usability (#1030)

> **Status: archived spec** — dated 2026-09-14; a point-in-time record kept for archaeology, not current guidance. The landed outcome is recorded in the [CHANGELOG](../../CHANGELOG.md).

## 1. Verified problem

At current `develop` commit `3d6d0ab9`, the terminal gate accepts 60 columns
by 20 rows, but the setup layout spends nine rows on the brand panel and six
rows on the stack overview. The compositor places the active `PromptPanel` at
row 23 and `CommandSummary` at row 21, entirely outside the 20-row viewport.
The focused widget still exists, which makes the failure especially confusing:
keyboard input changes hidden state while the screen shows only decorative
content and a clipped shortcut list.

The defect remains through 23 rows; the prompt is only fully visible at 27
rows for the smallest two-option fixture. Real prompts can require more space.
The existing admission boundary is otherwise deliberate and already routes
59×20 and 60×19 terminals to the linear flow, so this issue will make the
accepted floor usable rather than raise it.

## 2. Visual and interaction design

The normal-height layout remains equivalent in structure and appearance: the
six-row Atlas lockup, stack overview, rounded panels and command summary retain
their current spacing and palette. Footer density is independently responsive
to width so the complete actions never disappear when a narrow terminal grows
taller.

Below 30 rows, the screen enters a compact-height state. The nine-row brand
panel becomes a three-row bordered identity strip whose single content row says
`ATLAS` (or the configured brand name) in the existing cyan accent. The setup
overview is hidden while a prompt is active, and all decorative vertical
gutters collapse. The command summary keeps its bordered one-row minimum and
the footer keeps its bordered action strip; every remaining row belongs to the
prompt. Prompt caption spacing contracts, and a long subtitle is capped so it
cannot displace the focused control or option viewport. The full subtitle and
overview return as soon as the terminal grows.

The compact footer uses shorter, prompt-specific labels and tighter separators:
options show move/next/back, multiselect shows move/mark/next/back, and text,
number and secret inputs show next/back. `Ctrl+Q quit` remains named in the
footer title. The full 120-cell setup inventory returns once the terminal is at
least 132 columns wide, which accounts for the screen, border and content
padding around it. During launch, the compact footer prioritizes filters and
the applicable cancel, detach or stop action. Tabs remain on the brand panel's
bottom border. These are presentation changes only; every existing binding
continues to work.

The screen toggles CSS classes and widget presentation in place on Textual's
`Resize` event. It never reconstructs the prompt, option rows, inputs or tab
bodies. The same focused widget object therefore survives normal → minimum →
normal resizing along with input text, selected row, checked values, scroll
position and wizard selections.

When launch retires the prompt, compact Setup reveals the live overview in the
available body area rather than leaving a blank tab. Compact Logs gives the log
pane the same body area. The normal launch layout is unchanged.

## 3. Atlas-specific design system

- **Palette:** preserve `#12131e` screen background, `#0e0f18` panels,
  `#2b2f4a` borders, `#7dcfff` accent and the existing readable text tokens.
- **Type:** preserve terminal monospace text and current weight hierarchy.
- **Layout:** normal mode remains the existing stacked Atlas console; compact
  mode is a bordered identity strip followed by the active work surface and
  action strip.
- **Signature:** the large block logo folds into a one-row Atlas wordmark at
  the exact size where work must take priority, then unfolds on resize without
  changing state.

This avoids a generic responsive redesign: the compact strip derives from the
existing terminal lockup, border language, configured brand identity and Atlas
palette. No new color, animation, font or UI framework is introduced.

## 4. Accessibility and limits

The documented target is WCAG 2.2 AA contrast for normal text: at least 4.5:1.
Existing automated palette tests measure muted and faint interactive text on
the primary and inset backgrounds against that target; issue-specific
measurements record 10.76:1 for the compact accent, 11.43:1 for primary text,
9.16:1 for success, 11.05:1 for warning, 6.18:1 for error and 4.54:1 for
the faintest text on the primary background (all are slightly higher on the
inset panel background). Selected rows retain cursor glyphs, checkbox text and labels,
and launch status retains words/icons, so meaning is never color-only.

Keyboard verification covers every prompt kind, the filter/search focus path,
back/next navigation, resize round trips, the final review prompt and both
launch tabs. This establishes keyboard operability and visible focus in the
tested Textual renderer. It does not claim screen-reader compatibility.

Measurements use Textual 8.2.8 `run_test()` with cell sizes 60×20, 59×20,
60×19 and 120×44, Atlas's dark theme and the committed palette. Terminal font,
emoji width and emulator-specific image protocols remain outside the headless
compositor; real-terminal screenshots provide a separate visual check.

## 5. Scope, compatibility and rollback

Change only the Textual wizard screen, its brand/footer presentation helpers,
targeted tests, canonical wizard documentation and captured screenshots. The
existing generator copies the screenshot directory to both outputs but only
rewrites diagram paths; extend its asset map so a nested canonical page's image
path resolves after the wiki flattens that page. Keep
the terminal admission boundary, linear fallback, wizard data model, command
generation, launch pipeline, service configuration and large-screen layout
unchanged.

Reverting the issue commit restores the previous layout. There is no data,
configuration or migration state to reconcile.
