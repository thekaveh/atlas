import pytest

from ui.textual.widgets.atlas_splash import AtlasSplash


def test_skip_is_idempotent():
    calls = []
    s = AtlasSplash(on_done=lambda: calls.append(1))
    s.skip()
    s.skip()
    assert calls == [1]


def test_skip_marks_done_and_fires_callback():
    calls = []
    s = AtlasSplash(on_done=lambda: calls.append(1))
    assert s._done is False
    s.skip()
    assert s._done is True
    assert calls == [1]


def test_on_done_optional():
    # on_done defaults to a no-op; skip must not raise without a callback.
    AtlasSplash().skip()


def _splash_wizard(monkeypatch):
    """A two-step WizardScreen that mounts the opt-in splash (no session log)."""
    from ui.textual.screens import wizard_screen
    from ui.textual.widgets.atlas_splash import should_show_splash
    from ui.textual.widgets.prompt_panel import PromptOption, PromptStep

    monkeypatch.setenv("ATLAS_SPLASH", "1")
    monkeypatch.delenv("ATLAS_NO_SPLASH", raising=False)
    # Block-art artwork: never queries the terminal's image protocol.
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    monkeypatch.setattr(wizard_screen.WizardScreen, "_open_launch_log_tee", lambda *a, **k: None)
    if not should_show_splash(no_splash=False):
        pytest.skip("splash artwork is not present in this checkout")
    steps = [
        PromptStep(
            title=title, step_index=i, step_total=2, heading=title, subtitle="",
            options=[PromptOption(value="x", label="X")], default_value="x",
        )
        for i, title in enumerate(("A", "B"), start=1)
    ]
    return wizard_screen.WizardScreen(steps=steps, services=[], no_splash=False)


@pytest.mark.parametrize("key", ["enter", "escape", "down"])
def test_a_bound_key_only_skips_the_splash(monkeypatch, key):
    """The wizard binds Enter/Esc/arrows with priority, so they used to act
    on the hidden wizard behind the splash: Enter committed step A unseen and
    Esc on the first step exited. Any key must only skip the splash."""
    import asyncio

    from textual.app import App

    scr = _splash_wizard(monkeypatch)

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(scr)

    async def scenario():
        results = []
        app = _App()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            results.append(any(isinstance(w, AtlasSplash) for w in scr.children))
            await pilot.press(key)
            await pilot.pause()
            results.append(any(isinstance(w, AtlasSplash) for w in scr.children))
            results.append((dict(scr._selections), scr._step_index, app.return_code))
        return results

    shown, still_shown, (selections, step_index, exited) = asyncio.run(scenario())

    assert shown is True, "the splash must mount for this test to mean anything"
    assert still_shown is False, "a key press must skip the splash"
    assert selections == {} and step_index == 0, "the key must not commit a hidden step"
    assert exited is None, "the key must not exit the wizard"
