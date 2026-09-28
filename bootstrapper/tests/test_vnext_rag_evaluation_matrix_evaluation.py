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


def test_decision_records_that_implementation_moved_downstream() -> None:
    """#1190: the "Atlas should add" decision stays, but is dated and points at
    the #565 closing decision that moved the runner to rag-showcase#24."""
    text = EVALUATION.read_text(encoding="utf-8")
    decision = text[text.index("## 1. Decision"): text.index("## 2. ")]
    status = decision.index("implementation transferred downstream")

    assert status < decision.index("Atlas **should** add")
    for link in (
        "https://github.com/thekaveh/atlas/issues/565#issuecomment-4964093037",
        "https://github.com/thekaveh/rag-showcase/issues/24",
    ):
        assert link in decision
    assert "not as current Atlas scope" in decision
