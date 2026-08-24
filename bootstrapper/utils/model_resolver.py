"""
model_resolver.py — DB-free active-model set computation and convention winner picker.

OVERVIEW
--------
This module is the host-side replacement for the activation+ranking logic that
the former ``sync-catalog.py`` + the former ``public.llms`` DB ``ORDER BY`` performed at
container-init time. Instead of querying Postgres, it drives model selection
purely from:
  • the curated YAML catalogs (loaded by ``utils.llm_catalog``)
  • env vars (selection knobs the user sets via the wizard or ``.env``)
  • an optional ``ollama_tags`` argument for host-side auto-discovered models

This makes the three "which model should I use for role X?" decisions available
to the bootstrapper, wizard, and env_assembler without requiring a running DB or
Docker stack — enabling compile-time defaults (e.g. ``.env.example`` values)
and single-source-of-truth answers before any container is up.

IMPORTANT DIVERGENCE FROM THE FORMER sync-catalog.py
------------------------------------------------------
When a provider's ``*_USER_MODELS`` is empty, this module ALWAYS falls back to
the catalog's ``default_active=True`` names. The former ``sync-catalog.py`` instead preserved
whatever rows were already active in the database on a re-run. That distinction
is moot here (there is no DB to preserve state) and is correct for bootstrap,
wizard, and ``.env.example`` generation, but callers must NOT treat
``active_models()`` as a mirror of a running stack's persisted DB state.

ACTIVATION RULES
----------------
These replicate the former ``sync-catalog.py``'s DB-write logic without touching the DB:

Ollama (provider ``'ollama'``):
  1. ``LLM_PROVIDER_SOURCE`` must start with ``'ollama-'`` (i.e. not ``'none'``
     or ``'disabled'``) — otherwise no ollama models are active.
  2. Base name set = ``OLLAMA_USER_MODELS`` (CSV) if non-empty, else the
     catalog's ``default_active=True`` ollama names.
  3. If ``ollama_tags`` is provided (host-side auto-import), union them in.
  4. Union ``OLLAMA_CUSTOM_MODELS`` (CSV).
  5. Catalog entries whose name is in the set are included (in catalog order).
     Any active name NOT in the catalog gets a synthesized ``CatalogEntry``
     with generic ``content=8`` defaults (appended after catalog entries).

Cloud (each provider in ``CLOUD_PROVIDERS``, in canonical order):
  1. Must be both enabled (``LITELLM_<PROV>_ENABLED=true``) and keyed
     (``<PROV>_API_KEY`` non-empty) — otherwise provider produces no actives.
  2. Name set = ``<PROV>_USER_MODELS`` (CSV) if non-empty, else the
     catalog's ``default_active=True`` names for that provider.
  3. Catalog entries whose name is in the set are included (in catalog order).
     Any selected name NOT in the catalog gets a synthesized entry (content=8).

ORDERING = PRIORITY
-------------------
``active_models()`` returns entries in a STABLE priority order:
  ollama catalog entries (catalog order) → ollama synthesized
  → openai catalog → openai synthesized
  → anthropic catalog → anthropic synthesized
  → openrouter catalog → openrouter synthesized

This order is used directly by ``best()``: the FIRST entry with the requested
capability > 0 wins. With the default config (ollama-container-cpu, no cloud
keys), this yields ``ollama/qwen3.8:latest`` for both content and vision.

EMBEDDING CARVE-OUT
-------------------
``best('embeddings', ...)`` EXISTS in this module (it's needed for the B3
wizard step and B4's model picker). However, ``resolved_defaults()`` deliberately
EXCLUDES ``LITELLM_EMBEDDING_MODEL`` from its return value.

Rationale: ``LITELLM_EMBEDDING_MODEL`` defaults to ``ollama/nomic-embed-text``
(768-dim), which matches the ``public.memory_facts.embedding vector(768)``
pgvector column used by the backend's memory fallback. Switching to a
different-dimension embedding model (e.g. qwen3-embedding:0.6b at 1536 dims)
would silently break all existing embedding storage. Therefore that var stays
an explicit static default in ``services/litellm/service.yml``; the resolver
never overrides it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Optional

# Container-safe dual-mode import.
#
# This module is consumed in two contexts:
#   1. Bootstrapper venv (host): `bootstrapper/utils/` is a proper Python
#      package, so `from utils import llm_catalog` resolves against the
#      package's __init__ and works fine.
#   2. litellm-init container: `bootstrapper/utils/` is bind-mounted as
#      `/catalog` and the scripts import modules LOOSE (no `utils` package).
#      In that context `from utils import llm_catalog` raises ImportError
#      because there is no `utils` package on sys.path — only the bare
#      `/catalog` directory. The fallback branch handles this case.
#
# `_csv` follows the same split, but for a different reason (#535
# followups review, finding R2): `bootstrapper/wizard/` — home of the
# canonical `parse_csv` (`wizard.model.llm_rules.parse_csv`) — is never
# bind-mounted into litellm-init at all (only `bootstrapper/utils/` is,
# as `/catalog`; see services/litellm/compose.yml). So the container
# branch below cannot reach it under EITHER import spelling, unlike
# `llm_catalog`/`cloud_providers`, which are the same source file on
# both sides of the mount. It keeps its own small inlined copy instead.
try:                                   # bootstrapper venv (package context)
    from utils import llm_catalog
    from utils.llm_catalog import CatalogEntry
    from utils.cloud_providers import CLOUD_PROVIDERS
    from wizard.model.llm_rules import parse_csv as _csv
except ImportError:                    # container /catalog (loose modules)
    import llm_catalog  # type: ignore[no-redef]
    from llm_catalog import CatalogEntry  # type: ignore[no-redef]
    from cloud_providers import CLOUD_PROVIDERS  # type: ignore[no-redef]

    def _csv(val: str | None) -> list[str]:  # type: ignore[no-redef]
        """Split a comma-separated string into a list of non-empty
        stripped tokens. Byte-identical to
        ``wizard.model.llm_rules.parse_csv`` — inlined rather than
        imported because this branch runs inside litellm-init, which
        cannot reach ``bootstrapper/wizard/`` (see the note above)."""
        if not val:
            return []
        return [s.strip() for s in val.split(",") if s.strip()]


# ---------------------------------------------------------------------------
# Local helpers — mirror the former sync-catalog.py with safe extensions
# ---------------------------------------------------------------------------

def _truthy(val: str | None) -> bool:
    """Accepts a conservative superset of the former sync-catalog.py's truthy set (adds 'on');
    harmless since no env var uses other spellings."""
    return (val or "").strip().lower() in ("true", "1", "yes", "on", "enabled")


# `_csv` itself is defined above, in the dual-mode import block (#535
# followups review, finding R2) — it needs a different definition per
# branch (import the canonical wizard.model.llm_rules.parse_csv vs. an
# inlined fallback), unlike every other helper here.


# ---------------------------------------------------------------------------
# Synthesized-entry factory
# ---------------------------------------------------------------------------

def looks_like_embedding(name: str) -> bool:
    """Heuristic for whether a model is an embedding model, used to classify
    models the curated catalog cannot speak for (live-discovered / custom).

    Every mainstream embedding family carries ``embed`` in its name
    (nomic-embed-text, mxbai-embed-large, qwen3-embedding, snowflake-arctic-embed,
    granite-embedding, text-embedding-3-*). We strip any ``provider/`` prefix and
    ``:tag`` suffix, then test for that token. No curated-catalog *content* model
    contains ``embed`` (guarded by ``test_no_catalog_content_model_looks_like_embedding``),
    so this never misclassifies a chat model as an embedding one.
    """
    bare = (name or "").rsplit("/", 1)[-1].split(":", 1)[0].lower()
    return "embed" in bare


def _synthesize(provider: str, name: str) -> CatalogEntry:
    """Create a minimal CatalogEntry for a model not in the curated catalog.

    Mirrors ``LIVE_DEFAULTS`` in the former sync-catalog.py: content=8,
    structured_content=5, vision/embeddings=0 — EXCEPT when the name looks like
    an embedding model (:func:`looks_like_embedding`), in which case it is
    synthesized as embeddings=1, content=0 so it is never offered as a chat /
    vision default. vision=0 is always conservative — we cannot infer vision
    capability from a model name alone.
    """
    is_embed = looks_like_embedding(name)
    return CatalogEntry(
        provider=provider,
        name=name,
        content=0 if is_embed else 8,
        structured_content=0 if is_embed else 5,
        vision=0,
        embeddings=1 if is_embed else 0,
        context_window=0,
        size_gb=None,
        description=f"Live-discovered/custom {provider} model (not in curated catalog)",
        default_active=False,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def active_models(
    env: Mapping[str, str],
    *,
    ollama_tags: Iterable[str] | None = None,
) -> list[CatalogEntry]:
    """Return the active catalog entries for the given env selection.

    Replicates the former sync-catalog.py's activation rules WITHOUT a DB (env is the
    persistent selection store). See module docstring for the full rule set.

    Args:
        env: A mapping of env var names to string values (e.g. os.environ or
            a dict from ``.env`` parsing). Missing keys are treated as empty
            strings (= "not set").
        ollama_tags: Optional list of model-tag strings returned by the
            Ollama ``/api/tags`` endpoint (host-side auto-import).  When
            provided, they are unioned into the Ollama active set.  Pass
            ``None`` (the default) to skip auto-import (e.g. container
            sources, tests without a live Ollama).

            Container vs. host source distinction: For container Ollama
            sources (``ollama-container-*``), pass ``ollama_tags=None``.
            Host ``/api/tags`` auto-import only applies to host-side sources
            (``ollama-localhost``) with ``OLLAMA_AUTO_IMPORT_LOCAL_MODELS=true``.
            The resolver does NOT gate on source type — it unions any tags
            it is given, so the caller is responsible for only passing tags
            for host-side sources.

    Returns:
        Ordered list of active CatalogEntry objects.  Order IS priority
        (see module docstring).  Empty list when nothing is enabled.
    """
    result: list[CatalogEntry] = []
    result.extend(_active_ollama(env, ollama_tags))
    for provider in CLOUD_PROVIDERS:
        result.extend(_active_cloud(env, provider))
    return result


def best(
    category: str,
    env: Mapping[str, str],
    *,
    ollama_tags: Iterable[str] | None = None,
) -> Optional[str]:
    """Return the litellm model-id of the highest-priority active model for
    the given capability category.

    Args:
        category: One of ``'content'``, ``'embeddings'``, ``'vision'``.
        env: Env var mapping (same as ``active_models``).
        ollama_tags: Optional Ollama auto-import tag list.

    Returns:
        The litellm model id string if a capable active model exists, else
        ``None``.

        Format:
          • Ollama models → ``'ollama/<name>'``
          • Cloud models  → bare ``name`` (matches litellm-init per-provider routing)
    """
    for entry in active_models(env, ollama_tags=ollama_tags):
        cap_value = getattr(entry, category, 0)
        if cap_value and cap_value > 0:
            if entry.provider == "ollama":
                return f"ollama/{entry.name}"
            return entry.name
    return None


def resolved_defaults(
    env: Mapping[str, str],
    *,
    ollama_tags: Iterable[str] | None = None,
) -> dict[str, str]:
    """Return the env-var overrides this resolver owns.

    Exactly two keys are returned:
      • ``LITELLM_DEFAULT_MODEL``  — best content-capable active model
      • ``LITELLM_VISION_MODEL``   — best vision-capable active model

    NOTE: ``LITELLM_EMBEDDING_MODEL`` is deliberately EXCLUDED.  Changing
    the embedding model changes the vector dimension, which would silently
    break the ``public.memory_facts.embedding vector(768)`` pgvector column
    in supabase-db.  That var stays a static explicit default in
    ``services/litellm/service.yml``.

    Args:
        env: Env var mapping.  Pass ``{}`` (empty dict) to get the
            template/default-config result — what ``.env.example`` should
            contain when no cloud keys are set.
        ollama_tags: Optional Ollama auto-import tag list.

    Returns:
        Dict with exactly the two keys above.  Values are empty strings when
        no capable active model exists (e.g. all providers disabled).
    """
    return {
        "LITELLM_DEFAULT_MODEL": best("content", env, ollama_tags=ollama_tags) or "",
        "LITELLM_VISION_MODEL": best("vision", env, ollama_tags=ollama_tags) or "",
    }


# ---------------------------------------------------------------------------
# Embedding dimension safety
# ---------------------------------------------------------------------------

#: The dimension the stack REQUIRES of its embedding model.  This is imposed by
#: the consumer, NOT the model: the backend ``public.memory_facts.embedding``
#: column is declared ``vector(768)`` in
#: ``services/supabase/db/scripts/14-backend-memory.sql``.  An embedding model
#: of any other dimension fails the memory INSERT at runtime inside Postgres
#: with a dimension-mismatch error and no traceback to the model choice.
#:
#: SINGLE SOURCE: this constant mirrors that DDL literal.  If the column width
#: ever changes, update both in lockstep.  Per-model output dimensions are NOT
#: hardcoded here — they live in the YAML catalogs' ``dim:`` field and are read
#: via :func:`dim_for_model_id`.
MEMORY_FACTS_EMBEDDING_DIM = 768


def dim_for_model_id(model_id: str | None) -> Optional[int]:
    """Return the declared output dimension for a LiteLLM model id, or ``None``.

    Looks the id up in the YAML catalogs (``utils.llm_catalog``) by reproducing
    the same id formatting :func:`best` uses — ``ollama/<name>`` for Ollama
    entries, the bare ``name`` for cloud providers — and returns that entry's
    ``dim`` field.  ``None`` for unknown ids, ids whose entry declares no
    ``dim`` (content/vision/live-scraped/custom models), or empty input.
    """
    if not model_id:
        return None
    target = model_id.strip()
    # Real LITELLM_EMBEDDING_MODEL values are canonical litellm-ids (ollama
    # prefixed, cloud bare), but tolerate a leading ``<provider>/`` so a
    # hand-set ``openai/text-embedding-3-large`` still resolves. Model names
    # never contain ``/`` (ollama uses ``:`` for tags), so the bare tail is safe.
    bare_target = target.rsplit("/", 1)[-1]
    for entry in llm_catalog.all_catalog_entries():
        entry_id = (
            f"ollama/{entry.name}" if entry.provider == "ollama" else entry.name
        )
        if target == entry_id or bare_target == entry.name:
            return entry.dim
    return None


def embedding_dim_warning(model: str | None) -> Optional[str]:
    """Return a human-readable warning when ``model``'s declared embedding
    dimension is not :data:`MEMORY_FACTS_EMBEDDING_DIM` (768).

    The wizard writes ``LITELLM_EMBEDDING_MODEL`` from the user's pick; a
    non-768-dim choice silently breaks the backend ``memory_facts vector(768)``
    pgvector inserts.  Callers surface this string as a non-fatal warning at
    ``.env``-write time (we warn rather than block — an operator who migrated
    the column may legitimately want a wider model).

    The dimension is read from the YAML catalog (:func:`dim_for_model_id`), so
    declaring ``dim:`` on a new embedding entry is all it takes to extend this
    guard.  Returns ``None`` for 768-dim models, empty/None input, and models
    whose dimension the catalog does not declare.
    """
    if not model:
        return None
    dim = dim_for_model_id(model)
    if dim is not None and dim != MEMORY_FACTS_EMBEDDING_DIM:
        return (
            f"LITELLM_EMBEDDING_MODEL='{model}' produces {dim}-dim embeddings, but the "
            f"backend memory_facts column is vector({MEMORY_FACTS_EMBEDDING_DIM}). "
            f"Memory writes will fail unless you migrate that column or choose a "
            f"{MEMORY_FACTS_EMBEDDING_DIM}-dim model (e.g. ollama/nomic-embed-text)."
        )
    return None


# ---------------------------------------------------------------------------
# Private activation helpers
# ---------------------------------------------------------------------------

def _active_ollama(
    env: Mapping[str, str],
    ollama_tags: Iterable[str] | None,
) -> list[CatalogEntry]:
    """Compute the active Ollama entries per the activation rules."""
    source = env.get("LLM_PROVIDER_SOURCE", "ollama-container-cpu").strip().lower()

    # Disabled / none → no ollama actives
    if not source.startswith("ollama-"):
        return []

    # Base name set: explicit user selection or catalog defaults
    user_models = _csv(env.get("OLLAMA_USER_MODELS", ""))
    if user_models:
        active_names: set[str] = set(user_models)
    else:
        active_names = set(llm_catalog.default_active_names("ollama"))

    # Union host-side auto-import tags (only meaningful for ollama-localhost)
    if ollama_tags is not None:
        active_names.update(str(t).strip() for t in ollama_tags if str(t).strip())

    # Union custom models
    custom = _csv(env.get("OLLAMA_CUSTOM_MODELS", ""))
    active_names.update(custom)

    # Build result: catalog-ordered entries first, then synthesized
    catalog_entries = llm_catalog.ollama_entries()
    result: list[CatalogEntry] = []
    seen: set[str] = set()

    for entry in catalog_entries:
        if entry.name in active_names:
            result.append(entry)
            seen.add(entry.name)

    # Synthesize entries for active names not in the catalog
    for name in sorted(active_names - seen):  # sorted for stable ordering
        result.append(_synthesize("ollama", name))

    return result


def _active_cloud(
    env: Mapping[str, str],
    provider,  # CloudProvider dataclass
) -> list[CatalogEntry]:
    """Compute the active entries for one cloud provider."""
    enabled = _truthy(env.get(provider.enabled_flag_var, ""))
    has_key = bool((env.get(provider.api_key_var, "") or "").strip())

    if not (enabled and has_key):
        return []

    user_models = _csv(env.get(provider.user_models_var, ""))
    if user_models:
        active_names: set[str] = set(user_models)
    else:
        active_names = set(llm_catalog.default_active_names(provider.key))

    catalog_entries = llm_catalog.cloud_entries(provider.key)
    result: list[CatalogEntry] = []
    seen: set[str] = set()

    for entry in catalog_entries:
        if entry.name in active_names:
            result.append(entry)
            seen.add(entry.name)

    # Synthesize entries for selected names not in the catalog
    for name in sorted(active_names - seen):
        result.append(_synthesize(provider.key, name))

    return result
