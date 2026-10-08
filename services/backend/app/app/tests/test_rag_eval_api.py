from __future__ import annotations

import importlib
import os
import sys


def _stub_required_env(monkeypatch):
    for var, default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
        ("LITELLM_BASE_URL", "http://litellm:4000"),
        ("LITELLM_API_KEY", "sk-atlas"),
        ("LITELLM_DEFAULT_MODEL", "ollama/qwen3.6:latest"),
    ):
        if not os.environ.get(var):
            monkeypatch.setenv(var, default)


def _fresh_main(monkeypatch):
    _stub_required_env(monkeypatch)
    sys.modules.pop("main", None)
    return importlib.import_module("main")


def test_rag_eval_endpoint_returns_metric_scores(monkeypatch):
    main = _fresh_main(monkeypatch)

    from fastapi.testclient import TestClient
    from rag_eval_service import RagEvaluationResponse, RagEvaluationResult

    def fake_evaluate(request):
        assert request.metrics == ["faithfulness"]
        return RagEvaluationResponse(
            metrics=["faithfulness"],
            record_count=1,
            evaluator_model="ollama/qwen3.6:latest",
            embeddings_model=None,
            results=[
                RagEvaluationResult(
                    record_index=0,
                    scores={"faithfulness": 0.88},
                    metadata={"source": "fake-ragas"},
                )
            ],
            metadata={"runner": "fake"},
        )

    monkeypatch.setattr(main, "evaluate_rag_records", fake_evaluate)

    response = TestClient(main.app).post(
        "/api/rag/evaluate",
        json={
            "records": [
                {
                    "question": "What does Atlas use for model routing?",
                    "answer": "Atlas uses LiteLLM.",
                    "contexts": ["LiteLLM is Atlas's OpenAI-compatible model gateway."],
                }
            ],
            "metrics": ["faithfulness"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["metrics"] == ["faithfulness"]
    assert body["record_count"] == 1
    assert body["results"][0]["scores"]["faithfulness"] == 0.88


def test_rag_eval_endpoint_rejects_empty_records(monkeypatch):
    main = _fresh_main(monkeypatch)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/api/rag/evaluate",
        json={"records": [], "metrics": ["faithfulness"]},
    )

    assert response.status_code == 422


def test_evaluator_failure_is_a_502_with_a_fixed_detail(monkeypatch):
    """#1354: a LiteLLM/Ragas failure is the server's, not the caller's, and its
    raw text is logged, not returned."""
    main = _fresh_main(monkeypatch)
    from fastapi.testclient import TestClient
    from rag_eval_service import RagEvaluationUpstreamError

    def fail(_request):
        raise RagEvaluationUpstreamError("litellm.APIError: key sk-live-abc rejected")

    monkeypatch.setattr(main, "evaluate_rag_records", fail)
    response = TestClient(main.app).post(
        "/api/rag/evaluate",
        json={"records": [{"question": "q", "answer": "a", "contexts": ["c"]}], "metrics": ["faithfulness"]},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "RAG evaluation failed"


def test_workflows_route_maps_an_unreachable_n8n_to_503(monkeypatch):
    import httpx

    main = _fresh_main(monkeypatch)
    from fastapi.testclient import TestClient

    async def unreachable():
        raise httpx.ConnectError("connection refused")

    async def upstream_500(_workflow_id):
        request = httpx.Request("GET", "http://n8n:5678/api/v1/workflows/w1")
        raise httpx.HTTPStatusError("boom", request=request, response=httpx.Response(500, request=request))

    monkeypatch.setattr(main.n8n_client, "list_workflows", unreachable)
    monkeypatch.setattr(main.n8n_client, "get_workflow", upstream_500)
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    client = TestClient(main.app)

    assert client.get("/workflows").status_code == 503
    assert client.get("/workflows/w1").status_code == 502


def test_evaluator_4xx_is_the_callers_error_and_others_are_upstream():
    from rag_eval_service import RagEvaluationUpstreamError, _evaluation_error

    class BadModel(Exception):
        status_code = 404

    class Outage(Exception):
        status_code = 503

    assert type(_evaluation_error(BadModel("model not found"))).__name__ == "RagEvaluationError"
    assert isinstance(_evaluation_error(Outage("upstream down")), RagEvaluationUpstreamError)
    assert isinstance(_evaluation_error(RuntimeError("boom")), RagEvaluationUpstreamError)


def test_evaluator_errors_hide_upstream_text_and_backend_auth_is_upstream():
    from rag_eval_service import RagEvaluationUpstreamError, _evaluation_error

    class Auth(Exception):
        status_code = 401

    class BadModel(Exception):
        status_code = 404

    class InstructorRetryException(Exception):  # keeps the HTTP error as its cause
        pass

    leaked = BadModel("model gpt-x at http://10.0.0.5:11434 key sk-abc not found")
    assert "sk-abc" not in str(_evaluation_error(leaked)) and "HTTP 404" in str(_evaluation_error(leaked))
    assert isinstance(_evaluation_error(Auth("bad key sk-abc")), RagEvaluationUpstreamError)
    wrapped = InstructorRetryException("retries exhausted")
    wrapped.__cause__ = BadModel("unknown model")
    assert type(_evaluation_error(wrapped)).__name__ == "RagEvaluationError"
