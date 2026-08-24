"""A degraded model-picker commit must not wipe saved model CSVs.

Regression guards for the network-blip data-loss path:
- PromptPanel.selected_option (multiselect) returns SECRET_KEEP when the
  step has no real options (provider crash → [] / placeholder-only row),
  instead of an empty CSV.
- _selections_to_args treats SECRET_KEEP as "no change" for the Ollama,
  ComfyUI, and cloud model buckets, so OLLAMA_USER_MODELS et al. survive.
"""
from __future__ import annotations

from ui.textual.integration import _selections_to_args
from ui.textual.widgets.prompt_panel import (
    PromptOption,
    PromptPanel,
    PromptStep,
    SECRET_KEEP,
)
from wizard.comfyui_steps import COMFYUI_MODELS_TITLE
from wizard.llm_steps import OLLAMA_MODELS_TITLE


def _multiselect_panel(options):
    panel = PromptPanel()
    panel._step = PromptStep(
        title=OLLAMA_MODELS_TITLE, step_index=1, step_total=1,
        heading="x", subtitle="", options=options, kind="multiselect",
    )
    return panel


def test_placeholder_only_step_commits_keep_sentinel():
    panel = _multiselect_panel([
        PromptOption(value="", label="(catalog unreachable)", hint="", badges=[]),
    ])
    opt = panel.selected_option
    assert opt is not None and opt.value == SECRET_KEEP


def test_empty_options_step_commits_keep_sentinel():
    panel = _multiselect_panel([])
    opt = panel.selected_option
    assert opt is not None and opt.value == SECRET_KEEP


def test_healthy_step_with_nothing_checked_still_commits_empty_csv():
    """Explicit deselect-all on a healthy list is a real user intent."""
    panel = _multiselect_panel([
        PromptOption(value="qwen3.8:latest", label="qwen", hint="", badges=[]),
    ])
    opt = panel.selected_option
    assert opt is not None and opt.value == ""


def test_selections_to_args_skips_keep_sentinel_for_model_buckets():
    result = _selections_to_args(
        selections={
            OLLAMA_MODELS_TITLE: SECRET_KEEP,
            COMFYUI_MODELS_TITLE: SECRET_KEEP,
        },
        services_info=[],
        current_base_port=63000,
        env_vars={},
    )
    # Outer dict shape: source_args + stack_options buckets.
    blob = repr(result)
    assert "OLLAMA_USER_MODELS" not in blob
    assert "COMFYUI_USER_MODELS" not in blob
    assert SECRET_KEEP not in blob


def test_selections_to_args_still_persists_real_csv():
    result = _selections_to_args(
        selections={OLLAMA_MODELS_TITLE: "b-model,a-model"},
        services_info=[],
        current_base_port=63000,
        env_vars={},
    )
    blob = repr(result)
    assert "'OLLAMA_USER_MODELS': 'a-model,b-model'" in blob


def test_launch_prune_drops_skip_hidden_step_commits():
    """A commit from a step whose skip-predicate is true at launch time
    must not reach _selections_to_args — e.g. the user visits the
    ComfyUI picker, commits '0 selected', Backs out and disables
    ComfyUI; the stale empty CSV used to wipe COMFYUI_USER_MODELS for a
    now-disabled service. Mirrors WizardScreen._transition_to_launch's
    prune loop."""
    from ui.textual.widgets.prompt_panel import PromptStep

    picker = PromptStep(
        title=COMFYUI_MODELS_TITLE, step_index=2, step_total=2,
        heading="x", subtitle="", options=[], kind="multiselect",
        skip_if_prev=lambda sel: sel.get("ComfyUI  ·  source") == "disabled",
    )
    selections = {
        "ComfyUI  ·  source": "disabled",
        COMFYUI_MODELS_TITLE: "",          # stale empty commit
    }
    from ui.textual.screens.wizard_screen import prune_skip_hidden_selections
    pruned = prune_skip_hidden_selections([picker], selections)
    result = _selections_to_args(pruned, [], 63000, env_vars={})
    assert "COMFYUI_USER_MODELS" not in repr(result)


# ────────────────────────────────────────────────────────────────────────────
# Fallback-catalog notice row (bug: ollama.com/library markup drift made
# list_library_entries() return ~0 entries, and the wizard silently fell
# back to the ~5-model curated catalog with no visible signal). The fix
# prepends an informational, non-selectable notice row (empty value —
# same "not a real option" convention as the placeholder-only rows
# above) ahead of the real fallback options. These tests guard that the
# notice row (a) doesn't get treated as "no real options" (SECRET_KEEP),
# (b) can't be toggled into the committed CSV, and (c) a real selection
# alongside it still commits normally.
# ────────────────────────────────────────────────────────────────────────────


def _notice_plus_real_panel():
    notice = PromptOption(
        value="", label="⚠ showing curated fallback models", hint="", badges=[],
    )
    real = PromptOption(value="model-a", label="model-a", hint="", badges=[])
    return _multiselect_panel([notice, real]), notice, real


def test_notice_row_is_not_toggleable():
    panel, _notice, _real = _notice_plus_real_panel()
    panel._selected_index = 0  # cursor on the notice row
    panel.toggle_focused()
    assert panel._checked_values == set(), (
        "toggling the empty-value notice row must not add anything to "
        "the checked set"
    )


def test_real_option_alongside_notice_row_still_toggles():
    panel, _notice, _real = _notice_plus_real_panel()
    panel._selected_index = 1  # cursor on the real option
    panel.toggle_focused()
    assert panel._checked_values == {"model-a"}


def test_notice_row_does_not_trigger_keep_sentinel():
    """A mixed [notice, real] list has a real (non-empty-value) option,
    so this must NOT take the 'no real options' SECRET_KEEP path — the
    curated fallback (or a suspiciously-small live scrape) is still a
    usable list the user can select from."""
    panel, _notice, _real = _notice_plus_real_panel()
    panel._checked_values = {"model-a"}
    opt = panel.selected_option
    assert opt is not None
    assert opt.value == "model-a"
