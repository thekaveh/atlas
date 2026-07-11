from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
EVALUATION = REPO_ROOT / "docs" / "strategy" / "rag-evaluation-matrix-evaluation.md"


def test_rag_evaluation_matrix_evaluation_records_required_decisions() -> None:
    text = EVALUATION.read_text(encoding="utf-8")

    required_phrases = (
        # framing: an evaluation artifact, not the runner
        "evaluation artifact",
        "not an implementation of the matrix runner",
        "Acceptance Criteria For The Future Implementation Ticket",
        # the go/no-go shape
        "headless CLI/library",
        "not a new evaluator",
        "disabled by default",
        "downstream-owned",
        # reuse the landed surfaces, do not duplicate the evaluator
        "POST /api/rag/evaluate",
        "ragas==0.4.3",
        "#378",
        "#411",
        "#413",
        # the evidence contract
        "approach-evidence contract",
        "not_evaluable",
        # honest metric taxonomy (three distinct classes)
        "Ragas evaluator-model metrics",
        "deterministic operational metrics",
        "judge-panel scores",
        "mathematically objective",
        # durable output + rankings
        "append-safe JSONL",
        "deterministic summary JSON",
        "without hiding per-question failures",
        "longitudinal",
        # reproducibility anchor from #413
        "revision",
        # downstream payoff
        "rag-showcase",
    )

    missing = [p for p in required_phrases if p not in text]
    assert not missing, f"evaluation doc missing required phrases: {missing}"


def test_rag_evaluation_matrix_evaluation_links_official_sources() -> None:
    text = EVALUATION.read_text(encoding="utf-8")

    for url in (
        "https://docs.ragas.io/en/stable/concepts/metrics/",
        "https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/",
        "https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/answer_relevance/",
        "https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/",
        "https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/",
    ):
        assert url in text, f"missing official source link: {url}"
