"""Regression contracts for issue #1030's accepted 60x20 TUI floor."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest
from textual.app import App

from ui.term_caps import MIN_TERMINAL_COLS, MIN_TERMINAL_ROWS, is_tui_capable
from ui.textual.screens.wizard_screen import WizardScreen
from ui.textual.widgets import (
    BrandInfo,
    BrandPanel,
    CommandSummary,
    InfoPanel,
    PromptOption,
    PromptPanel,
    PromptStep,
)


NORMAL_SIZE = (120, 44)
MINIMUM_SIZE = (60, 20)


class _App(App):
    def __init__(self, screen: WizardScreen) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


_OPEN: list[tuple[WizardScreen, Path | None]] = []


@pytest.fixture(autouse=True)
def _cleanup_launch_logs():
    yield
    while _OPEN:
        screen, path = _OPEN.pop()
        screen._close_launch_log_tee()
        if path is not None:
            path.unlink(missing_ok=True)


def _screen(step: PromptStep, *, services=None) -> WizardScreen:
    screen = WizardScreen(
        steps=[step],
        services=list(services or []),
        brand=BrandInfo(name="Atlas", tagline="Self-hosted Engineering Platform"),
        no_splash=True,
    )
    _OPEN.append((screen, screen._launch_log_path))
    return screen


def _step(kind: str, *, subtitle: str = "Choose a value.", wrap=False) -> PromptStep:
    options = [
        PromptOption(value="alpha", label="Alpha", hint="first choice"),
        PromptOption(value="beta", label="Beta", hint="second choice"),
    ]
    return PromptStep(
        title=f"{kind.title()} prompt",
        step_index=1,
        step_total=1,
        heading=f"{kind.title()} heading",
        subtitle=subtitle,
        options=options,
        default_value=(
            "63000" if kind == "number" else "" if kind == "secret" else "alpha"
        ),
        default_values=["alpha"] if kind == "multiselect" else [],
        kind=kind,
        wrap_subtitle=wrap,
    )


def _rows(screen: WizardScreen) -> list[str]:
    return [
        "".join(segment.text for segment in strip)
        for strip in screen.app.screen._compositor.render_strips()
    ]


def _visible_rows(widget, viewport_height: int) -> int:
    return max(
        0,
        min(widget.region.bottom, viewport_height) - max(widget.region.y, 0),
    )


@pytest.mark.parametrize(
    ("kind", "focus_id", "content"),
    [
        ("options", "option-list", "Alpha"),
        ("multiselect", "option-list", "Alpha"),
        ("number", "number-input", "63000"),
        ("secret", "secret-input", "paste API key"),
        ("text", "number-input", "alpha"),
    ],
)
def test_every_prompt_kind_is_operable_at_the_accepted_floor(
    kind: str, focus_id: str, content: str
) -> None:
    screen = _screen(_step(kind))

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            rows = _rows(screen)
            return (
                _visible_rows(screen.query_one(PromptPanel), MINIMUM_SIZE[1]),
                _visible_rows(screen.query_one(CommandSummary), MINIMUM_SIZE[1]),
                getattr(screen.app.focused, "id", None),
                rows,
                screen.has_class("compact-height"),
            )

    prompt_rows, summary_rows, focused_id, rows, compact = asyncio.run(scenario())
    rendered = "\n".join(rows)
    assert (
        compact,
        prompt_rows >= 5,
        summary_rows >= 3,
        focused_id,
        content in rendered,
        "Command summary" in rendered,
        "next" in rendered.lower(),
        "back" in rendered.lower(),
        "ATLAS" in rendered,
    ) == (True, True, True, focus_id, True, True, True, True, True)


@pytest.mark.parametrize(
    ("kind", "expected", "absent"),
    [
        ("options", ("move", "next", "back", "ctrl+q", "quit"), ("mark",)),
        (
            "multiselect",
            ("move", "mark", "next", "back", "ctrl+q", "quit"),
            (),
        ),
        ("number", ("next", "back", "ctrl+q", "quit"), ("move", "mark")),
        ("secret", ("next", "back", "ctrl+q", "quit"), ("move", "mark")),
        ("text", ("next", "back", "ctrl+q", "quit"), ("move", "mark")),
    ],
)
def test_compact_setup_hints_match_the_active_prompt(
    kind: str, expected: tuple[str, ...], absent: tuple[str, ...]
) -> None:
    screen = _screen(_step(kind))

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            return "\n".join(_rows(screen)).lower()

    rendered = asyncio.run(scenario())
    assert ({item for item in expected if item not in rendered}, {item for item in absent if item in rendered}) == (set(), set())


def test_narrow_footer_stays_complete_when_height_grows() -> None:
    screen = _screen(_step("multiselect"))

    async def scenario():
        async with _App(screen).run_test(size=(60, 29)) as pilot:
            await pilot.pause()
            initial = (screen.has_class("compact-height"), "\n".join(_rows(screen)))
            await pilot.resize_terminal(60, 30)
            at_thirty = (screen.has_class("compact-height"), "\n".join(_rows(screen)))
            await pilot.resize_terminal(60, 44)
            at_forty_four = (screen.has_class("compact-height"), "\n".join(_rows(screen)))
            return initial, at_thirty, at_forty_four

    initial, at_thirty, at_forty_four = asyncio.run(scenario())
    assert (
        initial[0],
        at_thirty[0],
        at_forty_four[0],
        all(token in initial[1].lower() for token in ("back", "ctrl+q", "quit")),
        all(token in at_thirty[1].lower() for token in ("back", "ctrl+q", "quit")),
        all(token in at_forty_four[1].lower() for token in ("back", "ctrl+q", "quit")),
        "█████" in at_thirty[1],
    ) == (True, False, False, True, True, True, True)


def test_wide_footer_restores_the_full_shortcut_inventory() -> None:
    screen = _screen(_step("multiselect"))

    async def scenario():
        async with _App(screen).run_test(size=(132, 44)) as pilot:
            await pilot.pause()
            return "\n".join(_rows(screen)).lower(), screen._compact_footer

    rendered, compact_footer = asyncio.run(scenario())
    assert (
        compact_footer,
        all(
            token in rendered
            for token in ("navigate", "toggle", "confirm", "search", "filter", "back", "quit")
        ),
    ) == (False, True)


def test_long_description_and_long_list_keep_the_cursor_visible() -> None:
    step = PromptStep(
        title="Models",
        step_index=1,
        step_total=1,
        heading="Choose models",
        subtitle=" ".join(["Long explanatory context"] * 24),
        wrap_subtitle=True,
        options=[
            PromptOption(value=f"model-{index}", label=f"Model {index}")
            for index in range(1, 31)
        ],
        default_values=[],
        kind="multiselect",
    )
    screen = _screen(step)

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            for _ in range(15):
                await pilot.press("down")
            await pilot.press("space")
            await pilot.pause()
            return _rows(screen), screen._prompt.selected_index, set(screen._prompt._checked_values)

    rows, selected_index, checked = asyncio.run(scenario())
    rendered = "\n".join(rows)
    assert selected_index == 15
    assert checked == {"model-16"}
    assert "Model 16" in rendered
    assert "back" in rendered.lower()


def test_resize_round_trip_preserves_focused_option_and_checked_state() -> None:
    screen = _screen(_step("multiselect"))

    async def scenario():
        async with _App(screen).run_test(size=NORMAL_SIZE) as pilot:
            await pilot.pause()
            focused = screen.app.focused
            await pilot.press("down", "space")
            before = (screen._prompt.selected_index, set(screen._prompt._checked_values))
            await pilot.resize_terminal(*MINIMUM_SIZE)
            minimum = (
                screen.app.focused is focused,
                screen._prompt.selected_index,
                set(screen._prompt._checked_values),
                _rows(screen),
            )
            await pilot.resize_terminal(*NORMAL_SIZE)
            normal = (
                screen.app.focused is focused,
                screen._prompt.selected_index,
                set(screen._prompt._checked_values),
                _rows(screen),
            )
            return before, minimum, normal, screen.has_class("compact-height")

    before, minimum, normal, compact_after = asyncio.run(scenario())
    assert before == (1, {"alpha", "beta"})
    assert minimum[:3] == (True, 1, {"alpha", "beta"})
    assert "Beta" in "\n".join(minimum[3])
    assert normal[:3] == (True, 1, {"alpha", "beta"})
    assert "Self-hosted Engineering Platform" in "\n".join(normal[3])
    assert compact_after is False


def test_minimum_keyboard_next_and_back_round_trip_has_no_focus_trap() -> None:
    steps = [
        _step("options"),
        PromptStep(
            title="API key",
            step_index=2,
            step_total=3,
            heading="Enter a key",
            subtitle="The value is masked.",
            kind="secret",
        ),
        PromptStep(
            title="Review",
            step_index=3,
            step_total=3,
            heading="Review the configuration",
            subtitle="Press Escape to make changes.",
            options=[PromptOption("yes", "Launch"), PromptOption("no", "Back")],
            default_value="no",
        ),
    ]
    screen = WizardScreen(
        steps=steps,
        services=[],
        brand=BrandInfo(name="Atlas", tagline="Self-hosted Engineering Platform"),
        no_splash=True,
    )
    _OPEN.append((screen, screen._launch_log_path))

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.press("t", "o", "k", "e", "n", "enter")
            await pilot.pause()
            review = (screen._step_index, getattr(screen.app.focused, "id", None), _rows(screen))
            await pilot.press("escape")
            await pilot.pause()
            secret = (
                screen._step_index,
                getattr(screen.app.focused, "id", None),
                screen._prompt._secret_input.value,
                _rows(screen),
            )
            await pilot.press("escape")
            await pilot.pause()
            options = (
                screen._step_index,
                getattr(screen.app.focused, "id", None),
                screen._prompt.selected_index,
                _rows(screen),
            )
            return review, secret, options

    review, secret, options = asyncio.run(scenario())
    assert review[0:2] == (2, "option-list")
    assert "Review the configuration" in "\n".join(review[2])
    assert secret[0:3] == (1, "secret-input", "token")
    assert "Enter a key" in "\n".join(secret[3])
    assert options[0:3] == (0, "option-list", 1)
    assert "Options heading" in "\n".join(options[3])


def test_compact_hints_refresh_when_navigation_changes_prompt_kind() -> None:
    steps = [
        _step("options"),
        PromptStep(
            title="API key",
            step_index=2,
            step_total=3,
            heading="Enter a key",
            subtitle="The value is masked.",
            kind="secret",
        ),
        PromptStep(
            title="Models",
            step_index=3,
            step_total=3,
            heading="Choose models",
            subtitle="Select any models.",
            options=[PromptOption("alpha", "Alpha"), PromptOption("beta", "Beta")],
            kind="multiselect",
        ),
    ]
    screen = WizardScreen(
        steps=steps,
        services=[],
        brand=BrandInfo(name="Atlas", tagline="Self-hosted Engineering Platform"),
        no_splash=True,
    )
    _OPEN.append((screen, screen._launch_log_path))

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            secret = "\n".join(_rows(screen)).lower()
            await pilot.press("t", "enter")
            await pilot.pause()
            multiselect = "\n".join(_rows(screen)).lower()
            return secret, multiselect

    secret, multiselect = asyncio.run(scenario())
    assert (
        all(token in secret for token in ("next", "back", "ctrl+q", "quit")),
        any(token in secret for token in ("move", "mark")),
        all(token in multiselect for token in ("move", "mark", "next", "back")),
    ) == (True, False, True)


def test_minimum_search_and_filter_focus_path_stays_visible() -> None:
    step = PromptStep(
        title="Models",
        step_index=1,
        step_total=1,
        heading="Choose models",
        subtitle="Search or filter the catalog.",
        options=[
            PromptOption("alpha", "Alpha", badges=["vision"]),
            PromptOption("beta", "Beta", badges=["tools"]),
        ],
        kind="multiselect",
        filter_tags=("vision", "tools"),
    )
    screen = _screen(step)

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            await pilot.press("/")
            await pilot.press("b", "e", "t", "a")
            searched = (getattr(screen.app.focused, "id", None), _rows(screen))
            await pilot.press("tab")
            await pilot.press("f")
            filtered = (getattr(screen.app.focused, "id", None), _rows(screen))
            return searched, filtered

    searched, filtered = asyncio.run(scenario())
    assert searched[0] == "search-input"
    assert "Beta" in "\n".join(searched[1])
    assert filtered[0] == "option-list"
    assert "Filter" in "\n".join(filtered[1])


@pytest.mark.parametrize("kind", ["number", "secret", "text"])
def test_resize_round_trip_preserves_focused_input_and_text(kind: str) -> None:
    screen = _screen(_step(kind))

    async def scenario():
        async with _App(screen).run_test(size=NORMAL_SIZE) as pilot:
            await pilot.pause()
            focused = screen.app.focused
            await pilot.press("x", "y", "z")
            value = focused.value
            await pilot.resize_terminal(*MINIMUM_SIZE)
            at_minimum = (screen.app.focused is focused, focused.value, _rows(screen))
            await pilot.resize_terminal(*NORMAL_SIZE)
            at_normal = (screen.app.focused is focused, focused.value, _rows(screen))
            return value, at_minimum, at_normal

    value, minimum, normal = asyncio.run(scenario())
    assert value.endswith("xyz")
    assert minimum[0:2] == (True, value)
    assert normal[0:2] == (True, value)
    assert "Command summary" in "\n".join(minimum[2])


def test_minimum_review_and_launch_tabs_are_not_blank() -> None:
    review = PromptStep(
        title="Confirm",
        step_index=1,
        step_total=1,
        heading="Launch the stack with this configuration?",
        subtitle="Review the command before continuing.",
        options=[PromptOption(value="yes", label="Launch"), PromptOption(value="no", label="Back")],
        default_value="no",
    )
    screen = _screen(review)

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            review_rows = _rows(screen)
            await screen._transition_to_launch()
            await pilot.pause()
            logs_rows = _rows(screen)
            screen.show_tab(BrandPanel.TAB_SETUP)
            await pilot.pause()
            setup_rows = _rows(screen)
            return review_rows, logs_rows, setup_rows, screen.query_one(InfoPanel).display

    review_rows, logs_rows, setup_rows, overview_display = asyncio.run(scenario())
    assert "Launch the stack" in "\n".join(review_rows)
    assert "Command summary" in "\n".join(review_rows)
    assert "Stack startup" in "\n".join(logs_rows)
    assert "cancel" in "\n".join(logs_rows).lower()
    assert overview_display is True
    assert "Stack overview" in "\n".join(setup_rows)


def test_normal_layout_retains_existing_panel_budget() -> None:
    screen = _screen(_step("options"))

    async def scenario():
        async with _App(screen).run_test(size=NORMAL_SIZE) as pilot:
            await pilot.pause()
            return (
                screen.query_one(BrandPanel).region.height,
                screen.query_one(InfoPanel).display,
                screen.has_class("compact-height"),
                _rows(screen),
            )

    brand_height, overview_display, compact, rows = asyncio.run(scenario())
    assert brand_height == 9
    assert overview_display is True
    assert compact is False
    assert "█████" in "\n".join(rows)


def test_compact_identity_uses_the_configured_brand_name() -> None:
    screen = WizardScreen(
        steps=[_step("options")],
        services=[],
        brand=BrandInfo(
            name="Private Atlas Fork",
            tagline="Private engineering platform",
        ),
        no_splash=True,
    )
    _OPEN.append((screen, screen._launch_log_path))

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            return _rows(screen)

    rendered = "\n".join(asyncio.run(scenario()))
    assert "PRIVATE ATLAS FORK" in rendered
    assert "Private engineering platform" in rendered


@pytest.mark.parametrize(
    ("cols", "rows", "expected"),
    [(60, 20, True), (59, 20, False), (60, 19, False)],
)
def test_rendered_floor_matches_the_terminal_gate(
    monkeypatch, cols: int, rows: int, expected: bool
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(
        shutil,
        "get_terminal_size",
        lambda *args, **kwargs: os.terminal_size((cols, rows)),
    )
    assert (MIN_TERMINAL_COLS, MIN_TERMINAL_ROWS) == MINIMUM_SIZE
    assert is_tui_capable() is expected


def test_committed_screenshots_preserve_the_atlas_palette() -> None:
    required = {"#12131e", "#0e0f18", "#2b2f4a", "#7dcfff"}
    screenshot_root = Path(__file__).resolve().parents[2] / "docs" / "screenshots"
    observed = {
        name: {
            token
            for token in required
            if token in (screenshot_root / name).read_text(encoding="utf-8").lower()
        }
        for name in (
            "wizard-minimum-terminal.svg",
            "wizard-normal-terminal.svg",
        )
    }
    assert observed == {
        "wizard-minimum-terminal.svg": required,
        "wizard-normal-terminal.svg": required,
    }
