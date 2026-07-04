---
category-fit: agents
generated: 2026-07-04
license: Apache-2.0 / GPL-3.0 / LGPL-3.0
name: Live Trading Services
referenced-by: []
slug: live-trading-services
type: external-service
upstream: https://hummingbot.org/docs/
---

# Live Trading Services

## Headline
Rejected-for-now trading execution services for Hummingbot, Freqtrade, and NautilusTrader; Atlas should stay research and paper-first until safety, secrets, audit, and operator-risk controls exist.

## Problem it solves
Live trading engines can run strategy backtests, paper or sandbox simulations, and real exchange execution. Hummingbot and Freqtrade are useful crypto-bot ecosystems, while NautilusTrader is a serious multi-asset backtesting, sandbox, and live-trading engine. They become relevant only after Atlas proves a safe financial research path and has explicit controls for real-money workflows.

## Rejected-for-now decision (2026-07-04)
Atlas should keep live trading services rejected for now and must not add `services/hummingbot/service.yml`, must not add `services/freqtrade/service.yml`, and must not add `services/nautilustrader/service.yml` in this decision ticket.

The approved trading posture is read-only financial research and paper portfolios in JupyterHub. There must be no live exchange trading, no default broker or exchange execution path, and no UI language that implies investment advice, guaranteed performance, or "push-button trading AI." Financial docs and notebooks must keep the existing not financial advice posture.

Current upstream docs make the boundary clear:

- Hummingbot supports paper trading and testnet-style flows, but its product surface is still automated trading against centralized and decentralized exchanges.
- Freqtrade supports dry-run mode with a simulated wallet, but also documents live-trade operation.
- NautilusTrader shares core code across backtest, sandbox, and live systems and documents live deployment as a first-class mode.

That shared backtest-to-live path is exactly why Atlas should not add these as selectable services until the platform can prevent accidental promotion from research to real-money execution.

## Stack wiring sketch
No current Atlas wiring should be added while live trading services remain rejected. If a later ticket reopens this decision, the first topology should be paper or sandbox only:

- JupyterHub -> Hummingbot/Freqtrade/NautilusTrader for controlled strategy notebooks, paper portfolio handoff, and backtest notebooks only.
- MinIO -> trading services for historical CSV/parquet datasets, model artifacts, and immutable run inputs.
- MLflow -> trading services for paper-run metrics, parameters, artifacts, and model or strategy comparison, never as a promotion-to-live control plane in the first slice.
- Langfuse -> backend/JupyterHub for LLM-generated strategy explanation and traceability, not for autonomous order approval.
- TimescaleDB -> trading services only after the watchlisted trading-data slice defines isolated schemas, retention, and read-only/paper credentials.
- Redpanda -> trading services only for future market-data fan-out, not for order-routing commands in the first slice.
- Grafana/Superset -> TimescaleDB/MLflow/MinIO for read-only dashboards over paper and historical runs.
- n8n -> trading services only through a promotion workflow that is disabled by default and guarded by explicit operator approval.
- Infisical or OpenBao -> trading services for scoped paper/sandbox secrets before any exchange credential is accepted.

## Effort
Large. The container mechanics are not the hard part; the hard part is service admission, secrets isolation, auditability, route scoping, safe defaults, paper/live separation, exchange-specific credential semantics, and proving that Atlas cannot accidentally execute real orders.

## Risks & open questions
- Financial harm: live order execution can lose real money, so Atlas must treat this as a high-risk domain rather than a normal app integration.
- Secrets: private exchange keys must not live in `.env`, notebooks, n8n nodes, browser storage, or logs. Read-only keys and paper/sandbox keys must be distinguishable from live trade-enabled keys.
- Operator controls: promotion from notebook/backtest/paper to live must require explicit human approval, clear capital limits, venue limits, kill switches, and audit records.
- Route posture: no Kong route for order-execution APIs should be exposed by default. Public UIs can exist only after auth, disclaimers, and role checks are settled.
- Data topology: historical market data, paper orders, live orders, and audit logs need separate schemas, retention policies, and provenance rules.
- Automation boundary: n8n, agents, LLM summaries, and notebooks must not be able to call private/trading methods or sign orders by default.
- Licensing and fit: Hummingbot is Apache-2.0, Freqtrade is GPL-3.0, and NautilusTrader is LGPL-3.0; Atlas must evaluate distribution and modification implications before bundling images.
- Scope: NautilusTrader's own project posture keeps distributed orchestration, UI dashboards, and built-in AI/ML tooling out of scope, so Atlas would own more control-plane behavior than the engine provides.

## Future service contract if reopened
- **Tracks:** `trading` and `all`. Do not add live trading services to `data-eng`, `gen-ai-eng`, or `ml-eng` by default. The current `trading` track remains read-only financial research and paper portfolios until this decision is explicitly reopened.
- **Category:** `agents` for bot/strategy runners such as Hummingbot and Freqtrade. NautilusTrader may be `apps` if Atlas ships it as a notebook/backtest service surface rather than a bot daemon. Do not introduce a new category unless Atlas later adds a broader `trading` service class.
- **Sources:** `HUMMINGBOT_SOURCE=disabled|container|localhost`, `FREQTRADE_SOURCE=disabled|container|localhost`, and `NAUTILUSTRADER_SOURCE=disabled|container|localhost`; all disabled by default. Container modes must start in paper mode or sandbox mode. Localhost modes must require explicit operator-managed endpoint URLs and must not import live credentials automatically.
- **Wizard placement:** a trading safety step after the OpenBB/CCXT financial research kit and after secrets-manager setup. Prompt copy must say the service is paper/sandbox only, not financial advice, disabled by default, and cannot place live orders in the first Atlas integration.
- **Ports and aliases:** allocate any UI/API ports through Atlas topology/category slot rules with custom `BASE_PORT` support. Candidate aliases are `hummingbot.localhost`, `freqtrade.localhost`, and `nautilustrader.localhost`, but order-execution APIs must stay internal-only or disabled until auth and risk controls exist.
- **Kong route behavior:** no Kong route for order-execution APIs by default. If a read-only dashboard route exists, it must be separated from any API that can create, cancel, edit, transfer, withdraw, borrow, or submit orders.
- **Required dependencies:** JupyterHub, MinIO, MLflow, and Langfuse for the first paper/backtest slice. Infisical or OpenBao, audit logs, read-only keys, and operator risk controls are prerequisites before any credentialed exchange connector exists.
- **Optional dependencies:** TimescaleDB for isolated market-history schemas, Redpanda for market-data streams, Grafana or Superset for read-only paper-run dashboards, and backend/n8n only for guarded orchestration.
- **Downstream consumers:** JupyterHub notebooks, backend read-only summaries, n8n approval flows, Grafana/Superset dashboards, and MLflow experiment tracking. LLM/agent consumers must receive summaries and tool-limited analysis rather than direct order placement.
- **Manifest and topology:** if reopened, each service needs an explicit `services/<name>/service.yml`, topology row, source validation, env assembly, generated docs, and `data_flow.calls` entries that distinguish market-data reads, paper orders, live-order-capable surfaces, audit writes, and secrets access.
- **Init companion:** likely yes. An init companion should create paper profiles, seed safe example configs, validate that live trading is disabled, check credential scopes, create isolated log/audit directories, and refuse startup when live credentials appear without an explicit future approval flag.
- **Volumes and secrets:** separate config, logs, strategy code, historical data, paper-run state, and audit volumes. Secrets must come from a secrets manager, not static `.env` values, once any credentialed connector exists.
- **Tests required:** manifest validation, source validation, env-example assembly, track membership, disabled-default behavior, no-live route audit, Kong alias gating, custom `BASE_PORT`, compose/source permutations, docs drift, research schema, financial-helper private-method blocks, stale `.env` migration, localhost-mode endpoint validation, missing-secrets failures, init idempotency, and service-specific smoke tests that prove paper mode cannot submit live orders.
- **Edge cases:** disabled JupyterHub, disabled MLflow, missing MinIO, missing secrets manager, stale live exchange keys in `.env`, paper-to-live config drift, exchange sandbox outages, wrong venue/account, clock skew, duplicate bot instances, queue backlogs, strategy code injection, leaked logs, disk growth, disabled track behavior, prod-profile restrictions, and generated-doc drift.

## Revisit criteria
Reconsider Hummingbot, Freqtrade, or NautilusTrader only after all of these are true:

- The OpenBB/CCXT financial research kit has enough usage to justify a paper/backtest service.
- Paper mode, sandbox mode, secrets management, audit logs, and explicit operator risk controls are already shipped.
- Atlas has a secrets-manager path for read-only, paper, sandbox, and live credentials with separate scopes.
- Atlas has clear disclaimers and product copy that avoid investment advice or autonomous-money-management claims.
- A future issue defines exactly one paper-first service slice, not three engines at once.

## Why now (and why not sooner)
This ticket should capture the rejection boundary now because the trading track already exists and the financial research kit is already notebook-ready. Keeping the decision explicit prevents a future worker from reading "trading track" as permission to add a live execution engine.

## Upstream evidence
- https://hummingbot.org/docs/
- https://hummingbot.org/client/global-configs/paper-trade/
- https://hummingbot.org/hummingbot-api/
- https://github.com/hummingbot/dashboard
- https://www.freqtrade.io/en/stable/
- https://www.freqtrade.io/en/stable/configuration/
- https://www.freqtrade.io/en/stable/bot-usage/
- https://nautilustrader.io/docs/latest/concepts/overview/
- https://nautilustrader.io/docs/latest/concepts/backtesting/
- https://nautilustrader.io/docs/latest/concepts/live/
- https://github.com/nautechsystems/nautilus_trader

## Cross-references
- `../../strategy/atlas-vnext-strategy-report.md#82-trading--financial-ai-track`
- `../../strategy/atlas-vnext-strategy-report.md#94-reject-or-defer-for-now`
- `../candidates/timescaledb.md`
