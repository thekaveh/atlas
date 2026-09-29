"""Every review detail is reachable with the keyboard (#1179).

The live command summary caps itself at four rows and cannot take focus, and
the service table shows each row's source options, dependencies and URLs only
in a mouse-hover tooltip. ``ctrl+o`` opens a read-only overlay that carries
both; these pilot tests drive it with key presses alone at the 60x20 floor.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from textual.app import App

from ui.textual.screens.review_details import ReviewDetails, command_lines
from ui.textual.screens.wizard_screen import WizardScreen
from ui.textual.widgets import BrandInfo, PromptOption, PromptStep, ServiceRow, ServiceTable
from ui.textual.widgets.command_summary import masked_flags

MINIMUM_SIZE = (60, 20)

#: 19 flags plus the program line: a 20-line command in the overlay.
FLAGS = [(f"--service-{i:02d}-source", f"container-variant-{i:02d}") for i in range(19)]

ROWS = [
    # Long enough that its card must wrap at the 60-column floor.
    ServiceRow(
        name="llm-provider", source="ollama-container-gpu", alias="ollama", alias_port="11434",
        port=":63012", source_options=[
            "ollama-container-cpu", "ollama-container-gpu", "ollama-localhost", "none",
        ],
        depends_on=["supabase", "redis", "litellm", "kong-api-gateway", "backend"],
    ),
    *(
        ServiceRow(
            name=f"svc-{i}", source="container", alias=f"svc{i}", alias_port="8000",
            port=f":{64000 + i}", source_options=["container", "localhost", "disabled"],
            depends_on=["supabase", "redis"] if i % 2 else [],
        )
        for i in range(1, 6)
    ),
]


class _App(App):
    def __init__(self, screen: WizardScreen) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


_OPEN: list[WizardScreen] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    while _OPEN:
        screen = _OPEN.pop()
        screen._close_launch_log_tee()
        screen._launch_log_path.unlink(missing_ok=True)


def _steps() -> list[PromptStep]:
    return [
        PromptStep(
            title="Services · pick", step_index=1, step_total=2,
            heading="Which services?", kind="multiselect",
            options=[PromptOption(value=f"svc-{i}", label=f"svc-{i}") for i in range(4)],
        ),
        PromptStep(
            title="Confirm", step_index=2, step_total=2, heading="Launch?",
            options=[PromptOption(value="yes", label="Yes"), PromptOption(value="no", label="No")],
        ),
    ]


def _screen() -> WizardScreen:
    screen = WizardScreen(
        steps=_steps(), services=[ServiceRow(**r.__dict__) for r in ROWS],
        brand=BrandInfo(name="Atlas", tagline="Self-hosted Engineering Platform"),
        no_splash=True,
    )
    _OPEN.append(screen)
    return screen


def _rows(app: App) -> list[str]:
    return ["".join(seg.text for seg in strip) for strip in app.screen._compositor.render_strips()]


_CHROME = re.compile(r"[─-╿▀-▟]")


def _flat(rows) -> str:
    return re.sub(r"\s+", " ", " ".join(_CHROME.sub(" ", row) for row in rows))


def test_every_line_of_a_20_line_command_is_reachable_by_keyboard_at_60x20():
    """AC1."""
    screen = _screen()
    lines = command_lines(screen._command_summary.program, FLAGS)
    assert len(lines) == 20

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            screen._command_summary.set_flags(FLAGS)
            await pilot.press("ctrl+o")
            await pilot.pause()
            seen = list(_rows(screen.app))
            opened = isinstance(screen.app.screen, ReviewDetails)
            for _ in range(25):
                await pilot.press("down")
                await pilot.pause()
                seen.extend(_rows(screen.app))
            return _flat(seen), opened

    rendered, opened = asyncio.run(scenario())
    missing = [line.rstrip(" \\").strip() for line in lines if line.rstrip(" \\").strip() not in rendered]
    assert (opened, missing) == (True, [])


def test_keyboard_service_card_matches_the_hover_tooltip():
    """AC2: at the 60x20 floor every row's keyboard card is the hover
    tooltip verbatim, and every value in it is actually on screen."""
    screen = _screen()

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+o", "tab")
            await pilot.pause()
            cards, screens = [], []
            for index in range(len(ROWS)):
                if index:
                    await pilot.press("down")
                    await pilot.pause()
                cards.append(str(screen.app.screen.query_one("#service-card").render()))
                screens.append(_flat(_rows(screen.app)))
            return cards, screens

    cards, screens = asyncio.run(scenario())
    tooltips = [ServiceTable._build_tooltip(row).plain for row in ROWS]
    hidden = [
        (row.name, value)
        for row, rendered in zip(ROWS, screens)
        for _label, value in ServiceTable._tooltip_pairs(row)
        if re.sub(r"\s+", " ", value) not in rendered
    ]
    assert (cards, hidden) == (tooltips, [])


def test_escape_restores_step_cursor_and_selections():
    """AC3."""
    screen = _screen()

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            await pilot.press("down", "space", "down")
            await pilot.pause()

            def state():
                prompt = screen._prompt
                return (screen._step_index, prompt._selected_index, sorted(prompt._checked_values))

            before = state()
            await pilot.press("ctrl+o", "tab", "down", "down", "tab", "down")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            return before, state(), screen.app.screen is screen

    before, after, back = asyncio.run(scenario())
    assert (after, back) == (before, True)


@pytest.mark.parametrize("suffix", ["PASSWORD", "SECRET", "KEY", "TOKEN"])
def test_copied_command_masks_secret_valued_flags(suffix):
    """AC5: the copy action never carries a secret value."""
    secret = "".join(("canary", "-", suffix.lower(), "-value"))
    flags = [("--base-port", "63000"), (f"--my-service-{suffix.lower()}", secret)]
    screen = _screen()

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            screen._command_summary.set_flags(flags)
            await pilot.press("ctrl+o", "y")
            await pilot.pause()
            return screen.app.clipboard

    copied = asyncio.run(scenario())
    assert (
        secret in copied,
        "--base-port 63000" in copied,
        f"--my-service-{suffix.lower()} '<set>'" in copied,
        masked_flags([(f"--x-{suffix.lower()}", secret)]),
    ) == (False, True, True, [(f"--x-{suffix.lower()}", "<set>")])


def test_prompt_first_option_stays_visible_at_60x20_after_the_overlay_closes():
    """AC6."""
    screen = _screen()

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            before = _flat(_rows(screen.app))
            await pilot.press("ctrl+o")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            return before, _flat(_rows(screen.app))

    before, after = asyncio.run(scenario())
    assert ("svc-0" in after, after == before) == (True, True)
