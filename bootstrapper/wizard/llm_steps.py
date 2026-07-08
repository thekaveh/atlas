"""
LLM cluster prompt-step builders for the Textual wizard.

Two consumers in ``bootstrapper/ui/textual/integration.py``:
  * ``build_ollama_steps(env_vars)`` — two steps: a unified models
    multiselect (container modes show the library catalog; localhost/
    external show merged [pulled] + [library]) and a free-text
    "additional to pull" step that fires only for container sources.
    Gating via ``skip_if_prev`` on ``LLM_PROVIDER_SOURCE``.
  * ``build_cloud_steps(env_vars, warn)`` — three (secret + multiselect)
    pairs for OpenAI / Anthropic / OpenRouter. The multiselect step
    is gated by the secret step's result (clear/empty → skip) and
    fetches its option list live via ``cloud_models`` (with curated
    catalog fallback).

Why a separate module: ``_build_steps_and_rows`` in integration.py was
~600 lines because every closure for these step lists lived inline.
Extracting the LLM cluster keeps integration.py focused on the screen
glue (steps + rows + state).
"""

from __future__ import annotations

from typing import Callable, Dict, List

from utils.cloud_models import (
    list_anthropic_models,
    list_openai_models,
    list_openrouter_models,
)
from utils.llm_catalog import (
    cloud_entries,
    default_active_names,
    ollama_entries,
)
from utils.model_resolver import (
    MEMORY_FACTS_EMBEDDING_DIM,
    dim_for_model_id,
    looks_like_embedding,
)
from utils.ollama_discovery import list_pulled_models
from utils.ollama_library import OllamaLibraryEntry, list_library_entries
from utils.cloud_providers import CLOUD_PROVIDERS

from ui.textual.widgets.prompt_panel import (
    SECRET_CLEAR,
    SECRET_KEEP,
    PromptOption,
    PromptStep,
)


def _csv(val: str | None) -> list[str]:
    """Split a comma-separated string into a list of non-empty stripped tokens."""
    if not val:
        return []
    return [s.strip() for s in val.split(",") if s.strip()]


LLM_ENGINE_TITLE = "LLM Engine  ·  source"
LLM_DEFAULT_CONTENT_TITLE = "LLM defaults  ·  chat model"
LLM_DEFAULT_EMBED_TITLE   = "LLM defaults  ·  embedding model"
LLM_DEFAULT_VISION_TITLE  = "LLM defaults  ·  vision model"
# Single unified Ollama models step. Replaces the previous pulled+library
# split, which produced two near-duplicate pages for localhost/external
# users (the library was a strict superset of /api/tags). Now: container
# sources show library only (nothing's pulled yet); localhost/external
# show a merged list with [pulled] / [library] badges.
OLLAMA_MODELS_TITLE = "Ollama  ·  models"
OLLAMA_CUSTOM_TITLE = "Ollama  ·  additional models to pull"


def cloud_secret_title(provider_name: str) -> str:
    """Wizard step title for the per-provider API-key (secret) step.
    Kept in lockstep with the (matching) ``cloud_models_title`` so the
    splitter in ``integration._selections_to_args`` can find them by
    name without redoing the f-string in every call site.
    """
    return f"{provider_name} Cloud  ·  API key"


def cloud_models_title(provider_name: str) -> str:
    """Wizard step title for the per-provider model multiselect step."""
    return f"{provider_name} Cloud  ·  models"


# ─── helpers ───────────────────────────────────────────────────────────


def _selected_llm_source(env_vars: Dict[str, str], selections: dict) -> str:
    v = selections.get(LLM_ENGINE_TITLE)
    if v:
        return v.strip().lower()
    return (env_vars.get("LLM_PROVIDER_SOURCE", "ollama-container-cpu") or "").strip().lower()


def _is_localhost_or_external(src: str) -> bool:
    return "localhost" in src or "external" in src


def _is_container_ollama(src: str) -> bool:
    return src.startswith("ollama-container-")


# ─── Ollama steps ──────────────────────────────────────────────────────



# ── module-level helpers for build_ollama_steps ─────────────────────
# Hoisted from inside build_ollama_steps: none of these capture any
# closure state, and at module level the badge-merge / sort logic is
# unit-testable (the 285-line builder shrank accordingly).
# Recency cutoff for the two-bucket sort. Models updated within
# this many days of the scrape are "recent" (sorted to the top by
# pulls); older models drop into the legacy bucket. 365 days
# picked deliberately so the boundary lines up with the relative
# "1 year ago" string Ollama emits on the listing page.
_LEGACY_THRESHOLD_DAYS = 365

# Curated-catalog badge → capability alias. The curated catalog
# uses the plural form ``embeddings`` (an older convention), the
# live scrape uses the singular ``embedding`` (Ollama's published
# capability tag). They mean the same thing — dedupe to the
# capability form so a row doesn't show both side-by-side.
_CAPABILITY_ALIASES = {"embeddings": "embedding"}

def _merge_badges(
    *,
    status: str | None,
    capabilities: frozenset[str],
    curated: list[str],
    legacy: bool,
) -> list[str]:
    """Compose a row's final badge list, deduping overlapping
    capability terms (alias-aware) and prepending status / legacy
    markers in a stable order.

    Output order: ``[status?, legacy?, *sorted_capabilities, *curated]``.
    Curated badges that alias to an already-present capability
    (e.g. ``embeddings`` ↔ ``embedding``) are dropped so the row
    never shows ``[embedding] [embeddings]``.
    """
    seen: set[str] = set()
    out: list[str] = []
    if status:
        out.append(status); seen.add(status)
    if legacy:
        out.append("legacy"); seen.add("legacy")
    for cap in sorted(capabilities):
        if cap in seen:
            continue
        out.append(cap); seen.add(cap)
    for b in curated:
        canonical = _CAPABILITY_ALIASES.get(b, b)
        if canonical in seen:
            continue
        out.append(b); seen.add(canonical)
    return out

def _compose_hint(description: str, updated: str) -> str:
    """Hint line: curated description + relative-time annotation.

    Both halves are optional; the joiner is shown only when both
    sides are non-empty so we don't render lonely separators.
    """
    parts: list[str] = []
    if description:
        parts.append(description)
    if updated:
        parts.append(f"updated {updated}")
    return "  ·  ".join(parts)

def _is_legacy(entry: OllamaLibraryEntry) -> bool:
    # Unknown age (None) is treated as recent — better than
    # banishing every model to the legacy bucket when an upstream
    # wording change breaks the relative-time parser.
    return entry.age_days is not None and entry.age_days >= _LEGACY_THRESHOLD_DAYS

def _sort_key(opt: PromptOption) -> tuple:
    # Two-bucket sort: recent (bucket 0) first, legacy (bucket 1)
    # last. Within each bucket: by pull count descending, then
    # alphabetical. The ``legacy`` badge is the bucket marker
    # added by the caller — keeps PromptOption agnostic of
    # OllamaLibraryEntry fields.
    return (
        1 if "legacy" in opt.badges else 0,
        -opt.pulls,
        opt.value,
    )

def _build_library_options(
    library_entries: List[OllamaLibraryEntry],
) -> List[PromptOption]:
    """Map :class:`OllamaLibraryEntry` rows to :class:`PromptOption`,
    merging in catalog metadata (description / curated badges)
    when available and surfacing capability tags + sizes + pulls.

    Badge order: status (optional) → legacy (optional) → sorted
    capabilities → curated badges (deduped against capabilities).
    """
    curated_meta = {e.name: e for e in ollama_entries()}
    opts: List[PromptOption] = []
    for entry in library_entries:
        meta = curated_meta.get(entry.name)
        curated_badges = list(meta.badges) if meta else []
        badges = _merge_badges(
            status=None,
            capabilities=entry.capabilities,
            curated=curated_badges,
            legacy=_is_legacy(entry),
        )
        opts.append(PromptOption(
            value=entry.name, label=entry.name,
            hint=_compose_hint(
                meta.description if meta else "",
                entry.updated,
            ),
            badges=badges,
            pulls=entry.pulls,
            sizes=entry.sizes,
        ))
    return opts

def _catalog_fallback_entries() -> List[OllamaLibraryEntry]:
    """Synthesize :class:`OllamaLibraryEntry` rows from the curated
    catalog. Used when the live ollama.com/library scrape fails so
    the wizard still surfaces *some* tags (only ``embedding`` /
    ``vision`` can be inferred from the catalog's structured
    capability flags — ``thinking`` / ``tools`` / ``audio`` aren't
    in CatalogEntry, so the fallback rows simply omit them).
    Sizes and pulls aren't recoverable from the catalog, so both
    default to empty / 0.
    """
    out: List[OllamaLibraryEntry] = []
    for e in ollama_entries():
        caps: set[str] = set()
        if getattr(e, "embeddings", 0) > 0:
            caps.add("embedding")
        if getattr(e, "vision", 0) > 0:
            caps.add("vision")
        out.append(OllamaLibraryEntry(
            name=e.name,
            capabilities=frozenset(caps),
            sizes=(),
            pulls=0,
            updated="",
        ))
    return out


def build_ollama_steps(
    env_vars: Dict[str, str],
    warn: Callable[[str], None] | None = None,
) -> List[PromptStep]:
    """Build the two Ollama steps (unified models multiselect +
    container-only free-text "additional to pull"). ``skip_if_prev``
    predicates gate them on the selected ``LLM_PROVIDER_SOURCE``.

    ``warn`` is the same launch-log sink the cloud steps use — passed
    here so live-discovery failures (``/api/tags`` unreachable) get
    persisted to ``/tmp/atlas-launch-*.log`` instead of
    silently producing an empty multiselect.
    """
    _warn = warn or (lambda _msg: None)
    ollama_default_actives = default_active_names("ollama")
    ollama_existing = {
        s.strip()
        for s in (env_vars.get("OLLAMA_USER_MODELS", "") or "").split(",")
        if s.strip()
    }
    ollama_default_values = (
        sorted(ollama_existing) if ollama_existing else ollama_default_actives
    )
    existing_custom = (env_vars.get("OLLAMA_CUSTOM_MODELS", "") or "").strip()

    def _ollama_upstream_for_wizard(selections: dict) -> str:
        src = _selected_llm_source(env_vars, selections)
        url = (env_vars.get("LITELLM_OLLAMA_UPSTREAM", "") or "").strip()
        if "localhost" in src:
            # Honor the user's port override — the 5th consumer site of
            # the localhost-port symmetry rule (runtime_sc, Kong,
            # service_config, localhost_validator already read it).
            port = (env_vars.get("OLLAMA_LOCALHOST_PORT", "") or "").strip() or "11434"
            return f"http://localhost:{port}"
        if "external" in src and url:
            return url
        return ""


    def _merged_ollama_options(selections: dict) -> List[PromptOption]:
        """Build the unified Ollama models option list.

        Source-aware behavior:
          * ``ollama-container-*``: nothing is pulled yet (the in-stack
            container isn't running at wizard time), so just return the
            library scrape (with curated catalog as fallback).
          * ``ollama-localhost``: query the upstream's ``/api/tags`` AND
            scrape ollama.com/library, then merge:
              - already-pulled rows (from /api/tags) appear first with a
                ``pulled`` badge
              - library entries also present in /api/tags get the
                ``pulled`` badge too (in their library position)
              - library-only entries get a ``library`` badge

        On failure: degrades gracefully — if the library scrape fails we
        keep the pulled list (or curated catalog); if /api/tags fails
        we keep the library list with a placeholder note in the warn
        sink.
        """
        src = _selected_llm_source(env_vars, selections)

        # Library scrape is shared by every source.
        library_entries = list_library_entries(timeout=5.0)
        if not library_entries:
            _warn(
                "[warn/ollama-fetch] ollama.com/library scrape returned no entries "
                "— falling back to curated OLLAMA_DEFAULT_CATALOG"
            )
            library_entries = _catalog_fallback_entries()
        # Drop Ollama Cloud-exclusive models — they cannot be pulled,
        # so surfacing them in a multiselect that drives ``ollama pull``
        # would be misleading. Hybrid entries (cloud + local sizes)
        # have ``cloud_only=False`` and remain in the list with their
        # local variants intact.
        cloud_skipped = [e.name for e in library_entries if e.cloud_only]
        if cloud_skipped:
            library_entries = [e for e in library_entries if not e.cloud_only]
            _warn(
                f"[info/ollama-fetch] excluded {len(cloud_skipped)} cloud-only "
                f"Ollama Cloud model(s) — not pullable: "
                f"{', '.join(cloud_skipped[:6])}"
                + (" …" if len(cloud_skipped) > 6 else "")
            )

        if not _is_localhost_or_external(src):
            # Container modes: library only. Nothing to merge.
            if not library_entries:
                return [PromptOption(
                    value="",
                    label="(catalog unreachable — ollama.com/library scrape failed)",
                    hint="check network access; ollama-pull will pull the model_resolver active set on next start",
                    badges=[],
                )]
            opts = _build_library_options(library_entries)
            # Two-bucket sort: recent first (sorted by pulls desc),
            # legacy (updated > 365 days ago) second. Within each
            # bucket: pulls desc, alphabetical tiebreak.
            opts.sort(key=_sort_key)
            return opts

        # Localhost/external: merge /api/tags + library.
        url = _ollama_upstream_for_wizard(selections)
        pulled_names: List[str] = []
        if not url:
            _warn(
                "[warn/ollama-fetch] no Ollama upstream URL resolved for "
                f"{src!r} — showing library entries only"
            )
        else:
            pulled_names = list_pulled_models(url, timeout=2.0)
            if not pulled_names:
                _warn(
                    f"[warn/ollama-fetch] /api/tags at {url} returned 0 models — "
                    "showing library entries only (run `ollama pull <name>` "
                    "on the host to populate)"
                )

        pulled_set = set(pulled_names)
        library_by_name = {e.name: e for e in library_entries}
        curated_meta = {e.name: e for e in ollama_entries()}

        opts: List[PromptOption] = []
        # 1. Pulled-but-not-in-library: rare (custom local builds, dev
        #    versions). These have no scrape data — pulls=0, sizes=(),
        #    age unknown, so they neither get a ``legacy`` badge nor
        #    surface near the top. They drop to the alphabetical tail.
        for name in pulled_names:
            # /api/tags names are always TAGGED (qwen3.6:latest) while
            # library keys are bare families (qwen3.6) — compare the
            # family root or every normal pull duplicates its library
            # row as a bogus "(local model, not in public library)".
            if name in library_by_name or name.split(":", 1)[0] in library_by_name:
                continue
            meta = curated_meta.get(name)
            curated_badges = list(meta.badges) if meta else []
            opts.append(PromptOption(
                value=name, label=name,
                hint=meta.description if meta else "(local model, not in public library)",
                badges=_merge_badges(
                    status="pulled",
                    capabilities=frozenset(),
                    curated=curated_badges,
                    legacy=False,
                ),
            ))
        # 2. Library entries — items also pulled get the ``pulled``
        #    status badge prepended; library-only get ``library``.
        #    Capability tags, sizes, pulls, and age flow from the
        #    OllamaLibraryEntry. ``pulled_variants`` carries the bare
        #    variant tags pulled for this family so each leaf can
        #    render its own [pulled]/[library] status independently
        #    of the parent — e.g. ``qwen3.6:35b-a3b-coding-mxfp8``
        #    pulled but ``qwen3.6:latest`` not, or vice versa.
        for entry in library_entries:
            meta = curated_meta.get(entry.name)
            curated_badges = list(meta.badges) if meta else []
            # Family-level status: "pulled" if ANY tag of this family
            # is on the host (the parent row carries an aggregate
            # status). Per-leaf status is computed below in
            # ``_leaf_render_data`` from ``pulled_variants``.
            family_prefix = entry.name + ":"
            family_tags_pulled = frozenset(
                n[len(family_prefix):] for n in pulled_names
                if n.startswith(family_prefix)
            )
            status = "pulled" if (
                entry.name in pulled_set or family_tags_pulled
            ) else "library"
            opts.append(PromptOption(
                value=entry.name, label=entry.name,
                hint=_compose_hint(
                    meta.description if meta else "",
                    entry.updated,
                ),
                badges=_merge_badges(
                    status=status,
                    capabilities=entry.capabilities,
                    curated=curated_badges,
                    legacy=_is_legacy(entry),
                ),
                pulls=entry.pulls,
                sizes=entry.sizes,
                pulled_variants=family_tags_pulled,
            ))
        opts.sort(key=_sort_key)
        if not opts:
            return [PromptOption(
                value="",
                label=f"(no Ollama models reachable at {url or '<no upstream>'})",
                hint="check the upstream URL or pull a model on the host with `ollama pull <name>`",
                badges=[],
            )]
        return opts

    return [
        # Single unified step. Container modes get library only;
        # localhost/external get a merged [pulled] + [library] view.
        # See _merged_ollama_options for details.
        PromptStep(
            title=OLLAMA_MODELS_TITLE,
            step_index=0, step_total=0,
            heading="Which Ollama models to register?",
            subtitle=(
                "Container: full ollama.com/library catalog (ollama-pull fetches checked at startup). "
                "Localhost/external: [pulled] = already on your Ollama host (usable immediately); "
                "[library] = in the public catalog but NOT on your host yet (you'll need to "
                "`ollama pull <name>` afterwards). "
                "Recent models first (by pull count); [legacy] = updated > 1 year ago. "
                "Each row's 2nd line shows variant sizes — both Ollama tag (8b) and approximate "
                "Q4 disk footprint (4.8GB); selected variants render in green. "
                "Multi-variant rows show a ▶ — press Space on the parent to expand its tree, "
                "Space on a leaf (variant) toggles that specific tag. Single-variant rows toggle "
                "directly. Press `f` to cycle capability filter chips. Enter confirms the step."
            ),
            options=[],
            default_values=ollama_default_values,
            service_name="",
            kind="multiselect",
            skip_if_prev=lambda sel: not _selected_llm_source(env_vars, sel).startswith("ollama-"),
            options_provider=_merged_ollama_options,
            # Capability filter chips. Exact label set matches the
            # ``x-test-capability`` values observed on ollama.com/library
            # — keep these lowercase and aligned with what the parser
            # in utils/ollama_library.py extracts.
            filter_tags=("embedding", "thinking", "vision", "tools", "audio"),
        ),
        PromptStep(
            title=OLLAMA_CUSTOM_TITLE,
            step_index=0, step_total=0,
            heading="Additional Ollama models to pull?",
            subtitle=(
                "Comma-separated names not in the catalog (e.g. 'llama3.3, deepseek-r1'). "
                "Press Enter to skip. Each gets pulled by ollama-pull at startup."
            ),
            options=[],
            default_value=existing_custom,
            service_name="",
            kind="text",
            skip_if_prev=lambda sel: not _is_container_ollama(
                _selected_llm_source(env_vars, sel)
            ),
        ),
    ]


# ─── Cloud steps ───────────────────────────────────────────────────────


def _make_cloud_options_provider(
    provider_key: str,
    secret_title: str,
    api_key_var: str,
    env_vars: Dict[str, str],
    warn: Callable[[str], None],
):
    """Return a PromptStep options_provider that fetches the live model
    list for the given cloud provider. Falls back to the curated
    catalog on any failure (network, auth, empty result).

    Resolution order for the API key:
      1. The secret-step value if it's a real key (not sentinel/empty).
      2. Existing value from .env (when secret step returned SECRET_KEEP).
      3. None — only OpenRouter works keyless; others return [].
    """
    def _resolve_key(selections: dict) -> str:
        v = selections.get(secret_title)
        if isinstance(v, str) and v and v not in (SECRET_KEEP, SECRET_CLEAR):
            return v
        return (env_vars.get(api_key_var, "") or "").strip()

    def _curated_options() -> List[PromptOption]:
        return [
            PromptOption(
                value=e.name, label=e.name,
                hint=e.description, badges=list(e.badges),
            )
            for e in cloud_entries(provider_key)
        ]

    def _provider(selections: dict) -> List[PromptOption]:
        try:
            if provider_key == "openrouter":
                live = list_openrouter_models(timeout=5.0, on_warn=warn)
            else:
                api_key = _resolve_key(selections)
                if provider_key == "openai":
                    live = list_openai_models(api_key, timeout=5.0, on_warn=warn)
                elif provider_key == "anthropic":
                    live = list_anthropic_models(api_key, timeout=5.0, on_warn=warn)
                else:
                    live = []
        except Exception as exc:  # noqa: BLE001
            warn(f"[warn/{provider_key}-fetch] unexpected error: {type(exc).__name__}: {exc}")
            live = []
        if not live:
            return _curated_options()
        return [
            PromptOption(value=m.id, label=m.label, hint=m.description, badges=[])
            for m in live
        ]

    return _provider


def _make_cloud_skip_predicate(
    secret_title: str,
    source_var: str,
    api_key_var: str,
    env_vars: Dict[str, str],
):
    """skip_if_prev: True when the user's secret-step result says the
    provider should stay disabled.

    Decision matrix (secret-step result × .env source × .env key):

      result == SECRET_CLEAR or ""
          → user explicitly disabled. Skip multiselect.
      result == SECRET_KEEP and .env source == 'enabled'
          → already enabled. Show multiselect.
      result == SECRET_KEEP and .env source != 'enabled' and key set
          → AMBIGUOUS. The user pressed Enter past a step that already
          had a key in .env but a disabled source. We treat that as
          "use existing key + enable provider" (the integration layer
          auto-promotes the source flag in _selections_to_args). Show
          multiselect so they can pick models.
      result == SECRET_KEEP and .env source != 'enabled' and no key
          → freshly disabled provider, nothing to enable with. Skip.
      result == real key string
          → user typed a new key. Show multiselect.
    """
    def _skip(selections: dict) -> bool:
        v = selections.get(secret_title)
        if v is None:
            return False
        if v == SECRET_CLEAR or v == "":
            return True
        if v == SECRET_KEEP:
            existing_source = (env_vars.get(source_var, 'disabled') or '').strip().lower()
            existing_key = (env_vars.get(api_key_var, '') or '').strip()
            if existing_source == 'enabled':
                return False
            # Disabled in .env. Skip unless there's an existing key to
            # auto-promote with.
            return not existing_key
        # Real key typed in.
        return False
    return _skip


def build_cloud_steps(
    env_vars: Dict[str, str],
    warn: Callable[[str], None],
) -> List[PromptStep]:
    """Build the three (secret + multiselect) pairs for OpenAI,
    Anthropic, OpenRouter."""
    cloud_steps: List[PromptStep] = []
    for provider in CLOUD_PROVIDERS:
        name, source_var, api_key_var = provider.name, provider.source_var, provider.api_key_var
        existing_key = (env_vars.get(api_key_var, "") or "").strip()
        existing_source = (env_vars.get(source_var, "disabled") or "").strip().lower()
        secret_title = cloud_secret_title(name)
        if existing_key and existing_source == "enabled":
            secret_subtitle = (
                f"{name} is already enabled. Press Enter to keep the saved key, "
                "type a replacement key, or type 'clear' to disable."
            )
            secret_keep_hint = (
                "key saved and provider enabled  ·  Enter keeps enabled  ·  "
                "type a new key to replace  ·  type \"clear\" + Enter to disable"
            )
        elif existing_key:
            secret_subtitle = (
                f"A {name} key is saved but {source_var} is disabled. "
                "Press Enter to enable with the saved key, type a replacement key, "
                "or type 'clear' to remove it."
            )
            secret_keep_hint = (
                "key saved but provider disabled  ·  Enter enables with saved key  ·  "
                "type a new key to replace  ·  type \"clear\" + Enter to remove"
            )
        else:
            secret_subtitle = (
                f"Paste your {name} API key to enable. "
                "Press Enter (empty) to leave disabled. The key is stored in .env."
            )
            secret_keep_hint = None
        cloud_steps.append(PromptStep(
            title=secret_title,
            step_index=0, step_total=0,
            heading=f"Use {name} through LiteLLM?",
            subtitle=secret_subtitle,
            options=[],
            default_value=existing_key,
            service_name="",
            kind="secret",
            secret_keep_hint=secret_keep_hint,
        ))
        provider_key = provider.key
        catalog_rows = cloud_entries(provider_key)
        existing_models = {
            s.strip()
            for s in (env_vars.get(provider.user_models_var, "") or "").split(",")
            if s.strip()
        }
        if existing_models:
            default_values = sorted(existing_models)
        else:
            curated = default_active_names(provider_key)
            default_values = curated or [e.name for e in catalog_rows]
        cloud_steps.append(PromptStep(
            title=cloud_models_title(name),
            step_index=0, step_total=0,
            heading=f"Which {name} models do you want available?",
            subtitle=(
                "Live from /v1/models (filtered). Space toggles, Enter confirms. "
                "Picks are persisted to .env and activate the model set via model_resolver on next start."
            ),
            options=[],
            default_values=default_values,
            service_name="",
            kind="multiselect",
            skip_if_prev=_make_cloud_skip_predicate(
                secret_title, source_var, api_key_var, env_vars,
            ),
            options_provider=_make_cloud_options_provider(
                provider_key, secret_title, api_key_var, env_vars, warn,
            ),
        ))
    return cloud_steps


# ─── Default model steps ───────────────────────────────────────────────


def _litellm_id(provider: str, name: str) -> str:
    """Format a (provider, model-name) pair as a LiteLLM model id.

    Ollama models are prefixed with ``ollama/`` (matching the litellm-init
    routing table and ``model_resolver.best``'s return format). Every other
    provider (openai, anthropic, openrouter) uses the bare model name because
    LiteLLM routes them by the provider header rather than an id prefix.

    >>> _litellm_id("ollama", "qwen3.6:latest")
    'ollama/qwen3.6:latest'
    >>> _litellm_id("openai", "gpt-4o")
    'gpt-4o'
    """
    if provider == "ollama":
        return f"ollama/{name}"
    return name


def build_default_model_steps(
    env_vars: Dict[str, str],
    warn: Callable[[str], None] | None = None,
) -> List[PromptStep]:
    """Build the three final wizard steps that let the user choose the default
    chat (content), embedding, and vision models across all enabled providers.

    The selections write ``LITELLM_DEFAULT_MODEL``, ``LITELLM_EMBEDDING_MODEL``,
    and ``LITELLM_VISION_MODEL`` to ``.env`` via the wizard's
    ``apply_user_model_selections`` pipeline.

    Convention pre-selection
    ------------------------
    * **Chat / content:** pre-selects the first content-capable model in
      catalog-priority order among the user's current selections.
    * **Embedding:** pre-selects the current ``LITELLM_EMBEDDING_MODEL`` value
      from ``.env``, falling back to ``ollama/nomic-embed-text`` (the 768-dim
      safe default).  A visible warning in the heading/subtitle reminds the
      user that changing this can break the backend's pgvector fallback.
    * **Vision:** pre-selects the first vision-capable model in priority order,
      or ``""`` (none / skip) when no vision model is selected.

    Skip logic
    ----------
    Content and embedding steps are skipped when no LLM provider is active
    (Ollama disabled AND no cloud provider enabled via a real secret or
    SECRET_KEEP-with-existing-key).  The vision step is also skipped when no
    vision-capable model is among the user's selections.

    Embedding-dimension caveat
    --------------------------
    The backend's ``public.memory_facts.embedding vector(768)`` pgvector column
    is sized for nomic-embed-text (768 dims).  If the user picks a
    different-dimension model, that column and all embedding storage will break.
    The warning is surfaced in the embedding step's heading and subtitle; the
    default is deliberately NOT changed — users who understand the risk can
    still override it with eyes open.
    """
    _warn = warn or (lambda _msg: None)

    def _no_llm_active(selections: dict) -> bool:
        """Return True (→ skip) when no LLM provider is active.

        Ollama: source must start with ``ollama-``.
        Cloud: any provider must have a real key (or SECRET_KEEP on an
        existing key) to be considered active.
        """
        # Check Ollama
        src = _selected_llm_source(env_vars, selections)
        ollama_active = src.startswith("ollama-")

        # Check cloud providers
        cloud_active = False
        for p in CLOUD_PROVIDERS:
            v = selections.get(cloud_secret_title(p.name))
            if v is None:
                # Step not visited — check .env
                existing_source = (env_vars.get(p.source_var, "disabled") or "").strip().lower()
                existing_key = (env_vars.get(p.api_key_var, "") or "").strip()
                if existing_source == "enabled" and existing_key:
                    cloud_active = True
                    break
            elif v == SECRET_KEEP:
                existing_source = (env_vars.get(p.source_var, "disabled") or "").strip().lower()
                existing_key = (env_vars.get(p.api_key_var, "") or "").strip()
                if existing_source == "enabled" and existing_key:
                    cloud_active = True
                    break
                # Disabled in .env but has key → auto-promote path
                if existing_key:
                    cloud_active = True
                    break
            elif v and v not in (SECRET_CLEAR,):
                # Real key typed → provider is active
                cloud_active = True
                break

        return not (ollama_active or cloud_active)

    def _gather_selected_models(selections: dict) -> List[tuple]:
        """Return a list of (provider, name) pairs for the user's current
        selections across all providers.

        For Ollama: reads from the selections dict; falls back to .env when
        the step returned SECRET_KEEP (degraded fetch).
        For cloud: reads per-provider multiselect; falls back to .env when
        the step was not visited.
        """
        pairs: List[tuple] = []

        # Ollama
        ollama_v = selections.get(OLLAMA_MODELS_TITLE)
        if ollama_v is None or ollama_v == SECRET_KEEP:
            # Step not visited / degraded — fall back to saved .env value
            ollama_names = _csv(env_vars.get("OLLAMA_USER_MODELS", ""))
        else:
            ollama_names = _csv(ollama_v)
        src = _selected_llm_source(env_vars, selections)
        if src.startswith("ollama-"):
            for name in ollama_names:
                pairs.append(("ollama", name))

        # Cloud providers
        for p in CLOUD_PROVIDERS:
            secret_v = selections.get(cloud_secret_title(p.name))
            # Provider is active if secret was not cleared
            if secret_v == SECRET_CLEAR or secret_v == "":
                continue
            models_v = selections.get(cloud_models_title(p.name))
            if models_v is None or models_v == SECRET_KEEP:
                # Not visited / degraded → fall back to .env
                names = _csv(env_vars.get(p.user_models_var, ""))
            else:
                names = _csv(models_v)
            # Determine if provider is active
            if secret_v is not None and secret_v not in (SECRET_KEEP, SECRET_CLEAR, ""):
                # Real key was typed
                provider_active = True
            elif secret_v == SECRET_KEEP or secret_v is None:
                # Auto-promote: a saved key makes the provider active for the
                # default-model pickers regardless of its source flag (matches
                # _no_llm_active's disabled-but-keyed-as-active stance). The
                # prior `existing_key and (existing_source == "enabled" or
                # existing_key)` was a tautology — the outer `existing_key and`
                # already required truthiness, so the RHS always reduced to
                # existing_key — which always evaluated to bool(existing_key).
                existing_key = (env_vars.get(p.api_key_var, "") or "").strip()
                provider_active = bool(existing_key)
            else:
                provider_active = False
            if provider_active:
                for name in names:
                    pairs.append((p.key, name))

        return pairs

    def _classify(provider: str, name: str, category: str) -> bool:
        """Return True if (provider, name) qualifies for ``category``.

        Category is one of ``'content'``, ``'embeddings'``, ``'vision'``.
        Looks up the curated catalog by exact name OR by family root (the
        catalog mixes bare families like ``nomic-embed-text`` with tagged
        entries like ``qwen3.6:latest``, while wizard selections are tagged —
        ``nomic-embed-text:latest`` — so a tag-insensitive match on both sides
        is required or every tagged embedding model falls through). Models the
        catalog can't speak for are classified by :func:`looks_like_embedding`
        so embedding models are never offered as the chat/vision default;
        everything else is treated as content-only (mirrors _synthesize).
        """
        entries = ollama_entries() if provider == "ollama" else cloud_entries(provider)
        root = name.split(":", 1)[0]
        for entry in entries:
            if entry.name == name or entry.name.split(":", 1)[0] == root:
                return getattr(entry, category, 0) > 0
        # Not in catalog → live-discovered / custom. Keep embedding models out
        # of the content/vision pickers; treat the rest as content-only.
        if looks_like_embedding(name):
            return category == "embeddings"
        return category == "content"

    def _description(provider: str, name: str) -> str:
        """Return the catalog description for (provider, name), or empty.

        Matches by exact name or family root (tag-insensitive) — same reason as
        ``_classify``: a tagged selection (``nomic-embed-text:latest``) must
        still find its bare catalog entry (``nomic-embed-text``).
        """
        catalog = ollama_entries() if provider == "ollama" else cloud_entries(provider)
        root = name.split(":", 1)[0]
        for entry in catalog:
            if entry.name == name or entry.name.split(":", 1)[0] == root:
                return entry.description
        return ""

    def _build_options_for_category(selections: dict, category: str) -> List[PromptOption]:
        """Build PromptOption list for the given category from current selections."""
        pairs = _gather_selected_models(selections)
        opts: List[PromptOption] = []
        for provider, name in pairs:
            if not _classify(provider, name, category):
                continue
            litellm_id = _litellm_id(provider, name)
            hint = _description(provider, name)
            opts.append(PromptOption(value=litellm_id, label=name, hint=hint))
        return opts

    def _default_for_embeddings(env_vars: Dict[str, str]) -> str:
        saved = (env_vars.get("LITELLM_EMBEDDING_MODEL", "") or "").strip()
        return saved if saved else "ollama/nomic-embed-text"

    def _has_vision_models(selections: dict) -> bool:
        return bool(_build_options_for_category(selections, "vision"))

    # ── Step 1: default chat / content model ─────────────────────────
    def _content_options(selections: dict) -> List[PromptOption]:
        return _build_options_for_category(selections, "content")

    def _skip_no_llm_or_no_content(selections: dict) -> bool:
        # An options step with zero options traps the user: selected_option is
        # None, so Enter is a silent no-op (only Esc/Ctrl+Q escape). A provider
        # can be active yet contribute no content model (e.g. only an embedding
        # model selected), so skip rather than render an empty, stuck step.
        return _no_llm_active(selections) or not _content_options(selections)

    _embedding_default = _default_for_embeddings(env_vars)

    # ── Step 2: default embedding model ──────────────────────────────
    def _embed_options(selections: dict) -> List[PromptOption]:
        # Auto-match: stable-sort so embedding models whose declared dim equals
        # the consumer's required dimension (memory_facts vector(768)) come
        # first — index 0 is the wizard's pre-selected default. Catalog/selection
        # order is preserved as the tiebreaker within each dim group, and models
        # with no declared dim sort after the matching ones.
        opts = _build_options_for_category(selections, "embeddings")
        return sorted(
            opts,
            key=lambda o: 0 if dim_for_model_id(o.value) == MEMORY_FACTS_EMBEDDING_DIM else 1,
        )

    def _skip_no_llm_or_no_embed(selections: dict) -> bool:
        # Same empty-options trap as content: a provider can be active with no
        # embedding model selected (e.g. a cloud-only chat setup). Skip the step
        # instead of stranding the user on a stuck, optionless prompt;
        # LITELLM_EMBEDDING_MODEL keeps its existing value.
        return _no_llm_active(selections) or not _embed_options(selections)

    # ── Step 3: default vision model ─────────────────────────────────
    def _vision_options(selections: dict) -> List[PromptOption]:
        none_opt = PromptOption(value="", label="— none / skip —")
        capable = _build_options_for_category(selections, "vision")
        return [none_opt] + capable

    def _skip_no_llm_or_no_vision(selections: dict) -> bool:
        return _no_llm_active(selections) or not _has_vision_models(selections)

    # Pre-select the saved vision model on a re-run so confirming the step
    # doesn't silently wipe LITELLM_VISION_MODEL. Empty (fresh setup) leaves
    # the "— none / skip —" sentinel pre-selected. (Mirrors how the embedding
    # step pre-fills from its saved value.)
    _vision_default = (env_vars.get("LITELLM_VISION_MODEL", "") or "").strip()

    # default_value semantics for these options steps:
    #   - content: default_value=None → the WizardScreen pre-selects the FIRST
    #     option (highest-priority content model) after options_provider runs.
    #   - embedding: default_value=_embedding_default → the current saved value
    #     (or the 768-dim safe default), deterministic at build time.
    #   - vision: default_value=_vision_default → the saved LITELLM_VISION_MODEL
    #     so a re-run doesn't wipe it; empty on a fresh setup (pre-selects the
    #     "— none / skip —" sentinel). NOT the first option.

    return [
        PromptStep(
            title=LLM_DEFAULT_CONTENT_TITLE,
            step_index=0, step_total=0,
            heading="Which model should be the default for chat?",
            subtitle=(
                "This sets LITELLM_DEFAULT_MODEL — the fallback used by the backend and "
                "Open WebUI when no model is specified. Pre-selected to the highest-priority "
                "content-capable model from your current selections."
            ),
            options=[],
            default_value=None,
            service_name="",
            kind="options",
            skip_if_prev=_skip_no_llm_or_no_content,
            options_provider=_content_options,
        ),
        PromptStep(
            title=LLM_DEFAULT_EMBED_TITLE,
            step_index=0, step_total=0,
            heading="Which model should be used for embeddings?  ⚠ Dimension-sensitive",
            subtitle=(
                "This sets LITELLM_EMBEDDING_MODEL. "
                "Most stacks should keep the 768-dim default (nomic-embed-text); "
                "a different-dimension model can break the backend's pgvector fallback. "
                "Pre-selected to the current saved value (or the 768-dim safe default)."
            ),
            options=[],
            default_value=_embedding_default,
            service_name="",
            kind="options",
            skip_if_prev=_skip_no_llm_or_no_embed,
            options_provider=_embed_options,
        ),
        PromptStep(
            title=LLM_DEFAULT_VISION_TITLE,
            step_index=0, step_total=0,
            heading="Which model should handle image/vision inputs?",
            subtitle=(
                "This sets LITELLM_VISION_MODEL — used by the backend when an image is "
                "attached to a request. Pre-selected to your saved vision model, or "
                "'— none / skip —' on a fresh setup. Choose '— none / skip —' to leave unset."
            ),
            options=[],
            default_value=_vision_default,
            service_name="",
            kind="options",
            skip_if_prev=_skip_no_llm_or_no_vision,
            options_provider=_vision_options,
        ),
    ]
