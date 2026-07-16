from __future__ import annotations

from collections import OrderedDict
import inspect
import os
from typing import Any, Callable, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


MetricName = Literal[
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
]

_REFERENCE_METRICS = {"context_precision", "context_recall"}

# Central per-metric evidence requirements: (needs_retrieved_contexts,
# needs_reference). A metric is evaluable for a record only when the record
# carries the evidence the metric needs; otherwise it is reported as
# not_evaluable (a None score) rather than failing the request (#597).
# answer_relevancy needs only question + answer; the context metrics need
# retrieved contexts (and context_precision/recall also need ground_truth).
_METRIC_REQUIREMENTS: Dict[MetricName, tuple[bool, bool]] = {
    "answer_relevancy": (False, False),
    "faithfulness": (True, False),
    "context_precision": (True, True),
    "context_recall": (True, True),
}


class RagEvaluationDependencyError(RuntimeError):
    pass


class RagEvaluationError(RuntimeError):
    pass


class RagEvaluationRecord(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    answer: str = Field(min_length=1, max_length=16000)
    # contexts may be empty: answer_relevancy needs only question+answer, so a
    # contextless record (e.g. a graph-RAG answer with no exposed text chunks)
    # is still valid — context-requiring metrics are reported not_evaluable (#597).
    contexts: List[str] = Field(min_length=0, max_length=50)
    ground_truth: Optional[str] = Field(default=None, max_length=16000)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RagEvaluationRequest(BaseModel):
    records: List[RagEvaluationRecord] = Field(min_length=1, max_length=100)
    metrics: List[MetricName] = Field(
        default_factory=lambda: ["faithfulness", "answer_relevancy"]
    )
    evaluator_model: Optional[str] = Field(default=None, max_length=256)
    embeddings_model: Optional[str] = Field(default=None, max_length=256)
    raise_exceptions: bool = False


class RagEvaluationResult(BaseModel):
    record_index: int
    scores: Dict[str, Optional[float]]
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RagEvaluationResponse(BaseModel):
    metrics: List[MetricName]
    record_count: int
    evaluator_model: str
    embeddings_model: Optional[str]
    results: List[RagEvaluationResult]
    metadata: Dict[str, Any] = Field(default_factory=dict)


Runner = Callable[[list[dict[str, Any]], list[str], dict[str, Any]], list[dict[str, Any]]]


def _unique_metrics(metrics: list[MetricName]) -> list[MetricName]:
    unique: list[MetricName] = []
    for metric in metrics:
        if metric not in unique:
            unique.append(metric)
    return unique


def _litellm_base_url() -> str:
    value = (
        os.getenv("LITELLM_BASE_URL")
        or os.getenv("OPENAI_API_BASE")
        or os.getenv("OPENAI_BASE_URL")
        or "http://litellm:4000"
    ).rstrip("/")
    if not value.endswith("/v1"):
        value = f"{value}/v1"
    return value


def _evaluation_config(request: RagEvaluationRequest) -> dict[str, Any]:
    evaluator_model = (
        request.evaluator_model
        or os.getenv("RAGAS_EVALUATOR_MODEL")
        or os.getenv("LITELLM_DEFAULT_MODEL")
        or ""
    ).strip()
    if not evaluator_model:
        raise ValueError(
            "RAGAS_EVALUATOR_MODEL or LITELLM_DEFAULT_MODEL must be set for RAG evaluation"
        )

    embeddings_model = (
        request.embeddings_model
        or os.getenv("RAGAS_EMBEDDINGS_MODEL")
        or os.getenv("LITELLM_EMBEDDING_MODEL")
        or ""
    ).strip() or None

    return {
        "llm_base_url": _litellm_base_url(),
        "llm_api_key": os.getenv("LITELLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "",
        "evaluator_model": evaluator_model,
        "embeddings_model": embeddings_model,
        "raise_exceptions": request.raise_exceptions,
    }


def _runner_records(records: list[RagEvaluationRecord]) -> list[dict[str, Any]]:
    return [
        {
            "user_input": record.question,
            "response": record.answer,
            "retrieved_contexts": record.contexts,
            "reference": record.ground_truth,
        }
        for record in records
    ]


def _eligible_metrics(
    record: RagEvaluationRecord, metrics: list[MetricName]
) -> list[MetricName]:
    """Metrics a record can actually evaluate given its evidence.

    A metric whose evidence requirement the record doesn't meet is dropped
    (reported as ``not_evaluable`` / a ``None`` score by the caller) rather
    than failing the request — so a contextless graph-RAG answer can still be
    scored on ``answer_relevancy`` while ``faithfulness`` is not_evaluable.
    """
    has_context = bool(record.contexts)
    has_reference = bool(record.ground_truth)
    eligible: list[MetricName] = []
    for metric in metrics:
        needs_context, needs_reference = _METRIC_REQUIREMENTS[metric]
        if needs_context and not has_context:
            continue
        if needs_reference and not has_reference:
            continue
        eligible.append(metric)
    return eligible


def _metric_objects(metric_names: list[str], *, llm, embeddings):
    """Construct the requested Ragas metric instances for the pinned
    ``ragas==0.4.3`` runtime.

    The ``ragas.metrics.collections`` metrics are the *modern* metrics: they
    require an ``InstructorLLM`` (built via :func:`ragas.llms.llm_factory`),
    not the legacy ``LangchainLLMWrapper``, and they take the llm/embeddings
    at construction (a no-arg ``Metric()`` raises ``TypeError``). The class
    exported for answer-relevancy is ``AnswerRelevancy`` (``ResponseRelevancy``
    does not exist in 0.4.3 — it was the surface symptom of #596).
    """
    try:
        from ragas.metrics.collections import (
            AnswerRelevancy,
            ContextPrecision,
            ContextRecall,
            Faithfulness,
        )
    except ModuleNotFoundError as exc:
        raise RagEvaluationDependencyError(
            "Ragas metric dependencies are not installed; install ragas==0.4.3 "
            "and langchain-community>=0.3.0,<0.4"
        ) from exc

    if "answer_relevancy" in metric_names and embeddings is None:
        raise ValueError(
            "answer_relevancy requires an embeddings model — set "
            "RAGAS_EMBEDDINGS_MODEL or LITELLM_EMBEDDING_MODEL"
        )

    def build(name: str):
        if name == "answer_relevancy":
            return AnswerRelevancy(llm=llm, embeddings=embeddings)
        if name == "faithfulness":
            return Faithfulness(llm=llm)
        if name == "context_precision":
            return ContextPrecision(llm=llm)
        if name == "context_recall":
            return ContextRecall(llm=llm)
        raise ValueError(f"Unknown Ragas metric: {name}")

    return [build(name) for name in metric_names]


def _score_collection_metrics(
    records: list[dict[str, Any]], metric_objects: list[Any], *, raise_exceptions: bool
) -> list[dict[str, Any]]:
    """Run Ragas collection metrics through their modern batch API.

    ``ragas.metrics.collections`` objects are intentionally not instances of
    the legacy ``ragas.metrics.base.Metric`` protocol accepted by deprecated
    ``ragas.evaluate()``. Each collection metric instead exposes ``ascore`` and
    ``batch_score``. Filter records to the concrete ``ascore`` signature so a
    metric never receives unrelated sample fields.
    """
    rows: list[dict[str, Any]] = [
        {"scores": {}, "metadata": {}} for _ in records
    ]
    for metric in metric_objects:
        name = str(getattr(metric, "name", "") or "")
        if not name:
            raise RagEvaluationError(
                f"Ragas collection metric {type(metric).__name__} has no name"
            )
        accepted = set(inspect.signature(metric.ascore).parameters)
        inputs = [
            {key: value for key, value in record.items() if key in accepted}
            for record in records
        ]
        try:
            results = metric.batch_score(inputs)
        except Exception as exc:
            if raise_exceptions:
                raise
            detail = f"{type(exc).__name__}: {exc}"
            for row in rows:
                row["scores"][name] = None
                row["metadata"].setdefault("metric_errors", {})[name] = detail
            continue
        if len(results) != len(records):
            raise RagEvaluationError(
                f"Ragas metric {name} returned {len(results)} result(s) "
                f"for {len(records)} record(s)"
            )
        for row, result in zip(rows, results):
            row["scores"][name] = getattr(result, "value", None)
            reason = getattr(result, "reason", None)
            if reason:
                row["metadata"].setdefault("metric_reasons", {})[name] = reason
    return rows


def _run_ragas_evaluation(
    records: list[dict[str, Any]], metrics: list[str], config: dict[str, Any]
) -> list[dict[str, Any]]:
    try:
        from openai import AsyncOpenAI
        from ragas.embeddings import OpenAIEmbeddings
        from ragas.llms import llm_factory
    except ModuleNotFoundError as exc:
        raise RagEvaluationDependencyError(
            "Ragas evaluation dependencies are not installed or are incompatible"
        ) from exc

    # ragas 0.4.3's collections metrics require the modern InstructorLLM
    # abstraction. Build one from an OpenAI-compatible client pointed at the
    # LiteLLM gateway (the same base_url/key the legacy LangchainLLMWrapper
    # used); llm_factory wraps it into the InstructorLLM the metrics expect.
    # Collection metrics execute through async ``ascore`` even when callers use
    # their synchronous ``batch_score`` convenience wrapper. Give Ragas an async
    # OpenAI client so both InstructorLLM.agenerate() and embedding a* methods
    # match that execution contract.
    openai_client = AsyncOpenAI(
        api_key=config["llm_api_key"],
        base_url=config["llm_base_url"],
    )
    llm = llm_factory(config["evaluator_model"], client=openai_client)
    embeddings = None
    if config.get("embeddings_model"):
        embeddings = OpenAIEmbeddings(
            client=openai_client,
            model=config["embeddings_model"],
        )

    try:
        return _score_collection_metrics(
            records,
            _metric_objects(metrics, llm=llm, embeddings=embeddings),
            raise_exceptions=bool(config.get("raise_exceptions")),
        )
    except RagEvaluationError:
        raise
    except Exception as exc:  # pragma: no cover - exercised only with live evaluator calls.
        raise RagEvaluationError(str(exc)) from exc


def _result_scores(
    row: dict[str, Any], metrics: list[MetricName], eligible: list[MetricName]
) -> dict[str, Optional[float]]:
    """Pull each requested metric's score from a runner row. Metrics that were
    not eligible for the record (not run) are ``None`` — ``not_evaluable``."""
    source = row.get("scores", row)
    eligible_set = set(eligible)
    scores: dict[str, Optional[float]] = {}
    for metric in metrics:
        if metric not in eligible_set:
            scores[metric] = None  # not_evaluable — record lacked the evidence
            continue
        value = source.get(metric)
        scores[metric] = None if value is None else float(value)
    return scores


def evaluate_rag_records(
    request: RagEvaluationRequest,
    *,
    runner: Runner = _run_ragas_evaluation,
) -> RagEvaluationResponse:
    metrics = _unique_metrics(request.metrics)
    config = _evaluation_config(request)

    # Group records by their eligible-metric set so each group runs the runner
    # once with only the metrics its records can evaluate. A metric a record
    # can't support is reported as not_evaluable (None), not a request failure.
    groups: "OrderedDict[tuple[MetricName, ...], list[int]]" = OrderedDict()
    for index, record in enumerate(request.records):
        eligible = tuple(_eligible_metrics(record, metrics))
        groups.setdefault(eligible, []).append(index)

    raw_by_index: dict[int, dict[str, Any]] = {}
    eligible_by_index: dict[int, list[MetricName]] = {}
    for eligible_tuple, indices in groups.items():
        eligible = list(eligible_tuple)
        for i in indices:
            eligible_by_index[i] = eligible
        if not eligible:
            # No requested metric is evaluable for these records (e.g. only
            # context metrics requested but the record has no contexts).
            for i in indices:
                raw_by_index[i] = {}
            continue
        group_records = [request.records[i] for i in indices]
        group_rows = runner(_runner_records(group_records), eligible, config)
        for i, row in zip(indices, group_rows):
            raw_by_index[i] = row

    results = [
        RagEvaluationResult(
            record_index=index,
            scores=_result_scores(
                raw_by_index.get(index, {}), metrics, eligible_by_index[index]
            ),
            metadata=dict(raw_by_index.get(index, {}).get("metadata", {}) or {}),
        )
        for index in range(len(request.records))
    ]
    return RagEvaluationResponse(
        metrics=metrics,
        record_count=len(request.records),
        evaluator_model=config["evaluator_model"],
        embeddings_model=config.get("embeddings_model"),
        results=results,
        metadata={
            "runner": "ragas",
            "llm_base_url": config["llm_base_url"],
        },
    )
