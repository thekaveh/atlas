---
category-fit: data
generated: 2026-07-04
license: Apache-2.0 / Timescale License
name: TimescaleDB
referenced-by: [supabase]
slug: timescaledb
type: external-service
upstream: https://github.com/timescale/timescaledb
---

# TimescaleDB

## Headline
Postgres time-series extension for tick, OHLCV, order-book, and paper-trading history once Atlas has a real trading-data slice.

## Watchlist decision (2026-07-04)

Keep TimescaleDB on the watchlist for now: Atlas **must not add `services/timescaledb/service.yml` yet** and must not modify the Supabase Postgres image in this ticket. TimescaleDB belongs in a later trading-data slice, not standalone Atlas platform infrastructure.

The current trading track is intentionally read-only financial research plus paper portfolios in JupyterHub. TimescaleDB becomes useful after those notebooks create enough tick, OHLCV, order-book, trade, or paper-order history to justify hypertables, compression/columnstore, and retention policies. Until then, plain MinIO datasets and notebook-owned CSV/parquet are enough.

Future service shape, if a later trading-data ticket promotes this:

- Track membership: `trading` and `all`. Do not add it to data-eng by default unless a non-trading time-series contract is separately approved.
- Service category: `data`, because it is storage/infrastructure for market history.
- Source values/default: `TIMESCALEDB_SOURCE=disabled|extension|container`, disabled by default.
- Deployment choice: explicitly choose between enabling TimescaleDB inside the existing Supabase Postgres family, building a custom Supabase-compatible Postgres image, or running a separate TimescaleDB Postgres family. Do not silently mutate the protected Supabase core database.
- Wizard placement: trading/data storage section after the financial research kit, with prompt copy warning that it is historical/paper market data only.
- Topology and port strategy: no new port for `extension` mode; allocate a `data` topology slot only for a separate container mode.
- Kong route behavior: no public Kong route. Database access remains in-network or host Postgres tooling only.
- Required dependencies: `timescaledb -> supabase` for extension mode, or a separate Postgres-compatible data volume for container mode.
- Optional producers/consumers: `jupyterhub -> timescaledb` for OpenBB/CCXT research inserts, optional `redpanda -> timescaledb` for future market-data stream sinks, MLflow for paper-run metadata, MinIO for raw CSV/parquet archives, and Grafana/Superset later for read-only dashboards.
- Data guardrails: isolated `trading` database/schema, read-only/paper ingestion credentials, no live exchange credentials, explicit symbol/venue/timeframe namespace, and no mutation path from notebooks to real broker/exchange APIs.
- Retention policies: define raw tick, OHLCV, order-book snapshot, trade, and paper-order retention before storage is added.
- Hypertable design: define time column, partitioning dimensions, hypertable chunk interval, indexes, compression/columnstore policy, and downsampling/continuous aggregate strategy.
- Init companion: likely yes for either path. It must create isolated schemas/roles, install/validate the extension if applicable, create hypertables idempotently, and apply retention/compression policies only to Atlas-owned tables.
- Tests required for a future service PR: manifest validation, source validation, env assembly, topology/category, track membership, disabled default, no public Kong route, custom `BASE_PORT`, migration/idempotency checks, retention/compression SQL fixtures, read-only/paper credential tests, compose source-permutation coverage, docs drift, and an opt-in smoke that creates a tiny hypertable in an isolated schema.
- Edge cases: existing Supabase volume without TimescaleDB libraries, extension upgrade/downgrade, TimescaleDB license mode differences, hypertable creation reruns, accidental writes to public/auth schemas, stale `.env`, disabled financial research kit, disk growth, and generated-doc drift.

## Problem it solves
Trading research needs high-volume time-series storage once notebooks move beyond tiny CSV samples. TimescaleDB keeps market data close to Postgres semantics while adding hypertables, compression/columnstore, continuous aggregates, and retention automation. For Atlas, the right first use is paper and historical data, not live execution.

## Stack wiring sketch
- jupyterhub -> timescaledb for read-only OpenBB/CCXT market-data research inserts and paper portfolio history.
- timescaledb -> supabase when implemented as an extension path on the existing Postgres family.
- redpanda -> timescaledb later if market-data streams need a durable sink.
- minio -> timescaledb only by import jobs that load archived CSV/parquet datasets.
- grafana/superset -> timescaledb later for read-only dashboards.

## Effort
medium — the SQL is manageable, but the hard part is choosing extension-vs-container architecture without destabilizing Supabase, then defining market-data schemas, retention, compression, and paper-only safety boundaries.

## Risks & open questions
- Supabase image coupling: enabling TimescaleDB inside the existing DB may require a custom image or extension install path, which affects the most critical always-on service.
- Storage growth: tick/order-book data grows quickly; raw retention and downsampling must be explicit from day one.
- Safety: a trading database can become a bridge to live trading if API credentials and notebook helpers are not constrained to read-only/paper behavior.
- License and feature mode: TimescaleDB has Apache-licensed and Timescale-licensed/community feature surfaces; retention/compression behavior must be verified against the chosen image/tag.

## Upstream evidence
- https://github.com/timescale/timescaledb
- https://github.com/timescale/timescaledb-docker
- https://hub.docker.com/r/timescale/timescaledb
- https://www.tigerdata.com/docs/build/data-management/data-retention/create-a-retention-policy
- https://www.tigerdata.com/docs/build/examples/analyze-financial-tick-data
