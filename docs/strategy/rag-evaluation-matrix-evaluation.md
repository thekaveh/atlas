# RAG Approach-by-Dataset Evaluation Matrix Evaluation

Generated for issue #416, "Build Next: RAG approach-by-dataset evaluation matrix on top of Ragas".

This is an **evaluation artifact**: it records a go/no-go decision and the evidence, contracts, and schema that a *separate future implementation ticket* must build against. It is deliberately not an implementation of the matrix runner. It mirrors the existing Atlas evaluation deliverables `docs/strategy/infisical-secrets-manager-evaluation.md` and `docs/strategy/authentik-sso-pilot-evaluation.md`.

## 1. Decision

Atlas **should** add a reusable **RAG approach-by-dataset evaluation matrix** as a **headless CLI/library orchestration layer** on top of the already-landed Ragas surface (#378), the consumer LiteLLM approach aliases (#411), and the reproducible ingestion profiles (#413). The matrix layer is an **orchestrator and result schema**, **not a new evaluator**: it reuses the backend `POST /api/rag/evaluate` surface for Ragas scoring and calls each approach through LiteLLM/OpenAI-compatible APIs, rather than duplicating evaluator configuration.

The **evidence and ranking contract is the product**. The first slice must be conservative:

- **Downstream-owned inputs.** Consumers own their corpora, questions, reference answers, and reports; Atlas owns the generic matrix execution, the raw-result schema, and the metric computation wiring.
- **Disabled by default.** No new always-on service; the runner ships as an opt-in CLI/library plus an optional backend route, and adds zero default consumers.
- **Evidence before scoring.** A plain OpenAI chat alias guarantees only an answer. Rows without retrieved contexts/sources stay **`not_evaluable`** for context-dependent metrics instead of being silently sent to Ragas and scored as if evidence existed.
- **Honest metric taxonomy.** Ragas evaluator-model metrics, deterministic operational metrics, and judge-panel scores are kept distinct in both the schema and the rankings. Ragas values are **not described as mathematically objective** — they are LLM-evaluator-computed and depend on the evaluator model and prompts.

## 2. Current Upstream Findings

### 2.1. What already exists on `main`

The foundation the matrix layer builds on has landed:

- **#378 Ragas surface.** The backend exposes `POST /api/rag/evaluate` (`services/backend/app/app/main.py`), implemented in `services/backend/app/app/rag_eval_service.py`. Supported metrics are `faithfulness`, `answer_relevancy`, `context_precision`, and `context_recall`; `context_precision` and `context_recall` are **reference metrics** that require a `ground_truth`. The default metric set is `["faithfulness", "answer_relevancy"]`. The dependency is pinned to `ragas==0.4.3` and is intentionally limited to the backend and JupyterHub surfaces (there is a companion `services/jupyterhub/build/notebooks/14_ragas_evaluation.ipynb`). A per-answer/context row is exactly the unit the matrix must feed.
- **#411 consumer LiteLLM model aliases.** A consumer can register OpenAI-compatible approaches declaratively as LiteLLM `model_name` rows (`litellm_models` in `atlas.consumer.yml`). This is how the matrix references "approaches" (`vanilla-rag`, `graph-rag`, `n8n-adaptive-rag`, …) as callable model aliases through one gateway.
- **#413 consumer RAG ingestion profiles.** A consumer declares versioned `rag_ingestion_profiles`; each profile carries a content-hash **`revision`** and produces a durable ingestion job. That `revision` + profile/job id is the reproducibility anchor that ties an evaluation row to the exact corpus state it was scored against.
- **#414 LightRAG query profiles** (adjacent, landed) let a consumer expose named graph-RAG query flavors as approach aliases — a natural source of `graph-rag local k=30` vs `graph-rag hybrid k=10` matrix approaches.

### 2.2. Ragas metric semantics (official sources)

Ragas metrics are **LLM-assisted evaluators**, not closed-form math. Faithfulness and the relevancy/context metrics prompt an evaluator LLM (and, for some, embeddings) and are therefore sensitive to the evaluator model, its version, and the prompt. Reviewed official documentation:

- Ragas metrics overview: https://docs.ragas.io/en/stable/concepts/metrics/
- Faithfulness: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/
- Response (answer) relevancy: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/answer_relevance/
- Context precision: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/
- Context recall: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/

Because these are evaluator-model-dependent, the matrix must record the evaluator model/provider version alongside every Ragas score and must **not** present Ragas numbers as mathematically objective ground truth. They are one class of signal among three (see §6).

## 3. Why Not Treat A Chat Alias As Sufficient Evidence

The naive shape — "call each `model` alias, collect the answer, hand it to Ragas" — is unsafe because most of the interesting metrics are **context-dependent**:

- `faithfulness` needs the **retrieved contexts** the answer was grounded in.
- `context_precision` / `context_recall` need both the **retrieved contexts** and a **`ground_truth`/reference**.
- Only `answer_relevancy` is computable from the answer (+ question) alone.

A plain OpenAI-compatible chat alias returns an answer but is not obligated to return the contexts/sources it used. If the harness fabricates empty contexts or silently drops rows, the resulting scores are misleading. Therefore the harness must define an **approach-evidence contract** up front, and a row that cannot supply the evidence a metric needs is recorded as **`not_evaluable`** for that metric — preserved, not hidden, and never scored as if evidence existed.

## 4. The Approach-Evidence Contract

Before any scoring, each `(approach, dataset, question)` execution records an **evidence row**. Fields:

| Field | Required | Meaning |
|---|---|---|
| `answer` | yes | The approach's generated answer text. |
| `retrieved_contexts` | when available | Ordered list of context chunks the answer was grounded in. Absent ⇒ context metrics are `not_evaluable`. |
| `sources` | when available | Source/document identifiers or URIs for the contexts (provenance). |
| `latency_ms` | yes | Wall-clock latency of the approach call (deterministic operational metric). |
| `token_usage` | when available | Prompt/completion/total tokens if the provider reports them. |
| `error` | on failure | Structured error/timeout record; the row is retained, not dropped. |
| `status` | yes | One of `ok`, `error`, `timeout`, `not_evaluable`. |

An approach can supply evidence either by returning an OpenAI-compatible response whose payload includes contexts/sources, or by being a two-call approach (retrieve, then answer) the runner understands. The contract is explicit so a consumer knows what a compliant approach must emit to be scored on context-dependent metrics.

## 5. The Versioned Matrix Schema

The runner consumes a **versioned** matrix definition (`version: 1`) with:

- `datasets[]` — `id`, `corpus_profile` (a #413 ingestion profile name for reproducibility), `questions_file`, and optional `reference`/`ground_truth` fields per question.
- `approaches[]` — `model` (a LiteLLM alias, typically from #411/#414), plus optional per-approach `params` and a declared evidence capability (`answer_only` vs `answer_with_contexts`).
- `metrics.ragas[]` — the subset of Ragas metrics to apply (`faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`), routed to `POST /api/rag/evaluate`. (The issue's example YAML named this key `objective`; it is renamed `ragas` here because — per §6 — these values are evaluator-model-dependent and must not be labelled "objective".)
- `metrics.judge_panel` — optional; `enabled`, `models[]`. Kept **separate** from the Ragas metrics.
- `run` — `retries`, `timeout_s`, `concurrency`, and a `seed` where an approach honors one.
- `reproducibility` — recorded automatically (see §7).

Question ids are stable and required so the same approach can be compared across increasing dataset complexity (**longitudinal** comparison), and so a resumed run can skip completed `(approach, dataset, question)` cells.

## 6. Metric Taxonomy (Three Distinct Classes)

The schema and every ranking keep three classes distinct and never blend them into a single "score":

1. **Ragas evaluator-model metrics** — `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`. LLM-evaluator-computed; evaluator model/version recorded; **not mathematically objective**.
2. **Deterministic operational metrics** — `latency_ms`, `token_usage`, error/timeout rates, `not_evaluable` counts. Reproducible and provider-reported; these are the only truly deterministic numbers.
3. **Judge-panel scores** — optional LLM-as-judge panel; explicitly subjective, panel models recorded, and reported in their own columns. Disabling the panel must not change the Ragas evaluator-model columns or the deterministic operational columns.

## 7. Reproducibility Metadata

Every run and every row records enough to make longitudinal comparison valid:

- Approach model + provider + version (as resolved through LiteLLM).
- Evaluator model + version used by the Ragas surface.
- Prompt/config **hashes** for the approach and the evaluator configuration.
- Ingestion **profile name + `revision` + job id** (#413) for each dataset's corpus state.
- Matrix schema version, runner version, and a run id/timestamp (supplied by the caller, not wall-clock-derived inside a pure function).
- Per-row `latency_ms`, `token_usage`, and `error`.

## 8. Durable Output Contract

- **Canonical raw output is append-safe JSONL** — one line per `(approach, dataset, question)` evidence+scores row. Append-safe so an interrupted run resumes by reading completed cells and continuing, and so a row is never lost.
- **Deterministic summary JSON** — computed from the JSONL: per-dataset and overall aggregates. Given the same JSONL it is byte-stable (sorted keys, no wall-clock inside the pure summary function).
- **CSV / Markdown are derived views** only, generated from the JSONL/summary; they are never the source of truth.
- **Every failed or skipped question is preserved** — `error`, `timeout`, and `not_evaluable` rows appear in the JSONL and are counted in the summary. Rankings are computed **without hiding per-question failures**; a dataset-level or overall ranking must surface the coverage denominator (how many rows were actually evaluable) next to the aggregate.

## 9. Ranking Rules

- Rankings are computed per dataset **and** overall, per metric class, never as a single blended number.
- Ties are reported as ties (stable, explicit) rather than broken by row order.
- The longitudinal view holds an approach fixed and shows how its scores and its `not_evaluable`/error coverage shift as datasets become more complex or more graph-native.
- A ranking that drops rows to look better is a defect: coverage is always reported next to the aggregate.

## 10. Where It Lives

- **Headless first.** A CLI/library that runs in CI or a local script is the first surface. It calls approaches through LiteLLM and Ragas through the existing `POST /api/rag/evaluate` service; it does **not** duplicate evaluator configuration or re-pin `ragas`.
- **Optional backend route later.** A thin backend endpoint that kicks off / streams a matrix run can follow, but is not required for the first slice and must stay disabled-by-default with no new default consumers.
- **Langfuse** integration (trace correlation) is explicitly deferred; it is a later enhancement, not a first-pass requirement.

## 11. Acceptance Criteria For The Future Implementation Ticket

- A versioned matrix schema (`version: 1`) exists with datasets, approach aliases, question ids, expected/reference fields, evaluator metrics, optional judges, retries/timeouts, and reproducibility metadata.
- The runner accepts multiple datasets × multiple model aliases and runs **headlessly** in CI or a local script.
- Each cell records answer, retrieved contexts/sources where available, latency, token usage if available, errors/timeouts, and metric scores; rows lacking evidence are `not_evaluable`, never fabricated.
- Ragas metrics from #378 are applied per answer/context row through the existing `POST /api/rag/evaluate` surface (no duplicated evaluator config, no re-pinned `ragas`).
- Optional judge-panel scoring is supported and clearly separated from the Ragas evaluator-model metrics and the deterministic operational metrics; Ragas is not described as mathematically objective.
- Canonical output is **append-safe JSONL**; a deterministic summary JSON is derived; CSV/Markdown are derived views; a run resumes after interruption without losing or double-counting cells.
- Dataset-level and overall rankings are computed without hiding per-question failures, with coverage reported next to each aggregate, and support longitudinal comparison of one approach across increasing dataset complexity.
- Reproducibility metadata (model/provider versions, prompt/config hashes, ingestion profile/`revision`/job id, run id) is recorded so longitudinal comparisons are valid.
- Tests cover: two approaches × two datasets, resume-after-interruption, one failing approach, missing contexts/references (`not_evaluable`), Ragas-only mode (no judges), judge-disabled and judge-failure modes, tie handling, and deterministic summary generation.
- Docs include a `rag-showcase`-shaped example and explain which metrics are evaluator-model-based, which are deterministic-operational, and which are subjective judge-panel scores.
- The runner ships **disabled by default** with zero new default consumers; corpora, questions, and reports remain downstream-owned.

## 12. Recommendation

Close #416 as an evaluation artifact once this document lands. Create a separate **implementation** issue for the matrix runner that builds to §11, starting with the headless CLI/library that orchestrates the existing #378 Ragas surface and LiteLLM approach aliases. Keep the first slice narrow: schema + evidence contract + JSONL/summary output + rankings-with-coverage + tests, with the optional backend route and Langfuse correlation as explicit follow-ups. The downstream payoff is that `rag-showcase` (tracked in `thekaveh/rag-showcase#24`) can retire its bespoke comparison runner and result schema in favor of the Atlas matrix outputs while keeping its dataset-specific narrative and reporting.
