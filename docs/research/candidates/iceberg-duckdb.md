---
category-fit: data
generated: 2026-05-19
license: Apache-2.0
name: Apache Iceberg + DuckDB
referenced-by: [minio]
slug: iceberg-duckdb
type: external-service
upstream: https://iceberg.apache.org/
---

# Apache Iceberg + DuckDB

## 1. Headline
Open table format (Iceberg) on top of MinIO plus an embedded SQL engine (DuckDB) that gives the stack a queryable analytics tier over the artifact buckets without standing up a warehouse.

## 2. Problem it solves
MinIO holds five buckets of unstructured artifacts (ComfyUI renders, docling parses, n8n exports, jupyter datasets, backend blobs). There's no way to ask cross-bucket questions like "how many docling parses last week produced a payload >1 MB?" or "what's the token cost per ComfyUI workflow?" without writing one-off scripts. Iceberg + DuckDB turns Parquet/JSON dumped to MinIO into versioned tables that any consumer can query via standard SQL.

## 3. Stack wiring sketch
- jupyterhub → DuckDB (embedded in the notebook kernel) → Iceberg catalog → minio (`s3://analytics/`).
- backend → DuckDB (Python `duckdb` package) → same Iceberg tables for serving aggregated stats.
- n8n → DuckDB CLI (HTTP node calling a sidecar) for scheduled rollups.
- doc-processor → writes parsed docs as Iceberg-compatible Parquet to `s3://analytics/docling/`.
- A lightweight `iceberg-rest-catalog` container (Tabular's reference image) sits between consumers and minio for metadata coordination.

## 4. Effort
medium — new `iceberg-rest-catalog` service.yml + Kong alias + a sixth MinIO bucket slot for `analytics`; consumer wiring is a `pip install duckdb iceberg` in the relevant images.

## 5. Risks & open questions
- DuckDB's Iceberg writer is newer than its reader — production writes may be limited; could mitigate by writing Parquet directly and registering via REST catalog.
- Catalog persistence: REST catalog needs Postgres → another Supabase tenant schema, or SQLite-on-volume.
- Migration story when Iceberg spec versions bump.

## 6. Why now (and why not sooner)
This only becomes interesting once the artifact buckets actually have content. With MinIO just landing and the per-consumer buckets pre-provisioned, the data layer is finally ready to support an analytics overlay; doing this earlier would have been speculative.

## 7. Upstream evidence
- https://iceberg.apache.org/ — spec + REST catalog reference.
- https://duckdb.org/docs/extensions/iceberg.html — DuckDB Iceberg extension docs.
- https://min.io/blog/iceberg-on-minio/ — MinIO's own Iceberg integration write-up.
