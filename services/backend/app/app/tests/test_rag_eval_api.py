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
