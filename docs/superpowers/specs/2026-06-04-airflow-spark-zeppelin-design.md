# Design — Apache Airflow + Spark Cluster + Apache Zeppelin

**Spec date:** 2026-06-04
**Status:** Draft — pending review
**Brainstorming session:** 2026-06-04 (this document)
**Author:** Claude Code (Sonnet 4.6) for kaveh.razavi@gmail.com

---

## 1. Summary

This spec adds three new services to the `atlas` stack as a single
coordinated change set:

- **Apache Spark 4.1.2** — standalone-mode cluster (1 master + N workers + history
  server). Distinct from the existing Ray substrate; targets batch / SQL /
  DataFrame workloads. Spark Connect (gRPC) enabled so Zeppelin and external
  clients can submit jobs.
- **Apache Zeppelin 0.12.0** — Spark-first notebook UI. Coexists with
  JupyterHub (which keeps the Python + Scala kernel surface for general
  data science). Zeppelin's value is the pre-configured Spark / SQL / Shell
  / JDBC interpreter stack. Gated on `SPARK_SOURCE != disabled` — Zeppelin
  without Spark is just a weaker JupyterHub.
- **Apache Airflow 3.2.2** — code-defined DAG orchestrator. Coexists with
  n8n (which keeps the visual / event-driven workflow surface). Airflow's
  AI/ML SDK (new in 3.0) brings `LangChainOperator`, `OpenAIOperator`,
  `EmbeddingsOperator` etc., wired to point at our LiteLLM gateway.

All three services default to `disabled` per the convention for
heavyweight optional services (Ray, Prometheus, Grafana). Users opt in
via `--<svc>-source container` CLI flags or the interactive wizard.

The PR will be one large landing covering ~75 files across compose,
bootstrapper, docs, tests, and audit scripts.

---

## 2. Decisions made (locked from brainstorming)

| # | Decision | Rationale |
|---|---|---|
| D1 | Single design doc + single big-bang PR | User-chosen for cross-service coherence; review burden accepted. |
| D2 | Spark + Ray coexist as distinct substrates | Ray = Python-native compute. Spark = batch/SQL. No bridge code. |
| D3 | Zeppelin is Spark-first, gated on `SPARK_SOURCE != disabled` | Zeppelin's killer feature is its Spark interpreter. |
| D4 | Airflow coexists with n8n | Different audiences (visual vs code-defined DAGs). |
| D5 | Airflow uses LocalExecutor + dedicated `airflow` DB on Supabase Postgres | Simplest topology, reuses existing Postgres family. |
| D6 | Spark cluster = 1 master + 2 workers (default) + history server | `SPARK_WORKER_COUNT` adjustable via wizard (1-8) mirroring Ray pattern. |
| D7 | All three services default to `disabled` (opt-in) | Match heavyweight-service convention. |
| D8 | Approach A — fit into existing 6 categories | Spark → `data`, Airflow → `agents`, Zeppelin → `apps`. No `topology.py` surgery. |
| D9 | Airflow 3.x (not 2.x) | User requirement — gets the new AI/ML SDK with LLM operators wired to LiteLLM. |
| D10 | Comprehensive integration matrix covered in §5 | User requirement — every plausible pair documented. |

---

## 3. Per-service design

### 3.1. Spark cluster

#### 3.1.1. Containers

| Container | Role | Image | Internal port(s) |
|---|---|---|---|
| `spark-master` | Cluster coordinator + Web UI + Spark Connect server | `bitnami/spark:4.1.2` | 7077 (RPC), 8080 (Web UI), 15002 (Spark Connect gRPC) |
| `spark-worker` | Compute executor; scales via `SPARK_WORKER_SCALE` | `bitnami/spark:4.1.2` | 8081 (Worker Web UI; auto-allocated per replica), 35000-35499 (executor random ports) |
| `spark-history` | Post-mortem job inspection (reads from MinIO event log bucket) | `bitnami/spark:4.1.2` | 18080 (Web UI) |

`bitnami/spark` is chosen over `apache/spark` because Bitnami's image
ships the standalone-mode entrypoint, env-var-driven config (no
`spark-defaults.conf` to mount), and a non-root user — matching the rest
of the stack's pattern.

#### 3.1.2. Image pin

`SPARK_IMAGE` defaults to `bitnami/spark:4.1.2` (latest stable as of
2026-06-04). 4.x dropped Scala 2.12 — Spark 4 binaries are Scala
2.13-only. Compatible with Zeppelin 0.12's Spark interpreter (which
supports Spark 3.x + 4.x).

#### 3.1.3. Ports + Kong aliases

| Env var | Default slot | Internal | Kong alias | Visibility |
|---|---|---|---|---|
| `SPARK_MASTER_UI_PORT` | data band (next free in 10-29) | 8080 | `spark.localhost` | external (browser) |
| `SPARK_MASTER_RPC_PORT` | not host-published | 7077 | — | internal only |
| `SPARK_CONNECT_PORT` | not host-published | 15002 | — | internal only (Zeppelin reaches it via `spark-master:15002`) |
| `SPARK_HISTORY_PORT` | data band | 18080 | `spark-history.localhost` | external (browser) |

Kong aliases use `preserve_host: True` per `reference_kong_preserve_host.md`
— Spark Web UI emits redirect URLs that must come back through the alias.

#### 3.1.4. Source variants

```yaml
sources:
  var: SPARK_SOURCE
  default: disabled
  options:
    - id: container
      label: "Container (standalone cluster)"
      secondary_number:
        env_var: SPARK_WORKER_COUNT
        default_value: 2
        number_min: 1
        number_max: 8
        unit_suffix: "workers"
        description: "Number of spark-worker replicas alongside the master. 1-8."
    - id: disabled
      label: "Disabled"
```

No `localhost` source variant — running a Spark cluster on the host is
exotic enough that we'd rather users explicitly hand-edit `.env` than
surface the option in the wizard.

#### 3.1.5. `depends_on`

```yaml
depends_on:
  required:
    - minio       # Spark History Server reads event log from MinIO bucket
  optional:
    - supabase    # JDBC interpreter is a runtime user choice, not a boot dep
    - prometheus  # opt-in scrape target if Prometheus is enabled
```

Spark is in the `data` category, **not** `infra`, so the
`project_infra_slot_pin_kong_ray.md` rule for pinning `kong + ray`
**does not apply** (no existing non-infra service pins them — verified
via grep across `services/*/service.yml`). The Kong + Ray slot
invariant is held by `services/grafana/service.yml`'s pin in the infra
band; Spark's data-band placement is independent.

The `minio` dependency is genuine — `spark-history` reads
`s3a://spark-history/` as its event log directory. If MinIO is disabled,
history server has nothing to read and refuses to start; the bootstrapper
hard-fails the source step in that case.

#### 3.1.6. `runtime_sc` + compose env (dual-write — see `project_runtime_sc_vs_compose_env_dual_write.md`)

The dual-write rule applies: every static enablement flag must appear
in BOTH `service.yml::runtime_sc.<source>.environment` AND
`compose.yml::services.<container>.environment`. The first feeds the
bootstrapper's resolver; the second is what actually reaches the
container.

Key env vars for the master:

```yaml
SPARK_MODE: master
SPARK_MASTER_HOST: spark-master  # bind to compose DNS name
SPARK_RPC_AUTHENTICATION_ENABLED: "no"
SPARK_RPC_ENCRYPTION_ENABLED: "no"
SPARK_LOCAL_SSL_ENABLED: "no"
# History server reads from MinIO via S3A
SPARK_HISTORY_OPTS: >-
  -Dspark.history.fs.logDirectory=s3a://spark-history/
  -Dspark.hadoop.fs.s3a.endpoint=http://minio:9000
  -Dspark.hadoop.fs.s3a.access.key=${MINIO_ROOT_USER}
  -Dspark.hadoop.fs.s3a.secret.key=${MINIO_ROOT_PASSWORD}
  -Dspark.hadoop.fs.s3a.path.style.access=true
# Spark Connect
SPARK_NO_DAEMONIZE: "true"
SPARK_DAEMON_JAVA_OPTS: -Dspark.connect.grpc.binding.port=15002
```

#### 3.1.7. Init container — `spark-init`

```yaml
spark-init:
  image: alpine:latest   # per project_init_container_pattern.md
  depends_on:
    minio-init:
      condition: service_completed_successfully
  command:
    - sh
    - -c
    - |
      apk add --no-cache mc >/dev/null
      mc alias set minio http://minio:9000 ${MINIO_ROOT_USER} ${MINIO_ROOT_PASSWORD}
      mc mb --ignore-existing minio/spark-history
      echo "spark-init: spark-history bucket ready"
```

Creates the `spark-history` MinIO bucket if missing. Idempotent — re-runs
on every `start` are no-ops.

#### 3.1.8. Wizard step (mirrors Ray pattern)

The source-step UI carries the `SecondaryNumberInput` widget exactly as
Ray does today. Step ordering matters: **Spark step must come BEFORE
Zeppelin step** so Zeppelin's enable path can branch on Spark's source.

### 3.2. Zeppelin notebook

#### 3.2.1. Containers

| Container | Role | Image | Internal port |
|---|---|---|---|
| `zeppelin` | Notebook + interpreter host | `apache/zeppelin:0.12.0` | 8080 |

Zeppelin runs all interpreters in-process by default (we don't enable
the per-interpreter isolated K8s mode — that's overkill for this stack).
Spark, Python, Markdown, Shell, and JDBC interpreters all live in the
same container.

#### 3.2.2. Image pin

`ZEPPELIN_IMAGE` defaults to `apache/zeppelin:0.12.0`. 0.12 is the latest
stable; 0.11.2 is the previous LTS-style line. Zeppelin's release cadence
is slow (annual) and we lock at minor.

#### 3.2.3. Ports + Kong alias

| Env var | Default slot | Internal | Kong alias |
|---|---|---|---|
| `ZEPPELIN_PORT` | apps band (next free in 80-99) | 8080 | `zeppelin.localhost` |

Single port. Kong alias with `preserve_host: True` (Zeppelin emits
asset URLs that must come back through the alias).

#### 3.2.4. Source variants

```yaml
sources:
  var: ZEPPELIN_SOURCE
  default: disabled
  options:
    - id: container
      label: "Container (Spark-first notebook)"
    - id: disabled
      label: "Disabled"
```

No `localhost` source variant — Zeppelin's value is its Spark wiring,
which requires the in-stack Spark cluster.

#### 3.2.5. `depends_on`

```yaml
depends_on:
  required:
    - spark       # the whole point of Zeppelin in this stack
  optional:
    - supabase    # JDBC interpreter pre-configured; only used if user runs SQL cells
    - minio       # accessed transitively via Spark's S3A — not a Zeppelin-direct boot dep
    - litellm     # Python interpreter calls LiteLLM (optional, user-driven)
```

Zeppelin is in `apps` (not `infra`), so the `project_infra_slot_pin_kong_ray.md`
pin rule does not apply.

**Gating rule**: if `ZEPPELIN_SOURCE=container` AND `SPARK_SOURCE=disabled`,
the bootstrapper hard-fails the source step with an actionable error:
*"Zeppelin requires Spark to be enabled (`--spark-source container`).
Set Spark first or disable Zeppelin."*

#### 3.2.6. `runtime_sc` + compose env

Zeppelin interpreter config injected via env (Zeppelin auto-detects):

```yaml
ZEPPELIN_PORT: 8080
ZEPPELIN_LOG_DIR: /logs
ZEPPELIN_NOTEBOOK_DIR: /notebook

# Spark interpreter — points at the in-stack cluster.
SPARK_MASTER: spark://spark-master:7077
# Newer alternative — Spark Connect (gRPC); set both so users can switch.
SPARK_CONNECT_URL: sc://spark-master:15002

# S3A through the Spark interpreter — Zeppelin notebooks can read/write MinIO.
SPARK_SUBMIT_OPTIONS: >-
  --conf spark.hadoop.fs.s3a.endpoint=http://minio:9000
  --conf spark.hadoop.fs.s3a.access.key=${MINIO_ROOT_USER}
  --conf spark.hadoop.fs.s3a.secret.key=${MINIO_ROOT_PASSWORD}
  --conf spark.hadoop.fs.s3a.path.style.access=true

# JDBC interpreter — pre-configured for Supabase Postgres.
ZEPPELIN_JDBC_POSTGRES_URL: jdbc:postgresql://supabase-db:5432/${SUPABASE_DB_NAME}
ZEPPELIN_JDBC_POSTGRES_USER: ${SUPABASE_DB_USER}
ZEPPELIN_JDBC_POSTGRES_PASSWORD: ${SUPABASE_DB_PASSWORD}
```

#### 3.2.7. Init container — `zeppelin-init`

Materializes a starter notebook at `/notebook/spark_basics.zpln` and
seeds the Spark + JDBC interpreter config files. Uses vanilla
`alpine:latest` + inline `apk add` per `project_init_container_pattern.md`.

#### 3.2.8. Sample notebook

`services/zeppelin/notebooks/spark_basics.zpln` (Zeppelin's native JSON
format) — demonstrates: Spark DataFrame creation, MinIO read/write,
Postgres JDBC read, a `%md` markdown cell with the navigation guide.

### 3.3. Apache Airflow

#### 3.3.1. Containers

| Container | Role | Image | Internal port |
|---|---|---|---|
| `airflow-webserver` | Web UI (port 8080) + API server | `apache/airflow:3.2.2` | 8080 |
| `airflow-scheduler` | DAG scheduler + LocalExecutor task runner | `apache/airflow:3.2.2` | — |
| `airflow-init` | DB migration + admin user creation + connection seeding | `apache/airflow:3.2.2` | — |

Three containers total — `airflow-init` runs once and exits; webserver
and scheduler are long-running.

LocalExecutor (single-process task pool) is the chosen executor — no
CeleryExecutor + Redis broker overhead. If users later need
CeleryExecutor, that's a future spec.

#### 3.3.2. Image pin

`AIRFLOW_IMAGE` defaults to `apache/airflow:3.2.2`. Airflow 3.2 ships:

- AI/ML SDK with `LangChainOperator`, `OpenAIOperator`, `BedrockOperator`,
  `VertexAIOperator`, and `EmbeddingsOperator` — all wireable to LiteLLM's
  OpenAI-compatible endpoint.
- DAG-versioning UI improvements.
- Native deferrable-operator support for long-running async tasks.
- Postgres 16 support.

Customization: we ship a thin `Dockerfile` (under
`services/airflow/build/`) that extends `apache/airflow:3.2.2` with the
following providers preinstalled via pip:

- `apache-airflow-providers-apache-spark` — SparkSubmitOperator
- `apache-airflow-providers-amazon` — S3Hook (works with MinIO via custom endpoint)
- `apache-airflow-providers-postgres` — Postgres connection for Supabase
- `apache-airflow-providers-redis` — Redis sensor/hook
- `apache-airflow-providers-weaviate` — vector store operations
- `apache-airflow-providers-neo4j` — graph DB operations
- `apache-airflow-providers-openai` — wired to LiteLLM via the OpenAI-compat path
- `apache-airflow-providers-langchain` — LangChain operators (AI/ML SDK)

#### 3.3.3. Ports + Kong alias

| Env var | Default slot | Internal | Kong alias |
|---|---|---|---|
| `AIRFLOW_PORT` | agents band (next free in 60-79) | 8080 | `airflow.localhost` |

Single port. Kong alias with `preserve_host: True`.

#### 3.3.4. Source variants

```yaml
sources:
  var: AIRFLOW_SOURCE
  default: disabled
  options:
    - id: container
      label: "Container (LocalExecutor on Supabase Postgres)"
    - id: disabled
      label: "Disabled"
```

#### 3.3.5. `depends_on`

```yaml
depends_on:
  required:
    - supabase    # Airflow's metadata DB lives in Supabase Postgres (new `airflow` database)
  optional:
    - spark       # SparkSubmitOperator needs Spark; `spark_default` connection seeded conditionally
    - minio       # S3 hooks; `minio_default` connection seeded conditionally
    - litellm     # LLM operators; `litellm_default` connection seeded conditionally
    - redis       # `redis_default` connection seeded conditionally (RedisHook). NOT used as a broker since executor is Local, not Celery.
    - weaviate    # `weaviate_default` connection seeded conditionally
    - neo4j       # `neo4j_default` connection seeded conditionally
    - prometheus  # opt-in metrics scrape
```

Airflow is in `agents` (not `infra`); the `kong + ray` pin rule does not
apply (existing `agents`-band services like n8n and hermes don't pin
them either).

#### 3.3.6. `runtime_sc` + compose env

Critical env vars (a much fuller list lives in the implementation plan):

```yaml
AIRFLOW__CORE__EXECUTOR: LocalExecutor
AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://${AIRFLOW_DB_USER}:${AIRFLOW_DB_PASSWORD}@supabase-db:5432/airflow
AIRFLOW__CORE__FERNET_KEY: ${AIRFLOW_FERNET_KEY}            # bootstrapper-generated
AIRFLOW__WEBSERVER__SECRET_KEY: ${AIRFLOW_SECRET_KEY}        # bootstrapper-generated
AIRFLOW__API__AUTH_BACKENDS: airflow.api.auth.backend.basic_auth
AIRFLOW__CORE__LOAD_EXAMPLES: "false"
AIRFLOW_UID: 50000      # match the upstream image's user
_AIRFLOW_DB_MIGRATE: "true"           # init container only
_AIRFLOW_WWW_USER_USERNAME: admin
_AIRFLOW_WWW_USER_PASSWORD: ${AIRFLOW_ADMIN_PASSWORD}  # bootstrapper-generated
```

#### 3.3.7. Init container — `airflow-init`

Runs **once** at start; performs:

1. Wait for `supabase-db` to be healthy.
2. Run `airflow db migrate` against the `airflow` database.
3. Create the `airflow` Postgres database if missing (via `psql`).
4. Create the admin user.
5. Seed Airflow **Connections** based on which sibling services are
   enabled:
   - `spark_default` → host `spark-master`, port `7077` (only if
     `SPARK_SOURCE=container`)
   - `minio_default` → endpoint `http://minio:9000` with MinIO root
     credentials (only if `MINIO_SOURCE=container`)
   - `litellm_default` → base URL `http://litellm:4000/v1` with
     `LITELLM_MASTER_KEY` (only if `LITELLM_SOURCE != disabled`)
   - `postgres_supabase` → host `supabase-db`, port `5432`, db
     `${SUPABASE_DB_NAME}`
   - `weaviate_default` → URL `http://weaviate:8080` (only if
     `WEAVIATE_SOURCE != disabled`)
   - `neo4j_default` → URI `bolt://neo4j:7687` (only if
     `NEO4J_SOURCE != disabled`)
   - `redis_default` → host `redis`, port `6379` (only if
     `REDIS_SOURCE != disabled`)
6. Seed a starter DAG at `/opt/airflow/dags/example_etl.py` that exercises
   the Spark + MinIO + LiteLLM connections.

#### 3.3.8. Bootstrapper-generated secrets

Three new auto-generated values per `bootstrapper/utils/key_generator.py`:

- `AIRFLOW_FERNET_KEY` — 32-byte URL-safe base64 (Fernet format)
- `AIRFLOW_SECRET_KEY` — random 32-char string for Flask sessions
- `AIRFLOW_ADMIN_PASSWORD` — random 24-char password for the admin user

Plus `AIRFLOW_DB_USER` / `AIRFLOW_DB_PASSWORD` for the dedicated
`airflow` database role on Supabase Postgres (created by the init
container via `psql`).

#### 3.3.9. Starter DAG

`services/airflow/dags/example_etl.py` — demonstrates:

```python
from airflow import DAG
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.langchain.operators.langchain import LangChainOperator
# ... + a Postgres read step against supabase-db

with DAG("example_etl_with_llm", schedule="@daily", catchup=False) as dag:
    spark_step = SparkSubmitOperator(...)   # submits to spark://spark-master
    llm_step = LangChainOperator(...)        # calls LiteLLM via the litellm_default connection
    s3_step = ...                             # uploads result to MinIO
    spark_step >> llm_step >> s3_step
```

---

## 4. Topological ordering + port allocation

### 4.1. Topological insertion

The bootstrapper's slot allocator (`bootstrapper/services/topology.py`)
orders services by:

1. Topological rank derived from `depends_on.required`
2. Tie-break: alphabetical

Each service falls into its category's port band per `CATEGORY_SLOTS`:

| Category | Slot band (offset from BASE_PORT=63000) | Current users | New additions |
|---|---|---|---|
| `infra` | 0–9 | kong (0), kong-https (1), ray (2), redis (5/7), prometheus, grafana | none |
| `data` | 10–29 | supabase (10-17), minio (18-19), neo4j (20-21), weaviate (22) | **spark-master-ui, spark-history** |
| `llm` | 30–39 | litellm, ollama | none |
| `media` | 40–59 | comfyui, docling, searxng, parakeet, speaches, chatterbox, tts/stt | none |
| `agents` | 60–79 | hermes, n8n, openclaw | **airflow** |
| `apps` | 80–99 | jupyterhub, open-webui, local-deep-researcher, backend | **zeppelin** |

After insertion, the next free slots will be (approximate — final values
come from running `topology.build_topology()` after the new manifests
land):

| Service | Port var | Expected slot |
|---|---|---|
| Spark Master Web UI | `SPARK_MASTER_UI_PORT` | 63023 (first free in data after weaviate at 63022) |
| Spark History | `SPARK_HISTORY_PORT` | 63024 |
| Airflow Web UI | `AIRFLOW_PORT` | 63064 (next free in agents) |
| Zeppelin | `ZEPPELIN_PORT` | 63084 (next free in apps) |

The infra-band pin rule (`project_infra_slot_pin_kong_ray.md`) is
**not applicable** here — none of our three new services lands in
`infra`. The `KONG_HTTP_PORT=63000` / `RAY_DASHBOARD_PORT=63002`
invariant continues to be held by Grafana's explicit pin in
`services/grafana/service.yml::depends_on.required` (added in PR #33,
commit `d8b8846`). Spark's, Zeppelin's, and Airflow's `depends_on`
blocks reflect only their actual functional dependencies. The
`test_port_pin_kong_ray.py` regression test still passes after these
manifests land because the topology's tie-break is unaffected.

### 4.2. Port-collision risk

Spark's internal worker-shuffle ports (35000–35499 range, dynamic) live
entirely on the `backend-network` and never reach the host. No host port
allocation needed; no collision risk with the existing 63000–63099
window.

### 4.3. Ordering of compose `include:` directives

`docker-compose.yml`'s top-level `include:` block gets three new entries
appended **after** existing service includes:

```yaml
include:
  # ... existing 24 entries ...

  # Compute additions (2026-06-04)
  - services/spark/compose.yml
  - services/zeppelin/compose.yml
  - services/airflow/compose.yml
```

---

## 5. Comprehensive integration matrix

Pair-by-pair coverage of every plausible integration involving the three
new services. Marked **CRITICAL** (ship in this PR), **NICE-TO-HAVE**
(future spec), or **SKIP** (no genuine value).

### 5.1. Spark × existing services

| Pair | Direction | Status | Mechanism |
|---|---|---|---|
| Spark × MinIO | Spark→MinIO | **CRITICAL** | Spark `s3a://` connector. Hadoop endpoint configured via `SPARK_HISTORY_OPTS` and propagated to workers. Spark History reads `s3a://spark-history/`. |
| Spark × Supabase Postgres | Spark→Supabase | **CRITICAL** (config only) | JDBC connection string available via `spark.jdbc.postgres.url` env var. Pre-configured in the master's env; users add `--jars postgresql-jdbc.jar` for actual reads. |
| Spark × Ray | none | **SKIP** | Per D2, they coexist as distinct substrates with no bridge. |
| Spark × Kong | Spark→Kong (browser) | **CRITICAL** | Web UI + History UI aliased via `spark.localhost` and `spark-history.localhost` with `preserve_host: True`. |
| Spark × Prometheus | Prometheus→Spark | **CRITICAL** (opt-in) | JMX exporter sidecar on master + workers — scrape job added to `services/prometheus/config/prometheus.yml` (gated on `SPARK_SOURCE != disabled`). |
| Spark × Grafana | Grafana→Spark via Prometheus | **CRITICAL** (opt-in) | New `spark.json` dashboard ships in `services/grafana/config/provisioning/dashboards/` (gated on `SPARK_SOURCE != disabled`). |
| Spark × LiteLLM | Spark→LiteLLM (UDF) | **NICE-TO-HAVE** | Users write UDFs that call LiteLLM; no library shipped. Defer. |
| Spark × Weaviate | Spark↔Weaviate | **NICE-TO-HAVE** | Weaviate Spark Connector exists; not bundled. Defer. |
| Spark × Neo4j | Spark↔Neo4j | **NICE-TO-HAVE** | Neo4j Spark Connector exists; not bundled. Defer. |
| Spark × Redis | Spark↔Redis | **SKIP** | Spark-Redis library is unmaintained. |
| Spark × Hermes | Hermes→Spark | **NICE-TO-HAVE** | Hermes could submit Spark jobs via REST; not wired. Defer. |
| Spark × n8n | n8n→Spark | **SKIP** | n8n has no native Spark node; HTTP-trigger Airflow instead. |
| Spark × Ollama | none | **SKIP** | Go through LiteLLM. |

### 5.2. Zeppelin × existing services

| Pair | Direction | Status | Mechanism |
|---|---|---|---|
| Zeppelin × Spark | Zeppelin→Spark | **CRITICAL** | Spark interpreter pre-configured. `SPARK_MASTER=spark://spark-master:7077` + `SPARK_CONNECT_URL=sc://spark-master:15002`. |
| Zeppelin × MinIO | Zeppelin→MinIO (via Spark) | **CRITICAL** | S3A endpoint baked into `SPARK_SUBMIT_OPTIONS`; users `df.write.parquet("s3a://bucket/path")` directly. |
| Zeppelin × Supabase Postgres | Zeppelin→Supabase | **CRITICAL** | JDBC interpreter pre-configured. SQL cells query Postgres without per-notebook setup. |
| Zeppelin × JupyterHub | none | **SKIP** | Per D3, they coexist; no integration. |
| Zeppelin × Kong | Zeppelin→Kong (browser) | **CRITICAL** | `zeppelin.localhost` alias with `preserve_host: True`. |
| Zeppelin × LiteLLM | Zeppelin→LiteLLM | **NICE-TO-HAVE** | Python interpreter can call LiteLLM via `openai.OpenAI()`; doc only, no preconfiguration. |
| Zeppelin × Neo4j | Zeppelin→Neo4j (via JDBC) | **NICE-TO-HAVE** | Neo4j JDBC driver exists; not bundled. Defer. |
| Zeppelin × Weaviate | Zeppelin→Weaviate | **NICE-TO-HAVE** | Python REST calls; doc only. |
| Zeppelin × Redis | none | **SKIP** | Low value. |
| Zeppelin × Airflow | none | **SKIP** | Confusing surface — DAGs live in Airflow. |
| Zeppelin × Prometheus | Prometheus→Zeppelin | **NICE-TO-HAVE** | Zeppelin emits JMX metrics; opt-in scrape. Defer. |

### 5.3. Airflow × existing services

| Pair | Direction | Status | Mechanism |
|---|---|---|---|
| Airflow × Supabase Postgres | Airflow→Supabase | **CRITICAL** | Airflow's own metadata DB lives in a new `airflow` database on Supabase Postgres. Plus a `postgres_supabase` Airflow Connection for user DAGs that need to query the main `${SUPABASE_DB_NAME}` database. |
| Airflow × Spark | Airflow→Spark | **CRITICAL** | `apache-airflow-providers-apache-spark` bundled; `spark_default` Connection seeded by `airflow-init` pointing at `spark-master:7077`. Sample DAG uses `SparkSubmitOperator`. |
| Airflow × MinIO | Airflow→MinIO | **CRITICAL** | `apache-airflow-providers-amazon` bundled; `minio_default` Connection seeded with `endpoint_url=http://minio:9000` + MinIO root creds. `S3Hook` Just Works™. |
| Airflow × LiteLLM | Airflow→LiteLLM | **CRITICAL** (user requirement D9) | `apache-airflow-providers-openai` + `apache-airflow-providers-langchain` bundled. `litellm_default` Connection seeded with base URL `http://litellm:4000/v1` + `LITELLM_MASTER_KEY`. Sample DAG demonstrates `LangChainOperator`. |
| Airflow × Redis | Airflow→Redis | **CRITICAL** (light) | `redis_default` Connection seeded with `host=redis, port=6379`. Used by RedisHook/RedisPubSubSensor. LocalExecutor does NOT use Redis as a broker. |
| Airflow × Weaviate | Airflow→Weaviate | **CRITICAL** | `apache-airflow-providers-weaviate` bundled; `weaviate_default` Connection seeded with URL `http://weaviate:8080`. |
| Airflow × Neo4j | Airflow→Neo4j | **CRITICAL** | `apache-airflow-providers-neo4j` bundled; `neo4j_default` Connection seeded with URI `bolt://neo4j:7687`. |
| Airflow × Hermes | Hermes→Airflow | **CRITICAL** (user requirement D10) | Airflow's REST API at `airflow.localhost/api/v2/dags/<dag_id>/dagRuns` is callable from Hermes. Hermes-side: a documented action that triggers DAGs. No code in Hermes — just an entry in `services/hermes/README.md` showing the curl pattern. Bidirectional auth via Airflow's `basic_auth` (admin password). |
| Airflow × Kong | Airflow→Kong (browser + API) | **CRITICAL** | `airflow.localhost` alias with `preserve_host: True`. The same alias serves both UI and REST API (Airflow's API lives under `/api/v2/`). |
| Airflow × n8n | none | **SKIP** | Coexist; cross-trigger surface is confusing. Per D4. |
| Airflow × Backend | Backend→Airflow | **NICE-TO-HAVE** | Backend could trigger DAGs via REST; not wired. Defer. |
| Airflow × JupyterHub | none | **SKIP** | Notebooks can trigger DAGs via REST but it's rare. Defer if requested. |
| Airflow × Zeppelin | none | **SKIP** | Same reason as JupyterHub. |
| Airflow × Ollama | Airflow→Ollama via LiteLLM | **CRITICAL** (covered by Airflow×LiteLLM) | No direct integration — always through LiteLLM. |
| Airflow × Prometheus | Prometheus→Airflow | **NICE-TO-HAVE** | Airflow 3.x emits OpenTelemetry; bridge to Prometheus via `otelcol`. Out of scope for this spec. |

### 5.4. New-service × New-service pairs

| Pair | Direction | Status | Mechanism |
|---|---|---|---|
| Airflow × Spark | Airflow→Spark | **CRITICAL** | Covered above. SparkSubmitOperator + `spark_default` connection. |
| Airflow × Zeppelin | none | **SKIP** | No real use case. |
| Spark × Zeppelin | Zeppelin→Spark | **CRITICAL** | Covered above. Zeppelin's Spark interpreter. |

### 5.5. Pairs omitted from the tables above (SKIP by default)

For brevity, the per-service tables above include only pairs with
plausible integration value. The following existing services have NO
in-design pair with any of Spark / Zeppelin / Airflow and are SKIPPED
by default:

- **Audio services** (chatterbox, parakeet, speaches): batch / SQL
  compute has no use for `/v1/audio/*` endpoints.
- **Search + scraping** (searxng): operator-triggerable via the generic
  `HTTPHook` in Airflow if needed — no pre-wired connection.
- **ComfyUI**: image-generation REST API; same generic-HTTP story.
- **Frontend services** (open-webui): doesn't talk to backend
  orchestration directly.

If any of these surfaces a real workload (e.g. Airflow scheduling
nightly batch transcription jobs via `parakeet`), the implementation
plan can re-open the pair via a generic `HTTPHook` connection — no
spec change needed.

### 5.6. Integration matrix summary

- **20 CRITICAL pairs** ship in this PR (most are config-only — `_default`
  Connection seeding + env-var population).
- **10 NICE-TO-HAVE pairs** documented as future work.
- **9 SKIP pairs** explicitly out of scope.

---

## 6. Wizard UX additions

### 6.1. New wizard steps

Three new source-selection steps added to the wizard flow, **in this
order** (so Zeppelin's gating logic can read Spark's selection):

1. **Spark cluster** (placed in the `data` category section)
   - Options: `container` (with `SecondaryNumberInput` for `SPARK_WORKER_COUNT`, range 1–8) | `disabled`
   - Description: "Standalone Spark cluster (master + workers + history) for batch / SQL / Spark Connect workloads. Independent from Ray."
2. **Zeppelin notebook** (placed in the `apps` category section, AFTER Spark)
   - Options: `container` | `disabled`
   - Gating: if `SPARK_SOURCE=disabled` AND user picks `container`, wizard surfaces an inline error and rolls back the selection.
   - Description: "Spark-first notebook UI. Requires Spark."
3. **Apache Airflow** (placed in the `agents` category section)
   - Options: `container` | `disabled`
   - Description: "Code-defined DAG orchestrator. Coexists with n8n; targets data-engineer audience. LLM operators wired to LiteLLM."

### 6.2. New CLI flags

```
--spark-source [container|disabled]
--spark-workers N                     # 1-8, mirrors --ray-worker-count
--zeppelin-source [container|disabled]
--airflow-source [container|disabled]
```

Per `project_cli_source_flag_three_seams.md`, each flag adds 4 seams:

1. `bootstrapper/start.py` — `@click.option(...)` decl + parameter in `main()` signature
2. `bootstrapper/utils/source_override_manager.py` — `source_mapping` entry
3. `bootstrapper/ui/textual/integration.py` — collector dict entry
4. `bootstrapper/ui/textual/screens/wizard_screen.py` — selection-lambda entry

The `--spark-workers` flag follows Ray's `--ray-worker-count` pattern
exactly (same widget type, same handler in `start.py`).

### 6.3. Display names + descriptions

In `bootstrapper/wizard/service_discovery.py`:

```python
DISPLAY_NAME_OVERRIDES['spark']    = 'Spark Cluster'
DISPLAY_NAME_OVERRIDES['zeppelin'] = 'Zeppelin'
DISPLAY_NAME_OVERRIDES['airflow']  = 'Airflow'

SERVICE_DESCRIPTIONS['spark']    = 'Distributed compute (batch/SQL/DataFrame)'
SERVICE_DESCRIPTIONS['zeppelin'] = 'Spark-first notebook UI'
SERVICE_DESCRIPTIONS['airflow']  = 'Code-defined DAG orchestrator'
```

---

## 7. Bootstrapper plumbing

### 7.1. `service_config.py` additions

Three new `_generate_*_config()` functions following the
`_generate_<svc>_config` pattern. Critical: they must read
`<X>_LOCALHOST_PORT` if a `localhost` source is later added (none of the
three has one in v1, per the per-service designs). Pass 2 fixed 3 sites
with this asymmetric-override bug — the symmetry test
(`test_localhost_port_consumer_symmetry.py`) will guard the new code.

### 7.2. `key_generator.py` additions

New functions for the three Airflow secrets + the Airflow DB role
password + the Airflow admin password:

```python
def generate_airflow_fernet_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()

def generate_airflow_secret_key() -> str:
    return secrets.token_urlsafe(32)

def generate_airflow_admin_password() -> str:
    return secrets.token_urlsafe(18)  # 24 chars after b64
```

Wired into `generate_missing_keys()` so a fresh `.start.sh` populates
all three on first launch.

### 7.3. `localhost_validator.py` — no entries

None of the three new services has a `localhost` source variant in v1.
`SERVICE_CHECKS` stays unchanged.

### 7.4. `kong_config_generator.py` additions

Four new routes (all with `preserve_host: True`):

- `generate_spark_service()` — `spark.localhost` → `spark-master:8080`
- `generate_spark_history_service()` — `spark-history.localhost` → `spark-history:18080`
- `generate_zeppelin_service()` — `zeppelin.localhost` → `zeppelin:8080`
- `generate_airflow_service()` — `airflow.localhost` → `airflow-webserver:8080`

Each gated on its source-var (`get_env_value('SPARK_SOURCE') != 'disabled'`
etc.).

### 7.5. `hosts_manager.py` additions

```python
GENAI_HOSTS += [
    "spark.localhost",
    "spark-history.localhost",
    "zeppelin.localhost",
    "airflow.localhost",
]
```

### 7.6. `dependency_manager.py` additions

`scale_var_mapping`:
- `SPARK_SOURCE` → `[SPARK_MASTER_SCALE, SPARK_WORKER_SCALE, SPARK_HISTORY_SCALE]`
- `ZEPPELIN_SOURCE` → `[ZEPPELIN_SCALE]`
- `AIRFLOW_SOURCE` → `[AIRFLOW_WEBSERVER_SCALE, AIRFLOW_SCHEDULER_SCALE, AIRFLOW_INIT_SCALE]`

`source_var_mapping`: trivial 1:1 additions.

### 7.7. `port_manager.py` additions

New `PORT_MAPPING` entries:

- `SPARK_MASTER_UI_PORT`, `SPARK_HISTORY_PORT` — data band offsets
- `ZEPPELIN_PORT` — apps band offset
- `AIRFLOW_PORT` — agents band offset

---

## 8. Tests

### 8.1. New test files

1. `bootstrapper/tests/test_spark_worker_count.py` — analogue of
   `test_localhost_port_override.py` for Ray's worker count. Asserts the
   wizard's source step for Spark carries a `SecondaryNumberInput` with
   `env_var=SPARK_WORKER_COUNT`, `number_min=1`, `number_max=8`.
2. `bootstrapper/tests/test_airflow_connection_seeding.py` — unit test
   for the connection-seeding logic in `airflow-init`. Mocks the
   conditional gating (only seed `spark_default` when `SPARK_SOURCE=container`,
   etc.).
3. `bootstrapper/tests/test_zeppelin_spark_gating.py` — asserts the
   bootstrapper hard-fails source-step selection when
   `ZEPPELIN_SOURCE=container` AND `SPARK_SOURCE=disabled`.

### 8.2. Extensions to existing tests

- `test_fragment_equivalence.py` — baseline regen for the new 7 service
  blocks.
- `test_fragment_bind_sources.py` — auto-covers the new fragments via
  parametrization (24 → 27 fragments). No code change needed.
- `test_port_pin_kong_ray.py` — assertion expanded with a comment that
  the new services correctly pin `kong + ray` (verified by topology
  re-run).
- `test_localhost_port_consumer_symmetry.py` — new entries for
  `SPARK_*_PORT` and `ZEPPELIN_PORT` and `AIRFLOW_PORT` will appear in
  `.env.example` but ONLY if any of them has a `LOCALHOST_PORT` variant.
  None does in v1, so `_KNOWN_NO_CONSUMER` stays unchanged.
- `test_kong_alias_routes.py` — 4 new aliases asserted (spark, spark-history, zeppelin, airflow).
- `test_source_permutations.py` — 4 new source-permutation entries
  (1 per service plus 1 for the Spark+Zeppelin gating).

### 8.3. Audit scripts

- `scripts/check-compose-source-deps.py` — `REQUIRED_DEPENDENCIES`
  gains: `(spark, supabase)`, `(spark, minio)`, `(zeppelin, spark)`,
  `(zeppelin, minio)`, `(airflow, supabase)`.
- `scripts/check-kong-routes.py` — `EXPECTED_HOST_ROUTES` gains 4 entries.
- `scripts/check-docs-drift.py` — picks up the new `services/*/README.md`
  files automatically once they're generated via `docs.regen`.

---

## 9. Documentation

### 9.1. New service READMEs

- `services/spark/README.md` — full numbered-section README following
  the `services/grafana/README.md` template. Sections: Overview, Access,
  Configuration, Cluster topology, Dashboards (Web UI + History),
  Dependencies & Integrations (auto-gen), Troubleshooting.
- `services/zeppelin/README.md` — full numbered-section README. Sections:
  Overview, Access, Configuration, Notebooks (Spark + JDBC quickstart),
  Dependencies & Integrations (auto-gen), Troubleshooting.
- `services/airflow/README.md` — full numbered-section README. Sections:
  Overview, Access, Configuration, Connections seeded (the matrix from
  §5.3 in narrative form), DAG samples, Troubleshooting.

### 9.2. Cross-service updates

- `README.md` (top-level) — 3 new rows in §4.1 Service Overview; new
  bullet in §2.3; if §3.4 Observability mentions specific services,
  update there too.
- `docs/CHANGELOG.md` — single `### Added — Airflow + Spark + Zeppelin
  compute tier` entry with the integration matrix summarized.
- `docs/deployment/ports-and-routes.md` — 4 new routes + their internal
  ports.
- `docs/deployment/source-configuration.md` — 3 new `_SOURCE` sub-sections
  with `container` / `disabled` walkthroughs.
- `docs/quick-start/interactive-setup-wizard.md` — 3 new options.
- `docs/ROADMAP.md` — mark these three off if listed; add the
  NICE-TO-HAVE follow-up integrations as new entries.
- `services/kong/README.md` — 4 new route bullets + curl examples.
- Cross-references in `services/spark/README.md`,
  `services/zeppelin/README.md`, and `services/airflow/README.md` to
  each other (Airflow's `SparkSubmitOperator` → links Spark; Zeppelin's
  Spark interpreter → links Spark; etc.).

### 9.3. Auto-generated `Dependencies & Integrations` sections

Each new service's `service.yml::data_flow.calls` declares its outbound
calls. Per `project_data_flow_calls_cross_service_regen.md`, edits to
any of the three new manifests' `data_flow.calls` requires
`python -m docs.regen --all` (not `regen <svc>`), and the hermes byte-
equivalence golden fixtures need re-baking if the changes ripple into
hermes (Airflow→Hermes integration via REST adds an entry —> re-bake).

Initial `data_flow.calls` for each:

- `services/spark/service.yml`:
  ```yaml
  data_flow:
    calls:
      - supabase   # JDBC reads
      - minio      # s3a:// history + user data
      - kong       # health endpoints exposed via alias
  ```
- `services/zeppelin/service.yml`:
  ```yaml
  data_flow:
    calls:
      - spark      # the whole point
      - supabase   # JDBC interpreter
      - minio      # via Spark
      - litellm    # optional Python interpreter use
      - kong       # web UI alias
  ```
- `services/airflow/service.yml`:
  ```yaml
  data_flow:
    calls:
      - supabase   # metadata DB + user-defined Postgres connections
      - spark      # SparkSubmitOperator
      - minio      # S3 hooks
      - litellm    # LLM operators
      - weaviate   # vector store ops
      - neo4j      # graph ops
      - redis      # RedisHook
      - kong       # web UI alias
  ```

---

## 10. CHANGELOG entry (sketch)

```markdown
### Added — Apache Airflow + Apache Spark cluster + Apache Zeppelin

Three new services added in a single coordinated landing as the
stack's compute / orchestration tier:

- **Spark cluster** (`SPARK_SOURCE=disabled|container`) — Apache Spark
  4.1.2 in standalone mode: 1 master + N workers (default 2, range
  1-8 via the new `--spark-workers` flag mirroring Ray's
  `--ray-worker-count`) + history server. Spark Connect gRPC enabled
  on port 15002. Web UI at `spark.localhost`, history at
  `spark-history.localhost`. Persists event logs to the
  `spark-history` MinIO bucket (created by spark-init).

- **Zeppelin notebook** (`ZEPPELIN_SOURCE=disabled|container`) —
  Apache Zeppelin 0.12.0 with pre-configured Spark / SQL (JDBC to
  Supabase Postgres) / Shell / Markdown interpreters. Gated on
  `SPARK_SOURCE != disabled` — Zeppelin without Spark refuses to
  start with an actionable error. Web UI at `zeppelin.localhost`.

- **Apache Airflow** (`AIRFLOW_SOURCE=disabled|container`) — Apache
  Airflow 3.2.2 (LocalExecutor) with the new AI/ML SDK enabled. Bundled
  providers: apache-spark, amazon (MinIO via custom endpoint),
  postgres, redis, weaviate, neo4j, openai, langchain. Metadata DB
  lives in a new `airflow` database on Supabase Postgres (created by
  airflow-init). LangChainOperator + OpenAIOperator connections
  pre-seeded against LiteLLM. Sample `example_etl_with_llm` DAG ships
  in `services/airflow/dags/`. Web UI + REST API at `airflow.localhost`.

**Comprehensive integration coverage** (per the design doc's
integration matrix in §5):

- Spark: MinIO (s3a), Supabase Postgres (JDBC), Kong (preserve_host),
  Prometheus (JMX), Grafana (dashboard).
- Zeppelin: Spark (interpreter), MinIO (via Spark), Supabase Postgres
  (JDBC), Kong (preserve_host).
- Airflow: Supabase Postgres (metadata + user conn), Spark
  (SparkSubmitOperator), MinIO (S3Hook), LiteLLM (LangChain/OpenAI
  operators), Redis (RedisHook), Weaviate, Neo4j. Hermes → Airflow via
  REST API documented in services/hermes/README.md.

**Wizard additions**: 3 new source steps + Spark's worker-count
inline numeric input mirrors Ray's pattern. CLI: `--spark-source`,
`--spark-workers N`, `--zeppelin-source`, `--airflow-source`.

**Defaults**: all three services default to `disabled` matching the
heavyweight-services convention (Ray, Prometheus, Grafana). Opt in
via wizard or CLI flag.

See [docs/superpowers/specs/2026-06-04-airflow-spark-zeppelin-design.md]
for the full design.
```

---

## 11. Files touched (estimated ~75)

The exhaustive file list belongs in the implementation plan (writing-plans
step). High-level fan-out:

- **New service folders** (3): `services/spark/`, `services/zeppelin/`,
  `services/airflow/` — each with `service.yml`, `compose.yml`, `README.md`,
  `architecture.{svg,html}` (auto-gen), and any `init/scripts/`,
  `notebooks/`, `dags/`, `build/Dockerfile`.
- **Top-level compose**: 3 new `include:` lines.
- **Bootstrapper** (~13 files per the service-addition checklist): all 4
  CLI seams × 3 services = 12 edits; `key_generator.py` for the 3 Airflow
  secrets; `kong_config_generator.py` for 4 new routes; `hosts_manager.py`
  for 4 new hostnames; `dependency_manager.py` mappings; `port_manager.py`
  mappings; `service_config.py` for 3 new endpoint builders.
- **Docs** (~9 files): listed in §9.
- **Audit scripts** (3 files): listed in §8.3.
- **Tests** (3 new + 6 extended): listed in §8.1–§8.2.
- **Auto-generated** (regen runs at end): every service's
  `architecture.svg` + `architecture.html` + `README.md` §5 may shift due
  to new cross-references.
- **`.env.example`**: ~30 new lines.

---

## 12. Open risks

1. **Memory footprint** — All three enabled simultaneously add an
   estimated ~7-9 GB RAM to the stack (Spark ~3-4 GB, Airflow ~1-2 GB,
   Zeppelin ~1-2 GB). Already default-disabled, but worth a section in
   each service's README warning developers.
2. **Airflow 3.2 stability** — Released 2026-05-29 (one week before this
   spec). If we hit a regression, fall back to 3.1.8 (released 2026-03-11)
   via `AIRFLOW_IMAGE=apache/airflow:3.1.8`. No spec changes needed.
3. **Bitnami's Spark image future** — Bitnami images are maintained by
   Broadcom post-acquisition; long-term availability is uncertain. We pin
   a digest at install time as a hedge; if the image becomes unavailable,
   migrating to `apache/spark` is a Dockerfile change in
   `services/spark/build/` (not introduced in this spec — we use the
   image directly).
4. **Supabase Postgres tenant isolation** — Adding the `airflow` database
   to Supabase Postgres assumes the Supabase init scripts allow a third-
   party database. Verify in implementation: `services/supabase/db/init/`
   may need a new `08-create-airflow-db.sql` script if Supabase's
   container locks down `CREATE DATABASE`. Currently expected to work;
   confirmable during implementation.
5. **Spark + Ray port-collision risk** — Both ship JMX-style metrics on
   non-standard ports. None overlap today (Ray = 8265 internal, Spark
   master = 8080 internal). No conflict expected.
6. **Zeppelin's session affinity** — Zeppelin uses sticky sessions for
   notebook state. With a single container instance this is a non-issue.
   If we later scale Zeppelin to N replicas, Kong needs session-affinity
   wiring.

---

## 13. Acceptance criteria

A reviewer can mark this PR ready-to-merge when:

- [ ] All 3 services boot in their `container` source variant via
      `./start.sh --spark-source container --zeppelin-source container
      --airflow-source container`.
- [ ] `docker exec atlas-airflow-webserver airflow connections list`
      shows all expected `_default` connections seeded based on which
      sibling services are enabled.
- [ ] `docker exec atlas-zeppelin` can run a sample notebook that does
      a Spark DataFrame round-trip with MinIO.
- [ ] `docker exec atlas-spark-master spark-submit --version` reports
      4.1.2.
- [ ] All 4 Kong aliases resolve in the browser with no host-rewrite
      issues (verified via `curl -I` from the host).
- [ ] All new + extended tests pass (`pytest` green).
- [ ] `python -m docs.regen --all --check` clean.
- [ ] All 5 `scripts/check_*.py` audit scripts PASS.
- [ ] CHANGELOG entry rendered and reviewed.
- [ ] Memory footprint with all 3 enabled measured + documented in the
      respective READMEs.

---

## 14. Out of scope (deferred to future specs)

- **CeleryExecutor for Airflow** — current spec is LocalExecutor-only.
- **Spark-on-Kubernetes** or **Spark-on-Ray (RayDP)** — current spec is
  standalone mode only.
- **NICE-TO-HAVE pairs from §5** — Weaviate Spark Connector, Neo4j Spark
  Connector, Spark-LiteLLM UDFs, Backend→Airflow trigger, Zeppelin
  Prometheus scrape, etc. Each is a separate ~1-day follow-up.
- **Airflow DAG bundle management** — current spec ships DAGs as files
  bind-mounted into the container; production setups would use git-sync
  or DAG bundles.
- **Multi-user Airflow** — single admin user only.
- **Spark structured streaming** — batch-only in v1.
- **Spark history server log retention policies** — defaults to forever
  in v1.
- **Zeppelin notebook-permission system** — single anonymous user only.
- **CeleryExecutor migration path** — covered in a future spec when
  user load justifies it.
