"""LogPane styling preservation across a re-render, compose-line source
classification, and the source-filter chips."""
from __future__ import annotations

import pytest

from rich.text import Text

from ui.textual.widgets.log_pane import LogPane


def _capturing_pane():
    """A LogPane whose render calls are captured instead of touching the (un-
    mounted) RichLog buffer, so the record/filter logic can be exercised."""
    pane = LogPane()
    writes: list = []
    pane.write = lambda item: writes.append(item)  # type: ignore[method-assign]
    pane.clear = lambda: None  # type: ignore[method-assign]
    return pane, writes


def test_write_styled_preserves_styling_across_rerender():
    pane, writes = _capturing_pane()
    styled = Text("phase ok", style="bold yellow")
    pane.write_styled(styled, level="warn", source="pipeline")

    # The initial render emits the styled Text verbatim.
    assert writes and writes[-1] is styled

    writes.clear()
    # A filter re-render (e.g. clicking a chip) must re-emit the SAME styled Text
    # — not a plain fallback that drops the bold-yellow level coloring.
    pane.set_filter("all", set())
    assert writes, "re-render should re-emit the record"
    reemitted = writes[-1]
    assert isinstance(reemitted, Text)
    assert reemitted is styled  # exact styled Text re-emitted, not a plain fallback
    assert reemitted.style == "bold yellow"  # level coloring intact


def test_write_log_still_renders_after_rerender():
    # A plain (unstyled) log line still round-trips through a re-render.
    pane, writes = _capturing_pane()
    pane.write_log("plain line", level="info", source="docker")
    writes.clear()
    pane.set_filter("all", set())
    assert writes, "plain record should re-emit on re-render"
    assert isinstance(writes[-1], Text)


def _source_filter_screen(monkeypatch):
    """A WizardScreen with no starter and no session log. Callers request
    ``forbid_host_side_effects`` so building it cannot reach the host."""
    from utils import brand_logo
    from ui.textual import integration
    from ui.textual.screens import wizard_screen
    from ui.textual.widgets.prompt_panel import PromptOption, PromptStep

    monkeypatch.setattr(brand_logo, "resolve_from_env", lambda: brand_logo.resolve({}))
    monkeypatch.setattr(wizard_screen, "_PROJECT_PREFIX", "atlas-")
    monkeypatch.setattr(wizard_screen.WizardScreen, "_open_launch_log_tee", lambda *a, **k: None)
    monkeypatch.setattr(integration, "_set_wizard_warn_sink", lambda sink: None)
    monkeypatch.setattr(integration, "flush_pending_wizard_warnings", lambda: None)
    step = PromptStep(
        title="Fixture", step_index=1, step_total=1, heading="Fixture", subtitle="",
        options=[PromptOption(value="local", label="Local fixture")], default_value="local",
    )
    screen = wizard_screen.WizardScreen(steps=[step], services=[], no_splash=True)
    assert screen._starter is None and screen._launch_log_path is None
    return screen


@pytest.mark.usefixtures("forbid_host_side_effects")
@pytest.mark.parametrize("service", ["worker-2", "atlas-worker", "worker_2", "api", "n8n"])
def test_source_dropdown_preserves_classified_record_identity(monkeypatch, service):
    from ui.textual.screens.wizard_screen import _classify_compose_line

    screen = _source_filter_screen(monkeypatch)
    screen._log_pane.write = lambda item: None
    screen._log_pane.clear = lambda: None
    line = f"atlas-{service}-1  | source-filter fixture"
    source, level = _classify_compose_line(line)
    assert source == service
    screen._log_pane.write_log(line, source=source, level=level)
    assert screen._log_pane.known_sources() == [service]
    assert screen._log_chips._known_sources == [service]


@pytest.mark.usefixtures("forbid_host_side_effects")
@pytest.mark.parametrize("service", ["worker-2", "atlas-worker", "api"])
def test_source_popup_deselection_hides_and_clear_restores_records(monkeypatch, service):
    import asyncio
    from textual.app import App
    from textual.widgets import SelectionList
    from ui.textual.screens.wizard_screen import _classify_compose_line
    from ui.textual.widgets.block_logo import BrandPanel

    screen = _source_filter_screen(monkeypatch)

    class FixtureApp(App):
        def on_mount(self):
            self.push_screen(screen)

    async def scenario():
        async with FixtureApp().run_test(size=(120, 40)) as pilot:
            screen._phase = "launch"
            screen._logs_enabled = True
            screen.show_tab(BrandPanel.TAB_LOGS)
            line = f"atlas-{service}-1  | popup visibility fixture"
            source, level = _classify_compose_line(line)
            screen._log_pane.write_log(line, source=source, level=level)
            await pilot.pause()
            assert line in screen._log_pane.visible_text()
            screen.action_filter_sources()
            await pilot.pause()
            popup = screen._log_chips._open_popup
            assert popup is not None
            popup.query_one(SelectionList).deselect_all()
            popup.action_dismiss()
            await pilot.pause()
            assert screen._log_chips._trigger_label() == "0 of 1 visible ▾"
            assert screen._log_pane.visible_text() == ""
            screen.action_filter_all()
            await pilot.pause()
            assert screen._log_chips._trigger_label() == "1 of 1 visible ▾"
            assert line in screen._log_pane.visible_text()

    asyncio.run(scenario())


def test_source_chips_keep_an_already_classified_source_as_given(monkeypatch):
    """A second strip turned ``atlas-worker`` into ``worker`` and
    ``worker-2`` into ``worker``; add_source must not normalize again."""
    from ui.textual.screens import wizard_screen
    from ui.textual.widgets.log_filter_chips import LogFilterChips

    monkeypatch.setattr(wizard_screen, "_PROJECT_PREFIX", "atlas-")
    chips = LogFilterChips()
    for source in ("atlas-worker", "worker-2", "atlas-worker", "", "pipeline", "api"):
        chips.add_source(source)
    assert chips._known_sources == ["atlas-worker", "worker-2", "api"]
