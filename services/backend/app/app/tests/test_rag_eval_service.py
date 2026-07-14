from __future__ import annotations

import pytest
from pydantic import ValidationError

from rag_eval_service import (
    RagEvaluationRecord,
    RagEvaluationRequest,
    RagEvaluationResponse,
    evaluate_rag_records,
)


def test_rag_eval_builds_litellm_backed_runner_payload(monkeypatch):
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-atlas")
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "ollama/qwen3.6:latest")
    monkeypatch.setenv("LITELLM_EMBEDDING_MODEL", "ollama/nomic-embed-text")

    captured: dict[str, object] = {}

    def fake_runner(records, metrics, config):
        captured["records"] = records
        captured["metrics"] = metrics
        captured["config"] = config
        return [
            {
                "scores": {
                    "faithfulness": 0.91,
                    "answer_relevancy": 0.84,
                },
                "metadata": {"source": "fake-ragas"},
            }
        ]

    request = RagEvaluationRequest(
        records=[
            RagEvaluationRecord(
                question="What does Atlas route through LiteLLM?",
                answer="Atlas routes model calls through LiteLLM.",
                contexts=["LiteLLM is the OpenAI-compatible gateway for Atlas."],
            )
        ],
        metrics=["faithfulness", "answer_relevancy", "faithfulness"],
    )

    response = evaluate_rag_records(request, runner=fake_runner)

    assert isinstance(response, RagEvaluationResponse)
    assert response.metrics == ["faithfulness", "answer_relevancy"]
    assert response.record_count == 1
    assert response.evaluator_model == "ollama/qwen3.6:latest"
    assert response.embeddings_model == "ollama/nomic-embed-text"
    assert response.results[0].scores["faithfulness"] == 0.91
    assert captured["records"] == [
        {
            "user_input": "What does Atlas route through LiteLLM?",
            "response": "Atlas routes model calls through LiteLLM.",
            "retrieved_contexts": ["LiteLLM is the OpenAI-compatible gateway for Atlas."],
            "reference": None,
        }
    ]
    assert captured["metrics"] == ["faithfulness", "answer_relevancy"]
    assert captured["config"] == {
        "llm_base_url": "http://litellm:4000/v1",
        "llm_api_key": "sk-atlas",
        "evaluator_model": "ollama/qwen3.6:latest",
        "embeddings_model": "ollama/nomic-embed-text",
        "raise_exceptions": False,
    }


def test_reference_metrics_require_ground_truth():
    request = RagEvaluationRequest(
        records=[
            RagEvaluationRecord(
                question="Which service stores vectors?",
                answer="Weaviate stores vectors.",
                contexts=["Weaviate is Atlas's vector database."],
            )
        ],
        metrics=["context_precision"],
    )

    with pytest.raises(ValueError, match="ground_truth"):
        evaluate_rag_records(request, runner=lambda *_args, **_kwargs: [])


def test_rag_eval_record_rejects_empty_contexts():
    with pytest.raises(ValidationError):
        RagEvaluationRecord(
            question="What is Atlas?",
            answer="Atlas is a local engineering platform.",
            contexts=[],
        )


# ── #596: metric import + construction against the pinned ragas==0.4.3 ──────
# The previous code imported `ResponseRelevancy` (absent in 0.4.3) and built
# metrics no-arg (`Metric()`), so every live eval failed before reaching the
# evaluator. These tests exercise the real import + construction path that the
# fake-runner tests above deliberately bypass.


def _instructor_llm():
    """An InstructorLLM built from a dummy OpenAI-compatible client — ragas
    0.4.3's collections metrics require this abstraction, and constructing one
    makes no network call (the client is only used at evaluate() time)."""
    from openai import OpenAI
    from ragas.llms import llm_factory

    return llm_factory("gpt-4o-mini", client=OpenAI(api_key="dummy", base_url="http://localhost:1"))


def test_metric_objects_construct_all_four_against_pinned_ragas():
    from openai import OpenAI
    from ragas.embeddings import OpenAIEmbeddings

    from rag_eval_service import _metric_objects

    client = OpenAI(api_key="dummy", base_url="http://localhost:1")
    embeddings = OpenAIEmbeddings(client=client, model="text-embedding-3-small")

    metrics = _metric_objects(
        ["faithfulness", "answer_relevancy", "context_precision", "context_recall"],
        llm=_instructor_llm(),
        embeddings=embeddings,
    )
    assert len(metrics) == 4
    names = {getattr(m, "name", type(m).__name__) for m in metrics}
    # The answer-relevancy metric is the one whose symbol was wrong (#596).
    assert "answer_relevancy" in names
    assert {"faithfulness", "context_precision", "context_recall"} <= names


def test_metric_objects_rejects_answer_relevancy_without_embeddings():
    from rag_eval_service import _metric_objects

    with pytest.raises(ValueError, match="embeddings"):
        _metric_objects(["answer_relevancy"], llm=_instructor_llm(), embeddings=None)


def test_metric_objects_context_metrics_work_without_embeddings():
    """faithfulness / context_precision / context_recall need only the llm —
    only answer_relevancy requires embeddings."""
    from rag_eval_service import _metric_objects

    metrics = _metric_objects(
        ["faithfulness", "context_precision", "context_recall"],
        llm=_instructor_llm(),
        embeddings=None,
    )
    assert len(metrics) == 3

