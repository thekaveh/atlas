# Iceberg Advanced Smoke

This opt-in smoke validates the advanced lakehouse contract requested by the
data-eng track without adding infrastructure.

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

For the Zeppelin surface, also enable:

```bash
./start.sh --track data-eng \
  --spark-source container \
  --iceberg-rest-source container \
  --minio-source container \
  --jupyterhub-source container \
  --zeppelin-source container
```

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

The Spark Connect surface executes inside `${PROJECT_NAME:-atlas}-jupyterhub`
against `sc://spark-connect:15002`. The Zeppelin surface imports
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

Keeping both surfaces matters because Spark Connect and Zeppelin's Spark-submit
interpreter have different runtime paths even though they share the same
Iceberg REST catalog, MinIO warehouse, and Spark image.

## 6. CI Posture

Normal CI stays static and hermetic. `bootstrapper/tests/test_iceberg_advanced_smoke_suite.py`
guards that the script, notebooks, docs, advanced operations, S3A landing and
checkpoint paths, and no-new-service topology contract remain present. Running
the live smoke is an explicit operator choice because it requires the data-eng
stack to be up.
