"""
End-to-end integration tests for the Ollama → LiteLLM model registration flow.

These tests query the **actual running stack** (host Ollama + LiteLLM
proxy) and assert the user-facing invariants of the current architecture:

  * Every model on the host's Ollama appears in LiteLLM's
    ``/v1/models`` response. Under the current flow, ``litellm-init``
    (``services/litellm/init/scripts/init.py``) computes the active
    model set from the YAML catalogs (``services/ollama/models.yaml``,
    ``services/litellm/models.yaml``) and env vars via
    ``bootstrapper/utils/model_resolver.py``. When
    ``LLM_PROVIDER_SOURCE=ollama-localhost`` and
    ``OLLAMA_AUTO_IMPORT_LOCAL_MODELS=true``, ``litellm-init`` also
    queries the host's ``/api/tags`` via
    ``bootstrapper/utils/ollama_discovery.py`` and unions those locally-
    pulled models into the rendered ``config.yaml``. There is no DB /
    ``public.llms`` table involved — the YAML catalogs + env are the
    sole source of truth.

  * The wizard's options_provider, fed against the live Ollama,
    produces a PromptOption list where ``pulled_variants`` for each
    family matches what /api/tags actually returns. Covers the
    screenshot bug where per-leaf status didn't reflect host state.

Tests skip cleanly when the stack isn't up — they're meant for
``./start.sh``-after development cycles, not CI machines without
Docker.

Network / timing:
  * The /api/tags fetch uses a 3s timeout; failures count as "stack
    not up" and the suite skips rather than failing flakily.
  * Tests assume ``LLM_PROVIDER_SOURCE=ollama-localhost`` — the only
    mode where /api/tags is the source of truth. Container mode is
    populated by ollama-pull and the assertions don't apply.
"""

from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = REPO_ROOT / ".env"


# ────────────────────────────────────────────────────────────────────────────
# Helpers — surface the live state of the stack so tests skip cleanly when
# the relevant services aren't reachable.
# ────────────────────────────────────────────────────────────────────────────


def _read_env_var(name: str) -> str:
    """Read ``name`` from the repo's ``.env`` (not the test process's
    environment, which is empty by design in pytest). Returns ``""``
    if missing — caller treats that as "stack not configured"."""
    if not ENV_FILE.is_file():
        return ""
    pattern = re.compile(rf"^{re.escape(name)}=(.*)$", re.MULTILINE)
    match = pattern.search(ENV_FILE.read_text(encoding="utf-8"))
    if not match:
        return ""
    return match.group(1).strip().strip('"').strip("'")


def _http_get_json(url: str, *, headers=None, timeout: float = 3.0):
    """Tiny urllib wrapper that returns parsed JSON or None on any
    failure (unreachable, bad JSON, timeout). Centralised so the
    skip / fail boundary is uniform across the test cases below."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError):
        return None
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return None


def _ollama_upstream_url() -> str:
    """Return the URL that litellm-init / the wizard would query for
    /api/tags. Localhost mode is hardcoded to 11434 in the wizard;
    container and `none` sources return empty (no host-side upstream
    to probe)."""
    src = _read_env_var("LLM_PROVIDER_SOURCE")
    if "localhost" in src:
        return "http://localhost:11434"
    return ""


def _query_host_ollama_tags() -> list[str] | None:
    """Return host /api/tags model names, or None if unreachable
    (no upstream URL configured, network failure, etc.)."""
    upstream = _ollama_upstream_url()
    if not upstream:
        return None
    data = _http_get_json(f"{upstream.rstrip('/')}/api/tags")
    if not isinstance(data, dict):
        return None
    models = data.get("models")
    if not isinstance(models, list):
        return None
    out: list[str] = []
    for entry in models:
        name = (entry or {}).get("name") if isinstance(entry, dict) else None
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
    return out


def _query_litellm_models() -> list[str] | None:
    """Return LiteLLM /v1/models IDs, or None when the proxy isn't
    reachable. Reads the master key from .env for auth."""
    key = _read_env_var("LITELLM_MASTER_KEY")
    port = _read_env_var("LITELLM_PORT") or "63012"
    if not key:
        return None
    data = _http_get_json(
        f"http://localhost:{port}/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    if not isinstance(data, dict):
        return None
    rows = data.get("data")
    if not isinstance(rows, list):
        return None
    return [
        r["id"] for r in rows
        if isinstance(r, dict) and isinstance(r.get("id"), str)
    ]


# Module-level fixture-style skip: every test in the file needs both
# endpoints up. Per-test reasons make pytest output specific.
def _skip_unless_stack_up():
    host_tags = _query_host_ollama_tags()
    if host_tags is None:
        pytest.skip(
            "host Ollama unreachable at the configured upstream — "
            "stack not running or LLM_PROVIDER_SOURCE != ollama-localhost"
        )
    litellm_models = _query_litellm_models()
    if litellm_models is None:
        pytest.skip(
            "LiteLLM proxy unreachable on localhost:LITELLM_PORT — "
            "stack not up, or LITELLM_MASTER_KEY missing from .env"
        )
    return host_tags, litellm_models


# ────────────────────────────────────────────────────────────────────────────
# Test 1: LiteLLM catalog must contain every host Ollama model
# ────────────────────────────────────────────────────────────────────────────


def test_every_host_ollama_model_is_published_by_litellm():
    """The user's concrete bug: a model pulled on the host
    (``a host-pulled custom model tag``) didn't appear in LiteLLM's
    ``/v1/models``. Under the current flow, ``litellm-init`` fetches
    host ``/api/tags`` via ``ollama_discovery.list_pulled_models()``
    (when ``OLLAMA_AUTO_IMPORT_LOCAL_MODELS=true``) and includes those
    models in the rendered ``config.yaml``, so every host tag must show
    up in LiteLLM as ``ollama/<name>`` AND ``<name>`` (the bare alias
    litellm-init publishes for backward compat).

    The test compares the SET of names, so the order LiteLLM
    publishes them in (sorted by catalog vs by /api/tags) doesn't
    matter.
    """
    host_tags, litellm_models = _skip_unless_stack_up()

    # The wizard / Ollama publishes some models without a tag
    # (``nomic-embed-text``) and some with (``qwen3-embedding:0.6b``).
    # LiteLLM emits both ``ollama/X`` and bare ``X`` aliases for each.
    # We need to match each host tag against EITHER alias form.
    litellm_set = set(litellm_models)
    missing: list[str] = []
    for host_name in host_tags:
        prefixed = f"ollama/{host_name}"
        if prefixed not in litellm_set and host_name not in litellm_set:
            missing.append(host_name)

    assert not missing, (
        f"LiteLLM's /v1/models does not include every host Ollama "
        f"model. Missing: {sorted(missing)}.\n\n"
        f"This usually means OLLAMA_AUTO_IMPORT_LOCAL_MODELS=false in "
        f".env, or litellm-init couldn't reach the host's /api/tags "
        f"at boot (check `docker logs atlas-litellm-init`).\n\n"
        f"Host /api/tags returned: {sorted(host_tags)}\n"
        f"LiteLLM /v1/models returned: {sorted(litellm_set)}"
    )


def test_litellm_doesnt_advertise_phantom_ollama_models():
    """Inverse of the above: every Ollama-prefixed model in LiteLLM
    should correspond to a real host Ollama tag, OR be an entry
    explicitly listed in OLLAMA_USER_MODELS / OLLAMA_CUSTOM_MODELS
    (the operator-curated escape hatches).

    This guards against drift in the other direction: if the wizard
    once selected a model and the operator later deleted it from the
    host, LiteLLM shouldn't keep advertising it. Under the YAML flow,
    model_resolver computes the active set from the YAML catalogs +
    env; OLLAMA_AUTO_IMPORT_LOCAL_MODELS=true unions in /api/tags at
    litellm-init time. A stale model would persist only if it's still
    in OLLAMA_USER_MODELS or OLLAMA_CUSTOM_MODELS (env-level overrides
    the operator controls) — there is no DB table to clean up.
    The test is informational — it doesn't fail on stale rows but
    surfaces them in the pytest output for the operator to triage.
    """
    host_tags, litellm_models = _skip_unless_stack_up()

    # An entry counts as "an Ollama model in LiteLLM" if either:
    #   (a) it has an explicit ``ollama/`` prefix, OR
    #   (b) it's a bare alias whose ``ollama/<name>`` dual-alias is ALSO
    #       present (per LiteLLM's dual-alias convention for Ollama —
    #       see reference_litellm_quirks memory).
    # Bare aliases without a matching ``ollama/<name>`` are cloud-provider
    # entries (openai, anthropic, openrouter) and don't belong here.
    explicit_ollama = {
        m[len("ollama/"):] for m in litellm_models if m.startswith("ollama/")
    }
    bare_aliases = {m for m in litellm_models if "/" not in m}
    bare_ollama_dual = bare_aliases & explicit_ollama
    ollama_entries = explicit_ollama | bare_ollama_dual
    ollama_entries.discard("hermes-agent")  # the passthrough route

    declared_user = set(
        s.strip() for s in
        (_read_env_var("OLLAMA_USER_MODELS").split(","))
        if s.strip()
    )
    declared_custom = set(
        s.strip() for s in
        (_read_env_var("OLLAMA_CUSTOM_MODELS").split(","))
        if s.strip()
    )
    host_set = set(host_tags)

    # An Ollama model in LiteLLM is "explained" if it's on the host,
    # OR a wizard/operator override declares it (those land as rows
    # the operator owns).
    explained = host_set | declared_user | declared_custom
    # Also accept names that differ from host_tags only by the
    # implicit ``:latest`` suffix — LiteLLM's bare alias drops the
    # tag for some embedding models.
    explained_with_latest = explained | {
        n.split(":", 1)[0] for n in host_set
    } | {f"{n}:latest" for n in explained}

    unexplained = sorted(ollama_entries - explained_with_latest)
    if unexplained:
        pytest.fail(
            f"LiteLLM advertises Ollama models that are neither on the "
            f"host nor declared in OLLAMA_USER_MODELS / "
            f"OLLAMA_CUSTOM_MODELS — stale model_resolver entries:\n  "
            f"{', '.join(unexplained)}\n\n"
            f"A phantom Ollama model in /v1/models means it isn't "
            f"present on the host (/api/tags) AND isn't listed in "
            f"OLLAMA_USER_MODELS or OLLAMA_CUSTOM_MODELS — litellm-init's "
            f"active-set computation (model_resolver) included a stale "
            f"name from the YAML catalogs or env. Resolve by re-running "
            f"the wizard (which rewrites .env) or updating "
            f"OLLAMA_USER_MODELS in .env to remove the stale name."
        )


# ────────────────────────────────────────────────────────────────────────────
# Test 2: the wizard's options_provider, run against the live host
# ────────────────────────────────────────────────────────────────────────────


def test_wizard_options_provider_pulled_variants_matches_live_ollama():
    """Run the wizard's actual ``options_provider`` (no mocks) against
    the live host Ollama and the live ollama.com/library scrape, and
    assert that ``pulled_variants`` on each PromptOption agrees with
    what /api/tags reports.

    This is the wizard side of the same invariant the other test
    enforces on the LiteLLM side — together they ensure the wizard
    UI tells the same story as the rendered catalog. If they diverge,
    the wizard would either pre-check models that don't exist or fail
    to pre-check models that do.

    Tagged as a ``slow`` test because it makes two live HTTP calls
    (Ollama /api/tags + ollama.com/library scrape, ~5s on a cold run).
    Skips when the stack isn't up.
    """
    host_tags, _ = _skip_unless_stack_up()
    host_set = set(host_tags)

    # Import lazily so the import-time library scrape doesn't fire
    # during collection of skipped tests.
    from wizard.llm_steps import build_ollama_steps, OLLAMA_MODELS_TITLE

    steps = build_ollama_steps(
        env_vars={"LLM_PROVIDER_SOURCE": "ollama-localhost"},
        warn=lambda _msg: None,
    )
    multistep = next(s for s in steps if s.title == OLLAMA_MODELS_TITLE)
    opts = multistep.options_provider({})
    assert opts, "options_provider returned no options — wizard would be empty"

    # For each option, compute the names its ``value`` + ``pulled_variants``
    # claim are on the host, and assert that set equals the actual host
    # tags grouped by family.
    claimed_per_family: dict[str, set[str]] = {}
    for opt in opts:
        if not opt.pulled_variants:
            continue
        claimed_per_family.setdefault(opt.value, set()).update(
            f"{opt.value}:{t}" for t in opt.pulled_variants
        )

    # Bucket-1 (flat) entries: their value itself is a full ``family:tag``
    # name that should appear in host_set.
    for opt in opts:
        if ":" in opt.value and "pulled" in opt.badges and not opt.sizes:
            claimed_per_family.setdefault("_bucket1", set()).add(opt.value)

    all_claimed = {n for s in claimed_per_family.values() for n in s}

    # The wizard's claim should equal the host's reality — every host
    # tag is claimed by EITHER a family's pulled_variants entry OR a
    # bucket-1 flat option.
    missing_from_wizard = sorted(host_set - all_claimed)
    assert not missing_from_wizard, (
        f"The wizard's options_provider didn't surface these host "
        f"Ollama models as [pulled]:\n  "
        f"{', '.join(missing_from_wizard)}\n\n"
        f"Either family.pulled_variants is missing the bare tag OR "
        f"the bucket-1 flat option for a non-library family is "
        f"absent. Either way the wizard's UI will lie to the user "
        f"(unchecked / [library] when actually on the host)."
    )

    fabricated_by_wizard = sorted(all_claimed - host_set)
    assert not fabricated_by_wizard, (
        f"The wizard claims these models are on the host but "
        f"/api/tags disagrees:\n  "
        f"{', '.join(fabricated_by_wizard)}\n\n"
        f"This usually means a stale variant cache leaked across "
        f"tests or a library entry's `sizes` tuple includes a tag "
        f"that doesn't exist on this host."
    )
