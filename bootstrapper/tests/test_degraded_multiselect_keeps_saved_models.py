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
    result = _selections_to_args(pruned, [], 63000)
    assert "COMFYUI_USER_MODELS" not in repr(result)
