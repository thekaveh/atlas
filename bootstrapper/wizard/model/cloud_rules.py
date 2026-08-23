"""Cloud-provider enable/disable promotion rules (#535 Pass 1).

Extracted from ui/textual/integration.py::_selections_to_args. Two
independent wizard signals are reconciled here for each provider in
``CLOUD_PROVIDERS``:

  * the secret step   -> a real API key, SECRET_KEEP (Enter past an
    existing key), or SECRET_CLEAR (explicit clear)
  * the models step   -> selecting zero models is an explicit
    "disable this provider" override, not an omission, and it wins
    over everything the secret step decided -- including a freshly
    entered key or a KEEP promotion.

This is the subtlest rule in the wizard and the one most likely to
regress silently. The CLI flag path honours the same semantics, so it
must be reachable without importing the wizard's Textual layer.

SECRET_KEEP / SECRET_CLEAR sentinels move here from
``ui/textual/widgets/prompt_panel.py``: they are values in a domain
protocol, not widget state. ``prompt_panel.py`` re-imports them so
existing importers keep working.

Fix-round-1 note (#535 Pass 1, Task 7 review): the original
``_selections_to_args`` has TWO distinct "leave .env alone" paths --
``secret_v is None`` (the step was never visited, a bare ``pass``) and
a ``SECRET_KEEP`` that doesn't clear the auto-promote guard (already
enabled, so nothing to do). Neither writes ``source_args[cli_arg]`` at
all. ``CloudResolution.source`` must be able to express that same
"no verdict" state as ``None`` -- coercing it to a literal
``"disabled"`` (the first cut of this module did, via a
``source or "disabled"`` fallback) would force-disable an
already-enabled, already-keyed provider any time its secret step
simply isn't visited this run (narrower track, CLI-flag mode,
non-interactive run). That is silent destruction of a working
configuration, so it does not happen here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from utils.cloud_providers import CLOUD_PROVIDERS

# Sentinel return values for secret-input steps. Real API keys never
# match these strings, so downstream consumers can branch on intent
# without exposing the actual key. (Moved from prompt_panel.py.)
SECRET_KEEP = "<KEEP>"
SECRET_CLEAR = "<CLEAR>"

_PROVIDER_KEYS = frozenset(p.key for p in CLOUD_PROVIDERS)


@dataclass(frozen=True)
class CloudResolution:
    """The resolved enable/disable verdict for one cloud provider.

    ``source`` is ``None`` when there is NO VERDICT at all: the
    caller must leave this provider's ``CLOUD_*_SOURCE`` untouched in
    .env, exactly mirroring the original's ``source_args[cli_arg]``
    being left unset on its no-op paths. Do not default a ``None``
    source to ``"disabled"`` -- see the module docstring's fix-round-1
    note; that coercion is the bug this shape now prevents.

    ``api_key`` is symmetric with ``source``: ``None`` means "don't
    rewrite the stored key" (a KEEP with nothing to promote, or the
    secret step was never visited); ``""`` means "actively blank it"
    (CLEAR, an empty secret, or the zero-models disable override).
    """

    source: str | None  # "enabled" | "disabled" | None (no verdict -- leave .env alone)
    api_key: str | None
    models: list[str]


def resolve_cloud_provider(
    *,
    provider_key: str,
    secret_value: str | None,
    selected_models: Sequence[str],
    existing_key_set: bool,
    existing_source: str = "",
) -> CloudResolution:
    """Reconcile the secret step and the models step for one provider.

    ``provider_key`` must be a key declared in ``CLOUD_PROVIDERS``.

    ``existing_source`` is the provider's CURRENT ``CLOUD_*_SOURCE``
    value read from .env (the original's
    ``env_vars.get(source_var, 'disabled')``). It is consulted ONLY by
    the ``SECRET_KEEP`` branch, to reproduce the original's
    auto-promote guard exactly: when the provider is already enabled,
    SECRET_KEEP is a no-op (``source=None``), not a promotion and not
    a demotion. It defaults to ``""`` (normalizes the same as
    "disabled") so callers that never pass it -- i.e. every call site
    outside the KEEP branch -- are unaffected.
    """
    if provider_key not in _PROVIDER_KEYS:
        raise ValueError(f"Unknown cloud provider key: {provider_key!r}")

    models = list(selected_models)
    source: str | None
    api_key: str | None

    # ─── Secret-step intent ────────────────────────────────────────
    #   None                 -> step never visited. NO VERDICT: leave
    #                           .env alone -- the original's bare
    #                           ``pass`` path.
    #   SECRET_KEEP          -> already enabled -> no-op (no verdict,
    #                           forcing one would be destructive).
    #                           Not already enabled and a key exists
    #                           to keep -> promote to enabled. Not
    #                           already enabled and no key -> the
    #                           definite "disabled" it already was
    #                           (this is what
    #                           ``test_secret_keep_without_existing_key_does_not_enable``
    #                           pins down). The key itself is never
    #                           rewritten (api_key stays None).
    #   SECRET_CLEAR / ""    -> disable + blank the key.
    #   a real key string    -> enable + persist the key.
    if secret_value is None:
        source = None
        api_key = None
    elif secret_value == SECRET_KEEP:
        existing_source_norm = (existing_source or "disabled").strip().lower()
        if existing_source_norm == "enabled":
            source = None
        elif existing_key_set:
            source = "enabled"
        else:
            source = "disabled"
        api_key = None
    elif secret_value == SECRET_CLEAR or secret_value == "":
        source = "disabled"
        api_key = ""
    else:
        source = "enabled"
        api_key = secret_value

    # ─── Models-step override ──────────────────────────────────────
    # Zero selected models is an explicit disable that wins over
    # whatever the secret step decided above -- including a freshly
    # entered key, a KEEP promotion, or even a no-verdict None. This
    # mirrors the original: the multiselect block is a separate,
    # unconditional statement that runs regardless of what the secret
    # block did (or didn't do).
    if not models:
        source = "disabled"
        api_key = ""

    return CloudResolution(source=source, api_key=api_key, models=models)
