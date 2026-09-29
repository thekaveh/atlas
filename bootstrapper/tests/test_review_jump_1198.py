"""Jump straight to a previous decision from the review (#1198).

The wizard only stepped one prompt at a time, so revising an early answer from
the final confirm meant walking back through every step in between. The
review overlay (ctrl+o, #1179) now carries a searchable Decisions page, and
these pilot tests drive it with key presses alone.
"""

from __future__ import annotations

import asyncio

import pytest
from textual.app import App

from ui.textual.screens.review_details import DecisionsPage, ReviewDetails
from ui.textual.screens.wizard_screen import WizardScreen
from ui.textual.widgets import BrandInfo, PromptOption, PromptStep, ServiceRow
from ui.textual.widgets.prompt_panel import SecondaryNumberInput

MINIMUM_SIZE = (60, 20)

ENGINE = "LLM Engine  ·  source"
MODELS = "Ollama  ·  models"
WEAVIATE = "Weaviate  ·  source"
GRAFANA = "Grafana  ·  source"
CLOUD_KEY = "OpenAI Cloud  ·  API key"
CONFIRM = "Confirm  ·  launch the stack"


def _source_step(title: str, service: str, key: str) -> PromptStep:
    return PromptStep(
        title=title, step_index=1, step_total=1, heading=f"{service} source?",
        service_name=service, service_key=key,
        options=[PromptOption(value="container", label="Container"),
                 PromptOption(value="disabled", label="Disabled")],
    )


def _steps(extra_services: int = 0) -> list[PromptStep]:
    """Engine -> models (hidden when the engine is none, cleared when it
    changes) -> unrelated service steps -> launch confirm."""
    return [
        PromptStep(
            title=ENGINE, step_index=1, step_total=1, heading="Which engine?",
            service_name="LLM Engine", service_key="llm_provider",
            invalidates_on_change=(MODELS,),
            options=[PromptOption(value="ollama-container-cpu", label="Ollama CPU"),
                     PromptOption(value="ollama-container-gpu", label="Ollama GPU"),
                     PromptOption(value="none", label="None")],
        ),
        PromptStep(
            title=MODELS, step_index=1, step_total=1, heading="Which models?",
            kind="multiselect",
            skip_if_prev=lambda selections: selections.get(ENGINE) == "none",
            options=[PromptOption(value="llama3", label="llama3"),
                     PromptOption(value="qwen3", label="qwen3")],
        ),
        _source_step(WEAVIATE, "Weaviate", "weaviate"),
        _source_step(GRAFANA, "Grafana", "grafana"),
        *(
            _source_step(f"Extra {i:02d}  ·  source", f"Extra {i:02d}", f"extra_{i:02d}")
            for i in range(extra_services)
        ),
        PromptStep(
            title=CONFIRM, step_index=1, step_total=1, heading="Launch?",
            options=[PromptOption(value="yes", label="Yes"), PromptOption(value="no", label="No")],
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


def _screen(extra_services: int = 0, steps: list[PromptStep] | None = None,
            services: list[ServiceRow] | None = None) -> WizardScreen:
    screen = WizardScreen(
        steps=steps if steps is not None else _steps(extra_services), services=services or [],
        brand=BrandInfo(name="Atlas", tagline="Self-hosted Engineering Platform"),
        no_splash=True,
    )
    _OPEN.append(screen)
    return screen


def _rows(app: App) -> str:
    return "\n".join(
        "".join(segment.text for segment in strip)
        for strip in app.screen._compositor.render_strips()
    )


async def _answer_straight_through(pilot, *, weaviate_down: int = 0, extra: int = 0) -> None:
    """Engine cpu, models llama3, Weaviate (container unless moved), the rest
    container: lands on the launch confirm."""
    await pilot.press("enter")                        # engine: Ollama CPU
    await pilot.press("space", "enter")               # models: llama3
    await pilot.press(*(["down"] * weaviate_down), "enter")
    for _ in range(1 + extra):                        # Grafana + extras
        await pilot.press("enter")
    await pilot.pause()


async def _open_decisions(pilot) -> None:
    await pilot.press("ctrl+o", "tab", "tab")
    await pilot.pause()


def test_any_answered_step_is_reachable_and_editable_from_the_final_confirm():
    """AC1: from the final confirm, search, jump, edit and come back by key."""
    screen = _screen()

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await _answer_straight_through(pilot)
            at_confirm = screen._steps[screen._step_index].title
            await _open_decisions(pilot)
            await pilot.press(*"weav", "enter")
            await pilot.pause()
            jumped_to = screen._steps[screen._step_index].title
            await pilot.press("down", "enter")        # Weaviate -> Disabled
            await pilot.pause()
            reopened = isinstance(screen.app.screen, ReviewDetails)
            await pilot.press("i")                    # extends, not replaces
            await pilot.pause()
            return (at_confirm, jumped_to, screen._selections[WEAVIATE],
                    screen._steps[screen._step_index].title, reopened,
                    screen.app.screen.query_one("#decision-search").value)

    assert asyncio.run(scenario()) == (CONFIRM, WEAVIATE, "disabled", CONFIRM, True, "weavi")


def test_search_narrows_the_list_by_step_title_or_service():
    """AC2."""
    screen = _screen()

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await _answer_straight_through(pilot)
            await _open_decisions(pilot)
            # The list is filled on open, before anything is typed.
            listed_all = ([d.title for d in screen.app.screen._shown],
                          screen.app.screen.query_one("#decision-list").option_count)
            await pilot.press(*"grafana")
            await pilot.pause()
            by_title = ([d.title for d in screen.app.screen._shown], _rows(screen.app))
            await pilot.press(*(["backspace"] * 7), *"llm engine")
            await pilot.pause()
            by_service = [d.title for d in screen.app.screen._shown]
            # Words, not the title's "  ·  " separator, are what people type.
            await pilot.press(*(["backspace"] * 10), *"ollama models")
            await pilot.pause()
            by_words = [d.title for d in screen.app.screen._shown]
            return listed_all, by_title, by_service, by_words

    listed_all, (by_title, rendered), by_service, by_words = asyncio.run(scenario())
    assert (
        listed_all, by_title, "Grafana" in rendered, "Weaviate" in rendered, by_service, by_words,
    ) == (([ENGINE, MODELS, WEAVIATE, GRAFANA], 4), [GRAFANA], True, False, [ENGINE], [MODELS])


def test_changing_an_upstream_answer_clears_only_its_dependents():
    """AC3: a changed engine clears and re-prompts the models answer it
    invalidates; an engine of none hides and prunes it; unrelated answers
    survive both."""
    screen = _screen()

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await _answer_straight_through(pilot, weaviate_down=1)
            await _open_decisions(pilot)
            await pilot.press(*"engine", "enter", "down", "enter")   # cpu -> gpu
            await pilot.pause()
            reprompted = screen._steps[screen._step_index].title
            cleared = MODELS not in screen._selections
            await pilot.press("down", "space", "enter")              # models: qwen3
            await pilot.pause()
            after_change = dict(screen._selections)
            await pilot.press("escape")                              # leave the review
            await _open_decisions(pilot)
            await pilot.press(*"engine", "enter", "down", "enter")   # gpu -> none
            await pilot.pause()
            return reprompted, cleared, after_change, dict(screen._selections), \
                screen._steps[screen._step_index].title

    reprompted, cleared, after_change, after_none, landed = asyncio.run(scenario())
    assert (reprompted, cleared, after_change[MODELS], landed) == (MODELS, True, "qwen3", CONFIRM)
    assert (
        after_change[WEAVIATE], after_change[GRAFANA],
        MODELS in after_none, after_none[ENGINE], after_none[WEAVIATE], after_none[GRAFANA],
    ) == ("disabled", "container", False, "none", "disabled", "container")


def test_returning_from_an_edit_restores_list_scroll_and_cursor():
    """AC4: with enough decisions to scroll at 60x20, the list comes back at
    the same row and scroll offset after an edit."""
    screen = _screen(extra_services=24)

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await _answer_straight_through(pilot, extra=24)
            await _open_decisions(pilot)
            await pilot.press(*(["down"] * 18))
            await pilot.pause()
            options = screen.app.screen.query_one("#decision-list")
            before = (options.highlighted, options.scroll_y)
            await pilot.press("enter", "down", "enter")   # edit that step
            await pilot.pause()
            await pilot.pause()
            options = screen.app.screen.query_one("#decision-list")
            return before, (options.highlighted, options.scroll_y)

    before, after = asyncio.run(scenario())
    assert (before[1] > 0, after) == (True, before)


def test_a_jump_edit_emits_the_same_command_as_a_straight_run():
    """AC5."""
    straight, jumped = _screen(), _screen()

    async def run_straight():
        async with _App(straight).run_test(size=MINIMUM_SIZE) as pilot:
            await _answer_straight_through(pilot, weaviate_down=1)
            return list(straight._command_summary.flags)

    async def run_jumped():
        async with _App(jumped).run_test(size=MINIMUM_SIZE) as pilot:
            await _answer_straight_through(pilot)
            await _open_decisions(pilot)
            await pilot.press(*"weaviate", "enter", "down", "enter")
            await pilot.pause()
            return list(jumped._command_summary.flags)

    straight_flags, jumped_flags = asyncio.run(run_straight()), asyncio.run(run_jumped())
    assert (jumped_flags, ("--weaviate-source", "disabled") in jumped_flags) == (straight_flags, True)


def test_no_secret_value_appears_in_the_review_list():
    """AC6: a cloud-provider key answer shows as the mask, never its value."""
    secret = "".join(("sk", "-", "canary", "-", "review", "-", "1198"))
    steps = _steps()
    steps.insert(-1, PromptStep(title=CLOUD_KEY, step_index=1, step_total=1,
                                heading="OpenAI key?", kind="secret"))
    screen = _screen(steps=steps)

    async def scenario():
        async with _App(screen).run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            screen._selections.update({ENGINE: "none", WEAVIATE: "container", CLOUD_KEY: secret})
            await _open_decisions(pilot)
            return _rows(screen.app), [d.value for d in screen.app.screen._shown]

    rendered, values = asyncio.run(scenario())
    assert (secret in rendered, any(secret in v for v in values), "<set>" in values) == (
        False, False, True)


def test_esc_on_the_jumped_to_step_returns_unchanged_and_a_nested_jump_keeps_the_origin():
    """AC1/AC4: the jumped-to step opens on its current answer; Esc without
    confirming returns to the same list state, and a second jump made while
    editing still comes back to the confirm the review was opened from."""
    screen = _screen()

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await _answer_straight_through(pilot, weaviate_down=1)
            before = dict(screen._selections)
            await _open_decisions(pilot)
            await pilot.press("down", "down", "enter")          # Weaviate row
            await pilot.pause()
            opened_on = screen._prompt.selected_option.value
            await pilot.press("escape")
            await pilot.pause()
            options = screen.app.screen.query_one("#decision-list")
            cancelled = (dict(screen._selections), options.highlighted,
                         screen._steps[screen._step_index].title)
            await pilot.press("enter", "ctrl+o", "tab", "tab", *"grafana", "enter")
            await pilot.pause()
            await pilot.press("down", "enter")                   # Grafana -> Disabled
            await pilot.pause()
            return before, opened_on, cancelled, screen._selections[GRAFANA], \
                screen._steps[screen._step_index].title

    before, opened_on, cancelled, grafana, landed = asyncio.run(scenario())
    assert (opened_on, cancelled, grafana, landed) == (
        "disabled", (before, 2, CONFIRM), "disabled", CONFIRM)


def test_a_jump_made_mid_edit_still_reprompts_an_answer_cleared_earlier():
    """AC3/AC4 (review finding): the engine change clears models; jumping from
    that re-prompt to Grafana and committing must come back to models, not
    to the confirm with models unanswered. Answering it puts a row back
    above Grafana, and the reopened list's cursor follows Grafana, not its
    old row number."""
    screen = _screen()

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await _answer_straight_through(pilot)
            await _open_decisions(pilot)
            await pilot.press(*"engine", "enter", "down", "enter")   # cpu -> gpu
            await pilot.pause()
            # Models is unanswered, so Grafana is row 2 of Engine/Weaviate/Grafana.
            await pilot.press("ctrl+o", "tab", "tab", "down", "down", "enter", "down", "enter")
            await pilot.pause()
            back_on = screen._steps[screen._step_index].title
            await pilot.press("space", "enter")                        # models: llama3
            await pilot.pause()
            review = screen.app.screen
            cursor = review._shown[review.query_one("#decision-list").highlighted].title
            return (back_on, MODELS in screen._selections,
                    screen._steps[screen._step_index].title, review._state.row, cursor)

    # Row 2 now holds Weaviate: the cursor follows Grafana to row 3.
    assert asyncio.run(scenario()) == (MODELS, True, CONFIRM, 2, GRAFANA)


DEFAULT_MODEL = "LLM defaults  ·  chat model"
CONTEXT_VAR = "DEFAULT_CHAT_CONTEXT"
CHAT_ROW = "Chat default"


def _picked_models(selections: dict) -> list[PromptOption]:
    # The inline value exists only on these runtime rows, not the static ones.
    return [PromptOption(value=v, label=f"{v} (chat)",
                         secondary_number=SecondaryNumberInput(env_var=CONTEXT_VAR))
            for v in (selections.get(MODELS) or "").split(",") if v]


def _steps_with_default_model() -> list[PromptStep]:
    steps = _steps()
    steps.insert(-1, PromptStep(
        title=DEFAULT_MODEL, step_index=1, step_total=1, heading="Default chat model?",
        kind="options", options=[], options_provider=_picked_models,
        service_name=CHAT_ROW,
        skip_if_prev=lambda selections: not selections.get(MODELS),
    ))
    return steps


def test_an_answer_the_edit_takes_off_the_menu_is_asked_again_and_labelled():
    """AC3/AC5 (review finding): a runtime-built option list is rebuilt after
    the edit; an answer it no longer offers is dropped and re-prompted, as a
    straight run would, and every row shows the option's label. The dropped
    answer's runtime-only inline value goes with it, and its service row
    returns to pending."""
    screen = _screen(steps=_steps_with_default_model(),
                     services=[ServiceRow(name=CHAT_ROW, source="", pending=True)])

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.press("enter", "space", "down", "space", "enter")  # llama3 + qwen3
            await pilot.press("enter", "enter", "enter")                   # sources, default llama3
            await pilot.pause()
            first_default = (screen._selections[DEFAULT_MODEL],
                             f"__secondary__:{CONTEXT_VAR}" in screen._selections,
                             screen._services[0].pending)
            await _open_decisions(pilot)
            listed = {d.title: d.value for d in screen.app.screen._shown}
            await pilot.press(*"models", "enter", "space", "enter")        # drop llama3
            await pilot.pause()
            reprompted = screen._steps[screen._step_index].title
            dropped = ({DEFAULT_MODEL, f"__secondary__:{CONTEXT_VAR}"}.isdisjoint(screen._selections),
                       screen._services[0].pending)
            await pilot.press("enter")
            await pilot.pause()
            return (first_default, listed[DEFAULT_MODEL], listed[MODELS], reprompted, dropped,
                    screen._selections[DEFAULT_MODEL], screen._steps[screen._step_index].title)

    assert asyncio.run(scenario()) == (
        ("llama3", True, False), "llama3 (chat)", "llama3, qwen3", DEFAULT_MODEL, (True, True),
        "qwen3", CONFIRM)


def test_a_jump_past_the_origin_leaves_no_later_step_cached():
    """Re-review finding: only steps up to the current one may hold a
    cache, since a forward commit never invalidates later entries."""
    screen = _screen(steps=_steps_with_default_model())
    default_idx = next(i for i, st in enumerate(screen._steps) if st.title == DEFAULT_MODEL)

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.press("enter", "space", "enter", "enter", "enter", "enter")
            await pilot.press("escape", "escape")                    # back to Grafana
            await pilot.pause()
            origin = screen._step_index
            await pilot.press("ctrl+o", "tab", "tab", *"chat", "enter")
            await pilot.pause()
            cached_while_there = default_idx in screen._provider_cache
            generation = screen._fetch_generation
            await pilot.press("escape")
            await pilot.pause()
            # The bump discards a later step's fetch still in flight.
            return (screen._steps[origin].title, cached_while_there,
                    screen._step_index == origin, default_idx in screen._provider_cache,
                    screen._fetch_generation > generation)

    assert asyncio.run(scenario()) == (GRAFANA, True, True, False, True)


def test_esc_without_a_change_prunes_nothing_and_keeps_every_cache():
    """AC3 (review finding): leaving a jump without committing must not touch
    a hidden answer kept for later, nor drop a cached option list."""
    screen = _screen()

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.press("down", "down", "enter", "enter", "enter")  # engine none
            await pilot.pause()
            screen._selections[MODELS] = "llama3"      # hidden while the engine is none
            origin = screen._step_index
            screen._provider_cache[origin] = [PromptOption(value="yes", label="Yes")]
            screen._provider_done[origin] = True
            generation = screen._fetch_generation
            await _open_decisions(pilot)
            await pilot.press(*"weaviate", "enter", "escape")
            await pilot.pause()
            kept = (screen._selections.get(MODELS), origin in screen._provider_cache,
                    screen._fetch_generation == generation)
            await pilot.press("escape")
            await _open_decisions(pilot)
            await pilot.press(*"weaviate", "enter", "down", "enter")
            await pilot.pause()
            return kept, MODELS in screen._selections

    assert asyncio.run(scenario()) == (("llama3", True, True), False)


def test_decision_display_reads_sentinels_masks_secrets_and_labels_options():
    from ui.textual.screens.wizard_screen import decision_display
    from wizard.model.cloud_rules import SECRET_CLEAR, SECRET_KEEP

    text = PromptStep(title="Ollama  ·  additional models", step_index=1, step_total=1,
                      heading="More?", kind="text")
    secret = PromptStep(title=CLOUD_KEY, step_index=1, step_total=1, heading="Key?", kind="secret")
    runtime = PromptStep(title=DEFAULT_MODEL, step_index=1, step_total=1, heading="?",
                         kind="options", options=[])
    assert (
        decision_display(text, SECRET_KEEP), decision_display(text, SECRET_CLEAR),
        decision_display(secret, "value-not-shown"), decision_display(secret, ""),
        decision_display(runtime, "qwen3", _picked_models({MODELS: "qwen3"})),
    ) == ("keep current", "clear", "<set>", "not set", "qwen3 (chat)")


def test_esc_in_an_edit_holds_on_a_cleared_answer_then_lets_go():
    """AC1/AC3 (re-review findings): with a nested jump in flight, Esc on the
    re-prompted step stays there (its answer is needed) instead of walking
    Back to step 0 and exiting on the next Esc; a second Esc abandons the
    edit for the ordinary walk, which still visits every step on the way
    forward, so no step that cannot be answered traps the user."""
    screen = _screen()

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await _answer_straight_through(pilot)
            await _open_decisions(pilot)
            await pilot.press(*"engine", "enter", "down", "enter")   # cpu -> gpu
            await pilot.press("ctrl+o", "tab", "tab", *"grafana", "enter", "down", "enter")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            held = (screen._steps[screen._step_index].title, screen._review_edit is not None)
            await pilot.press("escape")
            await pilot.pause()
            let_go = (screen._steps[screen._step_index].title, screen._review_edit is None,
                      screen.app.is_running)
            await pilot.press("enter", "space", "enter", "enter", "enter")
            await pilot.pause()
            return held, let_go, screen._steps[screen._step_index].title, dict(screen._selections)

    held, let_go, landed, selections = asyncio.run(scenario())
    assert (held, let_go, landed) == ((MODELS, True), (ENGINE, True, True), CONFIRM)
    assert (selections[ENGINE], selections[MODELS], selections[GRAFANA]) == (
        "ollama-container-gpu", "llama3", "disabled")


def test_the_footer_offers_only_keys_that_work_on_the_page():
    """Re-review findings: Enter edits only on the Decisions page, where the
    search box takes y as text, so the footer offers each where it works,
    in one row at the 60-column floor."""

    class _Overlay(App):
        def __init__(self, decisions) -> None:
            super().__init__()
            self._decisions = decisions

        def on_mount(self) -> None:
            self.push_screen(ReviewDetails(lines=["./start.sh"], rows=[], decisions=self._decisions))

    async def footers(decisions, pages):
        async with _Overlay(decisions).run_test(size=MINIMUM_SIZE) as pilot:
            shown = []
            for _ in range(pages):
                await pilot.pause()
                footer = pilot.app.screen.query_one("#details-footer")
                shown.append((str(footer.render()), footer.size.height))
                await pilot.press("tab")
            return shown

    launch = asyncio.run(footers(None, 2))
    setup = asyncio.run(footers(DecisionsPage([]), 3))
    assert (
        [("↵ edit" in text, "y copy" in text, rows) for text, rows in launch],
        [("↵ edit" in text, "y copy" in text, rows) for text, rows in setup],
    ) == (
        [(False, True, 1), (False, True, 1)],
        [(False, True, 1), (False, True, 1), (True, False, 1)],
    )


def test_labels_come_from_the_options_the_answer_was_chosen_from():
    """Re-review finding: the Decisions page labels an answer from the
    options it was committed from, not from a later provider run or cache."""
    screen = _screen(steps=_steps_with_default_model())
    default_idx = next(i for i, st in enumerate(screen._steps) if st.title == DEFAULT_MODEL)

    async def scenario():
        async with _App(screen).run_test(size=MINIMUM_SIZE) as pilot:
            await pilot.press("enter", "space", "enter", "enter", "enter", "enter")
            await pilot.pause()
            screen._invalidate_provider_cache_from(0)
            screen._steps[default_idx].options_provider = (
                lambda _selections: [PromptOption(value="llama3", label="relabelled")])
            return {d.title: d.value for d in screen._review_decisions()}[DEFAULT_MODEL]

    assert asyncio.run(scenario()) == "llama3 (chat)"
