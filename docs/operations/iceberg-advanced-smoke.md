# 7.4. Iceberg Advanced Smoke

This opt-in smoke test checks advanced Iceberg operations on the existing
data-eng services. It adds no infrastructure.

## 1. Scope

- No new service, no new SOURCE, no new port, no new Kong route, and no new
  wizard step.
- Tracks: `data-eng` and `all`.
- Existing services: Spark, Iceberg REST, MinIO, JupyterHub, and optionally
  Zeppelin.
- Categories stay unchanged: Spark and Iceberg REST are `data`; JupyterHub and
  Zeppelin are `apps`; MinIO remains `data`.
- The smoke uses isolated `lakehouse.atlas_smoke` objects. It does not create
  `bronze`, `silver`, or `gold` namespaces because downstream projects own
  their medallion layout.

## 2. Start The Required Stack

```bash
./start.sh --track data-eng \
  --spark-source container \
  --iceberg-rest-source container \
  --minio-source container \
  --jupyterhub-source container
```

For the Zeppelin surface, add `--zeppelin-source container` to the same command.

## 3. Run The Smoke

Spark Connect path:

```bash
scripts/smoke-iceberg-advanced-sql.sh spark-connect
```

Zeppelin standalone Spark path:

```bash
scripts/smoke-iceberg-advanced-sql.sh zeppelin
```

Both:

```bash
scripts/smoke-iceberg-advanced-sql.sh all
```

Run the script from the Atlas root. It reads `PROJECT_NAME` from `.env`; the
shell value is a fallback, then `atlas`. The Spark Connect surface runs inside
`<PROJECT_NAME>-jupyterhub` against `sc://spark-connect:15002`. The Zeppelin surface imports
`services/zeppelin/notebooks/iceberg_advanced_sql.zpln` through the Zeppelin REST
API and runs it with Zeppelin's seeded standalone Spark interpreter at
`spark://spark-master:7077`.

## 4. Capabilities Covered

- `MERGE INTO` row-level upsert against an Iceberg format-version 2 table.
- Snapshot metadata and `VERSION AS OF` time travel.
- `CALL lakehouse.system.rollback_to_snapshot(...)`.
- `ALTER TABLE ... CREATE BRANCH` plus `spark.wap.branch` writes and
  `fast_forward`.
- `ALTER TABLE ... ADD COLUMN` schema evolution.
- Nested JSON parsing with `from_json` and `explode`.
- File-source Structured Streaming from `s3a://landing/...` into Iceberg with
  `writeStream.format("iceberg")` and `checkpointLocation` under
  `s3a://checkpoints/...`.
- Maintenance procedures: `rewrite_data_files`, `expire_snapshots`, and
  `remove_orphan_files`.

## 5. Notebook Surfaces

- JupyterHub: `services/jupyterhub/build/notebooks/12_iceberg_advanced_sql.ipynb`
  is the Spark Connect reference.
- Zeppelin: `services/zeppelin/notebooks/iceberg_advanced_sql.zpln` is the
  standalone Spark reference.

Both surfaces are kept because Spark Connect and Zeppelin's Spark-submit
interpreter use different runtime paths. They share the Iceberg REST catalog,
the MinIO warehouse and the Spark image.

## 6. CI Posture

Normal CI stays static and hermetic.
`bootstrapper/tests/test_iceberg_advanced_smoke_suite.py` checks that the
script, notebooks, docs, advanced operations, S3A landing and checkpoint paths
and the no-new-service topology contract are present. An operator runs the live
smoke explicitly, because it needs the data-eng stack to be up.
