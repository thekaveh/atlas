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
regress silently. It is domain truth, placed in wizard/model so the
`--no-tui` path can adopt the same semantics in a later pass without
importing the wizard's Textual layer. Today `integration.py` (the
Textual path) is its only caller.

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

Fix-round-3 note (#535 Pass 1, Task 8 review): ``selected_models`` has
the SAME "no verdict" defect ``source`` had in fix-round-1. The
original's models-step override only fires when the multiselect was
genuinely visited THIS session and produced an explicit empty CSV --
never when the step wasn't visited or a degraded SECRET_KEEP commit
left the saved CSV untouched. A plain ``Sequence[str]`` cannot express
"no answer this session" -- an empty sequence is indistinguishable
from a real "the user unchecked everything". The first cut of this
fix-round pushed that distinction into the CALLER (a magic non-empty
placeholder list fed in just to suppress the override), which forked
the rule across two layers and would have made every future caller
(the Pass 2/3 ViewModels included) reproduce the same hack. Making
``selected_models`` accept ``None`` -- a third "no verdict" state,
symmetric with ``source``/``api_key`` -- keeps the whole rule in one
place: ``None`` never overrides, only a real (possibly empty)
sequence can.
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

    ``models`` echoes whatever sequence the caller passed as
    ``selected_models`` (``[]`` when the caller passed ``None`` --
    there is nothing to echo). It is NOT authoritative for what a
    caller should persist as the models CSV: the original writes the
    RAW multiselect string verbatim, not a re-parsed/rejoined form, so
    that bookkeeping stays with the caller.
    """

    source: str | None  # "enabled" | "disabled" | None (no verdict -- leave .env alone)
    api_key: str | None
    models: list[str]


def resolve_cloud_provider(
    *,
    provider_key: str,
    secret_value: str | None,
    selected_models: Sequence[str] | None,
    existing_key_set: bool,
    existing_source: str,
) -> CloudResolution:
    """Reconcile the secret step and the models step for one provider.

    ``provider_key`` must be a key declared in ``CLOUD_PROVIDERS``.

    ``existing_source`` is the provider's CURRENT ``CLOUD_*_SOURCE``
    value read from .env (the original's
    ``env_vars.get(source_var, 'disabled')``). It is consulted ONLY by
    the ``SECRET_KEEP`` branch, to reproduce the original's
    auto-promote guard exactly: when the provider is already enabled,
    SECRET_KEEP is a no-op (``source=None``), not a promotion and not
    a demotion.

    Fix-round-2 note (#535 Pass 1, Task 7 review): this parameter is
    REQUIRED, deliberately with no default. A default of ``""``
    (normalizing to "disabled") would make it easy to *silently*
    reproduce the exact class of bug fix-round-1 eliminated -- a
    caller that simply forgets to pass it would get "the provider is
    not enabled" for free, and a SECRET_KEEP against an
    already-enabled, keyless provider would then read as "disabled"
    and force-disable a working configuration. Omitting it is now a
    ``TypeError`` at the call site instead of a silent .env
    corruption.

    ``selected_models`` carries a THIRD "no verdict" state alongside
    ``source``/``api_key`` (fix-round-3, #535 Pass 1 Task 8 review):

      * ``None``       -> the multiselect step produced no real answer
                          THIS session (never visited, or a degraded
                          SECRET_KEEP commit whose options never
                          loaded). The zero-models override below MUST
                          NOT fire -- whatever the secret step decided
                          stands untouched. This is not the same as
                          "zero selected"; conflating the two would
                          force-disable a provider merely because its
                          multiselect step wasn't reached this run
                          (narrower track, an already-enabled provider
                          whose secret step -- and therefore its
                          gating multiselect -- was skipped, etc).
      * ``[]``          -> the step WAS visited and the user explicitly
                          unchecked every model. This is a real,
                          unconditional override and wins over
                          everything the secret step decided.
      * non-empty       -> real selections; no override.
    """
    if provider_key not in _PROVIDER_KEYS:
        raise ValueError(f"Unknown cloud provider key: {provider_key!r}")

    models, zero_models_override = _classify_selected_models(selected_models)
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
    #                           already enabled and no key -> resolves
    #                           to "disabled" here (this is what
    #                           ``test_secret_keep_without_existing_key_does_not_enable``
    #                           pins down) -- but this is a KNOWN,
    #                           DELIBERATELY CARRIED divergence from
    #                           the original, which writes nothing to
    #                           source_args on this branch at all. It
    #                           is unreachable on the live path today:
    #                           ``PromptPanel`` only ever emits
    #                           SECRET_KEEP when a key already exists,
    #                           so `existing_key_set` is always True
    #                           whenever this branch's sibling
    #                           (`existing_key_set` false) could fire.
    #                           The key itself is never rewritten
    #                           (api_key stays None).
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
    # ``zero_models_override`` is true only when the multiselect was
    # genuinely visited THIS session and produced an explicit empty
    # answer (see the docstring's fix-round-3 note above and
    # ``_classify_selected_models`` below). It is the explicit disable
    # that wins over whatever the secret step decided above --
    # including a freshly entered key, a KEEP promotion, or even a
    # no-verdict None. This mirrors the original: the multiselect
    # block is a separate, unconditional statement that runs
    # regardless of what the secret block did (or didn't do) -- but
    # ONLY when it actually ran.
    if zero_models_override:
        source = "disabled"
        api_key = ""

    return CloudResolution(source=source, api_key=api_key, models=models)


def _classify_selected_models(
    selected_models: Sequence[str] | None,
) -> tuple[list[str], bool]:
    """Split ``selected_models`` into ``(models, zero_models_override)``.

    Kept as its own tiny function (rather than inlined into
    ``resolve_cloud_provider``) so the "no answer this session" vs.
    "explicit empty answer" distinction is expressed as a single,
    readable classification step instead of two separate ``is not
    None`` checks scattered across the caller -- see the fix-round-3
    note on ``resolve_cloud_provider``'s docstring for why the
    distinction exists at all.
    """
    if selected_models is None:
        return [], False
    models = list(selected_models)
    return models, not models
