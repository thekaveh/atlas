"""Regression guard: a comma-only / whitespace-junk models CSV must not be
misread as an explicit "zero models selected" override (#535 Pass 1,
Task 8 review, fix round 1/5).

The original ``_selections_to_args`` classified "zero selected models" by
testing the WHOLE multiselect commit string (``models_v.strip() == ""``),
not by whether any comma-split segment survives. A comma-only CSV like
``","`` produces a non-empty split LIST (``["", ""]``) -- the raw string
itself is non-empty/non-blank -- even though filtering out the blank
segments collapses that same list down to an EMPTY list of real
selections. The wiring in ``ui/textual/integration.py`` must key its
``selected_models`` classification off the same raw-string check the
original used, not off the parsed (filtered) segment list, or a
comma-only / whitespace-junk commit gets misclassified as "user
unchecked everything" and wrongly disables an otherwise-enabled
provider.

``PromptPanel.selected_option`` never actually emits a bare-comma string
today (it always joins real catalog names), so this is currently
UI-unreachable -- but parity is this plan's stated invariant, matching the
original exactly costs one classification branch, and reachability can
change when the option source changes (see the module this guards).
"""
from __future__ import annotations

from ui.textual.integration import _selections_to_args
from utils.cloud_providers import CLOUD_PROVIDERS
from wizard.llm_steps import cloud_models_title, cloud_secret_title

_PROVIDER = CLOUD_PROVIDERS[0]  # OpenAI
_SOURCE_KEY = _PROVIDER.source_var.lower()  # "cloud_openai_source"


def _selections(models_csv: str) -> dict:
    return {
        cloud_secret_title(_PROVIDER.name): "sk-real-key",
        cloud_models_title(_PROVIDER.name): models_csv,
    }


def test_comma_only_csv_does_not_trigger_zero_models_override():
    """"," has non-empty split segments after naive comma-splitting, but
    represents the SAME "nothing really selected" intent as "" once you
    strip it -- the original's own check. A real secret alongside it must
    still enable the provider, not get force-disabled."""
    source_args, stack_options = _selections_to_args(
        _selections(","), services_info=[], current_base_port=63000, env_vars={},
    )
    assert source_args.get(_SOURCE_KEY) == "enabled", source_args
    assert stack_options["cloud_api_keys"].get(_PROVIDER.api_key_var) == "sk-real-key"


def test_double_comma_csv_does_not_trigger_zero_models_override():
    source_args, _ = _selections_to_args(
        _selections(",,"), services_info=[], current_base_port=63000, env_vars={},
    )
    assert source_args.get(_SOURCE_KEY) == "enabled", source_args


def test_spaced_comma_csv_does_not_trigger_zero_models_override():
    source_args, _ = _selections_to_args(
        _selections(" , "), services_info=[], current_base_port=63000, env_vars={},
    )
    assert source_args.get(_SOURCE_KEY) == "enabled", source_args


def test_spaced_double_comma_csv_does_not_trigger_zero_models_override():
    source_args, _ = _selections_to_args(
        _selections(" , , "), services_info=[], current_base_port=63000, env_vars={},
    )
    assert source_args.get(_SOURCE_KEY) == "enabled", source_args


def test_genuinely_empty_csv_still_triggers_the_override():
    """Contrast case: a real explicit "" commit is still the true
    zero-models override and must still disable + wipe the key."""
    source_args, stack_options = _selections_to_args(
        _selections(""), services_info=[], current_base_port=63000, env_vars={},
    )
    assert source_args.get(_SOURCE_KEY) == "disabled", source_args
    assert stack_options["cloud_api_keys"].get(_PROVIDER.api_key_var) == ""
