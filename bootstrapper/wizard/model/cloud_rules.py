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

    ``api_key`` is ``None`` when nothing should be rewritten (a KEEP
    with nothing to promote, or the secret step was never visited);
    it is ``""`` when the key must be actively blanked (CLEAR, an
    empty secret, or the zero-models disable override).
    """

    source: str  # "enabled" | "disabled"
    api_key: str | None
    models: list[str]


def resolve_cloud_provider(
    *,
    provider_key: str,
    secret_value: str | None,
    selected_models: Sequence[str],
    existing_key_set: bool,
) -> CloudResolution:
    """Reconcile the secret step and the models step for one provider.

    ``provider_key`` must be a key declared in ``CLOUD_PROVIDERS``.
    """
    if provider_key not in _PROVIDER_KEYS:
        raise ValueError(f"Unknown cloud provider key: {provider_key!r}")

    models = list(selected_models)

    # ─── Secret-step intent ────────────────────────────────────────
    #   None                 -> step never visited; no verdict yet.
    #   SECRET_KEEP          -> promote to enabled only if there is an
    #                           existing key to keep; the key itself
    #                           is never rewritten (api_key stays
    #                           None -- "leave .env alone").
    #   SECRET_CLEAR / ""    -> disable + blank the key.
    #   a real key string    -> enable + persist the key.
    if secret_value is None:
        source: str | None = None
        api_key: str | None = None
    elif secret_value == SECRET_KEEP:
        source = "enabled" if existing_key_set else None
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
    # entered key or a KEEP promotion.
    if not models:
        source = "disabled"
        api_key = ""

    return CloudResolution(source=source or "disabled", api_key=api_key, models=models)
