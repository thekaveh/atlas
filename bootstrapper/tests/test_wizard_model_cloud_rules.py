"""Cloud-provider enable/disable promotion rules (#535 Pass 1).

Two signals are reconciled here:
  * the secret step   -> a key, SECRET_KEEP, or SECRET_CLEAR
  * the models step   -> zero selected models is an explicit disable

This is the subtlest rule in the wizard; these tests are the contract.
"""

from __future__ import annotations

import pytest

from wizard.model.cloud_rules import (
    SECRET_CLEAR,
    SECRET_KEEP,
    resolve_cloud_provider,
)


def test_new_key_with_models_enables():
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value="sk-test",
        selected_models=["gpt-4o"],
        existing_key_set=False,
        existing_source="disabled",  # not-KEEP branch: existing_source is unread; models a
                                      # provider that had never been enabled before this key
    )
    assert r.source == "enabled"
    assert r.api_key == "sk-test"
    assert r.models == ["gpt-4o"]


def test_zero_models_disables_even_with_a_valid_key():
    """Selecting no models is an explicit override, not an omission.

    It also wipes the just-entered key -- "for symmetry with
    SECRET_CLEAR (otherwise .env would keep a stale key for a
    disabled provider, which is misleading)", per the original.
    """
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value="sk-test",
        selected_models=[],
        existing_key_set=False,
        existing_source="disabled",  # not-KEEP branch: existing_source is unread
    )
    assert r.source == "disabled"
    assert r.api_key == "", "zero models must wipe the key, not keep it"


def test_secret_keep_with_existing_key_promotes_to_enabled():
    """User pressed Enter past an existing key: keep it, and enable.

    Models a provider that is NOT currently enabled -- that's what
    lets the original's auto-promote guard
    (``existing_source != 'enabled' and existing_key``) fire.
    """
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value=SECRET_KEEP,
        selected_models=["gpt-4o"],
        existing_key_set=True,
        existing_source="disabled",
    )
    assert r.source == "enabled"
    assert r.api_key is None, "KEEP must not rewrite the stored key"


def test_secret_keep_without_existing_key_does_not_enable():
    """Nothing to keep means nothing to enable.

    ``existing_source="disabled"`` matches the original's own default
    fill (``env_vars.get(source_var, 'disabled')``) for a provider
    that was never enabled and never had a key -- the guard's
    ``existing_key`` half is False, so it stays at the "disabled" it
    already was.
    """
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value=SECRET_KEEP,
        selected_models=["gpt-4o"],
        existing_key_set=False,
        existing_source="disabled",
    )
    assert r.source == "disabled"


def test_secret_clear_disables_and_blanks_the_key():
    """Not-KEEP branch: existing_source is unread. Models a provider
    that was previously enabled with a key, now being cleared."""
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value=SECRET_CLEAR,
        selected_models=["gpt-4o"],
        existing_key_set=True,
        existing_source="enabled",
    )
    assert r.source == "disabled"
    assert r.api_key == ""


def test_empty_secret_with_no_existing_key_disables():
    """Not-KEEP branch: existing_source is unread. Models a provider
    that never had a key and was never enabled."""
    r = resolve_cloud_provider(
        provider_key="anthropic",
        secret_value="",
        selected_models=[],
        existing_key_set=False,
        existing_source="disabled",
    )
    assert r.source == "disabled"


def test_resolution_is_frozen():
    """Callers merge these into env; accidental mutation would be a
    cross-provider leak."""
    import dataclasses

    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value="sk-test",
        selected_models=["gpt-4o"],
        existing_key_set=False,
        existing_source="disabled",  # not-KEEP branch: existing_source is unread
    )
    assert dataclasses.is_dataclass(r)
    try:
        r.source = "hacked"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("CloudResolution must be frozen")


def test_every_declared_provider_resolves():
    """Adding a 4th provider must not silently miss this rule."""
    from utils.cloud_providers import CLOUD_PROVIDERS

    for provider in CLOUD_PROVIDERS:
        r = resolve_cloud_provider(
            provider_key=provider.key,
            secret_value="sk-test",
            selected_models=["m"],
            existing_key_set=False,
            existing_source="disabled",  # not-KEEP branch: existing_source is unread
        )
        assert r.source in {"enabled", "disabled"}


# ── Fix round 1: regression tests for the Critical + Minor findings ──
#
# The original ``_selections_to_args`` has TWO "leave .env alone"
# paths that never write ``source_args[cli_arg]`` at all: a bare
# ``secret_v is None`` (step never visited), and a SECRET_KEEP whose
# auto-promote guard doesn't fire because the provider is already
# enabled. Both must resolve to ``source is None`` ("no verdict"), not
# a coerced "disabled" -- coercing either one would silently
# force-disable an already-enabled, already-keyed provider whenever
# its secret step isn't visited this run (narrower track, CLI-flag
# mode, non-interactive run).


def test_secret_step_never_visited_leaves_no_verdict():
    """Critical regression: ``secret_value=None`` must not be coerced
    to "disabled" -- that would force-disable an already-enabled
    provider whenever its secret step isn't visited this run.

    ``existing_source="enabled"`` here is deliberate, not incidental:
    the whole point of this regression is an ALREADY-ENABLED provider
    whose step just wasn't visited this run -- coercion would be
    destructive precisely because it silently overwrites a working
    "enabled" state. (The ``secret_value is None`` branch doesn't
    actually read ``existing_source``; it's set to make the scenario
    concrete for the reader.)
    """
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value=None,
        selected_models=["gpt-4o"],
        existing_key_set=True,
        existing_source="enabled",
    )
    assert r.source is None, "no verdict -- caller must leave .env alone"
    assert r.api_key is None, "no verdict -- must not rewrite the key either"


def test_secret_step_never_visited_with_zero_models_still_disables():
    """The models-step override is a separate, unconditional block in
    the original -- it fires even when the secret step (a different
    selections-dict entry) was never visited."""
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value=None,
        selected_models=[],
        existing_key_set=False,
        existing_source="disabled",  # None branch: existing_source is unread
    )
    assert r.source == "disabled"
    assert r.api_key == ""


def test_explicit_empty_secret_disables_and_blanks_even_with_models_selected():
    """An explicit "" secret takes the SECRET_CLEAR branch (blanks the
    key) -- unlike ``None`` (never visited, which leaves the key
    untouched). Zero-model tests alone can't distinguish these two,
    since the models override would blank the key either way."""
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value="",
        selected_models=["gpt-4o"],
        existing_key_set=True,
        existing_source="enabled",  # not-KEEP branch: existing_source is unread
    )
    assert r.source == "disabled"
    assert r.api_key == "", '"" must blank the key like SECRET_CLEAR, not leave it like None'


def test_secret_keep_is_a_noop_when_already_enabled_without_a_key():
    """Minor regression: the original's auto-promote guard is
    ``existing_source != 'enabled' and existing_key``. When the
    provider is already enabled, SECRET_KEEP is a no-op regardless of
    whether a key is on file -- not a computed "disabled"."""
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value=SECRET_KEEP,
        selected_models=["gpt-4o"],
        existing_key_set=False,
        existing_source="enabled",
    )
    assert r.source is None, "already enabled -- forcing a verdict would be destructive"
    assert r.api_key is None


# ── Fix round 2: existing_source is required, not defaulted ─────────
#
# A default of "" (normalizing to "disabled") made it easy to
# *silently* reproduce the exact class of bug fix round 1 eliminated:
# a caller that forgets the parameter gets "not enabled" for free, so
# a SECRET_KEEP against an already-enabled, keyless provider would
# read as "disabled" and force-disable a working configuration.
# Removing the default converts that into an immediate TypeError.


def test_existing_source_is_required():
    """Regression guard for the footgun itself: omitting
    ``existing_source`` must fail loudly (TypeError) rather than
    silently defaulting to "not enabled"."""
    with pytest.raises(TypeError):
        resolve_cloud_provider(
            provider_key="openai",
            secret_value=SECRET_KEEP,
            selected_models=["gpt-4o"],
            existing_key_set=True,
        )
