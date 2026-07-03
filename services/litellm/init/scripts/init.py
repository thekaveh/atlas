#!/usr/bin/env python3
"""
services/litellm/init/scripts/init.py — provision the LiteLLM Postgres
database and render ``volumes/litellm/config.yaml`` from the active model
set computed by ``model_resolver`` (YAML catalogs + env vars).

Replaces the previous alpine + ensure-litellm-db.sh approach. The
existing shell script only handled DB creation; this Python script
also renders the config.yaml that LiteLLM reads at startup.

Order of operations:
  1. Wait for Postgres to be reachable.
  2. CREATE DATABASE for LiteLLM's own Prisma schema if not present
     (LiteLLM's recommended layout — separate logical DB on the same
     server as Supabase).
  3. Compute the active model set via ``model_resolver.active_models()``
     (reads YAML catalogs + env vars — no DB query needed).
  4. For host-side Ollama sources with ``OLLAMA_AUTO_IMPORT_LOCAL_MODELS``
     enabled, also fetch ``/api/tags`` from the upstream to union in any
     locally-pulled models not in the curated catalog.
  5. Render config.yaml with model_list entries built per-provider
     and the standard settings block.
  6. Write to ``/litellm-config/config.yaml`` (host bind mount →
     ``volumes/litellm/config.yaml``). The file is OVERWRITTEN every
     run — env vars / YAML catalogs are the editable surface.

The host-side stub generator
(``bootstrapper/utils/litellm_config_generator.py``) checks our
sentinel header before clobbering this file with a fresh stub, but
the asymmetry is intentional: this script is authoritative for the
real config, so it always writes; the host generator is the polite
one that defers when our output is present.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import yaml


def _load_shared_settings():
    """Import ``litellm_settings`` from the bind-mounted /catalog dir.

    Sibling mount of /scripts (see docker-compose.yml). Register in sys.modules before
    exec_module so dataclass / future-annotations machinery resolves
    cleanly.

    Fails loudly when the bind mount is misconfigured: a silent fallback
    that duplicates ``litellm_settings.base_settings()`` here would
    eventually drift, and a missing mount means the operator's
    docker-compose.yml needs fixing — surfacing it now is more honest
    than papering over it.
    """
    path = _catalog_dir() / "litellm_settings.py"
    if not path.exists():
        sys.exit(
            f"❌ {path} not found — the ``./bootstrapper/utils:/catalog:ro`` "
            f"bind mount is missing or misconfigured. Check the litellm-init "
            f"service block in docker-compose.yml."
        )
    spec = importlib.util.spec_from_file_location("litellm_settings", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["litellm_settings"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module.base_settings(os.environ)


# ─── env ──────────────────────────────────────────────────────────────

# DB credentials — used ONLY by ensure_litellm_db() (CREATE DATABASE).
# No longer used to query public.llms; the active model set now comes
# from model_resolver (YAML catalogs + env) instead.
PG_HOST = os.environ.get("PGHOST", "supabase-db")
PG_PORT = int(os.environ.get("PGPORT", "5432"))
PG_USER = os.environ.get("PGUSER", "postgres")
PG_PASSWORD = os.environ.get("PGPASSWORD", "")
LITELLM_DB_NAME = os.environ.get("LITELLM_DB_NAME", "litellm")

LITELLM_OLLAMA_UPSTREAM = os.environ.get("LITELLM_OLLAMA_UPSTREAM", "http://ollama:11434").strip()

# Hermes Agent — appended as a `hermes-agent` model_list entry when
# HERMES_SOURCE != disabled. Hermes is a *runtime* (programmable agent
# loop), not a model provider, so it's deliberately kept out of the
# llm_catalog YAML taxonomy and stitched in here. Open WebUI,
# n8n, backend, jupyterhub all see it for free via LiteLLM.
HERMES_SOURCE = os.environ.get("HERMES_SOURCE", "disabled").strip().lower()
HERMES_ENDPOINT = os.environ.get("HERMES_ENDPOINT", "").strip()

CONFIG_OUT = Path(os.environ.get("LITELLM_CONFIG_OUT", "/litellm-config/config.yaml"))


# ─── DB connection ────────────────────────────────────────────────────
# NOTE: psycopg2 is imported lazily inside ensure_litellm_db() so that
# tests (and the resolver path) can load this module without needing
# the psycopg2-binary package from the container image.

def connect(dbname: str, autocommit: bool = False, retries: int = 30, delay: float = 2.0):
    """Connect with retry. Used ONLY for CREATE DATABASE (ensure_litellm_db).

    litellm-init no longer queries ``public.llms``; the active model set
    now comes from ``model_resolver`` instead (see ``fetch_active_models``).
    """
    import psycopg2  # noqa: PLC0415 — lazy import; not needed by resolver path

    last: Exception | None = None
    for attempt in range(retries):
        try:
            conn = psycopg2.connect(
                host=PG_HOST, port=PG_PORT, dbname=dbname,
                user=PG_USER, password=PG_PASSWORD, connect_timeout=5,
            )
            conn.autocommit = autocommit
            return conn
        except psycopg2.Error as e:
            last = e
            print(f"  ↳ DB '{dbname}' not ready (attempt {attempt + 1}/{retries}): {e}", flush=True)
            time.sleep(delay)
    sys.exit(f"❌ Could not connect to Postgres '{dbname}' after {retries} attempts: {last}")


def ensure_litellm_db() -> None:
    """CREATE DATABASE for LiteLLM if it doesn't already exist.

    Connects to the maintenance ``postgres`` DB because CREATE
    DATABASE can't run from inside the database it creates.
    """
    print(f"litellm-init: ensuring database '{LITELLM_DB_NAME}' exists", flush=True)
    conn = connect("postgres", autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s;",
                (LITELLM_DB_NAME,),
            )
            if cur.fetchone():
                print(f"  ↳ '{LITELLM_DB_NAME}' already exists", flush=True)
                return
            # CREATE DATABASE doesn't accept parameterised identifiers — quote manually.
            # LITELLM_DB_NAME is operator-controlled (.env), not user input.
            quoted = LITELLM_DB_NAME.replace('"', '""')
            cur.execute(f'CREATE DATABASE "{quoted}";')
            print(f"  ↳ created '{LITELLM_DB_NAME}'", flush=True)
    finally:
        conn.close()


# ─── catalog module loader ─────────────────────────────────────────────

def _catalog_dir() -> Path:
    """Resolve the catalog directory containing the shared bootstrapper utils,
    and make it importable.

    Search order (mirrors llm_catalog._find_models_dir's philosophy):
      1. ``ATLAS_CATALOG_DIR`` env var — test override.
      2. ``/catalog`` — the container bind-mount target (normal runtime).

    The resolved directory is also inserted onto ``sys.path`` (idempotently).
    The loaders below exec-load modules — notably ``model_resolver`` — whose
    CONTAINER code path uses loose sibling imports (``import llm_catalog``,
    ``from cloud_providers import CLOUD_PROVIDERS``) because there is no
    ``utils`` package inside ``/catalog``. ``importlib.exec_module`` does NOT
    add the module's own directory to ``sys.path``, so without this those
    fallback imports raise ``ModuleNotFoundError`` and litellm-init aborts.
    (In the bootstrapper venv ``utils`` IS a package, so the loose fallback is
    never taken — which is why this only bit the container.)

    Exits loudly when neither location exists so that misconfigured compose
    bind mounts surface immediately rather than silently yielding wrong config.
    """
    env_dir = os.environ.get("ATLAS_CATALOG_DIR")
    if env_dir:
        catalog = Path(env_dir)
    elif Path("/catalog").is_dir():
        catalog = Path("/catalog")
    else:
        sys.exit(
            "❌ /catalog not found and ATLAS_CATALOG_DIR is not set — "
            "the ``./bootstrapper/utils:/catalog:ro`` bind mount is missing or "
            "misconfigured. Check the litellm-init service block in docker-compose.yml."
        )
    if catalog.is_dir() and str(catalog) not in sys.path:
        sys.path.insert(0, str(catalog))
    return catalog


def _load_catalog_module(name: str):
    """Import a module from the bind-mounted ``/catalog`` directory.

    Uses the same importlib pattern as ``_load_shared_settings`` — load the
    file at ``<catalog_dir>/<name>.py`` and register it in ``sys.modules`` so
    intra-module imports (e.g. ``model_resolver`` → ``llm_catalog``) resolve
    cleanly against the same instances.

    The catalog directory is resolved by ``_catalog_dir()``:
      • In the container it is ``/catalog`` (the bind mount).
      • In tests it is the path set by ``ATLAS_CATALOG_DIR`` (pointing at
        ``bootstrapper/utils``), so the same modules are exercised without
        needing a running container.

    Exits loudly when the catalog is misconfigured: a silent fallback would
    hide operator errors that must be fixed in compose.yml.
    """
    if name in sys.modules:
        return sys.modules[name]
    catalog = _catalog_dir()
    path = catalog / f"{name}.py"
    if not path.exists():
        sys.exit(
            f"❌ {path} not found — the ``./bootstrapper/utils:/catalog:ro`` "
            f"bind mount is missing or misconfigured. Check the litellm-init "
            f"service block in docker-compose.yml."
        )
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ─── config rendering ─────────────────────────────────────────────────

def _maybe_fetch_ollama_tags() -> list[str] | None:
    """For host-side Ollama sources with auto-import enabled, query the
    upstream ``/api/tags`` so host-pulled models (beyond the curated
    catalog) land in the config.

    Returns ``None`` (skip auto-import) for container sources or when
    ``OLLAMA_AUTO_IMPORT_LOCAL_MODELS`` is not truthy.  Gating rules:
      • ``LLM_PROVIDER_SOURCE`` must start with ``ollama-localhost``
      • ``OLLAMA_AUTO_IMPORT_LOCAL_MODELS`` must be truthy

    Uses ``/catalog/ollama_discovery.py`` (the same module the wizard's
    ollama_discovery use) so discovery behaviour is consistent.
    Returns an empty list (not None) on fetch failure — resolver treats
    an empty list the same as no auto-import.
    """
    source = os.environ.get("LLM_PROVIDER_SOURCE", "").strip().lower()
    if not source.startswith("ollama-localhost"):
        return None
    auto = os.environ.get("OLLAMA_AUTO_IMPORT_LOCAL_MODELS", "").strip().lower()
    if auto not in ("true", "1", "yes", "on", "enabled"):
        return None
    try:
        od = _load_catalog_module("ollama_discovery")
        tags = od.list_pulled_models(LITELLM_OLLAMA_UPSTREAM)
        print(
            f"  ↳ auto-import: fetched {len(tags)} tag(s) from "
            f"{LITELLM_OLLAMA_UPSTREAM}/api/tags",
            flush=True,
        )
        return tags
    except Exception as exc:  # noqa: BLE001
        print(
            f"  ⚠ auto-import: failed to fetch /api/tags from "
            f"{LITELLM_OLLAMA_UPSTREAM}: {exc} — continuing without auto-import tags",
            flush=True,
        )
        return []


def fetch_active_models() -> list[tuple[str, str]]:
    """Return [(provider, name)] for every active model, computed from
    the YAML catalogs + env vars via ``model_resolver`` — no DB query.

    Previously this function queried ``SELECT provider, name FROM
    public.llms WHERE active = true``.  The active model set is now
    derived entirely from the YAML catalogs (``services/ollama/models.yaml``,
    ``services/litellm/models.yaml``) and the env vars the wizard writes
    (``LLM_PROVIDER_SOURCE``, ``OLLAMA_USER_MODELS``, ``LITELLM_*_ENABLED``,
    cloud ``*_API_KEY``, etc.), making litellm-init DB-free for config
    rendering.
    """
    mr = _load_catalog_module("model_resolver")
    tags = _maybe_fetch_ollama_tags()
    entries = mr.active_models(os.environ, ollama_tags=tags)
    return [(e.provider, e.name) for e in entries]


def render_model_list(active_rows: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Build LiteLLM's ``model_list`` from active model rows.

    Per-provider routing rules:
      • ollama       → model: ollama/{name}, api_base: $LITELLM_OLLAMA_UPSTREAM
      • openai       → model: {name}, api_key: os.environ/OPENAI_API_KEY
      • anthropic    → model: anthropic/{name}, api_key: os.environ/ANTHROPIC_API_KEY
      • openrouter   → model: {name}, api_key: os.environ/OPENROUTER_API_KEY
                       (names are already prefixed ``openrouter/...`` in the catalog)
    """
    out: list[dict[str, Any]] = []
    for provider, name in active_rows:
        if provider == "ollama":
            # Register Ollama models under BOTH names so every consumer
            # path works:
            #   • ``ollama/{name}`` — historical prefixed form used by
            #     backend's ``LITELLM_EMBEDDING_MODEL``, weaviate-init's
            #     ``/shared/weaviate-config.env``, and any code that
            #     uses LiteLLM's path-style routing convention.
            #   • bare ``{name}`` — what clients like Hermes Agent send
            #     after their internal prefix-stripper treats ``ollama/``
            #     as a provider hint and removes it before forwarding.
            #     Cloud providers (openai, anthropic, openrouter) are
            #     already registered under bare names in the branches
            #     below; this keeps Ollama consistent with them.
            # Both entries point at the same upstream so latency / spend
            # tracking aren't doubled — LiteLLM just sees two model_name
            # aliases for the same backing config.
            #
            # Adapter selection — ``ollama_chat`` for chat models,
            # ``ollama`` for embeddings:
            #   • ``ollama/X``      → LiteLLM hits Ollama's
            #     /api/generate (single-prompt completion). Tool calls
            #     do not work, multi-turn chat history is flattened,
            #     and the Ollama ``think`` parameter is silently
            #     dropped — so any thinking-capable model (qwen3,
            #     gpt-oss, deepseek-r1) gets cut off mid-``<think>``
            #     and returns an empty ``content`` field via this
            #     path. This breaks Hermes, Open WebUI, n8n,
            #     jupyterhub, and backend.
            #   • ``ollama_chat/X`` → LiteLLM hits Ollama's /api/chat
            #     (real OpenAI-shaped chat completions). Tool calls,
            #     chat history, vision payloads, and the ``think``
            #     param all flow through correctly. **This is what
            #     every chat consumer expects.**
            #
            # Embedding models, however, are SERVED by /v1/embeddings
            # — which LiteLLM only routes via the ``ollama/`` provider
            # (``ollama_chat/`` rejects embedding requests with
            # ``Unmapped LLM provider for this endpoint``). So:
            # embeddings get ``ollama/``, chat models get
            # ``ollama_chat/``. Detection is name-based ("embed" substring).
            # The curated catalog embeddings all carry it (nomic-embed-text,
            # qwen3-embedding:0.6b), so defaults are correct. CAVEAT: a
            # user-added embedding model WITHOUT "embed" in its name (e.g.
            # ``bge-m3``, ``e5-large``) would be mis-registered as a chat model
            # and fail /v1/embeddings — the resolver discards role info, so the
            # name is the only signal here. See services/litellm/README.md →
            # "Ollama adapter choice".
            #
            # ``think: false`` is set on chat entries only — it
            # defaults thinking-capable models to write their answer
            # straight into ``content`` instead of the side-channel
            # ``reasoning`` field. Non-thinking models ignore the
            # param. Consumers that explicitly want the thinking
            # trace can re-enable per-request by sending
            # ``"think": true`` in their chat-completions body.
            is_embedding = "embed" in name.lower()
            if is_embedding:
                ollama_params = {
                    "model": f"ollama/{name}",
                    "api_base": LITELLM_OLLAMA_UPSTREAM,
                }
            else:
                ollama_params = {
                    "model": f"ollama_chat/{name}",
                    "api_base": LITELLM_OLLAMA_UPSTREAM,
                    "think": False,
                }
            out.append({
                "model_name": f"ollama/{name}",
                "litellm_params": dict(ollama_params),
            })
            out.append({
                "model_name": name,
                "litellm_params": dict(ollama_params),
            })
            continue
        if provider == "openai":
            entry = {
                "model_name": name,
                "litellm_params": {
                    "model": name,
                    "api_key": "os.environ/OPENAI_API_KEY",
                },
            }
        elif provider == "anthropic":
            entry = {
                "model_name": name,
                "litellm_params": {
                    "model": f"anthropic/{name}",
                    "api_key": "os.environ/ANTHROPIC_API_KEY",
                },
            }
        elif provider == "openrouter":
            entry = {
                "model_name": name,
                "litellm_params": {
                    "model": name,
                    "api_key": "os.environ/OPENROUTER_API_KEY",
                },
            }
        else:
            print(f"  ⚠ skipping unknown provider '{provider}' for model '{name}'", flush=True)
            continue
        out.append(entry)
    return out


def hermes_model_entry() -> dict[str, Any] | None:
    """Return a model_list entry for `hermes-agent` when Hermes is alive.

    Returns None when HERMES_SOURCE is disabled OR HERMES_ENDPOINT is
    empty (which the bootstrapper sets for disabled / unresolved
    sources). Uses LiteLLM's openai-compatible passthrough — Hermes
    speaks the same /v1/chat/completions surface as OpenAI, so
    `model: openai/<name>` + `api_base` + `api_key` is sufficient.

    The leading `openai/` prefix is LiteLLM-specific routing syntax —
    Hermes itself sees the request as a normal chat-completion call.
    """
    if HERMES_SOURCE == "disabled" or not HERMES_ENDPOINT:
        return None
    # Trim trailing slash, append /v1 if not already present.
    base = HERMES_ENDPOINT.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return {
        "model_name": "hermes-agent",
        "litellm_params": {
            "model": "openai/hermes-agent",
            "api_base": base,
            "api_key": "os.environ/HERMES_API_KEY",
        },
    }


def lightrag_model_entry() -> dict[str, Any] | None:
    """Return a model_list entry for `lightrag` when LightRAG is enabled.

    Uses the `ollama_chat/` adapter: it POSTs to `<api_base>/api/chat`,
    which matches LightRAG's Ollama-compatible shim path exactly. The
    earlier `openai/` adapter targeted `<api_base>/chat/completions`, a
    path LightRAG doesn't expose — caused 404 on every query.
    """
    # Normalize like HERMES_SOURCE above — a padded/cased value must
    # not sneak a lightrag model row into /v1/models.
    if os.environ.get("LIGHTRAG_SOURCE", "disabled").strip().lower() == "disabled":
        return None
    endpoint = os.environ.get("LIGHTRAG_ENDPOINT", "")
    if not endpoint:
        return None
    return {
        "model_name": "lightrag",
        "litellm_params": {
            "model": "ollama_chat/lightrag",
            "api_base": endpoint.rstrip("/"),
            # Literal embed (not `os.environ/LIGHTRAG_API_KEY`): the key
            # is auto-generated once by generate_and_update_lightrag_api_key
            # (idempotent, never force-rotated). Rotation would require a
            # re-seed anyway, so a runtime env-directive buys nothing.
            "api_key": os.environ.get("LIGHTRAG_API_KEY", ""),
        },
        "model_info": {
            "mode": "chat",
            "description": (
                "LightRAG graph-augmented RAG. Encode query mode as "
                "system prompt prefix /hybrid|/local|/global|/naive|/mix."
            ),
        },
    }


def render_config(active_rows: list[tuple[str, str]]) -> dict[str, Any]:
    """Build the complete config.yaml dict (model_list + settings).
    The settings half comes from bootstrapper/utils/litellm_settings.py
    via the bind-mounted /catalog dir — single source of truth shared
    with the bootstrapper's host-side stub writer.

    Hermes Agent and LightRAG are stitched in here (they are runtime
    services, not catalog models) — see hermes_model_entry() and
    lightrag_model_entry() for details.
    """
    model_list = render_model_list(active_rows)
    hermes_entry = hermes_model_entry()
    if hermes_entry is not None:
        model_list.append(hermes_entry)
        print(
            f"  ↳ appended hermes-agent entry → {hermes_entry['litellm_params']['api_base']}",
            flush=True,
        )
    lightrag_entry = lightrag_model_entry()
    if lightrag_entry is not None:
        model_list.append(lightrag_entry)
        print(
            f"  ↳ appended lightrag entry → {lightrag_entry['litellm_params']['api_base']}",
            flush=True,
        )
    return {
        "model_list": model_list,
        **_load_shared_settings(),
    }


def write_config(config: dict[str, Any]) -> None:
    # Atomic write: a crash partway through the body would leave the
    # sentinel header on disk without a model_list. The bootstrapper's
    # stub writer keys off the sentinel to decide whether to preserve
    # the file, so a torn write would persist a broken config across
    # subsequent ./start.sh runs. Write to a tmp sibling first, then
    # os.replace(): on POSIX the rename is atomic within the same
    # filesystem (the bind mount lives on a single host fs).
    CONFIG_OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CONFIG_OUT.with_suffix(CONFIG_OUT.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        # The "Generated by litellm-init" header line is also our
        # managed-by sentinel — bootstrapper/utils/litellm_config_generator.py
        # checks for it before deciding whether to overwrite the file
        # with a stub.
        fh.write("# Generated by litellm-init/init.py from YAML catalogs via model_resolver.\n")
        fh.write("# DO NOT edit by hand — change models via the wizard or\n")
        fh.write("# OLLAMA_USER_MODELS / LITELLM_*_ENABLED env vars in .env.\n")
        fh.write("# This file is OVERWRITTEN on every ./start.sh.\n\n")
        yaml.safe_dump(config, fh, sort_keys=False, default_flow_style=False)
    os.replace(tmp_path, CONFIG_OUT)


# ─── main ─────────────────────────────────────────────────────────────

def main() -> int:
    print("litellm-init: starting", flush=True)
    print(
        f"  ↳ env: PGHOST={PG_HOST} LITELLM_DB={LITELLM_DB_NAME} "
        f"OLLAMA_UPSTREAM={LITELLM_OLLAMA_UPSTREAM} CONFIG_OUT={CONFIG_OUT} "
        f"LLM_SOURCE={os.environ.get('LLM_PROVIDER_SOURCE', '(unset)')}",
        flush=True,
    )
    try:
        ensure_litellm_db()
        rows = fetch_active_models()
        if rows:
            first = f"{rows[0][0]}/{rows[0][1]}"
            last = f"{rows[-1][0]}/{rows[-1][1]}"
            print(
                f"  ↳ {len(rows)} active model(s) from YAML catalogs "
                f"(first={first}, last={last})",
                flush=True,
            )
        else:
            print(
                "  ⚠ 0 active models resolved — LiteLLM will start "
                "with an empty model_list. Check LLM_PROVIDER_SOURCE, "
                "OLLAMA_USER_MODELS, and LITELLM_*_ENABLED in .env.",
                flush=True,
            )
        config = render_config(rows)
        write_config(config)
        n_entries = len(config.get("model_list", []))
        print(
            f"  ↳ wrote {CONFIG_OUT} ({n_entries} model_list entries "
            f"from {len(rows)} active model(s))",
            flush=True,
        )
    except Exception as exc:
        print(f"❌ litellm-init failed: {exc}", flush=True)
        traceback.print_exc()
        return 1
    print("litellm-init: done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
