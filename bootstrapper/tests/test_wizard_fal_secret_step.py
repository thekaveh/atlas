"""Tests for the FAL Cloud Media wizard API-token (secret) step (#517).

FAL is prompted with a masked API-token step (enter a key to enable, blank to
keep disabled) instead of a plain enabled/disabled source tile, placed right
after ComfyUI in the media category. These cover the step shape and the
key→source derivation in _selections_to_args.

#1255 moved fal onto the verdict table #1183 gave the cloud providers
(``resolve_secret_verdict``): Enter changes nothing, "enable"/"disable" flip
FAL_SOURCE and keep the saved key, and only "remove" blanks it.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ui.textual.integration import _selections_to_args, PICKER_STEP_TITLE
from ui.textual.widgets.prompt_panel import SECRET_KEEP, SECRET_CLEAR
from wizard.llm_steps import (
    FAL_DISPLAY_NAME,
    build_fal_secret_step,
    fal_secret_title,
)
from wizard.model.cloud_rules import (
    SECRET_DISABLE,
    SECRET_ENABLE,
    resolve_cloud_provider,
    resolve_secret_verdict,
)


def _noop(_msg: str) -> None:
    pass


# ─────────────────────────── step shape ───────────────────────────
def test_build_fal_secret_step_shape():
    steps = build_fal_secret_step({}, _noop)
    assert len(steps) == 1
    step = steps[0]
    assert step.title == fal_secret_title() == "FAL Cloud Media  ·  API key"
    assert step.kind == "secret"
    # service_name MUST be empty — else the grid-row source handler would write
    # the raw API key into the FAL row's source (a secret leak into the UI).
    assert step.service_name == ""
    assert step.options == []


def test_build_fal_secret_step_prefills_existing_key():
    steps = build_fal_secret_step(
        {"FAL_API_KEY": "fal-key-abc", "FAL_SOURCE": "enabled"}, _noop
    )
    step = steps[0]
    assert step.default_value == "fal-key-abc"
    assert step.secret_keep_hint is not None  # keep/replace/clear affordance


def test_build_fal_secret_step_no_key_no_hint():
    step = build_fal_secret_step({}, _noop)[0]
    assert step.default_value == ""
    assert step.secret_keep_hint is None


# ─────────────────────────── apply logic ───────────────────────────
def _fal_svc():
    # FAL stays discovered (kept in services_info) so its grid row + off-track
    # force-disable survive; only its source *step* is replaced.
    return SimpleNamespace(
        key="fal", display_name="FAL Cloud Media",
        options=["enabled", "disabled"], current_value="disabled",
    )


def test_key_enables_and_persists():
    source_args, opts = _selections_to_args(
        {fal_secret_title(): "fal-key-123"},
        [_fal_svc()], current_base_port=63000, env_vars={},
    )
    assert source_args["fal_source"] == "enabled"
    assert opts["cloud_api_keys"]["FAL_API_KEY"] == "fal-key-123"


def test_blank_disables_and_wipes():
    source_args, opts = _selections_to_args(
        {fal_secret_title(): ""},
        [_fal_svc()], current_base_port=63000, env_vars={"FAL_SOURCE": "enabled", "FAL_API_KEY": "old"},
    )
    assert source_args["fal_source"] == "disabled"
    assert opts["cloud_api_keys"]["FAL_API_KEY"] == ""


def test_clear_disables_and_wipes():
    source_args, opts = _selections_to_args(
        {fal_secret_title(): SECRET_CLEAR},
        [_fal_svc()], current_base_port=63000, env_vars={"FAL_SOURCE": "enabled", "FAL_API_KEY": "old"},
    )
    assert source_args["fal_source"] == "disabled"
    assert opts["cloud_api_keys"]["FAL_API_KEY"] == ""


def test_keep_leaves_a_disabled_keyed_fal_disabled():
    """#1255 AC1. This used to auto-promote: a bare Enter past a saved key
    wrote fal_source=enabled, so pressing Enter changed fal's state. Enter
    is now no verdict on either field, exactly like the cloud providers."""
    source_args, opts = _selections_to_args(
        {fal_secret_title(): SECRET_KEEP},
        [_fal_svc()], current_base_port=63000,
        env_vars={"FAL_SOURCE": "disabled", "FAL_API_KEY": "saved-key"},
    )
    assert "fal_source" not in source_args
    assert "FAL_API_KEY" not in opts["cloud_api_keys"]


def test_keep_when_already_enabled_leaves_source_alone():
    source_args, _ = _selections_to_args(
        {fal_secret_title(): SECRET_KEEP},
        [_fal_svc()], current_base_port=63000,
        env_vars={"FAL_SOURCE": "enabled", "FAL_API_KEY": "saved-key"},
    )
    # KEEP on an already-enabled provider must not flip it to disabled.
    assert source_args.get("fal_source") != "disabled"


def test_none_selection_leaves_source_untouched():
    # No picker + no fal selection → the fal apply is a no-op (off-track/never
    # visited leaves .env as-is; the generic source loop skips it too).
    source_args, _ = _selections_to_args(
        {}, [_fal_svc()], current_base_port=63000, env_vars={},
    )
    assert "fal_source" not in source_args


def test_no_enabled_with_blank_key_invariant():
    """The footgun this ticket fixes: never FAL_SOURCE=enabled with a blank key."""
    for blank in ("", SECRET_CLEAR):
        source_args, opts = _selections_to_args(
            {fal_secret_title(): blank},
            [_fal_svc()], current_base_port=63000, env_vars={},
        )
        assert source_args["fal_source"] == "disabled"
        assert opts["cloud_api_keys"].get("FAL_API_KEY", "") == ""


def test_off_track_fal_force_disabled_without_secret():
    # gen-ai-rag excludes fal → force-disabled via the track pass, even though
    # its secret step was skipped (no selection).
    source_args, _ = _selections_to_args(
        {PICKER_STEP_TITLE: "gen-ai-rag"},
        [_fal_svc()], current_base_port=63000, env_vars={},
    )
    assert source_args.get("fal_source") == "disabled"


# ─────────────────────── #1255: one verdict per intent ───────────────────────
_SAVED = {"FAL_SOURCE": "enabled", "FAL_API_KEY": "saved-key"}


def _fal_args(secret, env=None):
    """(fal_source written or None, FAL_API_KEY written or "<unwritten>")."""
    source_args, opts = _selections_to_args(
        {fal_secret_title(): secret},
        [_fal_svc()], current_base_port=63000, env_vars=dict(env or _SAVED),
    )
    return (
        source_args.get("fal_source"),
        opts["cloud_api_keys"].get("FAL_API_KEY", "<unwritten>"),
    )


def test_disable_turns_fal_off_and_keeps_the_key():
    """#1255 AC2: turning fal off is not a request to delete its key."""
    source, key = _fal_args(SECRET_DISABLE)
    assert source == "disabled"
    assert key == "<unwritten>"


def test_typed_disable_is_never_saved_as_the_key():
    """Before #1255 fal's block had no arm for the #1183 sentinels, so the
    word "disable" (which the prompt panel encodes as SECRET_DISABLE) fell
    through to the "real key" branch: fal_source=enabled and
    FAL_API_KEY="<DISABLE>". Same for "enable"."""
    for sentinel in (SECRET_DISABLE, SECRET_ENABLE):
        _, key = _fal_args(sentinel)
        assert key == "<unwritten>", sentinel
    assert _fal_args(SECRET_DISABLE)[0] == "disabled"


def test_enable_turns_fal_on_with_the_saved_key_without_rewriting_it():
    source, key = _fal_args(
        SECRET_ENABLE, {"FAL_SOURCE": "disabled", "FAL_API_KEY": "saved-key"},
    )
    assert source == "enabled"
    assert key == "<unwritten>"


def test_enable_without_a_saved_key_does_not_claim_fal_is_on():
    """FAL_SOURCE=enabled with no key is rejected by source_validator."""
    source, key = _fal_args(SECRET_ENABLE, {"FAL_SOURCE": "disabled"})
    assert source == "disabled"
    assert key == "<unwritten>"


def test_remove_is_the_only_verdict_that_blanks_the_key():
    """#1255 AC3: enumerate every secret verdict; exactly one erases the
    key. An empty entry is the same removal verdict as "remove" (that is
    how resolve_secret_verdict defines it), covered by
    test_blank_disables_and_wipes above."""
    verdicts = {
        "keep": SECRET_KEEP,
        "enable": SECRET_ENABLE,
        "disable": SECRET_DISABLE,
        "remove": SECRET_CLEAR,
        "new key": "fal-new-key",
    }
    blanked = [name for name, v in verdicts.items() if _fal_args(v)[1] == ""]
    assert blanked == ["remove"]


def test_fal_resolves_every_intent_exactly_like_a_cloud_provider():
    """One policy, not two copies (#1255 scope question): fal and the cloud
    resolver agree on every verdict once the cloud-only models override is
    out of the picture."""
    for existing_key_set in (True, False):
        for secret in (None, SECRET_KEEP, SECRET_ENABLE, SECRET_DISABLE,
                       SECRET_CLEAR, "", "some-key"):
            cloud = resolve_cloud_provider(
                provider_key="openai", secret_value=secret,
                selected_models=None, existing_key_set=existing_key_set,
                existing_source="disabled",
            )
            assert resolve_secret_verdict(
                secret, existing_key_set=existing_key_set,
            ) == cloud, (secret, existing_key_set)


@pytest.mark.parametrize(
    ("env", "words"),
    [
        (
            {"FAL_SOURCE": "enabled", "FAL_API_KEY": "saved-key"},
            ("Enter keeps it on", '"disable"', "new key", '"remove"'),
        ),
        (
            {"FAL_SOURCE": "disabled", "FAL_API_KEY": "saved-key"},
            ("Enter leaves it off", '"enable"', "new key", '"remove"'),
        ),
    ],
)
def test_the_key_hint_names_every_available_action(env, words):
    """#1255 AC4: the hint must say what each action does, and must never
    promise the old "Enter enables" promotion."""
    step = build_fal_secret_step(env, _noop)[0]
    for word in words:
        assert word in step.secret_keep_hint, word
    # The subtitle names the same typed words, quoted the way it prints them.
    for word in (w for w in words if w.startswith('"')):
        assert f"'{word.strip(chr(34))}'" in step.subtitle, word
    assert "Enter enables" not in step.secret_keep_hint
    assert "saved-key" not in step.secret_keep_hint + step.subtitle


def test_fal_display_name_matches_the_manifest():
    """The overview finds fal's row by this name; drift would silently stop
    the row from reflecting the verdict."""
    import yaml

    from wizard.llm_steps import FAL_DISPLAY_NAME as name
    manifest = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "services/fal/service.yml")
        .read_text(encoding="utf-8")
    )
    names = [r.get("display_name") for r in manifest.get("rows", [])]
    assert name in names


def _fal_row_after(secret, *, source, saved_key="saved-key"):
    """Apply one verdict to fal's service-table row; return (source, pending)."""
    from ui.textual.screens.wizard_screen import WizardScreen
    from ui.textual.widgets.service_table import ServiceRow

    row = ServiceRow(name=FAL_DISPLAY_NAME, source=source, pending=True)

    class _Stub:
        _services = [row]

        class _Table:
            @staticmethod
            def set_rows(_rows):
                pass

        _service_table = _Table()

        def _refresh_info_panel(self):
            pass

    step = build_fal_secret_step(
        {"FAL_SOURCE": source, "FAL_API_KEY": saved_key}, _noop,
    )[0]
    WizardScreen._apply_secret_step_to_fal_row(_Stub(), step, secret)
    return row.source, row.pending


@pytest.mark.parametrize(
    ("secret", "source", "expected"),
    [
        (SECRET_KEEP, "disabled", "disabled"),
        (SECRET_KEEP, "enabled", "enabled"),
        (SECRET_ENABLE, "disabled", "enabled"),
        (SECRET_DISABLE, "enabled", "disabled"),
        (SECRET_CLEAR, "enabled", "disabled"),
        ("fal-new-key", "disabled", "enabled"),
    ],
)
def test_the_overview_row_shows_the_verdict_the_launch_will_write(
    secret, source, expected,
):
    """fal's step has no service_name, so its row used to stay pending with
    the .env source whatever the user answered."""
    assert _fal_row_after(secret, source=source) == (expected, False)


def test_enable_without_a_saved_key_leaves_the_row_off():
    assert _fal_row_after(SECRET_ENABLE, source="disabled", saved_key="") == (
        "disabled", False,
    )


def test_a_non_fal_secret_step_leaves_the_fal_row_alone():
    from ui.textual.screens.wizard_screen import WizardScreen
    from ui.textual.widgets.service_table import ServiceRow
    from ui.textual.widgets.prompt_panel import PromptStep
    from wizard.llm_steps import cloud_secret_title

    row = ServiceRow(name=FAL_DISPLAY_NAME, source="disabled", pending=True)
    stub = SimpleNamespace(_services=[row])
    step = PromptStep(
        title=cloud_secret_title("OpenAI"), step_index=1, step_total=1,
        heading="h", kind="secret",
    )
    WizardScreen._apply_secret_step_to_fal_row(stub, step, SECRET_ENABLE)
    assert (row.source, row.pending) == ("disabled", True)


def _fal_summary_flags(secret):
    """The copy-pasteable flag list the wizard previews for one fal intent."""
    import asyncio

    from textual.app import App
    from ui.textual.screens.wizard_screen import WizardScreen

    step = build_fal_secret_step(_SAVED, _noop)[0]
    screen = WizardScreen(steps=[step], services=[], no_splash=True)

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(screen)

    async def scenario():
        async with _App().run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            screen._selections[step.title] = secret
            screen._refresh_command_summary()
            await pilot.pause()
            return list(screen._command_summary.flags)

    try:
        return asyncio.run(scenario())
    finally:
        screen._close_launch_log_tee()
        path = screen._launch_log_path
        if path is not None:
            path.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("secret", "expected"),
    [
        (SECRET_ENABLE, [("--fal-source", "enabled")]),
        (SECRET_DISABLE, [("--fal-source", "disabled")]),
        (SECRET_CLEAR, [("--fal-source", "disabled")]),
        ("fal-new-key", [("--fal-source", "enabled"), ("--fal-api-key", "<set>")]),
        (SECRET_KEEP, []),
    ],
)
def test_the_command_preview_replays_each_fal_intent(secret, expected):
    """The preview claims to be a copy-pasteable command, but it used to
    carry no fal flag at all, so replaying it could not reproduce a fal
    decision. It now projects fal's verdict exactly as it does a cloud
    provider's — and never the key itself."""
    flags = _fal_summary_flags(secret)
    assert [f for f in flags if "fal" in f[0]] == expected
    rendered = " ".join(f"{flag} {value}" for flag, value in flags)
    assert "fal-new-key" not in rendered
    assert "saved-key" not in rendered


@pytest.mark.parametrize(
    ("restored", "expected"),
    [
        ("disable", "pending: turned off, key kept"),
        ("enable", "pending: enabled with the saved key"),
        ("clear", "pending removal"),
        ("fal-new-key", "pending replacement loaded"),
    ],
)
def test_back_onto_an_answered_key_step_names_the_pending_action(restored, expected):
    """Going Back restores the typed word (#1183). A restored "disable" or
    "enable" used to be announced as a "pending replacement", as if the word
    were a new key."""
    from dataclasses import replace

    from ui.textual.widgets.prompt_panel import _secret_input_hint

    step = replace(build_fal_secret_step(_SAVED, _noop)[0], restored_input_value=restored)
    assert _secret_input_hint(step).startswith(expected)
