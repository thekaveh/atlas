"""The complete destructive warning stays reachable at 60x20 (#1168).

``PromptStep.wrap_subtitle`` promises a destructive warning is fully
readable before confirming, and the cold-start copy lists data loss,
`.env` re-creation and key regeneration. But the compact-height CSS caps
every subtitle at two rows with `overflow-y: hidden`, and at the supported
60x20 floor the warning does not fit in two rows — so part of what the
user was agreeing to was simply not on screen.

These drive the REAL cold-start step built by ``_build_steps_and_rows``,
not a synthetic long-description fixture, which is the third acceptance
criterion on the ticket.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from textual.app import App

from core.config_parser import ConfigParser
from ui.textual import integration as I
from ui.textual.screens.consequence_review import ConsequenceReview
from ui.textual.screens.wizard_screen import WizardScreen
from ui.textual.widgets import BrandInfo, PromptOption, PromptStep

MINIMUM_SIZE = (60, 20)
NORMAL_SIZE = (132, 44)

#: Every consequence the cold-start copy commits to. Each must be
#: readable by keyboard at the minimum size.
CONSEQUENCES = (
    "database records",
    "object files",
    "workflow/chat history",
    "models",
    "caches",
    "LOST",
    "re-creates .env",
    "regenerates keys/passwords",
    "Back up data and configuration first",
    "bind-mounted files and external volumes are kept",
)

SAFE_CHOICE = "No — keep existing data"


class _HostsManager:
    def __getattr__(self, _name):
        return lambda *a, **k: False


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
        screen, path = _OPEN.pop()
        screen._close_launch_log_tee()
        if path is not None:
            path.unlink(missing_ok=True)


def _cold_start_step() -> PromptStep:
    """The actual destructive step the wizard builds — not a fixture."""
    steps, *_ = I._build_steps_and_rows(ConfigParser(), _HostsManager())
    return next(s for s in steps if s.title.startswith("Cold start"))


def _screen(step: PromptStep) -> WizardScreen:
    screen = WizardScreen(
        steps=[step], services=[],
        brand=BrandInfo(name="Atlas", tagline="Self-hosted Engineering Platform"),
        no_splash=True,
    )
    _OPEN.append((screen, screen._launch_log_path))
    return screen


def _rows(screen: WizardScreen) -> list[str]:
    return [
        "".join(segment.text for segment in strip)
        for strip in screen.app.screen._compositor.render_strips()
    ]


#: Box-drawing and scrollbar glyphs sit at the row edges, so joining raw
#: rows would splice them into the middle of any wrapped phrase.
_CHROME = re.compile(r"[\u2500-\u257f\u2580-\u259f]")


def _flat(rows) -> str:
    """Rendered text with borders removed and wrapping collapsed, so a
    phrase split across two rows still reads as one phrase."""
    return re.sub(r"\s+", " ", " ".join(_CHROME.sub(" ", row) for row in rows))


# ─── The step really is the destructive one, and really is clipped ────


def test_the_cold_start_step_is_the_one_wrap_subtitle_protects():
    step = _cold_start_step()
    assert step.wrap_subtitle is True
    assert step.default_value == "no", "the safe choice must be preselected"
    for phrase in CONSEQUENCES:
        assert phrase in step.subtitle, phrase


def test_the_warning_does_not_fit_the_compact_prompt():
    """The premise: at 60x20 the two-row cap hides part of the warning.
    If this ever stops being true the overlay is no longer load-bearing."""
    screen = _screen(_cold_start_step())

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            return _flat(_rows(screen))

    rendered = asyncio.run(scenario())
    missing = [p for p in CONSEQUENCES if p not in rendered]
    assert missing, "the compact prompt now shows everything; re-check this fix"


# ─── AC1: every consequence readable by keyboard at 60x20 ─────────────


def _review_text(size, presses=()):
    """Open the review overlay and collect everything it renders while
    scrolling to the bottom."""
    screen = _screen(_cold_start_step())

    async def scenario():
        async with _App(screen).run_test(size=size) as pilot:
            await pilot.pause()
            for key in presses:
                await pilot.press(key)
            await pilot.press("ctrl+r")
            await pilot.pause()
            seen = list(_rows(screen))
            on_review = isinstance(screen.app.screen, ConsequenceReview)
            for _ in range(40):
                await pilot.press("down")
                await pilot.pause()
                seen.extend(_rows(screen))
            return _flat(seen), on_review

    return asyncio.run(scenario())


def test_every_consequence_is_readable_by_keyboard_at_the_minimum_size():
    """AC1."""
    rendered, on_review = _review_text(MINIMUM_SIZE)
    assert on_review, "ctrl+r must open the review overlay"
    missing = [p for p in CONSEQUENCES if p not in rendered]
    assert missing == [], missing


def test_every_consequence_is_readable_at_the_normal_size_too():
    rendered, _ = _review_text(NORMAL_SIZE)
    assert [p for p in CONSEQUENCES if p not in rendered] == []


def test_the_review_names_both_choices_and_their_scope():
    """"Keep project/volume scope visible" — the choices carry it."""
    rendered, _ = _review_text(MINIMUM_SIZE)
    assert SAFE_CHOICE in rendered
    assert "Yes — rebuild from scratch" in rendered
    assert "delete project volume data and regenerate configuration" in rendered


def test_the_review_says_nothing_is_confirmed_there():
    rendered, _ = _review_text(MINIMUM_SIZE)
    assert "nothing is confirmed here" in rendered


# ─── AC2: resizing never removes the safe cancel action ───────────────


def _safe_choice_after(sizes):
    screen = _screen(_cold_start_step())

    async def scenario():
        async with _App(screen).run_test(size=sizes[0]) as pilot:
            await pilot.pause()
            seen = []
            for size in sizes[1:]:
                pilot.app._size = None
                await pilot.resize_terminal(*size)
                await pilot.pause()
                seen.append(
                    (SAFE_CHOICE in _flat(_rows(screen)),
                     screen._prompt.selected_option.value)
                )
            return seen

    return asyncio.run(scenario())


def test_resizing_never_removes_the_safe_choice():
    """AC2, across the floor and back."""
    seen = _safe_choice_after(
        [MINIMUM_SIZE, NORMAL_SIZE, MINIMUM_SIZE, (60, 24), MINIMUM_SIZE]
    )
    assert all(visible for visible, _ in seen), seen
    assert {value for _, value in seen} == {"no"}, seen


def test_the_safe_choice_survives_opening_and_closing_the_review():
    screen = _screen(_cold_start_step())

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            before = screen._prompt.selected_option.value
            await pilot.press("ctrl+r")
            await pilot.pause()
            opened = isinstance(screen.app.screen, ConsequenceReview)
            await pilot.press("escape")
            await pilot.pause()
            closed = not isinstance(screen.app.screen, ConsequenceReview)
            return before, opened, closed, screen._prompt.selected_option.value, dict(screen._selections)

    before, opened, closed, after, selections = asyncio.run(scenario())
    assert (before, opened, closed, after) == ("no", True, True, "no")
    assert selections == {}, "the review must never commit an answer"


def test_leaving_the_review_does_not_advance_the_wizard():
    screen = _screen(_cold_start_step())

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+r")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            return screen._step_index

    assert asyncio.run(scenario()) == 0


# ─── The affordance is advertised, not hidden ─────────────────────────


def _footer_text(size):
    screen = _screen(_cold_start_step())

    async def scenario():
        async with _App(screen).run_test(size=size) as pilot:
            await pilot.pause()
            return _flat(_rows(screen))

    return asyncio.run(scenario())


@pytest.mark.parametrize("size", [MINIMUM_SIZE, NORMAL_SIZE])
def test_the_review_is_advertised_on_a_destructive_step(size):
    assert "review" in _footer_text(size).lower()


def test_a_non_destructive_step_does_not_advertise_a_review():
    step = PromptStep(
        title="Hosts setup", step_index=1, step_total=1,
        heading="Configure hosts?", subtitle="Short.", wrap_subtitle=False,
        options=[PromptOption("no", "No"), PromptOption("yes", "Yes")],
        default_value="no",
    )
    screen = _screen(step)

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.pause()
            rendered = _flat(_rows(screen)).lower()
            await pilot.press("ctrl+r")
            await pilot.pause()
            return rendered, isinstance(screen.app.screen, ConsequenceReview)

    rendered, opened = asyncio.run(scenario())
    assert "review" not in rendered
    assert opened is False, "ctrl+r must be a no-op off a destructive step"
