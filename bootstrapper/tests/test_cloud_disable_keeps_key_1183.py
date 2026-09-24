"""Disabling a cloud provider is separate from deleting its key (#1183).

Three behaviours were welded together before this change:

* `SECRET_KEEP` on a provider whose `.env` source was not `enabled` but
  which had a saved key resolved to `source = "enabled"` — a bare Enter
  changed the provider's state.
* `SECRET_CLEAR` and an empty secret set `source = "disabled"` **and**
  `api_key = ""` — the only way to turn a provider off also erased its
  credential.
* The zero-models override did the same, so unchecking every model
  silently deleted a working key.

`CLOUD_*_SOURCE` and `*_API_KEY` are separate facts, so the protocol now
carries one verdict per intent: keep both, enable with the stored key,
disable and keep the key, replace the key, remove the key.
"""

from __future__ import annotations

import pytest

from utils.cloud_providers import CLOUD_PROVIDERS
from ui.textual.widgets.prompt_panel import (
    SECRET_CONTROL_WORDS,
    PromptStep,
)
from wizard.llm_steps import (
    build_cloud_steps,
    cloud_models_title,
    cloud_secret_title,
)
from wizard.model.cloud_rules import (
    SECRET_CLEAR,
    SECRET_DISABLE,
    SECRET_ENABLE,
    SECRET_KEEP,
    resolve_cloud_provider,
)

_P = CLOUD_PROVIDERS[0]          # OpenAI
_KEY = "sk-stored-key-DO-NOT-LEAK"


def _resolve(secret, **kw):
    defaults = dict(
        provider_key=_P.key,
        secret_value=secret,
        selected_models=["gpt-5"],
        existing_key_set=True,
        existing_source="disabled",
    )
    defaults.update(kw)
    return resolve_cloud_provider(**defaults)


# ─── AC1: a disabled provider with a key stays disabled on Enter ──────


def test_a_disabled_provider_with_a_saved_key_stays_disabled_on_enter():
    """AC1. This used to resolve to source="enabled"."""
    r = _resolve(SECRET_KEEP)
    assert r.source is None, "no verdict — .env keeps saying disabled"
    assert r.api_key is None, "no verdict — the stored key is untouched"


def test_an_enabled_provider_with_a_saved_key_stays_enabled_on_enter():
    r = _resolve(SECRET_KEEP, existing_source="enabled")
    assert r.source is None
    assert r.api_key is None


@pytest.mark.parametrize("existing_source", ["disabled", "enabled", "", "DISABLED"])
def test_enter_never_writes_either_field_whatever_env_says(existing_source):
    """The migration obligation: an upgrade must neither enable a disabled
    provider nor erase a stored key when the user just presses Enter."""
    r = _resolve(SECRET_KEEP, existing_source=existing_source)
    assert r.source is None
    assert r.api_key is None


def test_enter_on_a_provider_with_no_key_still_writes_nothing():
    r = _resolve(SECRET_KEEP, existing_key_set=False)
    assert r.source is None
    assert r.api_key is None


# ─── AC2: disable preserves the key unless Remove is explicit ─────────


def test_disable_turns_the_provider_off_and_keeps_the_key():
    """AC2."""
    r = _resolve(SECRET_DISABLE)
    assert r.source == "disabled"
    assert r.api_key is None, "disable must not erase the credential"


def test_remove_is_the_only_verdict_that_erases_a_key():
    erasing = [
        secret for secret in
        (SECRET_KEEP, SECRET_ENABLE, SECRET_DISABLE, SECRET_CLEAR, "", None,
         "sk-a-new-key")
        if _resolve(secret).api_key == ""
    ]
    assert erasing == [SECRET_CLEAR, ""], erasing


def test_remove_turns_the_provider_off_and_erases_the_key():
    r = _resolve(SECRET_CLEAR)
    assert r.source == "disabled"
    assert r.api_key == ""


def test_an_empty_secret_on_a_keyless_provider_still_disables_and_blanks():
    """Unchanged: typing nothing where no key exists means "stay off"."""
    r = _resolve("", existing_key_set=False)
    assert r.source == "disabled"
    assert r.api_key == ""


def test_unchecking_every_model_disables_but_keeps_the_key():
    """AC2 through the models step. This used to erase the key."""
    r = _resolve(SECRET_KEEP, selected_models=[])
    assert r.source == "disabled"
    assert r.api_key is None


def test_unchecking_every_model_still_overrides_a_freshly_typed_key():
    """The override still wins over the secret step — only its treatment
    of the credential changed."""
    r = _resolve("sk-a-new-key", selected_models=[])
    assert r.source == "disabled"
    assert r.api_key is None


def test_an_unvisited_models_step_never_overrides_anything():
    r = _resolve(SECRET_ENABLE, selected_models=None)
    assert r.source == "enabled"


# ─── Enable ───────────────────────────────────────────────────────────


def test_enable_turns_the_provider_on_without_rewriting_the_key():
    r = _resolve(SECRET_ENABLE)
    assert r.source == "enabled"
    assert r.api_key is None


def test_enable_without_a_stored_key_does_not_claim_to_be_enabled():
    """There is nothing to enable with, and source_validator would
    auto-disable it moments later — so say so up front."""
    r = _resolve(SECRET_ENABLE, existing_key_set=False)
    assert r.source == "disabled"
    assert r.api_key is None


def test_a_typed_key_still_enables_and_persists():
    r = _resolve("sk-a-new-key")
    assert r.source == "enabled"
    assert r.api_key == "sk-a-new-key"


def test_an_unvisited_secret_step_is_still_a_no_verdict():
    r = _resolve(None)
    assert r.source is None
    assert r.api_key is None


def test_every_provider_resolves_the_same_way():
    for provider in CLOUD_PROVIDERS:
        keep = resolve_cloud_provider(
            provider_key=provider.key, secret_value=SECRET_KEEP,
            selected_models=["m"], existing_key_set=True,
            existing_source="disabled",
        )
        off = resolve_cloud_provider(
            provider_key=provider.key, secret_value=SECRET_DISABLE,
            selected_models=["m"], existing_key_set=True,
            existing_source="enabled",
        )
        assert (keep.source, keep.api_key) == (None, None), provider.key
        assert (off.source, off.api_key) == ("disabled", None), provider.key


# ─── The typed words the wizard accepts ───────────────────────────────


def test_each_control_word_maps_to_exactly_one_intent():
    assert SECRET_CONTROL_WORDS["enable"][0] == SECRET_ENABLE
    assert SECRET_CONTROL_WORDS["disable"][0] == SECRET_DISABLE
    assert SECRET_CONTROL_WORDS["remove"][0] == SECRET_CLEAR


def test_clear_remains_an_alias_for_remove():
    """The word documented before this change keeps working."""
    assert SECRET_CONTROL_WORDS["clear"][0] == SECRET_CLEAR
    assert SECRET_CONTROL_WORDS["clear"] == SECRET_CONTROL_WORDS["remove"]


def test_no_control_word_could_be_mistaken_for_an_api_key():
    for word in SECRET_CONTROL_WORDS:
        assert word.isalpha() and len(word) < 10, word


class _Panel:
    """The slice of PromptPanel that encodes a secret entry."""

    def __init__(self, raw: str, existing: str = _KEY):
        from ui.textual.widgets.prompt_panel import PromptPanel

        class _In:
            value = raw

        self._panel_cls = PromptPanel
        self._step = PromptStep(
            title=cloud_secret_title(_P.name), step_index=1, step_total=1,
            heading="h", kind="secret", default_value=existing,
        )
        self._secret_input = _In()
        self._selected_index = 0

    @property
    def selected_option(self):
        return self._panel_cls.selected_option.fget(self)


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("", SECRET_KEEP),
        ("enable", SECRET_ENABLE),
        ("ENABLE", SECRET_ENABLE),
        ("  disable  ", SECRET_DISABLE),
        ("remove", SECRET_CLEAR),
        ("clear", SECRET_CLEAR),
    ],
)
def test_the_secret_step_encodes_each_typed_word(typed, expected):
    assert _Panel(typed).selected_option.value == expected


def test_a_typed_key_is_returned_as_itself():
    assert _Panel("sk-brand-new").selected_option.value == "sk-brand-new"


def test_an_empty_entry_with_no_saved_key_means_stay_off():
    assert _Panel("", existing="").selected_option.value == ""


def test_no_committed_label_contains_the_key():
    for typed in ("", "enable", "disable", "remove", "clear"):
        assert _KEY not in _Panel(typed).selected_option.label


# ─── The model picker is skipped for a provider that stays off ────────


def _secret_step_and_picker(env):
    steps = build_cloud_steps(env, lambda _m: None)
    secret = next(s for s in steps if s.title == cloud_secret_title(_P.name))
    picker = next(s for s in steps if s.title == cloud_models_title(_P.name))
    return secret, picker


def _env(source="disabled", key=_KEY):
    return {_P.source_var: source, _P.api_key_var: key}


@pytest.mark.parametrize(
    ("secret_value", "env_source", "skipped"),
    [
        (SECRET_KEEP, "disabled", True),    # stays off -> nothing to pick
        (SECRET_KEEP, "enabled", False),    # stays on
        (SECRET_DISABLE, "enabled", True),  # turned off
        (SECRET_ENABLE, "disabled", False),  # turned on
        (SECRET_CLEAR, "enabled", True),    # key removed, off
        ("", "enabled", True),
        ("sk-new", "disabled", False),      # new key enables
    ],
)
def test_the_picker_is_shown_exactly_when_the_provider_will_be_on(
    secret_value, env_source, skipped,
):
    _secret, picker = _secret_step_and_picker(_env(source=env_source))
    got = picker.skip_if_prev({cloud_secret_title(_P.name): secret_value})
    assert got is skipped


def test_enable_without_a_saved_key_skips_the_picker():
    _secret, picker = _secret_step_and_picker(_env(key=""))
    assert picker.skip_if_prev({cloud_secret_title(_P.name): SECRET_ENABLE}) is True


def test_an_unanswered_secret_step_leaves_the_picker_visible():
    _secret, picker = _secret_step_and_picker(_env())
    assert picker.skip_if_prev({}) is False


def test_the_picker_heading_says_what_an_empty_selection_does():
    _secret, picker = _secret_step_and_picker(_env())
    assert f"none = {_P.name} off" in picker.heading


# ─── The secret step tells the user which words exist ─────────────────


def test_a_disabled_keyed_provider_offers_enable_and_never_promises_promotion():
    secret, _picker = _secret_step_and_picker(_env(source="disabled"))
    assert "enable" in secret.subtitle
    assert "remove" in secret.subtitle
    assert "Enter to leave it off" in secret.subtitle
    assert "Enter enables" not in secret.subtitle
    assert "Enter leaves it off and keeps the key" in secret.secret_keep_hint


def test_an_enabled_provider_offers_disable_separately_from_remove():
    secret, _picker = _secret_step_and_picker(_env(source="enabled"))
    assert "disable" in secret.subtitle
    assert "remove" in secret.subtitle
    assert "keeps the key" in secret.secret_keep_hint
    assert "deletes the key" in secret.secret_keep_hint


def test_no_secret_step_text_contains_the_saved_key():
    for source in ("disabled", "enabled"):
        secret, _picker = _secret_step_and_picker(_env(source=source))
        assert _KEY not in secret.subtitle
        assert _KEY not in (secret.secret_keep_hint or "")
        assert _KEY not in secret.heading


# ─── AC3: CLI/TUI parity, and no secret on the command line ───────────


def _args_for(secret, models=None, env=None):
    """Run the Textual path's selections->args mapping for one intent."""
    from ui.textual.integration import _selections_to_args

    selections = {cloud_secret_title(_P.name): secret}
    if models is not None:
        selections[cloud_models_title(_P.name)] = models
    source_args, stack_options = _selections_to_args(
        selections, services_info=[], current_base_port=63000,
        env_vars=env if env is not None else _env(),
    )
    cli_key = f"cloud_{_P.key}_source"
    return (
        source_args.get(cli_key),
        stack_options["cloud_api_keys"].get(_P.api_key_var, "<unwritten>"),
    )


def test_enter_past_a_disabled_provider_writes_neither_field():
    """AC1 end to end: no source flag, no key write, so .env is untouched."""
    assert _args_for(SECRET_KEEP) == (None, "<unwritten>")


def test_disable_writes_the_source_and_leaves_the_key_alone():
    assert _args_for(SECRET_DISABLE) == ("disabled", "<unwritten>")


def test_remove_writes_both_the_source_and_an_empty_key():
    assert _args_for(SECRET_CLEAR) == ("disabled", "")


def test_enable_writes_only_the_source():
    assert _args_for(SECRET_ENABLE) == ("enabled", "<unwritten>")


def test_a_new_key_writes_both():
    assert _args_for("sk-fresh") == ("enabled", "sk-fresh")


def test_unchecking_every_model_writes_the_source_but_not_the_key():
    assert _args_for(SECRET_KEEP, models="") == ("disabled", "<unwritten>")


def test_the_same_intent_resolves_identically_through_both_layers():
    """AC3: the resolver and the Textual mapping must agree, so a replayed
    intent lands in the same place whichever path produced it."""
    for secret in (SECRET_KEEP, SECRET_ENABLE, SECRET_DISABLE, SECRET_CLEAR,
                   "sk-fresh"):
        resolution = _resolve(secret)
        source, key = _args_for(secret)
        assert source == resolution.source, secret
        expected_key = "<unwritten>" if resolution.api_key is None else resolution.api_key
        assert key == expected_key, secret


def _summary_flags(secret):
    """The copy-pasteable flag list the wizard shows for one intent."""
    import asyncio

    from textual.app import App
    from ui.textual.screens.wizard_screen import WizardScreen

    title = cloud_secret_title(_P.name)
    step = PromptStep(
        title=title, step_index=1, step_total=1, heading="H", subtitle="",
        default_value=_KEY, kind="secret",
    )
    screen = WizardScreen(steps=[step], services=[], no_splash=True)

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(screen)

    async def scenario():
        async with _App().run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            screen._selections[title] = secret
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
        (SECRET_ENABLE, ("--cloud-openai-source", "enabled")),
        (SECRET_DISABLE, ("--cloud-openai-source", "disabled")),
        (SECRET_CLEAR, ("--cloud-openai-source", "disabled")),
    ],
)
def test_each_intent_previews_the_flag_that_reproduces_it(secret, expected):
    assert expected in _summary_flags(secret)


def test_enter_previews_no_cloud_flag_at_all():
    """Nothing changed, so a replay must not assert a state."""
    flags = _summary_flags(SECRET_KEEP)
    assert not [f for f in flags if "openai" in f[0]], flags


@pytest.mark.parametrize(
    "secret",
    [SECRET_KEEP, SECRET_ENABLE, SECRET_DISABLE, SECRET_CLEAR, "", _KEY],
)
def test_the_command_preview_never_contains_a_key(secret):
    """AC3: the summary is copy-pasteable, and a key on a command line
    ends up in shell history."""
    rendered = " ".join(f"{flag} {value}" for flag, value in _summary_flags(secret))
    assert _KEY not in rendered
    assert "sk-" not in rendered


def test_setting_a_new_key_previews_a_placeholder_not_the_key():
    flags = _summary_flags(_KEY)
    assert ("--openai-api-key", "<set>") in flags


# ─── Regressions this change could introduce, pinned ──────────────────


def test_going_back_restores_a_word_never_a_raw_sentinel():
    """The fall-through in ``_restored_free_text_input`` returns ``prior``
    verbatim, so a sentinel it does not know is preloaded into the masked
    input — and the next Enter commits ``"<DISABLE>"`` as the API key."""
    from ui.textual.screens.wizard_screen import _restored_free_text_input

    assert _restored_free_text_input(SECRET_KEEP) is None
    # "clear", not "remove": this helper also serves kind="text" steps,
    # whose parser knows only "clear" (see the round-trip test below).
    assert _restored_free_text_input(SECRET_CLEAR) == "clear"
    assert _restored_free_text_input(SECRET_DISABLE) == "disable"
    assert _restored_free_text_input(SECRET_ENABLE) == "enable"


def test_every_restored_word_round_trips_back_to_its_sentinel():
    """The restored text must re-encode to the same intent, or a Back then
    Enter would change what the user chose."""
    from ui.textual.screens.wizard_screen import _restored_free_text_input

    for sentinel in (SECRET_CLEAR, SECRET_DISABLE, SECRET_ENABLE):
        word = _restored_free_text_input(sentinel)
        assert SECRET_CONTROL_WORDS[word][0] == sentinel, sentinel


def test_the_restored_word_round_trips_on_a_text_step_too():
    """``_restored_free_text_input`` is shared with ``kind="text"`` steps
    (OLLAMA_CUSTOM_MODELS), whose parser knows only "clear". Restoring a
    word that step cannot re-encode commits it as literal text."""
    from ui.textual.screens.wizard_screen import _restored_free_text_input

    class _In:
        value = _restored_free_text_input(SECRET_CLEAR)

    class _TextPanel:
        def __init__(self):
            from ui.textual.widgets.prompt_panel import PromptPanel

            self._panel_cls = PromptPanel
            self._step = PromptStep(
                title="Ollama · additional models", step_index=1, step_total=1,
                heading="h", kind="text", default_value="llama3.3",
            )
            self._number_input = _In()
            self._selected_index = 0

        @property
        def selected_option(self):
            return self._panel_cls.selected_option.fget(self)

    assert _TextPanel().selected_option.value == SECRET_CLEAR


def test_no_sentinel_is_left_out_of_the_restore_map():
    from ui.textual.screens.wizard_screen import _restored_free_text_input

    for sentinel in (SECRET_KEEP, SECRET_CLEAR, SECRET_DISABLE, SECRET_ENABLE):
        restored = _restored_free_text_input(sentinel)
        assert restored != sentinel, f"{sentinel} falls through as literal text"


class _Summary:
    """Stands in for the Cloud APIs overview row."""

    def __init__(self, enabled: bool, key_set: bool):
        from ui.textual.widgets.info_box import CloudApiSummary

        self.entry = CloudApiSummary(name=_P.name, enabled=enabled, key_set=key_set)


def _overview_after(secret, *, enabled, key_set):
    """Apply one secret verdict to the overview row and report the result."""
    from ui.textual.screens.wizard_screen import WizardScreen

    row = _Summary(enabled, key_set).entry

    class _Stub:
        _cloud_apis = [row]

        def __init__(self):
            self.refreshed = False

        class _Row:
            @staticmethod
            def set_cloud_apis(_entries):
                pass

        _cloud_apis_row = _Row()

        def _refresh_info_panel(self):
            self.refreshed = True

    stub = _Stub()
    step = PromptStep(
        title=cloud_secret_title(_P.name), step_index=1, step_total=1,
        heading="h", kind="secret",
    )
    WizardScreen._apply_secret_step_to_cloud_apis(stub, step, secret)
    return row.enabled, row.key_set


def test_enter_does_not_flip_the_overview_to_enabled():
    """The overview used to mirror the auto-promotion. With no promotion
    left to mirror, claiming "on" would make the overview lie."""
    assert _overview_after(SECRET_KEEP, enabled=False, key_set=True) == (False, True)


def test_disable_shows_the_provider_off_with_its_key_still_stored():
    assert _overview_after(SECRET_DISABLE, enabled=True, key_set=True) == (False, True)


def test_enable_shows_the_provider_on():
    assert _overview_after(SECRET_ENABLE, enabled=False, key_set=True) == (True, True)


def test_enable_without_a_key_does_not_show_the_provider_on():
    assert _overview_after(SECRET_ENABLE, enabled=False, key_set=False) == (False, False)


def test_remove_shows_the_provider_off_with_no_key():
    assert _overview_after(SECRET_CLEAR, enabled=True, key_set=True) == (False, False)


def test_a_new_key_shows_the_provider_on_with_a_key():
    assert _overview_after("sk-fresh", enabled=False, key_set=False) == (True, True)


def _overview_after_models(csv, *, enabled, key_set):
    from ui.textual.screens.wizard_screen import WizardScreen

    row = _Summary(enabled, key_set).entry

    class _Stub:
        _cloud_apis = [row]

        class _Row:
            @staticmethod
            def set_cloud_apis(_entries):
                pass

        _cloud_apis_row = _Row()

        def _refresh_info_panel(self):
            pass

    step = PromptStep(
        title=cloud_models_title(_P.name), step_index=1, step_total=1,
        heading="h", kind="multiselect",
    )
    WizardScreen._apply_models_step_to_cloud_apis(_Stub(), step, csv)
    return row.enabled, row.key_set


def test_unchecking_every_model_shows_off_but_still_keyed():
    """The overview used to clear the key indicator here, telling the user
    their credential had been deleted when it had not."""
    assert _overview_after_models("", enabled=True, key_set=True) == (False, True)


def test_a_real_selection_leaves_the_overview_alone():
    assert _overview_after_models("gpt-5", enabled=True, key_set=True) == (True, True)


def test_a_degraded_models_commit_leaves_the_overview_alone():
    assert _overview_after_models(SECRET_KEEP, enabled=False, key_set=True) == (False, True)
