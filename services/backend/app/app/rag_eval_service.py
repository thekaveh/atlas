from __future__ import annotations

from typing import Any, Callable, Dict, List, Literal, Optional
import os

from pydantic import BaseModel, Field


MetricName = Literal[
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
]

_REFERENCE_METRICS = {"context_precision", "context_recall"}


class RagEvaluationDependencyError(RuntimeError):
    pass


class RagEvaluationError(RuntimeError):
    pass


class RagEvaluationRecord(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    answer: str = Field(min_length=1, max_length=16000)
    contexts: List[str] = Field(min_length=1, max_length=50)
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


def _validate_metric_requirements(
    records: list[RagEvaluationRecord], metrics: list[MetricName]
) -> None:
    if _REFERENCE_METRICS.intersection(metrics):
        missing = [index for index, record in enumerate(records) if not record.ground_truth]
        if missing:
            raise ValueError(
                "ground_truth is required for context_precision/context_recall metrics "
                f"(missing record indexes: {missing})"
            )


def _metric_objects(metric_names: list[str]):
    try:
        from ragas.metrics.collections import (
            ContextPrecision,
            ContextRecall,
            Faithfulness,
            ResponseRelevancy,
        )
    except ModuleNotFoundError as exc:
        raise RagEvaluationDependencyError(
            "Ragas metric dependencies are not installed; install ragas==0.4.3 "
            "and langchain-community>=0.3.0,<0.4"
        ) from exc

    registry = {
        "faithfulness": Faithfulness,
        "answer_relevancy": ResponseRelevancy,
        "context_precision": ContextPrecision,
        "context_recall": ContextRecall,
    }
    return [registry[name]() for name in metric_names]


def _run_ragas_evaluation(
    records: list[dict[str, Any]], metrics: list[str], config: dict[str, Any]
) -> list[dict[str, Any]]:
    try:
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
        from ragas import EvaluationDataset, SingleTurnSample, evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
    except ModuleNotFoundError as exc:
        raise RagEvaluationDependencyError(
            "Ragas evaluation dependencies are not installed or are incompatible"
        ) from exc

    samples = [
        SingleTurnSample(
            user_input=record["user_input"],
            response=record["response"],
            retrieved_contexts=record["retrieved_contexts"],
            reference=record.get("reference"),
        )
        for record in records
    ]
    dataset = EvaluationDataset(samples=samples)

    llm = LangchainLLMWrapper(
        ChatOpenAI(
            model=config["evaluator_model"],
            api_key=config["llm_api_key"],
            base_url=config["llm_base_url"],
            temperature=0,
        )
    )
    embeddings = None
    if config.get("embeddings_model"):
        embeddings = LangchainEmbeddingsWrapper(
            OpenAIEmbeddings(
                model=config["embeddings_model"],
                api_key=config["llm_api_key"],
                base_url=config["llm_base_url"],
            )
        )

    try:
        result = evaluate(
            dataset,
            metrics=_metric_objects(metrics),
            llm=llm,
            embeddings=embeddings,
            show_progress=False,
            raise_exceptions=bool(config.get("raise_exceptions")),
        )
    except Exception as exc:  # pragma: no cover - exercised only with live evaluator calls.
        raise RagEvaluationError(str(exc)) from exc

    if hasattr(result, "to_pandas"):
        return result.to_pandas().to_dict(orient="records")
    if hasattr(result, "scores"):
        return list(result.scores)
    return list(result)


def _result_scores(row: dict[str, Any], metrics: list[MetricName]) -> dict[str, Optional[float]]:
    source = row.get("scores", row)
    scores: dict[str, Optional[float]] = {}
    for metric in metrics:
        value = source.get(metric)
        scores[metric] = None if value is None else float(value)
    return scores


def evaluate_rag_records(
    request: RagEvaluationRequest,
    *,
    runner: Runner = _run_ragas_evaluation,
) -> RagEvaluationResponse:
    metrics = _unique_metrics(request.metrics)
    _validate_metric_requirements(request.records, metrics)
    config = _evaluation_config(request)
    records = _runner_records(request.records)
    raw_rows = runner(records, list(metrics), config)

    results = [
        RagEvaluationResult(
            record_index=index,
            scores=_result_scores(row, metrics),
            metadata=dict(row.get("metadata", {}) or {}),
        )
        for index, row in enumerate(raw_rows)
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
