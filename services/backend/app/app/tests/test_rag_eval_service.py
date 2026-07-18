from __future__ import annotations

import pytest

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


def test_reference_metrics_not_evaluable_without_ground_truth(monkeypatch):
    """#597: context_precision/recall without ground_truth are not_evaluable
    (None score) — the request succeeds instead of failing the whole batch."""
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "ollama/qwen3.6:latest")
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

    def runner(records, metrics, config):
        # The record lacks ground_truth → context_precision is not eligible →
        # the runner is NOT called for it.
        raise AssertionError("runner should not be called for an ineligible metric")

    response = evaluate_rag_records(request, runner=runner)
    assert response.results[0].scores["context_precision"] is None


def test_contextless_record_allows_answer_relevancy_and_marks_faithfulness_not_evaluable(monkeypatch):
    """#597 core: a contextless record (e.g. a graph-RAG answer) is valid and
    answer_relevancy is evaluated, while faithfulness is not_evaluable."""
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "ollama/qwen3.6:latest")
    request = RagEvaluationRequest(
        records=[
            RagEvaluationRecord(
                question="Which services depend on Project Cedar?",
                answer="Service A and Service B depend on Project Cedar.",
                contexts=[],
            )
        ],
        metrics=["answer_relevancy", "faithfulness"],
    )

    captured: dict[str, object] = {}

    def runner(records, metrics, config):
        captured["metrics"] = metrics
        captured["records"] = records
        # Only answer_relevancy should reach the runner (faithfulness needs contexts).
        return [{"scores": {"answer_relevancy": 0.77}}]

    response = evaluate_rag_records(request, runner=runner)
    assert captured["metrics"] == ["answer_relevancy"]
    assert response.results[0].scores["answer_relevancy"] == 0.77
    assert response.results[0].scores["faithfulness"] is None  # not_evaluable


def test_mixed_context_batch_groups_records_by_eligibility(monkeypatch):
    """A batch with one contextless and one contextual record runs the runner
    twice (one per eligibility group) and scores each correctly."""
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "ollama/qwen3.6:latest")
    request = RagEvaluationRequest(
        records=[
            RagEvaluationRecord(
                question="Q1", answer="A1", contexts=[],  # answer_relevancy only
            ),
            RagEvaluationRecord(
                question="Q2", answer="A2", contexts=["C2"],  # both metrics
            ),
        ],
        metrics=["answer_relevancy", "faithfulness"],
    )

    calls: list[list[str]] = []

    def runner(records, metrics, config):
        calls.append(list(metrics))
        return [{"scores": {m: 0.5 for m in metrics}} for _ in records]

    response = evaluate_rag_records(request, runner=runner)
    # Two groups: the contextless record (answer_relevancy) and the
    # contextual one (answer_relevancy + faithfulness).
    assert ["answer_relevancy"] in calls
    assert any("faithfulness" in c for c in calls)
    # Record 0: faithfulness not_evaluable.
    assert response.results[0].scores["faithfulness"] is None
    assert response.results[0].scores["answer_relevancy"] == 0.5
    # Record 1: both evaluable.
    assert response.results[1].scores["faithfulness"] == 0.5
    assert response.results[1].scores["answer_relevancy"] == 0.5


def test_contextless_record_is_now_schema_valid():
    """#597: contexts=[] is accepted at schema validation (previously rejected)."""
    record = RagEvaluationRecord(
        question="What is Atlas?",
        answer="Atlas is a local engineering platform.",
        contexts=[],
    )
    assert record.contexts == []


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


@pytest.fixture()
def _suppress_ragas_warnings():
    """ragas 0.4.3's analytics opens a uuid file without closing it (ResourceWarning
    on GC) and transformers logs when PyTorch is absent. Both are noise for these
    construction tests; suppress them (and force GC inside the suppression) so the
    suite stays green under the repo's ``-W error`` CI mode."""
    import gc
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield
        gc.collect()


def test_metric_objects_construct_all_four_against_pinned_ragas(_suppress_ragas_warnings):
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


def test_metric_objects_rejects_answer_relevancy_without_embeddings(_suppress_ragas_warnings):
    from rag_eval_service import _metric_objects

    with pytest.raises(ValueError, match="embeddings"):
        _metric_objects(["answer_relevancy"], llm=_instructor_llm(), embeddings=None)


def test_metric_objects_context_metrics_work_without_embeddings(_suppress_ragas_warnings):
    """faithfulness / context_precision / context_recall need only the llm —
    only answer_relevancy requires embeddings."""
    from rag_eval_service import _metric_objects

    metrics = _metric_objects(
        ["faithfulness", "context_precision", "context_recall"],
        llm=_instructor_llm(),
        embeddings=None,
    )
    assert len(metrics) == 3


def test_live_runner_executes_collection_metrics_in_one_async_client_lifecycle(
    monkeypatch, _suppress_ragas_warnings
):
    """Collection metrics are not legacy ``ragas.metrics.base.Metric`` objects.

    The live runner must use their modern batch API instead of passing them to
    deprecated ``ragas.evaluate()``, which rejects this type family before any
    evaluator call.
    """
    from ragas.metrics.result import MetricResult

    import rag_eval_service

    seen: list[dict[str, object]] = []

    class FakeFaithfulness:
        name = "faithfulness"

        async def ascore(self, user_input, response, retrieved_contexts):
            raise AssertionError("abatch_score should own async execution")

        def batch_score(self, inputs):
            raise AssertionError("sync batch_score must not create a separate event loop")

        async def abatch_score(self, inputs):
            seen.extend(inputs)
            return [
                MetricResult(value=0.91 - index / 10, reason=f"supported-{index}")
                for index, _ in enumerate(inputs)
            ]

    clients: dict[str, str] = {}
    client_refs = []

    def fake_metric_objects(names, *, llm, embeddings):
        clients["llm"] = type(llm.client).__name__
        clients["embeddings"] = type(embeddings.client).__name__
        client_refs.append(embeddings.client)
        return [FakeFaithfulness()]

    monkeypatch.setattr(rag_eval_service, "_metric_objects", fake_metric_objects)

    rows = rag_eval_service._run_ragas_evaluation(
        [
            {
                "user_input": "What does Atlas use for routing?",
                "response": "Atlas uses LiteLLM.",
                "retrieved_contexts": ["Atlas routes model calls through LiteLLM."],
                "reference": "LiteLLM",
            },
            {
                "user_input": "Where are vectors stored?",
                "response": "In Weaviate.",
                "retrieved_contexts": ["Atlas uses Weaviate as its vector store."],
                "reference": "Weaviate",
            },
        ],
        ["faithfulness"],
        {
            "llm_api_key": "dummy",
            "llm_base_url": "http://localhost:1/v1",
            "evaluator_model": "dummy-chat",
            "embeddings_model": "dummy-embed",
            "raise_exceptions": False,
        },
    )

    assert seen == [
        {
            "user_input": "What does Atlas use for routing?",
            "response": "Atlas uses LiteLLM.",
            "retrieved_contexts": ["Atlas routes model calls through LiteLLM."],
        },
        {
            "user_input": "Where are vectors stored?",
            "response": "In Weaviate.",
            "retrieved_contexts": ["Atlas uses Weaviate as its vector store."],
        },
    ]
    assert clients == {"llm": "AsyncInstructor", "embeddings": "AsyncOpenAI"}
    assert client_refs[0].is_closed()
    assert rows == [
        {
            "scores": {"faithfulness": 0.91},
            "metadata": {"metric_reasons": {"faithfulness": "supported-0"}},
        },
        {
            "scores": {"faithfulness": 0.81},
            "metadata": {"metric_reasons": {"faithfulness": "supported-1"}},
        },
    ]


def test_collection_metric_failure_is_explicit_unless_raise_exceptions() -> None:
    import asyncio

    import rag_eval_service

    class BrokenMetric:
        name = "faithfulness"

        async def ascore(self, user_input, response, retrieved_contexts):
            raise AssertionError("not called directly")

        async def abatch_score(self, inputs):
            raise RuntimeError("evaluator unavailable")

    records = [
        {
            "user_input": "Q",
            "response": "A",
            "retrieved_contexts": ["C"],
            "reference": None,
        }
    ]

    rows = asyncio.run(
        rag_eval_service._score_collection_metrics_async(
            records, [BrokenMetric()], raise_exceptions=False
        )
    )
    assert rows == [
        {
            "scores": {"faithfulness": None},
            "metadata": {
                "metric_errors": {
                    "faithfulness": "RuntimeError: evaluator unavailable"
                }
            },
        }
    ]

    with pytest.raises(RuntimeError, match="evaluator unavailable"):
        asyncio.run(
            rag_eval_service._score_collection_metrics_async(
                records, [BrokenMetric()], raise_exceptions=True
            )
        )


def test_dependency_error_propagates_as_503_not_400(monkeypatch):
    # A ragas dependency outage must reach the route as
    # RagEvaluationDependencyError (→ 503), not be re-wrapped into a plain
    # RagEvaluationError (→ 400). Subclassing keeps its identity through the
    # service's `except RagEvaluationError: raise` wrapper.
    from rag_eval_service import RagEvaluationDependencyError, RagEvaluationError

    assert issubclass(RagEvaluationDependencyError, RagEvaluationError)

    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-atlas")
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "ollama/qwen3.6:latest")
    monkeypatch.setenv("LITELLM_EMBEDDING_MODEL", "ollama/nomic-embed-text")

    def boom_runner(records, metrics, config):
        raise RagEvaluationDependencyError("ragas transitive dep missing")

    request = RagEvaluationRequest(
        records=[RagEvaluationRecord(question="q", answer="a", contexts=["c"])],
        metrics=["faithfulness"],
    )

    with pytest.raises(RagEvaluationDependencyError):
        evaluate_rag_records(request, runner=boom_runner)
