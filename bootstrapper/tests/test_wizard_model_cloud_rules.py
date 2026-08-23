"""Cloud-provider enable/disable promotion rules (#535 Pass 1).

Two signals are reconciled here:
  * the secret step   -> a key, SECRET_KEEP, or SECRET_CLEAR
  * the models step   -> zero selected models is an explicit disable

This is the subtlest rule in the wizard; these tests are the contract.
"""

from __future__ import annotations

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
    )
    assert r.source == "enabled"
    assert r.api_key == "sk-test"
    assert r.models == ["gpt-4o"]


def test_zero_models_disables_even_with_a_valid_key():
    """Selecting no models is an explicit override, not an omission."""
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value="sk-test",
        selected_models=[],
        existing_key_set=False,
    )
    assert r.source == "disabled"


def test_secret_keep_with_existing_key_promotes_to_enabled():
    """User pressed Enter past an existing key: keep it, and enable."""
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value=SECRET_KEEP,
        selected_models=["gpt-4o"],
        existing_key_set=True,
    )
    assert r.source == "enabled"
    assert r.api_key is None, "KEEP must not rewrite the stored key"


def test_secret_keep_without_existing_key_does_not_enable():
    """Nothing to keep means nothing to enable."""
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value=SECRET_KEEP,
        selected_models=["gpt-4o"],
        existing_key_set=False,
    )
    assert r.source == "disabled"


def test_secret_clear_disables_and_blanks_the_key():
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value=SECRET_CLEAR,
        selected_models=["gpt-4o"],
        existing_key_set=True,
    )
    assert r.source == "disabled"
    assert r.api_key == ""


def test_empty_secret_with_no_existing_key_disables():
    r = resolve_cloud_provider(
        provider_key="anthropic",
        secret_value="",
        selected_models=[],
        existing_key_set=False,
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
        )
        assert r.source in {"enabled", "disabled"}
