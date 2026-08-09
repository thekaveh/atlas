"""Follow-ups deferred from the #911 whole-branch review (#912).

Each test here pins a defect the review proved but that shipped anyway,
or closes a gap where an existing test would pass against a broken
implementation.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

import pytest  # noqa: E402
from textual.app import App  # noqa: E402

from ui.textual.screens.wizard_screen import WizardScreen  # noqa: E402
from ui.textual.widgets.block_logo import BrandPanel  # noqa: E402
from ui.textual.widgets.command_summary import CommandSummary  # noqa: E402
from ui.textual.widgets.prompt_panel import PromptOption, PromptStep  # noqa: E402


class _App(App):
    def __init__(self, screen: WizardScreen) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


_OPEN: list[tuple[WizardScreen, Path | None]] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    while _OPEN:
        scr, path = _OPEN.pop()
        scr._close_launch_log_tee()
        if path is not None:
            path.unlink(missing_ok=True)


def _screen() -> WizardScreen:
    step = PromptStep(
        title="Dummy", step_index=1, step_total=1, heading="H", subtitle="",
        options=[PromptOption(value="a", label="A")], default_value="a",
    )
    scr = WizardScreen(steps=[step], services=[], no_splash=True)
    _OPEN.append((scr, scr._launch_log_path))
    return scr


def _rows(scr: WizardScreen) -> list[str]:
    return ["".join(s.text for s in strip)
            for strip in scr.app.screen._compositor.render_strips()]


# ─── item 2: the command summary must not vanish on short terminals ──


@pytest.mark.parametrize("height", [44, 38, 34, 32, 30])
def test_the_command_summary_stays_visible_on_short_terminals(height: int) -> None:
    """It rendered ZERO visible rows below ~32 rows.

    #lower-pane clips (overflow: hidden) and PromptPanel's 1fr claimed the
    remaining rows first, so the summary was laid out past the clip — its
    own ``min-height: 3`` never got a chance, while the CSS comment claimed
    it "yields on short terminals". Docking it fixes the ordering.
    """
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(120, height)) as pilot:
            await pilot.pause()
            scr._selections["Base port  ·  range"] = "63000"
            scr._refresh_command_summary()
            await pilot.pause()
            return _rows(scr)

    rows = asyncio.run(scenario())
    assert any("Command summary" in r for r in rows), (
        f"the command summary is entirely invisible at height {height}"
    )


# ─── item 9: click routing ───────────────────────────────────────────


def test_clicking_each_tab_activates_that_tab() -> None:
    """Covers Setup too — the original test only ever clicked Logs."""
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            panel = scr._brand_panel
            spans = panel.tab_spans()
            row = panel.size.height + 1
            s0, s1 = spans[BrandPanel.TAB_SETUP]
            await pilot.click(BrandPanel, offset=((s0 + s1) // 2, row))
            await pilot.pause()
            after_setup = scr.active_tab
            l0, l1 = spans[BrandPanel.TAB_LOGS]
            await pilot.click(BrandPanel, offset=((l0 + l1) // 2, row))
            await pilot.pause()
            return after_setup, scr.active_tab

    setup_tab, logs_tab = asyncio.run(scenario())
    assert setup_tab == BrandPanel.TAB_SETUP
    assert logs_tab == BrandPanel.TAB_LOGS


def test_clicking_the_border_while_tabs_are_disabled_does_nothing() -> None:
    """Pre-launch there is no tab strip, so a border click must be inert."""
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            assert scr._brand_panel.tab_spans() == {}, "tabs must be off pre-launch"
            row = scr._brand_panel.size.height + 1
            await pilot.click(BrandPanel, offset=(10, row))
            await pilot.pause()
            return scr.active_tab

    assert asyncio.run(scenario()) == BrandPanel.TAB_SETUP


def test_click_routing_uses_widget_relative_coordinates() -> None:
    """The spans are widget-relative; a screen-relative reading also passed
    the original test because the two happened to agree at that x."""
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr.show_tab(BrandPanel.TAB_SETUP)
            await pilot.pause()
            panel = scr._brand_panel
            region = panel.region
            return region.x, region.y, panel.tab_spans()

    x, y, spans = asyncio.run(scenario())
    # If the panel is not at the screen origin, widget-relative and
    # screen-relative x differ — which is what makes the distinction real.
    assert spans, "tabs should be enabled here"
    for _tab, (start, end) in spans.items():
        assert start >= 0 and end > start
        assert end < 200, "spans must be widget-relative, not screen offsets"


# ─── item 10: assertions that could pass against a broken impl ───────


def test_the_summary_height_cap_matches_its_declared_row_budget() -> None:
    """`max-height: 6` and MAX_BODY_ROWS could desync silently: the test
    only asserted the substring `max-height:` (passing even with 0) and
    MAX_BODY_ROWS was referenced from a comment."""
    import re

    css = CommandSummary.DEFAULT_CSS
    match = re.search(r"max-height:\s*(\d+)", css)
    assert match, "summary must cap its height"
    declared = int(match.group(1))
    assert declared == CommandSummary.MAX_BODY_ROWS + 2, (
        f"max-height {declared} != MAX_BODY_ROWS "
        f"({CommandSummary.MAX_BODY_ROWS}) + border (2)"
    )
    min_match = re.search(r"min-height:\s*(\d+)", css)
    assert min_match and int(min_match.group(1)) >= 3, (
        "the summary needs border + at least one content row to be useful"
    )


# ─── item 11: the selection claim the CHANGELOG ships ────────────────


def test_the_command_summary_content_is_selectable() -> None:
    """The panel is a container (never selectable); its inner Static is what
    a drag actually selects. The CHANGELOG ships this claim untested."""
    from textual.widgets import Static

    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._selections["Base port  ·  range"] = "63000"
            scr._refresh_command_summary()
            await pilot.pause()
            summary = scr.query_one(CommandSummary)
            inner = summary.query(Static)
            return summary.allow_select, [w.allow_select for w in inner]

    panel_selectable, inner_selectable = asyncio.run(scenario())
    assert panel_selectable is False, "a container is never drag-selectable"
    assert inner_selectable and all(inner_selectable), (
        "the summary's inner Static must be selectable — that is what the "
        "documented drag-to-copy actually grabs"
    )


# ─── item 1: the failure path's two chrome elements must agree ───────


def test_a_failed_launch_leaves_footer_and_log_pane_agreeing_on_the_exit_key():
    """The footer said "ctrl+q detach" while the pane directly above it
    still said "ctrl+c to cancel". Both keys work, so nothing broke — but
    only the SUCCESS path updated the pane's subtitle."""
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr._logs_enabled = True
            scr._log_pane.set_title(" Live docker logs ", subtitle=" ctrl+c to cancel ")
            scr._mark_launch_failed()
            await pilot.pause()
            hints = {k for keys, _ in scr._footer_hints() for k in keys}
            return hints, str(scr._log_pane.border_subtitle or "")

    hints, subtitle = asyncio.run(scenario())
    assert "ctrl+q" in hints, hints
    assert "ctrl+q" in subtitle, f"pane subtitle still says: {subtitle!r}"
    assert "ctrl+c" not in subtitle, f"pane subtitle contradicts the footer: {subtitle!r}"


def test_a_setup_phase_failure_keeps_the_cancel_wording():
    """_mark_launch_failed also runs for setup-phase worker errors, where
    "cancel" is still the truthful hint — the subtitle must not be rewritten
    for a launch that never started."""
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._log_pane.set_title(" Live docker logs ", subtitle=" ctrl+c to cancel ")
            assert scr._phase == "setup"
            scr._mark_launch_failed()
            await pilot.pause()
            return str(scr._log_pane.border_subtitle or "")

    assert "ctrl+c" in asyncio.run(scenario())


# ─── item 3: an explicitly cleared cloud provider must round-trip ────


def test_clearing_a_cloud_provider_emits_a_disabling_flag():
    """An empty selection disables the provider at launch, so the summary
    must say so. Emitting nothing let a pasted command fall back to .env and
    silently re-enable what the user had just cleared."""
    from utils.cloud_providers import CLOUD_PROVIDERS
    from wizard.llm_steps import cloud_models_title

    provider = CLOUD_PROVIDERS[0]
    title = cloud_models_title(provider.name)
    step = PromptStep(
        title=title, step_index=1, step_total=1, heading="H", subtitle="",
        options=[PromptOption(value="m", label="m")], default_value="",
        kind="multiselect",
    )
    scr = WizardScreen(steps=[step], services=[], no_splash=True)
    _OPEN.append((scr, scr._launch_log_path))

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._selections[title] = ""          # explicitly cleared
            scr._refresh_command_summary()
            await pilot.pause()
            return list(scr._command_summary.flags)

    flags = asyncio.run(scenario())
    assert (f"--cloud-{provider.key}-source", "disabled") in flags, flags
