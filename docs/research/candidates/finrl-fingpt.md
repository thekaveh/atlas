---
category-fit: apps
generated: 2026-07-04
license: MIT
name: FinRL And FinGPT
referenced-by: []
slug: finrl-fingpt
type: external-service
upstream: https://github.com/AI4Finance-Foundation/FinRL
---

# FinRL And FinGPT

## Headline
Deferred-to-notebooks financial AI libraries for reinforcement-learning research and finance-language modeling; useful in Atlas only as guarded JupyterHub assets, not production trading intelligence.

## Problem it solves
FinRL gives researchers a financial reinforcement-learning framework for market environments, agents, and trading research workflows. FinGPT gives researchers and builders finance-oriented language-model tooling for sentiment, forecasting, benchmark, and task adaptation experiments. Both are valuable in the trading track, but their outputs can be mistaken for investment advice or automated trading decisions if Atlas presents them as services.

## Deferred-to-notebooks decision (2026-07-04)
Atlas should keep FinRL and FinGPT deferred to notebooks, must not add `services/finrl/service.yml`, and must not add `services/fingpt/service.yml`.

They are research notebook assets, not production trading intelligence. A future Atlas slice may add curated notebooks, pinned Python extras, or optional JupyterHub image profiles, but it must not present either project as push-button trading AI, a signal service, a live execution engine, or investment advice. The existing trading track remains read-only financial research and paper portfolios in notebooks; no live trading.

Current upstream evidence supports this boundary:

- FinRL is a financial reinforcement-learning framework organized around market environments, agents, and financial applications, with education and research examples.
- The FinRL docs include explicit trading-risk disclaimers and say the material is not financial advice.
- FinGPT is an open-source financial LLM framework and model family for finance-language tasks such as sentiment, forecasting, and benchmark/fine-tuning experiments.
- FinRL-Trading/FinRL-X points toward AI-native quantitative-trading infrastructure, which makes Atlas' notebook-only boundary more important until service-level trading guardrails exist.

## Stack wiring sketch
No standalone Atlas service wiring should be added while FinRL and FinGPT remain deferred. If a later notebook slice is approved, the expected topology is:

- JupyterHub -> FinRL/FinGPT packages for curated notebooks, local experiments, benchmark notebooks, and paper-only examples.
- OpenBB and CCXT -> notebooks for read-only market data; private CCXT methods and live exchange credentials remain blocked by Atlas helpers.
- MinIO -> notebooks for versioned datasets, parquet/CSV snapshots, model artifacts, and exported experiment inputs.
- MLflow -> notebooks for experiment metrics, parameters, artifacts, and reproducibility of paper or offline runs.
- LiteLLM -> FinGPT notebooks for LLM summarization or model comparison through Atlas' gateway, not autonomous investment decisions.
- Langfuse -> LiteLLM/notebook calls for traceability when LLM-generated strategy explanations or forecasts are produced.
- TimescaleDB -> notebooks only after the watchlisted trading-data slice defines isolated schemas, retention, and read-only/paper credentials.
- No current data_flow.calls should point from backend, n8n, Hermes, Open WebUI, or any execution surface to FinRL/FinGPT outputs as trading commands.

## Effort
Medium. Adding notebook examples is manageable, but doing it safely requires pinned dependencies, curated datasets, deterministic examples, disclaimers, model-size/resource limits, eval criteria, and paper-trading guardrails before these libraries appear in a user-facing track.

## Risks & open questions
- User-harm risk: generated forecasts or RL policies can be misread as investment advice or production trading signals.
- Data quality: financial RL and forecasting examples are highly sensitive to survivorship bias, look-ahead bias, split hygiene, corporate actions, fees, slippage, and venue differences.
- Reproducibility: notebooks need pinned datasets, seeds, package versions, and artifact storage before results are useful.
- Model risk: FinGPT examples may require model downloads, GPU memory, external APIs, or fine-tuning data that Atlas has not scoped.
- Execution boundary: FinRL outputs must not feed CCXT private methods, Hummingbot/Freqtrade/NautilusTrader live services, n8n workflows, or backend order APIs by default.
- Compliance posture: Atlas needs clear disclaimers and auditability before financial AI outputs become anything more than research.

## Future notebook contract if adopted
- **Tracks:** `trading`, `ml-eng`, and `all`. Keep the `trading` track copy as read-only financial research and paper portfolios until a later paper-service ticket is approved.
- **Category:** `apps`, because the approved surface is JupyterHub notebooks and image/package content, not a new runtime service. Do not add a new category for FinRL/FinGPT.
- **Sources:** no standalone SOURCE values. Do not add `FINRL_SOURCE` or `FINGPT_SOURCE`; use `JUPYTERHUB_SOURCE=container` plus a later explicit notebook/package flag only if Atlas establishes a JupyterHub image-profile convention. Any optional profile must be disabled by default.
- **Wizard placement:** after the OpenBB/CCXT financial research kit and before any trading execution discussion, with copy that these are research notebooks, not financial advice, not production trading intelligence, and not live execution.
- **Ports and routes:** no ports, no Kong aliases, and no direct URLs. Access remains through JupyterHub. Custom `BASE_PORT` only matters indirectly through JupyterHub, MLflow, MinIO, LiteLLM, and Langfuse.
- **Required dependencies:** JupyterHub, MinIO, MLflow, LiteLLM, Langfuse, OpenBB, and CCXT for the first notebook slice.
- **Optional dependencies:** TimescaleDB only after the trading-data watchlist decision is reopened; Ray only for later controlled training/evaluation scale-out; no live trading engines in the first slice.
- **Downstream consumers:** human notebook users and MLflow dashboards. Backend, n8n, Hermes, Open WebUI, and MCP consumers may read summaries only after a future tool-safety design, not raw trade signals.
- **Manifest and topology:** no service manifest, topology row, route, or `data_flow.calls` edge for FinRL/FinGPT while they are notebook assets. Future docs may mention conceptual notebook calls from JupyterHub to MinIO, MLflow, LiteLLM, and Langfuse.
- **Datasets:** curated datasets must have provenance, licenses, date ranges, train/validation/test splits, symbol universes, fees/slippage assumptions, and no hidden live credential requirement.
- **Eval criteria:** notebooks must report baseline comparisons, walk-forward or out-of-sample checks where applicable, drawdown, turnover, costs, and failure cases. Generated model text must be framed as analysis, not advice.
- **Paper-trading guardrails:** no live exchange credentials, no private CCXT methods, no order submission, no automatic promotion from notebook output to execution, and no agent workflow that treats outputs as orders.
- **Tests required:** focused decision tests, financial-helper guard tests, notebook registration checks, dependency pin checks if packages are added, no-service-manifest checks, research schema, docs drift, link checks, track membership, custom `BASE_PORT` indirect checks through existing services, and full bootstrapper pytest.
- **Edge cases:** missing optional GPU, model download failures, stale cached datasets, time-zone/calendar mismatches, look-ahead bias, empty symbols, insufficient history, API rate limits, disabled MinIO/MLflow/Langfuse, stale `.env` live keys, disabled trading track, notebook output copied into n8n, generated-doc drift, and misleading "AI trader" wording.

## Revisit criteria
Reconsider a FinRL/FinGPT notebook slice only when all of these are true:

- Atlas has curated datasets, eval criteria, and paper-trading guardrails.
- The OpenBB/CCXT financial research kit has stable usage and documented dataset/provenance patterns.
- The future work is framed as notebooks or image extras, not standalone services.
- Docs and notebooks include clear not financial advice disclaimers.
- No issue or PR presents FinRL or FinGPT as push-button trading AI or production trading intelligence.

## Why now (and why not sooner)
This decision belongs next to the live-trading rejection because FinRL and FinGPT can accidentally become the "AI" justification for unsafe trading automation. Capturing the notebook-only boundary now keeps the trading track useful for research while avoiding a production trading claim Atlas cannot safely support.

## Upstream evidence
- https://github.com/AI4Finance-Foundation/FinRL
- https://finrl.readthedocs.io/en/latest/index.html
- https://github.com/AI4Finance-Foundation/FinRL-Trading
- https://github.com/AI4Finance-Foundation/finrl-meta
- https://github.com/ai4finance-foundation/fingpt
- https://fingpt.io/
- https://ai4finance.org/research/fingpt-open-source-finllm.html

## Cross-references
- `../../strategy/atlas-vnext-strategy-report.md#82-trading--financial-ai-track`
- `../../strategy/atlas-vnext-strategy-report.md#94-reject-or-defer-for-now`
- `../candidates/live-trading-services.md`
- `../candidates/timescaledb.md`
