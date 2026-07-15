"""Tests for the LightRAG → TEI rerank adapter (#415).

Sync tests only (no pytest-asyncio in the backend CI venv). The translation
logic is driven directly against ``rerank_via_tei`` with a faked ``httpx.Client``;
the route/auth/status-mapping is driven through the FastAPI TestClient.
"""

from __future__ import annotations

import os

import httpx
import pytest

import lightrag_rerank_adapter as adapter
from lightrag_rerank_adapter import (
    RerankAdapterDependencyError,
    RerankAdapterRequest,
    RerankAdapterTimeoutError,
    RerankAdapterUpstreamError,
    rerank_via_tei,
)


class _FakeResponse:
    def __init__(self, status_code, payload=None, json_error=False):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not valid json")
        return self._payload


def _install_client(monkeypatch, *, resp=None, exc=None, sink=None):
    """Replace lightrag_rerank_adapter.httpx.Client with a fake.

    ``sink`` (a dict) captures the URL + json body the adapter POSTs so a test
    can assert the {query, documents} → {query, texts} translation.
    """

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None, **kwargs):
            if sink is not None:
                sink["url"] = url
                sink["json"] = json
            if exc is not None:
                raise exc
            return resp

    monkeypatch.setattr(adapter.httpx, "Client", _Client)


# ─── Translation + ordering ──────────────────────────────────────────


def test_translates_documents_to_texts_and_scores_to_relevance(monkeypatch):
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    sink: dict = {}
    _install_client(
        monkeypatch,
        resp=_FakeResponse(200, [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.2}]),
        sink=sink,
    )

    result = rerank_via_tei(
        RerankAdapterRequest(query="what is rag?", documents=["doc-a", "doc-b"])
    )

    # Forwarded TEI request uses {query, texts}, not {query, documents}.
    assert sink["url"] == "http://tei-reranker:80/rerank"
    assert sink["json"] == {"query": "what is rag?", "texts": ["doc-a", "doc-b"]}

    # TEI's `score` is renamed to LightRAG's `relevance_score`; index preserved.
    assert [(r.index, r.relevance_score) for r in result.results] == [(1, 0.9), (0, 0.2)]
    # return_documents defaults off → no echoed text.
    assert all(r.document is None for r in result.results)


def test_endpoint_trailing_slash_is_normalized(monkeypatch):
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80/")
    sink: dict = {}
    _install_client(monkeypatch, resp=_FakeResponse(200, [{"index": 0, "score": 0.5}]), sink=sink)
    rerank_via_tei(RerankAdapterRequest(query="q", documents=["only"]))
    assert sink["url"] == "http://tei-reranker:80/rerank"


def test_results_are_sorted_descending_with_index_tiebreak(monkeypatch):
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    # Deliberately unsorted, with a score tie between index 2 and 0.
    _install_client(
        monkeypatch,
        resp=_FakeResponse(
            200,
            [
                {"index": 0, "score": 0.5},
                {"index": 2, "score": 0.5},
                {"index": 1, "score": 0.9},
            ],
        ),
    )
    result = rerank_via_tei(
        RerankAdapterRequest(query="q", documents=["a", "b", "c"])
    )
    assert [r.index for r in result.results] == [1, 0, 2]


def test_top_n_truncates_after_sorting(monkeypatch):
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    _install_client(
        monkeypatch,
        resp=_FakeResponse(
            200,
            [
                {"index": 0, "score": 0.1},
                {"index": 1, "score": 0.9},
                {"index": 2, "score": 0.5},
            ],
        ),
    )
    result = rerank_via_tei(
        RerankAdapterRequest(query="q", documents=["a", "b", "c"], top_n=2)
    )
    assert [r.index for r in result.results] == [1, 2]


def test_return_documents_echoes_original_text(monkeypatch):
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    _install_client(
        monkeypatch,
        resp=_FakeResponse(200, [{"index": 1, "score": 0.7}, {"index": 0, "score": 0.3}]),
    )
    result = rerank_via_tei(
        RerankAdapterRequest(
            query="q", documents=["first", "second"], return_documents=True
        )
    )
    assert result.results[0].document == "second"
    assert result.results[1].document == "first"


def test_duplicate_documents_are_reranked_independently(monkeypatch):
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    sink: dict = {}
    _install_client(
        monkeypatch,
        resp=_FakeResponse(200, [{"index": 0, "score": 0.4}, {"index": 1, "score": 0.4}]),
        sink=sink,
    )
    result = rerank_via_tei(
        RerankAdapterRequest(query="q", documents=["same", "same"])
    )
    assert sink["json"]["texts"] == ["same", "same"]
    assert {r.index for r in result.results} == {0, 1}


# ─── Short-circuit + error mapping ───────────────────────────────────


def test_empty_documents_short_circuit_without_calling_tei(monkeypatch):
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")

    def _boom(*args, **kwargs):
        raise AssertionError("TEI must not be called for an empty document list")

    monkeypatch.setattr(adapter.httpx, "Client", _boom)
    result = rerank_via_tei(RerankAdapterRequest(query="q", documents=[]))
    assert result.results == []


def test_missing_endpoint_raises_dependency_error(monkeypatch):
    monkeypatch.delenv("TEI_RERANKER_ENDPOINT", raising=False)
    with pytest.raises(RerankAdapterDependencyError):
        rerank_via_tei(RerankAdapterRequest(query="q", documents=["a"]))


def test_tei_4xx_raises_upstream_error(monkeypatch):
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    _install_client(monkeypatch, resp=_FakeResponse(422, {"error": "bad"}))
    with pytest.raises(RerankAdapterUpstreamError):
        rerank_via_tei(RerankAdapterRequest(query="q", documents=["a"]))


def test_tei_5xx_raises_upstream_error(monkeypatch):
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    _install_client(monkeypatch, resp=_FakeResponse(503, "unavailable"))
    with pytest.raises(RerankAdapterUpstreamError):
        rerank_via_tei(RerankAdapterRequest(query="q", documents=["a"]))


def test_tei_timeout_raises_timeout_error(monkeypatch):
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    _install_client(monkeypatch, exc=httpx.ReadTimeout("slow"))
    with pytest.raises(RerankAdapterTimeoutError):
        rerank_via_tei(RerankAdapterRequest(query="q", documents=["a"]))


def test_tei_connection_error_raises_dependency_error(monkeypatch):
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    _install_client(monkeypatch, exc=httpx.ConnectError("refused"))
    with pytest.raises(RerankAdapterDependencyError):
        rerank_via_tei(RerankAdapterRequest(query="q", documents=["a"]))


@pytest.mark.parametrize("value", ("bad", "nan", "inf", "0", "-1", "3601"))
def test_rerank_timeout_rejects_malformed_or_unbounded_values(monkeypatch, value):
    monkeypatch.setenv("LIGHTRAG_RERANK_ADAPTER_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="timeout"):
        adapter._timeout_seconds()


def test_non_json_body_raises_upstream_error(monkeypatch):
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    _install_client(monkeypatch, resp=_FakeResponse(200, json_error=True))
    with pytest.raises(RerankAdapterUpstreamError):
        rerank_via_tei(RerankAdapterRequest(query="q", documents=["a"]))


def test_non_array_body_raises_upstream_error(monkeypatch):
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    _install_client(monkeypatch, resp=_FakeResponse(200, {"results": []}))
    with pytest.raises(RerankAdapterUpstreamError):
        rerank_via_tei(RerankAdapterRequest(query="q", documents=["a"]))


def test_out_of_range_index_raises_upstream_error(monkeypatch):
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    _install_client(monkeypatch, resp=_FakeResponse(200, [{"index": 5, "score": 0.9}]))
    with pytest.raises(RerankAdapterUpstreamError):
        rerank_via_tei(RerankAdapterRequest(query="q", documents=["a"]))


@pytest.mark.parametrize(
    "item",
    (
        {"index": True, "score": 0.5},
        {"index": 0.0, "score": 0.5},
        {"index": 0, "score": True},
        {"index": 0, "score": "0.5"},
        {"index": 0, "score": float("nan")},
        {"index": 0, "score": float("inf")},
    ),
)
def test_malformed_tei_item_types_raise_upstream_error(item):
    with pytest.raises(RerankAdapterUpstreamError):
        adapter._parse_tei_items([item], 1)


def test_item_missing_score_raises_upstream_error(monkeypatch):
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    _install_client(monkeypatch, resp=_FakeResponse(200, [{"index": 0}]))
    with pytest.raises(RerankAdapterUpstreamError):
        rerank_via_tei(RerankAdapterRequest(query="q", documents=["a"]))


# ─── Input bounds ────────────────────────────────────────────────────


def test_too_many_documents_rejected():
    with pytest.raises(Exception):
        RerankAdapterRequest(query="q", documents=["x"] * (adapter.MAX_DOCUMENTS + 1))


def test_oversized_document_rejected():
    with pytest.raises(Exception):
        RerankAdapterRequest(
            query="q", documents=["x" * (adapter.MAX_DOCUMENT_CHARS + 1)]
        )


def test_blank_query_rejected():
    with pytest.raises(Exception):
        RerankAdapterRequest(query="", documents=["a"])


def test_extra_fields_are_ignored():
    # LightRAG may send extra keys (model, return_documents, extra_body). The
    # adapter must not 422 on them.
    req = RerankAdapterRequest(
        query="q", documents=["a"], model="mxbai", unknown_future_field=True
    )
    assert req.model == "mxbai"


# ─── Route + bearer auth ─────────────────────────────────────────────
#
# Build a self-contained TestClient (like tests/test_main_app.py) rather than
# reusing the shared ``fastapi_client`` fixture: that fixture pulls in the Ray
# job-submission mock, and Ray is only present in sys.modules when the Ray test
# modules happen to be collected first. main.py's import closure does not need
# Ray, so a local client keeps this suite runnable in isolation.


def _stub_required_env(monkeypatch):
    for var, default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        if not os.environ.get(var):
            monkeypatch.setenv(var, default)


def _client(monkeypatch):
    _stub_required_env(monkeypatch)
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app)


def test_route_requires_bearer_token(monkeypatch):
    monkeypatch.setenv("LIGHTRAG_RERANK_ADAPTER_TOKEN", "sk-lightrag-rerank-secret")
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    client = _client(monkeypatch)

    resp = client.post("/lightrag/rerank", json={"query": "q", "documents": ["a", "b"]})
    assert resp.status_code == 401


def test_route_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("LIGHTRAG_RERANK_ADAPTER_TOKEN", "sk-lightrag-rerank-secret")
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    client = _client(monkeypatch)

    resp = client.post(
        "/lightrag/rerank",
        headers={"Authorization": "Bearer wrong"},
        json={"query": "q", "documents": ["a"]},
    )
    assert resp.status_code == 401


def test_route_503_when_token_unset(monkeypatch):
    monkeypatch.delenv("LIGHTRAG_RERANK_ADAPTER_TOKEN", raising=False)
    client = _client(monkeypatch)
    resp = client.post(
        "/lightrag/rerank",
        headers={"Authorization": "Bearer anything"},
        json={"query": "q", "documents": ["a"]},
    )
    assert resp.status_code == 503


def test_route_ok_with_valid_token(monkeypatch):
    monkeypatch.setenv("LIGHTRAG_RERANK_ADAPTER_TOKEN", "sk-lightrag-rerank-secret")
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    _install_client(
        monkeypatch,
        resp=_FakeResponse(200, [{"index": 1, "score": 0.8}, {"index": 0, "score": 0.1}]),
    )
    client = _client(monkeypatch)

    resp = client.post(
        "/lightrag/rerank",
        headers={"Authorization": "Bearer sk-lightrag-rerank-secret"},
        json={"query": "q", "documents": ["a", "b"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0] == {"index": 1, "relevance_score": 0.8, "document": None}


def test_route_maps_upstream_error_to_502(monkeypatch):
    monkeypatch.setenv("LIGHTRAG_RERANK_ADAPTER_TOKEN", "sk-lightrag-rerank-secret")
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    _install_client(monkeypatch, resp=_FakeResponse(500, "boom"))
    client = _client(monkeypatch)

    resp = client.post(
        "/lightrag/rerank",
        headers={"Authorization": "Bearer sk-lightrag-rerank-secret"},
        json={"query": "q", "documents": ["a"]},
    )
    assert resp.status_code == 502


def test_route_maps_timeout_to_504(monkeypatch):
    monkeypatch.setenv("LIGHTRAG_RERANK_ADAPTER_TOKEN", "sk-lightrag-rerank-secret")
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    _install_client(monkeypatch, exc=httpx.ReadTimeout("slow"))
    client = _client(monkeypatch)

    resp = client.post(
        "/lightrag/rerank",
        headers={"Authorization": "Bearer sk-lightrag-rerank-secret"},
        json={"query": "q", "documents": ["a"]},
    )
    assert resp.status_code == 504


def test_route_maps_missing_endpoint_to_503(monkeypatch):
    monkeypatch.setenv("LIGHTRAG_RERANK_ADAPTER_TOKEN", "sk-lightrag-rerank-secret")
    monkeypatch.delenv("TEI_RERANKER_ENDPOINT", raising=False)
    client = _client(monkeypatch)

    resp = client.post(
        "/lightrag/rerank",
        headers={"Authorization": "Bearer sk-lightrag-rerank-secret"},
        json={"query": "q", "documents": ["a"]},
    )
    assert resp.status_code == 503


# ─── Optional live smoke (opt-in) ────────────────────────────────────


@pytest.mark.skipif(
    not os.getenv("ATLAS_TEI_RERANKER_LIVE_ENDPOINT"),
    reason="set ATLAS_TEI_RERANKER_LIVE_ENDPOINT to a running TEI reranker to run the live smoke",
)
def test_live_rerank_against_real_tei(monkeypatch):
    monkeypatch.setenv(
        "TEI_RERANKER_ENDPOINT", os.environ["ATLAS_TEI_RERANKER_LIVE_ENDPOINT"]
    )
    result = rerank_via_tei(
        RerankAdapterRequest(
            query="What is graph-augmented retrieval?",
            documents=[
                "LightRAG combines a knowledge graph with dense vector retrieval.",
                "The mitochondria is the powerhouse of the cell.",
                "Reranking reorders retrieved passages by query relevance.",
            ],
            return_documents=True,
        )
    )
    assert len(result.results) == 3
    scores = [r.relevance_score for r in result.results]
    assert scores == sorted(scores, reverse=True)
    # The two on-topic passages should outrank the biology distractor.
    assert result.results[-1].index == 1
