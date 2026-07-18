# 5.2.1. Apache Airflow (DAG orchestrator)

Airflow runs as a 4-container family in the stack's `agents` band: `airflow-webserver` (Web UI + REST API; runs `airflow api-server`), `airflow-scheduler` (LocalExecutor task runner), `airflow-dag-processor` (parses DAG files into the metadata DB — required as a standalone service in Airflow 3.x; the scheduler no longer parses in-process), and `airflow-init` (one-shot bootstrap: DB migrate + admin user + Connection seeding).

## 1. Overview

Image: `apache/airflow:3.3.0` (Apache 2.0), wrapped by a local `services/airflow/build/Dockerfile` that adds the 9-provider bundle needed for the cross-stack integrations (apache-spark, amazon, postgres, redis, common-sql, weaviate, neo4j, openai, fab) plus `pyspark[connect]==4.1.2` (the `[connect]` extra pulls grpcio + companions; the Spark Connect smoke step in the sample DAG needs it). The image also installs Java 17, exposes PySpark's `spark-submit` on `PATH`, bakes the matching S3A/Iceberg jars into PySpark's jars directory, and builds `/opt/airflow/atlas-jars/atlas-lakehouse-smoke.jar` from source for the manual SparkSubmit lakehouse smoke. LocalExecutor is the only supported executor in v1 — tasks run in the scheduler's process pool. Metadata DB lives in a new `airflow` database on Supabase Postgres, created by `airflow-init` on first start.

## 2. Access

| Surface | URL | Auth |
|---|---|---|
| Web UI (direct) | `http://localhost:${AIRFLOW_PORT}` | `admin` / `${AIRFLOW_ADMIN_PASSWORD}` (FAB session cookie) |
| Web UI (Kong) | `http://airflow.localhost:${KONG_HTTP_PORT}` | Same |
| REST API | `http://airflow.localhost:${KONG_HTTP_PORT}/api/v2/` | JWT bearer — POST to `/auth/token` first; `AIRFLOW__FAB__AUTH_BACKENDS` (the fully-qualified basic_auth backend) applies to legacy FAB endpoints only, NOT `/api/v2/`. See §6 for the two-step curl. |

`AIRFLOW_ADMIN_PASSWORD` is auto-generated on first run and persisted to `.env`. Treat it like any other secret.

## 3. Configuration

```bash
AIRFLOW_SOURCE=disabled              # container | disabled
AIRFLOW_IMAGE=apache/airflow:3.3.0
AIRFLOW_PORT=                        # auto-assigned (agents band)
AIRFLOW_DB_USER=airflow              # role on Supabase Postgres
AIRFLOW_DB_PASSWORD=                 # auto-generated
AIRFLOW_FERNET_KEY=                  # auto-generated (Connection-password encryption)
AIRFLOW_SECRET_KEY=                  # auto-generated (AIRFLOW__API__SECRET_KEY — signs inter-process payloads in Airflow 3.x)
AIRFLOW_ADMIN_PASSWORD=              # auto-generated (admin login)
```

Auto-managed (resolved by the bootstrapper from `AIRFLOW_SOURCE`; do not hand-edit): `AIRFLOW_WEBSERVER_SCALE`, `AIRFLOW_SCHEDULER_SCALE`, `AIRFLOW_DAG_PROCESSOR_SCALE`, `AIRFLOW_INIT_SCALE`.

## 4. Seeded Connections

`airflow-init` runs once at first start and seeds Airflow Connection objects for every enabled sibling service. Each is gated on the sibling's `_SOURCE` env var:

| Connection ID | Type | Target | Gated on |
|---|---|---|---|
| `postgres_supabase` | postgres | `supabase-db:5432/${SUPABASE_DB_NAME}` | always (required dep) |
| `litellm_default` | openai | `http://litellm:4000/v1` with `LITELLM_MASTER_KEY` (the `/v1` lives in conn.host because OpenAIHook ignores `api_base` extras) | always (LiteLLM is locked always-on) |
| `redis_default` | redis | `redis:6379` with `REDIS_PASSWORD` | always (Redis ships container-only always-on, auth-on by default) |
| `spark_default` | spark | `spark://spark-master:7077` with `deploy-mode=cluster`, `spark-binary=spark-submit` | `SPARK_SOURCE=container` |
| `minio_default` | aws (S3-compat) | `http://minio:9000` with root creds, path-style addressing, region `us-east-1` | `MINIO_SOURCE=container` |
| `weaviate_default` | weaviate | host `weaviate`, port `8080`, gRPC `weaviate:50051` (via extra) | `WEAVIATE_SOURCE=container` (NOT `localhost` — the in-Compose DNS does not resolve in host-mode) |
| `neo4j_default` | neo4j | host `neo4j-graph-db`, port `7687`, login `${GRAPH_DB_USER}`, password `${GRAPH_DB_PASSWORD}` (Hook prepends `bolt://`) | `NEO4J_GRAPH_DB_SOURCE=container` (same caveat) |

Connection seeding is idempotent — `airflow-init` deletes-then-adds each Connection on every run, so changes to credentials propagate on the next `./start.sh`.

**Resolving seeded Connections outside a task.** Airflow 3's Task-SDK
connection lookup is task-context-sensitive. DAG tasks should keep using
hooks/operators such as `S3Hook(aws_conn_id="minio_default")` and
`SparkSubmitOperator(conn_id="spark_default")`, but standalone probes or
scripts run with `docker exec ... python ...` are outside a task execution context.
In that context, `BaseHook.get_connection(...)` or hook construction
can raise `AirflowNotFoundException` even when
`airflow connections get minio_default` shows the row in the metadata DB. For
preflight scripts, read the metadata DB directly instead:

```python
from airflow.models import Connection
from airflow.settings import Session

with Session() as session:
    conn = session.query(Connection).filter(Connection.conn_id == "minio_default").one()
```

The same pattern applies to `spark_default`. This direct
`airflow.settings.Session` + `airflow.models.Connection` access is for
standalone health/preflight scripts only; DAG tasks should keep using hooks/operators
so provider behavior, masking, and task-context semantics stay intact.

## 5. Sample DAG

`services/airflow/dags/example_etl_with_llm.py` ships pre-loaded. Three `PythonOperator` steps that smoke-test each Connection:

1. `spark_smoke` invokes Spark Connect at `sc://spark-connect:15002` via `pyspark[connect]`. Smoke-tests the Spark cluster's reachability via the Connect sidecar. Note: this does NOT exercise the seeded `spark_default` Connection — that one points at `spark://spark-master:7077` for user DAGs using `SparkSubmitOperator`. See the DAG docstring.
2. `summarize_via_litellm` calls LiteLLM's chat-completions endpoint via `OpenAIHook.get_conn()`. There is no `OpenAIOperator` class in `apache-airflow-providers-openai` (only `OpenAIEmbeddingOperator` and `OpenAITriggerBatchOperator`); the Hook is the right surface for chat. Defaults to `ollama/qwen3.6:latest` (Ollama-mode); swap to `gpt-4o-mini` or similar if running with `--llm-provider-source none` + `CLOUD_OPENAI_SOURCE=enabled`.
3. `list_minio_buckets` calls `S3Hook.list_buckets()` against `minio_default`.

A commented LangChain block at the bottom of the file shows the recommended pattern for chain-based LLM steps via `PythonOperator` (Apache has no published `apache-airflow-providers-langchain` package — wrap chains in a Python callable instead).

Use it as a template. Drop your own DAGs into `services/airflow/dags/` — they're bind-mounted into the container.

### 5.1. Lakehouse SparkSubmit smoke

`services/airflow/dags/lakehouse_spark_submit_smoke.py` is a manual DAG (`schedule=None`) for the data-engineering track. It prepares a tiny landing object, uploads the image-built validation JAR to `s3a://jars/atlas/lakehouse-smoke/latest/atlas-lakehouse-smoke.jar`, and runs `SparkSubmitOperator` with `deploy_mode="cluster"` against `spark://spark-master:7077`.

Cluster deploy mode is the Atlas default for this path because the Spark driver runs on a Spark worker that already carries the S3A and Iceberg runtime jars. Airflow still carries Java, `spark-submit`, `hadoop-aws`, the AWS SDK v2 bundle, and Iceberg jars so the submit client can resolve S3A resources and so client-mode experiments do not immediately fail on missing classes. After submission, the Spark provider polls post-submit driver status through the standalone master's backend-network-only REST endpoint at `spark-master:6066`; Atlas enables that endpoint on `spark-master` without exposing it through a host port or Kong route. The DAG passes explicit S3A, Iceberg REST, and Spark event-log config:

```python
SparkSubmitOperator(
    task_id="submit_lakehouse_s3a_jar",
    conn_id="spark_default",
    application="s3a://jars/atlas/lakehouse-smoke/latest/atlas-lakehouse-smoke.jar",
    java_class="com.atlas.spark.LakehouseSmoke",
    deploy_mode="cluster",
    conf={
        "spark.hadoop.fs.s3a.endpoint": "http://minio:9000",
        "spark.sql.catalog.lakehouse.uri": "http://iceberg-rest:8181",
        "spark.eventLog.enabled": "true",
        "spark.eventLog.dir": "s3a://spark-history/",
    },
)
```

Validation flow:

```bash
./start.sh --track data-eng \
  --airflow-source container \
  --spark-source container \
  --iceberg-rest-source container \
  --minio-source container
```

Trigger `lakehouse_spark_submit_smoke` from the UI at `http://airflow.localhost:${KONG_HTTP_PORT}` or use the Airflow REST API token flow shown in section 6. The Spark task should finish successfully, create/update `lakehouse.bronze.airflow_spark_submit_smoke`, and leave an event log visible in Spark History at `http://spark-history.localhost:${KONG_HTTP_PORT}`. If the DAG fails before submission, check that `minio_default` and `spark_default` were seeded by `airflow-init`; if it runs in Spark but Airflow reports a post-submit status failure, verify `http://spark-master:6066` is reachable from the Airflow scheduler container; if it fails during Spark execution, inspect the Spark driver in the master UI and the completed app in Spark History.

## 6. Hermes → Airflow integration

Hermes can trigger Airflow DAGs via the REST API. Airflow 3.x's public
`/api/v2/` uses JWT bearer tokens, NOT HTTP basic auth — two steps:

```bash
# 1. Exchange admin password for a short-lived JWT.
TOKEN=$(curl -fsS -X POST \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"${AIRFLOW_ADMIN_PASSWORD}\"}" \
  http://airflow.localhost:${KONG_HTTP_PORT}/auth/token | jq -r .access_token)

# 2. Trigger the DAG. `logical_date` is REQUIRED-but-nullable in
# Airflow 3.x's TriggerDAGRunPostBody schema — omit it and the API
# returns 422. Set to null to let the scheduler assign one.
curl -fsS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"logical_date": null, "conf": {}}' \
  http://airflow.localhost:${KONG_HTTP_PORT}/api/v2/dags/example_etl_with_llm/dagRuns
```

This pattern — agent runtime → orchestrated workflow — pairs Hermes's reactive surface with Airflow's batch/scheduled surface.

## 7. Dependencies & Integrations

### 7.1. Current — Upstream (this service calls)

| Service | Category |
|---|---|
| iceberg-rest | data |
| minio | data |
| neo4j | data |
| redis | data |
| redpanda | data |
| spark | data |
| supabase | data |
| weaviate | data |
| litellm | llm |

### 7.2. Current — Downstream (services that call this)

| Service | Category |
|---|---|
| kong | infra |
| hermes | agents |

### 7.3. Architecture diagram

![airflow architecture](./architecture.svg)

[Open the interactive HTML diagram](./architecture.html) for a full-screen view.

### 7.4. Future — Missing pair integrations

_No high-confidence opportunities identified._

### 7.5. Future — Candidate new services

_No high-confidence opportunities identified._

### 7.6. Future — Unused features in this service

_No high-confidence opportunities identified._

## 8. Troubleshooting

- **`airflow-init` fails with "database does not exist"** — Supabase Postgres might not be running yet. `airflow-init` depends_on `supabase-db: service_healthy` so this shouldn't happen, but if it does, `docker logs ${PROJECT_NAME}-airflow-init` shows the psql error.
- **Web UI login rejected** — `AIRFLOW_ADMIN_PASSWORD` in `.env` may have rotated. Check the value; if rotated, `airflow-init` re-runs and re-syncs the admin user on next `./start.sh`.
- **DAG appears in UI but won't run** — Scheduler may be lagging. `docker logs ${PROJECT_NAME}-airflow-scheduler` for parse errors. The scheduler poll interval defaults to 30s.
- **`summarize_via_litellm` (OpenAIHook) fails with `auth required`** — `litellm_default` Connection has the wrong `LITELLM_MASTER_KEY`. Re-run `./start.sh` to re-sync the Connection; alternatively edit it in the Web UI under Admin → Connections.
- **Spark `spark_smoke` task can't reach `sc://spark-connect:15002` (or `spark://spark-master:7077` from user `SparkSubmitOperator` DAGs)** — Either (a) Spark isn't running (`SPARK_SOURCE=disabled` in `.env`; enable it via `--spark-source container` or remove the Spark-dependent steps from your DAG), or (b) it's the first DAG run after stack-up and spark-connect's JVM hasn't finished binding 15002 yet (20-60s cold-start lag). Airflow's `retries: 1` + `retry_delay: 2m` in default_args usually masks (b); if it doesn't, re-trigger the DAG once spark-connect is up.
- **`spark_smoke` raises `ModuleNotFoundError: No module named 'pyspark'`** — the airflow image hasn't been rebuilt since `pyspark` was added to `services/airflow/build/requirements.txt`. Run `docker compose build airflow-webserver` and restart with `./start.sh`.
