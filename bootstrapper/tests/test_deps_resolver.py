"""Tests for bootstrapper.docs.deps_resolver — data-flow model."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

SERVICES_DIR = REPO_ROOT / "services"


def test_dep_graph_focus_is_service_name():
    from docs.deps_resolver import build_graph
    g = build_graph("hermes", SERVICES_DIR)
    assert g.focus == "hermes"
    assert g.category == "agents"


def test_upstream_comes_from_data_flow_calls():
    """build_graph reads focus.data_flow.calls and renders it as the upstream lane."""
    from docs.deps_resolver import build_graph
    g = build_graph("hermes", SERVICES_DIR)
    upstream_others = {e.other for e in g.upstream}
    # Hermes calls litellm, stt-provider, tts-provider, comfyui, searxng (per data_flow.calls)
    assert upstream_others >= {"litellm", "comfyui", "searxng"}


def test_downstream_comes_from_inverse_data_flow_calls():
    """A service appears downstream of focus if any other manifest's
    data_flow.calls includes focus."""
    from docs.deps_resolver import build_graph
    g = build_graph("litellm", SERVICES_DIR)
    downstream_others = {e.other for e in g.downstream}
    # backend, n8n, hermes, weaviate, jupyterhub, etc. all call litellm
    assert downstream_others >= {"backend", "n8n", "weaviate", "jupyterhub", "open-webui"}


def test_bidirectional_collapse_hermes_litellm():
    """litellm.data_flow.calls includes hermes; hermes.data_flow.calls includes litellm.
    Both edges must be marked bidirectional."""
    from docs.deps_resolver import build_graph
    g = build_graph("hermes", SERVICES_DIR)
    litellm_edges = [e for e in g.upstream if e.other == "litellm"]
    assert litellm_edges, "expected litellm in hermes upstream"
    assert litellm_edges[0].bidirectional


def test_dep_edge_has_no_kind_field():
    """The simpler DepEdge no longer carries kind/mechanism/failure_mode."""
    from docs.deps_resolver import DepEdge
    fields = set(DepEdge.__dataclass_fields__.keys())
    assert "kind" not in fields
    assert "mechanism" not in fields
    assert "failure_mode" not in fields


def test_dep_graph_byte_deterministic():
    from docs.deps_resolver import build_graph
    g1 = build_graph("hermes", SERVICES_DIR)
    g2 = build_graph("hermes", SERVICES_DIR)
    assert g1 == g2


def test_empty_data_flow_calls_means_empty_upstream():
    """minio has data_flow.calls: [] — so minio's upstream is empty."""
    from docs.deps_resolver import build_graph
    g = build_graph("minio", SERVICES_DIR)
    assert g.upstream == ()


def test_kong_fronted_services_in_upstream():
    """Kong's data_flow.calls lists services it fronts. Applying the
    universal convention 'focus.data_flow.calls = upstream lane' consistently,
    these services appear in Kong's UPSTREAM lane (Kong calls/routes to them).
    Kong's downstream callers: prometheus (scrapes Kong's Status API for the
    gateway metrics dashboard) and cloudflared (proxies inbound traffic to
    Kong's internal port)."""
    from docs.deps_resolver import build_graph
    g = build_graph("kong", SERVICES_DIR)
    assert len(g.upstream) > 10
    # Downstream: Prometheus + cloudflared.
    downstream_others = {e.other for e in g.downstream}
    assert downstream_others >= {"prometheus", "cloudflared"}


def test_aggregate_doc_folder_unions_underlying_manifests():
    """build_doc_graph('stt-provider') unions parakeet + speaches data_flow.calls."""
    from docs.deps_resolver import build_doc_graph
    g = build_doc_graph("stt-provider", SERVICES_DIR)
    # parakeet + speaches data_flow.calls: [] each; so stt-provider upstream is empty
    assert g.upstream == ()
    # but stt-provider IS called by hermes, n8n, backend, etc. — those should be downstream
    downstream_others = {e.other for e in g.downstream}
    assert "hermes" in downstream_others


def test_doc_folder_to_manifests_mapping_unchanged():
    """A.7 mapping table is unchanged."""
    from docs.deps_resolver import doc_folder_to_manifests
    assert doc_folder_to_manifests("hermes") == ("hermes",)
    assert set(doc_folder_to_manifests("stt-provider")) >= {"parakeet", "speaches"}
    assert doc_folder_to_manifests("multi2vec-clip") == ()


def test_cloud_providers_renders_as_edge_target():
    """litellm.data_flow.calls includes cloud-providers (a virtual manifest, not a doc folder).
    The resolver must still produce a DepEdge for it."""
    from docs.deps_resolver import build_graph
    g = build_graph("litellm", SERVICES_DIR)
    upstream_others = {e.other for e in g.upstream}
    assert "cloud-providers" in upstream_others
    # And it should be category-tagged (llm)
    cp_edge = next(e for e in g.upstream if e.other == "cloud-providers")
    assert cp_edge.other_category == "llm"


def test_aggregate_member_downstream_includes_role_consumers():
    """Regression: an aggregate MEMBER (parakeet/chatterbox/speaches/docling)
    is consumed via its role doc-folder name — kong/n8n/open-webui/hermes call
    `stt-provider`/`tts-provider`, not `parakeet`. build_doc_graph must fold the
    focus's containing doc folder into the downstream-match keys, otherwise the
    member rendered a misleading "No downstream consumers". (deps_resolver.py
    line ~168.)"""
    from docs.deps_resolver import build_doc_graph

    for member, expected in [
        ("parakeet", {"kong", "n8n", "open-webui", "hermes"}),
        ("chatterbox", {"kong", "n8n", "open-webui", "hermes"}),
        ("docling", {"kong", "n8n", "docling-lightrag-adapter"}),
    ]:
        g = build_doc_graph(member, SERVICES_DIR)
        downstream = {e.other for e in g.downstream}
        assert expected <= downstream, (
            f"{member} downstream {downstream} missing {expected - downstream}"
        )
        # The focus must never appear as its own consumer (self-loop guard).
        assert member not in downstream


def test_lightrag_document_flow_traverses_the_isolated_adapter():
    from docs.deps_resolver import build_graph

    lightrag = build_graph("lightrag", SERVICES_DIR)
    assert "docling-lightrag-adapter" in {edge.other for edge in lightrag.upstream}

    adapter = build_graph("docling-lightrag-adapter", SERVICES_DIR)
    assert "docling" in {edge.other for edge in adapter.upstream}
