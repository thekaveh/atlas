# 6.9. Data Engineering Lakehouse Flow

MinIO, Iceberg REST, Spark, JupyterHub, Zeppelin, Airflow, Trino, and Redpanda.

## 1. Diagram

[Open the interactive diagram](./data-engineering-lakehouse-flow.html).

## 2. Notes

Iceberg REST's catalog metadata lives in Supabase Postgres via a JDBC catalog, not a Hive metastore — if `CATALOG_URI` isn't pointed at `jdbc:postgresql://supabase-db:5432/iceberg`, the base image silently falls back to a local SQLite catalog and metadata vanishes on restart. Trino runs single-coordinator, no worker scaling, by design. Spark still starts with `ICEBERG_REST_SOURCE=disabled` for ML-only use; only lakehouse SQL fails.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `bootstrapper/services/topology.py`
- `docs/deployment/source-configuration.md`
