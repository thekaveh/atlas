"""Manifest ↔ imperative parity for `LIGHTRAG_RERANK_BINDING_HOST`.

Two declarations of the same env-var contract:

  1. **Manifest** — `services/lightrag/service.yml`
     `runtime_adaptive.lightrag.environment_adaptation.LIGHTRAG_RERANK_BINDING_HOST`
     declares ``""`` (the default: rerank adapter OFF).

  2. **Imperative** — `bootstrapper/services/service_config.py` emits the host
     at .env-render time: blank + ``LIGHTRAG_RERANK_BINDING=null`` by default,
     or the BACKEND rerank-adapter route when the operator opts in (#415).

Per `project_post_merge_env_staleness` memory: when these two drift, the
manifest looks correct in code review but the runtime value comes from the
imperative path — exactly the bug class that lost ~30 post-migration literals
before the fix shipped 2026-06-04.

The load-bearing invariant this guards: LightRAG rerank must NEVER be wired
directly at TEI (payload shapes differ). It may only route through the backend
adapter (``/lightrag/rerank``). The runtime permutations are covered by
`test_lightrag_tei_source_permutations`; this file guards the two source-level
declarations statically.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
LIGHTRAG_MANIFEST = REPO_ROOT / "services" / "lightrag" / "service.yml"
SERVICE_CONFIG = REPO_ROOT / "bootstrapper" / "services" / "service_config.py"


def _manifest_rerank_binding() -> str:
    data = yaml.safe_load(LIGHTRAG_MANIFEST.read_text(encoding="utf-8"))
    return (
        data["runtime_adaptive"]["lightrag"]["environment_adaptation"][
            "LIGHTRAG_RERANK_BINDING_HOST"
        ]
    )


def test_manifest_rerank_binding_host_default_stays_blank():
    """The manifest declares the DEFAULT (adapter off): a blank host. The real
    value is computed imperatively per LIGHTRAG_RERANK_ADAPTER_ENABLED + TEI.
    """
    value = _manifest_rerank_binding()
    assert value == "", (
        f"services/lightrag/service.yml::runtime_adaptive.lightrag"
        f".environment_adaptation.LIGHTRAG_RERANK_BINDING_HOST = {value!r}; "
        f"the default declaration must stay blank (adapter off by default)."
    )


def test_imperative_emitter_keeps_disabled_default_null_and_blank():
    """The default/disabled emission path still emits a blank host and the
    literal ``null`` binding (LightRAG crashes on an empty RERANK_BINDING)."""
    src = SERVICE_CONFIG.read_text(encoding="utf-8")
    assert "env_vars['LIGHTRAG_RERANK_BINDING_HOST'] = ''" in src
    assert "env_vars['LIGHTRAG_RERANK_BINDING'] = 'null'" in src


def test_imperative_emitter_never_wires_lightrag_directly_at_tei():
    """Every non-empty host the emitter assigns to LIGHTRAG_RERANK_BINDING_HOST
    must route through the backend adapter, never a ``tei-reranker`` host.

    Direct wiring is the original bug: LightRAG would POST {query, documents}
    at TEI's {query, texts} endpoint and 4xx/5xx at query time.
    """
    src = SERVICE_CONFIG.read_text(encoding="utf-8")
    assigned = re.findall(
        r"env_vars\['LIGHTRAG_RERANK_BINDING_HOST'\]\s*=\s*'([^']*)'", src
    )
    assert assigned, "expected at least one LIGHTRAG_RERANK_BINDING_HOST assignment"
    non_empty = [host for host in assigned if host]
    # There is exactly one non-empty host and it is the backend adapter route.
    assert non_empty == ["http://backend:8000/lightrag/rerank"], (
        f"unexpected LIGHTRAG_RERANK_BINDING_HOST value(s) {non_empty!r}; "
        f"rerank must route through the backend adapter, not directly at TEI."
    )
    for host in non_empty:
        assert "tei-reranker" not in host
        assert "/lightrag/rerank" in host
