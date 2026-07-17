#!/usr/bin/env bash
set -euo pipefail

surface="${1:-spark-connect}"
project="${PROJECT_NAME:-atlas}"
zeppelin_url="${ZEPPELIN_URL:-http://localhost:${ZEPPELIN_PORT:-63099}}"
notebook_path="services/zeppelin/notebooks/iceberg_advanced_sql.zpln"

usage() {
  cat <<'USAGE'
Usage: scripts/smoke-iceberg-advanced-sql.sh [spark-connect|zeppelin|all]

Prerequisites:
  ./start.sh --track data-eng \
    --spark-source container \
    --iceberg-rest-source container \
    --minio-source container \
    --jupyterhub-source container

For the Zeppelin surface also enable:
  --zeppelin-source container

This opt-in smoke covers Iceberg MERGE INTO, VERSION AS OF, rollback_to_snapshot,
CREATE BRANCH / spark.wap.branch, schema evolution, nested JSON/explode,
Structured Streaming from s3a://landing/ into Iceberg with checkpoints under
s3a://checkpoints/, and maintenance procedures rewrite_data_files,
expire_snapshots, and remove_orphan_files.
USAGE
}

require_container() {
  local name="$1"
  if ! docker inspect "$name" >/dev/null 2>&1; then
    echo "[smoke] missing container: $name" >&2
    echo "[smoke] Start with SPARK_SOURCE=container ICEBERG_REST_SOURCE=container MINIO_SOURCE=container." >&2
    return 1
  fi
}

load_env_file() {
  if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
    project="${PROJECT_NAME:-$project}"
    zeppelin_url="${ZEPPELIN_URL:-http://localhost:${ZEPPELIN_PORT:-63099}}"
  fi
}

run_spark_connect() {
  require_container "${project}-jupyterhub"
  echo "[smoke] running advanced Iceberg SQL through Spark Connect in ${project}-jupyterhub"
  docker exec -i "${project}-jupyterhub" python - <<'PY'
import os
from uuid import uuid4
from pyspark.sql import SparkSession
from pyspark.sql.types import LongType, StringType, StructField, StructType

spark_remote = os.getenv("SPARK_REMOTE", "sc://spark-connect:15002")
spark = SparkSession.builder.remote(spark_remote).getOrCreate()

namespace = "lakehouse.atlas_smoke"
table = f"{namespace}.advanced_sql"
stream_table = f"{namespace}.advanced_stream"
run_id = uuid4().hex[:12]
landing_path = f"s3a://landing/atlas-smoke-advanced-json/{run_id}"
checkpoint_path = f"s3a://checkpoints/atlas-smoke-advanced-json/{run_id}"

spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {namespace}")
spark.sql(f"DROP TABLE IF EXISTS {table}")
spark.sql(f"DROP TABLE IF EXISTS {stream_table}")

spark.sql(
    f"""
    CREATE TABLE {table} (
      id BIGINT,
      note STRING,
      metrics STRUCT<score: INT>
    )
    USING iceberg
    TBLPROPERTIES ('format-version'='2', 'write.wap.enabled'='true')
    """
)
spark.sql(
    f"""
    INSERT INTO {table}
    VALUES
      (1, 'alpha', named_struct('score', 10)),
      (2, 'bravo', named_struct('score', 20))
    """
)

initial_snapshot = spark.sql(
    f"SELECT snapshot_id FROM {table}.snapshots ORDER BY committed_at DESC LIMIT 1"
).collect()[0][0]

spark.sql(
    f"""
    MERGE INTO {table} AS t
    USING (
      SELECT 2 AS id, 'bravo-updated' AS note, named_struct('score', 25) AS metrics
      UNION ALL
      SELECT 3 AS id, 'charlie' AS note, named_struct('score', 30) AS metrics
    ) AS s
    ON t.id = s.id
    WHEN MATCHED THEN UPDATE SET note = s.note, metrics = s.metrics
    WHEN NOT MATCHED THEN INSERT (id, note, metrics) VALUES (s.id, s.note, s.metrics)
    """
)
spark.sql(f"SELECT * FROM {table} VERSION AS OF {initial_snapshot}").collect()
spark.sql(
    f"CALL lakehouse.system.rollback_to_snapshot(table => 'atlas_smoke.advanced_sql', snapshot_id => {initial_snapshot})"
).collect()
spark.sql(f"SELECT * FROM {table}").collect()

spark.sql(
    f"""
    MERGE INTO {table} AS t
    USING (SELECT 3 AS id, 'charlie' AS note, named_struct('score', 30) AS metrics) AS s
    ON t.id = s.id
    WHEN NOT MATCHED THEN INSERT (id, note, metrics) VALUES (s.id, s.note, s.metrics)
    """
)
spark.sql(f"ALTER TABLE {table} CREATE BRANCH atlas_wap")
spark.conf.set("spark.wap.branch", "atlas_wap")
spark.sql(f"INSERT INTO {table} VALUES (4, 'delta-from-wap', named_struct('score', 40))")
spark.conf.unset("spark.wap.branch")
spark.sql(f"SELECT * FROM {table} VERSION AS OF 'atlas_wap'").collect()
spark.sql("CALL lakehouse.system.fast_forward('atlas_smoke.advanced_sql', 'main', 'atlas_wap')").collect()
spark.sql(f"ALTER TABLE {table} DROP BRANCH atlas_wap")

spark.sql(f"ALTER TABLE {table} ADD COLUMN quality STRING")
spark.sql(f"INSERT INTO {table} VALUES (5, 'echo', named_struct('score', 50), 'accepted')")
spark.sql(
    """
    SELECT payload.id, event.kind, event.score
    FROM (
      SELECT from_json(
        raw,
        'id BIGINT, events ARRAY<STRUCT<kind: STRING, score: INT>>'
      ) AS payload
      FROM VALUES ('{"id": 6, "events": [{"kind": "nested", "score": 60}]}') AS raw_events(raw)
    )
    LATERAL VIEW explode(payload.events) exploded AS event
    """
).collect()

spark.sql(
    f"""
    CREATE TABLE {stream_table} (
      id BIGINT,
      event STRING
    )
    USING iceberg
    TBLPROPERTIES ('format-version'='2')
    """
)
spark.createDataFrame([(101, "landing-a"), (102, "landing-b")], ["id", "event"]).write.mode(
    "overwrite"
).json(landing_path)
schema = StructType(
    [
        StructField("id", LongType(), False),
        StructField("event", StringType(), True),
    ]
)
query = (
    spark.readStream.schema(schema)
    .json(landing_path)
    .writeStream.format("iceberg")
    .outputMode("append")
    .option("checkpointLocation", checkpoint_path)
    .trigger(availableNow=True)
    .toTable(stream_table)
)
query.awaitTermination(120)
assert spark.table(stream_table).count() >= 2

spark.sql("CALL lakehouse.system.rewrite_data_files(table => 'atlas_smoke.advanced_sql')").collect()
spark.sql(
    "CALL lakehouse.system.expire_snapshots(table => 'atlas_smoke.advanced_sql', retain_last => 1)"
).collect()
spark.sql(
    "CALL lakehouse.system.remove_orphan_files(table => 'atlas_smoke.advanced_sql', dry_run => true)"
).collect()

print("[smoke] Spark Connect advanced Iceberg SQL passed via", spark_remote)
PY
}

run_zeppelin() {
  require_container "${project}-zeppelin"
  if [[ ! -f "$notebook_path" ]]; then
    echo "[smoke] missing Zeppelin notebook artifact: $notebook_path" >&2
    return 1
  fi
  echo "[smoke] importing and running Zeppelin notebook at ${zeppelin_url}"
  python3 - "$zeppelin_url" "$notebook_path" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

zeppelin_url = sys.argv[1].rstrip("/")
notebook_path = sys.argv[2]


def request(method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{zeppelin_url}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        data = response.read().decode("utf-8")
    return json.loads(data) if data else {}


with open(notebook_path, encoding="utf-8") as fh:
    note = json.load(fh)
note["name"] = f"{note['name']} smoke {int(time.time())}"

created = request("POST", "/api/notebook/import", note)
if created.get("status") != "OK":
    raise SystemExit(f"failed to import Zeppelin note: {created}")
note_id = created["body"]
try:
    run = request("POST", f"/api/notebook/job/{note_id}")
    if run.get("status") != "OK":
        raise SystemExit(f"failed to start Zeppelin note: {run}")
    deadline = time.time() + 600
    while time.time() < deadline:
        status = request("GET", f"/api/notebook/job/{note_id}")
        body = status.get("body", [])
        states = {item.get("status") for item in body}
        if states and states <= {"FINISHED"}:
            break
        if states & {"ERROR", "ABORT", "CANCELED"}:
            raise SystemExit(f"Zeppelin note failed: {status}")
        time.sleep(5)
    else:
        raise SystemExit("timed out waiting for Zeppelin note")
    exported = request("GET", f"/api/notebook/export/{note_id}")
    text = json.dumps(exported)
    if "ERROR" in text:
        raise SystemExit(f"Zeppelin exported note contains ERROR: {text[:2000]}")
finally:
    try:
        request("DELETE", f"/api/notebook/{note_id}")
    except urllib.error.URLError:
        pass

print("[smoke] Zeppelin advanced Iceberg SQL passed through standalone Spark")
PY
}

load_env_file
case "$surface" in
  spark-connect)
    run_spark_connect
    ;;
  zeppelin)
    run_zeppelin
    ;;
  all)
    run_spark_connect
    run_zeppelin
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
