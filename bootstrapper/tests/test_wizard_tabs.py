"""Setup and Logs live in sibling containers; switching toggles display only.

Both bodies stay mounted so the stack overview keeps updating while the user
reads logs, and the log stream keeps appending while the user is on Setup.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

import pytest  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.worker import Worker, WorkerState  # noqa: E402

from ui.textual.screens.wizard_screen import WizardScreen  # noqa: E402
from ui.textual.widgets.block_logo import BrandPanel  # noqa: E402
from ui.textual.widgets.prompt_panel import PromptOption, PromptStep  # noqa: E402


class _App(App):
    def __init__(self, screen: WizardScreen) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


# M5: WizardScreen.__init__ opens /tmp/atlas-launch-*.log eagerly via
# NamedTemporaryFile(delete=False) (so wizard-time warnings land somewhere
# even before the user reaches the Logs tab) — every screen built via
# _screen()/_multiselect_screen() below leaks that fd + file unless closed
# and unlinked. Mirrors the close+unlink pattern in test_tui_launch_log.py.
# Captured at construction time (not at teardown): several tests reassign
# scr._launch_log_path mid-test, so the ORIGINAL path must be recorded here
# to still be reachable for cleanup.
_OPEN_SCREEN_LOGS: list[tuple[WizardScreen, Path | None]] = []


def _register(scr: WizardScreen) -> WizardScreen:
    _OPEN_SCREEN_LOGS.append((scr, scr._launch_log_path))
    return scr


@pytest.fixture(autouse=True)
def _cleanup_launch_log_tees():
    yield
    while _OPEN_SCREEN_LOGS:
        scr, path = _OPEN_SCREEN_LOGS.pop()
        scr._close_launch_log_tee()
        if path is not None:
            path.unlink(missing_ok=True)


def _screen() -> WizardScreen:
    # A single dummy step exercises the real (non-auto-launch) setup path
    # instead of an empty steps=[] list, which only ever pairs with
    # auto_launch=True in production (see ui/textual/integration.py) and
    # otherwise trips on_mount's unconditional _load_current_step() call.
    step = PromptStep(
        title="Dummy", step_index=1, step_total=1,
        heading="Dummy step", subtitle="",
        options=[PromptOption(value="a", label="A"), PromptOption(value="b", label="B")],
        default_value="a",
    )
    return _register(WizardScreen(steps=[step], services=[], no_splash=True))


def test_both_tab_bodies_are_mounted_and_only_one_is_visible():
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            setup = scr.query_one("#tab-setup")
            logs = scr.query_one("#tab-logs")
            return setup.display, logs.display, scr.active_tab

    setup_visible, logs_visible, active = asyncio.run(scenario())

    assert setup_visible is True
    assert logs_visible is False, "Logs body stays mounted but hidden"
    assert active == BrandPanel.TAB_SETUP


def test_show_tab_swaps_visibility_without_unmounting():
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            # show_tab() gates the Logs tab behind _logs_enabled, which only
            # _transition_to_launch() flips. This test exercises the raw swap
            # mechanism, so put the screen in the post-launch state first.
            scr._logs_enabled = True
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            setup = scr.query_one("#tab-setup")
            logs = scr.query_one("#tab-logs")
            return setup.display, logs.display, scr.active_tab, setup.is_mounted

    setup_visible, logs_visible, active, still_mounted = asyncio.run(scenario())

    assert setup_visible is False
    assert logs_visible is True
    assert active == BrandPanel.TAB_LOGS
    assert still_mounted is True, "hidden body must NOT be unmounted"


class _FakeWorker:
    """Minimal stand-in for textual.worker.Worker — on_worker_state_changed
    only reads ``.name`` and ``.error`` off the worker object."""

    def __init__(self, name: str, error: Exception) -> None:
        self.name = name
        self.error = error


def test_setup_phase_worker_error_surfaces_as_toast_not_pane_write():
    """The Logs tab is unreachable until launch (show_tab() gates it behind
    _logs_enabled), so a setup-phase worker failure — e.g. the cloud-provider
    options-fetch worker — must show up as a toast, not a write into the
    hidden pane the user cannot see. Regression test: _log_pane is built
    eagerly now (never None), so on_worker_state_changed must branch on
    self._phase, not on self._log_pane's now-meaningless None-ness.
    """
    scr = _screen()
    notifications: list[tuple[str, str]] = []
    pane_writes: list[str] = []

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            assert scr._phase == "setup"
            scr.notify = lambda msg, **kwargs: notifications.append(
                (msg, kwargs.get("title", ""))
            )
            scr._write_status = lambda msg, **kwargs: pane_writes.append(msg)

            event = Worker.StateChanged(
                _FakeWorker("options-fetch", RuntimeError("boom")),
                WorkerState.ERROR,
            )
            scr.on_worker_state_changed(event)
            await pilot.pause()

    asyncio.run(scenario())

    assert pane_writes == [], "setup-phase failure must not write to the hidden pane"
    assert len(notifications) == 1
    msg, title = notifications[0]
    assert "boom" in msg
    assert "options-fetch" in title


def test_launch_phase_worker_error_writes_to_pane_not_toast():
    """When the Logs tab is ACTUALLY the visible tab, a worker failure
    should write into the live pane (as before), not pop a toast over it —
    the pane write is already visible, so a toast would be redundant."""
    scr = _screen()
    notifications: list[tuple[str, str]] = []
    pane_writes: list[str] = []

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._phase = "launch"
            scr._active_tab = BrandPanel.TAB_LOGS
            scr.notify = lambda msg, **kwargs: notifications.append(
                (msg, kwargs.get("title", ""))
            )
            scr._write_status = lambda msg, **kwargs: pane_writes.append(msg)

            event = Worker.StateChanged(
                _FakeWorker("pipeline", RuntimeError("kaboom")),
                WorkerState.ERROR,
            )
            scr.on_worker_state_changed(event)
            await pilot.pause()

    asyncio.run(scenario())

    assert notifications == [], "a failure visible in the live pane must not ALSO pop a toast"
    assert len(pane_writes) == 1
    assert "kaboom" in pane_writes[0]


def test_launch_phase_worker_error_while_on_setup_also_pops_a_toast():
    """M10: a launch failure while the user is on Setup writes into the
    (hidden) Logs pane as before, but the pane write alone is invisible
    behind the currently-showing Setup tab — without a toast too, the run
    is silently dead until the user happens to switch tabs. (Spec §5/§8
    once described a Logs-tab "activity marker" for this; never built —
    this toast is the fix that actually ships.)
    """
    scr = _screen()
    notifications: list[tuple[str, str]] = []
    pane_writes: list[str] = []

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._phase = "launch"
            assert scr._active_tab == BrandPanel.TAB_SETUP, "default before any tab switch"
            scr.notify = lambda msg, **kwargs: notifications.append(
                (msg, kwargs.get("title", ""))
            )
            scr._write_status = lambda msg, **kwargs: pane_writes.append(msg)

            event = Worker.StateChanged(
                _FakeWorker("pipeline", RuntimeError("kaboom")),
                WorkerState.ERROR,
            )
            scr.on_worker_state_changed(event)
            await pilot.pause()

    asyncio.run(scenario())

    assert len(pane_writes) == 1, "the failure still lands in the (hidden) Logs pane"
    assert "kaboom" in pane_writes[0]
    assert len(notifications) == 1, "AND a toast, since the pane write alone is invisible"
    msg, title = notifications[0]
    assert "Logs tab" in msg
    assert title == "Launch failed"


def test_number_keys_switch_tabs_once_logs_are_enabled():
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            # Logs are gated before launch: "2" must be a no-op.
            await pilot.press("2")
            await pilot.pause()
            before = scr.active_tab
            scr._logs_enabled = True
            await pilot.press("2")
            await pilot.pause()
            after = scr.active_tab
            await pilot.press("1")
            await pilot.pause()
            return before, after, scr.active_tab

    before, after, back = asyncio.run(scenario())

    assert before == BrandPanel.TAB_SETUP, "Logs must be gated before launch"
    assert after == BrandPanel.TAB_LOGS
    assert back == BrandPanel.TAB_SETUP, "Setup stays reachable after launch"


def test_clicking_a_tab_label_on_the_border_switches_tabs():
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._brand_panel.set_tabs(BrandPanel.TAB_SETUP, enabled=True)
            await pilot.pause()
            spans = scr._brand_panel.tab_spans()
            start, end = spans[BrandPanel.TAB_LOGS]
            panel = scr._brand_panel
            y = panel.region.height - 1          # bottom border row
            await pilot.click(BrandPanel, offset=((start + end) // 2, y))
            await pilot.pause()
            return scr.active_tab

    assert asyncio.run(scenario()) == BrandPanel.TAB_LOGS


def _multiselect_screen() -> WizardScreen:
    """A ``kind="multiselect"`` step with ``filter_tags`` set mounts the
    real search ``Input`` (see ``PromptPanel._mount_search_input``) —
    needed to drive the search-focus interaction through the actual
    widget instead of stubbing ``has_search_focus()``.
    """
    step = PromptStep(
        title="Models", step_index=1, step_total=1,
        heading="Pick models", subtitle="",
        kind="multiselect",
        options=[PromptOption(value="a", label="A"), PromptOption(value="b", label="B")],
        filter_tags=("all",),
    )
    return _register(WizardScreen(steps=[step], services=[], no_splash=True))


def test_search_focused_digit_keys_land_in_the_input_not_a_tab_switch():
    """Regression for the review finding that the ``show_setup``/
    ``show_logs`` whitelist entries were inert: Textual's own upstream
    printable-key filter (``Input.check_consume_key``) already strips
    digit priority bindings — ``1``/``2`` included — from the binding
    chain before ``check_action`` is ever consulted, for any widget with
    focus, not just this screen's search box. This test exercises that
    real mechanism end-to-end through the actual search ``Input``
    instead of asserting on the (removed) whitelist entries. It also
    covers the one key that genuinely does need — and has — a whitelist
    entry: ``shift+tab``, which is not a printable character and so
    survives the upstream filter to reach ``check_action``.
    """
    scr = _multiselect_screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._prompt.focus_search()
            await pilot.pause()
            assert scr._prompt.has_search_focus()
            for ch in "qwen3.6":
                await pilot.press(ch)
            await pilot.pause()
            typed = scr._prompt._search_input.value
            tab_while_typing = scr.active_tab
            await pilot.press("shift+tab")
            await pilot.pause()
            return typed, tab_while_typing, scr.active_tab

    typed, tab_while_typing, tab_after_shift_tab = asyncio.run(scenario())

    assert typed == "qwen3.6", "digits/dot must land in the search box untouched"
    assert tab_while_typing == BrandPanel.TAB_SETUP, "typed digits must not switch tabs"
    assert tab_after_shift_tab == BrandPanel.TAB_LOGS, (
        "shift+tab still cycles tabs while the search box has focus"
    )


def test_logs_written_while_setup_is_active_are_not_lost():
    """The Logs body stays mounted, so a failure during launch is still in the
    pane when the user switches over."""
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._log_pane.write_log(
                "supabase-db ready", level="info", source="supabase-db"
            )
            scr._log_pane.write_log(
                "comfyui failed", level="error", source="comfyui"
            )
            await pilot.pause()
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            return [r.raw for r in scr._log_pane._records]

    lines = asyncio.run(scenario())

    assert any("supabase-db ready" in t for t in lines)
    assert any("comfyui failed" in t for t in lines)


def test_log_lines_written_while_hidden_reflow_to_the_real_width_on_reveal():
    """I1 regression: RichLog bakes each line's wrap width in at
    write()-time from ``scrollable_content_region.width``, which
    collapses to 0 while the Logs tab is hidden (``display: none``). Once
    the pane has been shown at least once (RichLog's ``_size_known``
    latches True on its first real resize), a later write while hidden
    still renders IMMEDIATELY rather than deferring — just baked at
    RichLog's ``min_width`` fallback instead of the real terminal width —
    and the strip never re-flows on its own. Real scenario: a 160-col
    terminal, user presses `1` mid-launch to check the overview, presses
    `2` back — every line written in that window stays double-wrapped
    forever (this asserts on rendered strip WIDTHS, not on ``_records``,
    which is buffered before rendering and so cannot see this bug).

    Uses an explicit ``min_width`` sentinel (33 — far narrower than the
    real ~130-col content region at size=(140, 44)) so the assertion
    doesn't depend on the exact chrome/padding math of the real
    WizardScreen layout, only on whether the baked strip width matches
    the sentinel (bug) or the real region (fixed by LogPane.reflow(),
    called from show_tab via call_after_refresh).
    """
    scr = _screen()
    line = "x" * 60  # > the 33 sentinel, so wrapping at 33 always splits it

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            pane = scr._log_pane
            # First reveal: latches RichLog._size_known True at the REAL
            # region width. Nothing buffered yet, so nothing to flush —
            # this step only exists to reach the "has been shown once,
            # now hidden again" state the bug needs.
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            scr.show_tab(BrandPanel.TAB_SETUP)
            await pilot.pause()

            pane.min_width = 33
            pane.write_log(line, level="info", source="pipeline")
            await pilot.pause()
            hidden_widths = [strip.cell_length for strip in pane.lines]

            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            revealed_widths = [strip.cell_length for strip in pane.lines]

            return hidden_widths, revealed_widths

    hidden_widths, revealed_widths = asyncio.run(scenario())

    assert len(hidden_widths) > 1 and max(hidden_widths) <= 33, (
        f"line must be baked WRAPPED at the min_width(33) sentinel while "
        f"hidden — got {hidden_widths}"
    )
    assert revealed_widths == [60], (
        f"show_tab's reveal must reflow to the real (much wider) region "
        f"width, un-wrapping the 60-char line back to one strip — got "
        f"{revealed_widths}"
    )


def test_filter_change_while_hidden_reflows_to_the_real_width_on_reveal():
    """R1 (I1 follow-up): ``LogPane.set_filter()`` -> ``_rerender()``
    bypassed the dirty flag entirely — only ``write_log``/``write_styled``
    marked it. A level-filter change made while the Logs tab is HIDDEN
    re-bakes the WHOLE buffer at the wrong width (same
    ``_write_record()``->``self.write()`` path a single hidden write
    uses) via ``_rerender``, but left ``_wrap_dirty`` False — so the next
    reveal's ``reflow()`` saw nothing to do and the lines stayed
    hard-wrapped forever. Reachable today: ``a``/``e``/``w``/``i`` (level
    filters) gate only on ``_phase == "launch"``, not the active tab —
    only ``s`` (source picker) got the tab gate in the I2 fix.

    Mirrors ``test_log_lines_written_while_hidden_reflow_to_the_real_
    width_on_reveal``'s structure: an explicit ``min_width`` sentinel
    makes the assertion independent of the real layout's chrome math.
    """
    scr = _screen()
    line = "x" * 60  # > the 33 sentinel, so wrapping at 33 always splits it

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._phase = "launch"
            pane = scr._log_pane
            # First reveal (latches _size_known True), write the line
            # while VISIBLE so it's already in the buffer, then hide.
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            pane.write_log(line, level="info", source="pipeline")
            await pilot.pause()
            scr.show_tab(BrandPanel.TAB_SETUP)
            await pilot.pause()

            pane.min_width = 33
            # action_filter_all only gates on _phase == "launch" (no tab
            # gate, unlike action_filter_sources after I2) — reachable
            # from Setup mid-launch. level stays "all" so the line still
            # passes the filter and gets re-baked (at the wrong width).
            scr.action_filter_all()
            await pilot.pause()
            hidden_widths = [strip.cell_length for strip in pane.lines]

            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            revealed_widths = [strip.cell_length for strip in pane.lines]

            return hidden_widths, revealed_widths

    hidden_widths, revealed_widths = asyncio.run(scenario())

    assert len(hidden_widths) > 1 and max(hidden_widths) <= 33, (
        f"a filter change while hidden must re-bake the line WRAPPED at "
        f"the min_width(33) sentinel too — got {hidden_widths}"
    )
    assert revealed_widths == [60], (
        f"show_tab's reveal must reflow to the real (much wider) region "
        f"width after a hidden filter change, un-wrapping the 60-char "
        f"line back to one strip — got {revealed_widths}"
    )


def test_logs_tab_reclaims_vertical_space():
    """The bug: 61 services + fixed chrome over-subscribed a 44-row terminal by
    6 rows and crushed the log pane. On the Logs tab the overview is hidden, so
    the pane must get a usable share back."""
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            return scr._log_pane.region.height

    assert asyncio.run(scenario()) >= 20


def test_source_popup_is_dismissed_when_switching_away_from_logs():
    """I2: `_SourcePopup` mounts on the SCREEN's ``popup`` layer (outside
    ``#tab-logs``), so ``#tab-logs.display = False`` alone can't hide it —
    left open, it renders on top of whichever tab is now showing.
    ``show_tab`` must dismiss it when switching away from Logs.
    """
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._phase = "launch"
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            scr._log_pane.write_log("kong up", level="info", source="kong")
            await pilot.pause()
            scr._log_chips.toggle_source_picker()
            await pilot.pause()
            popup_open_before = scr._log_chips._open_popup is not None

            scr.show_tab(BrandPanel.TAB_SETUP)
            await pilot.pause()
            popup_open_after = scr._log_chips._open_popup is not None
            return popup_open_before, popup_open_after

    popup_open_before, popup_open_after = asyncio.run(scenario())

    assert popup_open_before is True, "popup must actually open for this test to mean anything"
    assert popup_open_after is False, "switching to Setup must dismiss the orphaned popup"


def test_action_filter_sources_is_a_no_op_while_on_setup():
    """I2: ``action_filter_sources`` previously gated only on ``_phase ==
    "launch"``, not the active tab — pressing `s` while on Setup mid-launch
    opened a source-filter dropdown positioned for the invisible Logs pane.
    """
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._phase = "launch"
            scr._log_pane.write_log("kong up", level="info", source="kong")
            await pilot.pause()
            assert scr._active_tab == BrandPanel.TAB_SETUP, "default before any tab switch"
            scr.action_filter_sources()
            await pilot.pause()
            return scr._log_chips._open_popup is not None

    popup_open = asyncio.run(scenario())

    assert popup_open is False


def test_footer_shows_launch_hints_not_startup_after_a_1_2_roundtrip():
    """I3: ``show_tab`` used to install ``_STARTUP_HINTS`` for the Logs
    tab unconditionally, ignoring ``_launch_detach_ready`` — a ``1``→``2``
    round-trip after a successful launch regressed the footer back to
    "ctrl+c cancel" even though LogPane's own border subtitle already
    said "ctrl+q to detach"."""
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._phase = "launch"
            scr._launch_detach_ready = True  # "all services started"
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            scr.show_tab(BrandPanel.TAB_SETUP)
            await pilot.pause()
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            return scr._footer._body.hints

    hints = asyncio.run(scenario())

    labels = [label for _keys, label in hints]
    assert "detach" in labels
    assert "cancel" not in labels


def test_footer_hides_dead_setup_shortcuts_while_on_setup_mid_launch():
    """I4: ``_SETUP_HINTS`` advertised six shortcuts (navigate/toggle/
    confirm/search/filter/back) that all early-return once ``_phase !=
    "setup"`` — only the cancel escape hatch (and now the tab hint) are
    live on Setup mid-launch, before the run finishes."""
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._phase = "launch"
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            scr.show_tab(BrandPanel.TAB_SETUP)
            await pilot.pause()
            return scr._footer._body.hints

    hints = asyncio.run(scenario())

    labels = {label for _keys, label in hints}
    assert labels == {"cancel", "tabs"}, f"got {labels}"


def test_footer_shows_detach_not_cancel_on_setup_once_launch_is_ready():
    """Same state as above, but once the run has finished — ctrl+q
    genuinely detaches now, so the hint must say so even while Setup
    (not Logs) is the tab actually showing."""
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._phase = "launch"
            scr._launch_detach_ready = True
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            scr.show_tab(BrandPanel.TAB_SETUP)
            await pilot.pause()
            return scr._footer._body.hints

    hints = asyncio.run(scenario())

    labels = {label for _keys, label in hints}
    assert labels == {"detach", "tabs"}, f"got {labels}"


def test_footer_omits_tab_hint_before_logs_are_reachable():
    """The tab hint would be misleading before ``_logs_enabled`` — Logs
    isn't reachable yet, so ``1``/``2`` do nothing."""
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            return scr._footer._body.hints

    hints = asyncio.run(scenario())

    labels = {label for _keys, label in hints}
    assert "tabs" not in labels


def test_footer_updates_immediately_when_a_launch_failure_frees_ctrl_q():
    """``_mark_launch_failed()`` flips ``_launch_detach_ready`` True but,
    before this fix, never told the footer — a failure while on Setup
    left a stale "ctrl+c cancel" hint until the next, unrelated tab
    switch happened to recompute it."""
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._phase = "launch"
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            scr.show_tab(BrandPanel.TAB_SETUP)
            await pilot.pause()
            before = {label for _keys, label in scr._footer._body.hints}
            scr._mark_launch_failed()
            await pilot.pause()
            after = {label for _keys, label in scr._footer._body.hints}
            return before, after

    before, after = asyncio.run(scenario())

    assert before == {"cancel", "tabs"}
    assert after == {"detach", "tabs"}
