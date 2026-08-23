"""
Single source of truth for cloud LLM provider metadata.

Three pieces of state every consumer needs:
  • ``source_var`` — CLOUD_*_SOURCE selector (enabled / disabled).
  • ``api_key_var`` — matching API key env var.
  • ``enabled_flag_var`` — derived LITELLM_*_ENABLED flag emitted by
    ``service_config.py`` for the LiteLLM proxy + model_resolver.

Callers that previously held their own list:
  • ``bootstrapper/wizard/model/state_builder.py`` — overview rendering.
  • ``bootstrapper/services/source_validator.py`` — auto-disable
    on missing key.
  • ``bootstrapper/services/service_config.py`` — emit LITELLM_*_ENABLED.
  • ``bootstrapper/start.py`` — cloud key/source flag plumbing.

All of the above import from here EXCEPT start.py's inert-model-flag
warning loop, which necessarily keeps a local 3-tuple: it pairs each
provider with its Click KWARG locals (openai_models, …), which can't
be looked up from a shared registry without locals() tricks. Adding a
4th provider still means updating that loop by hand — the AST
seam-parity tests flag the miss. Order here is the canonical
user-facing order
(matches the wizard's step splice and the overview's Cloud APIs row).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class CloudProvider:
    name: str               # display label, e.g. "OpenAI"
    # Canonical lowercase identifier — e.g. "openai", "anthropic",
    # "openrouter". Used everywhere a provider key is needed (catalog
    # rows, env-var lookups, wizard step plumbing). Stored explicitly
    # rather than derived from ``name.lower()`` so that a future
    # provider with a multi-word display name ("Open Router",
    # "Together AI") doesn't silently break env-var lookups like
    # ``f"{key.upper()}_USER_MODELS"``.
    key: str
    source_var: str         # e.g. "CLOUD_OPENAI_SOURCE"
    api_key_var: str        # e.g. "OPENAI_API_KEY"
    enabled_flag_var: str   # e.g. "LITELLM_OPENAI_ENABLED"
    user_models_var: str    # e.g. "OPENAI_USER_MODELS"


CLOUD_PROVIDERS: Tuple[CloudProvider, ...] = (
    CloudProvider(
        name="OpenAI",
        key="openai",
        source_var="CLOUD_OPENAI_SOURCE",
        api_key_var="OPENAI_API_KEY",
        enabled_flag_var="LITELLM_OPENAI_ENABLED",
        user_models_var="OPENAI_USER_MODELS",
    ),
    CloudProvider(
        name="Anthropic",
        key="anthropic",
        source_var="CLOUD_ANTHROPIC_SOURCE",
        api_key_var="ANTHROPIC_API_KEY",
        enabled_flag_var="LITELLM_ANTHROPIC_ENABLED",
        user_models_var="ANTHROPIC_USER_MODELS",
    ),
    CloudProvider(
        name="OpenRouter",
        key="openrouter",
        source_var="CLOUD_OPENROUTER_SOURCE",
        api_key_var="OPENROUTER_API_KEY",
        enabled_flag_var="LITELLM_OPENROUTER_ENABLED",
        user_models_var="OPENROUTER_USER_MODELS",
    ),
)


def all_providers() -> Tuple[CloudProvider, ...]:
    """All cloud providers, in canonical user-facing order."""
    return CLOUD_PROVIDERS
