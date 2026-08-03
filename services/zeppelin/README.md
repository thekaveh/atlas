# 5.2.60. Apache Zeppelin (Spark-first notebook)

Zeppelin runs as a single container in the stack's `apps` band. The Spark interpreter is intended for the in-stack standalone Spark cluster (`spark://spark-master:7077`) plus MinIO S3A. `zeppelin-init` also seeds a Trino JDBC interpreter when `TRINO_SOURCE=container`; Supabase Postgres remains a manual JDBC profile. Notebooks live in `services/zeppelin/notebooks/`, bind-mounted into the container.

## 1. Overview

Image: `apache/zeppelin:0.12.1` (Apache 2.0), wrapped by `services/zeppelin/build/Dockerfile` so `/opt/spark` contains the matching Spark 4.1.2 runtime plus S3A and Iceberg lakehouse jars. All interpreters run in-process (no Kubernetes interpreter isolation). The Spark interpreter is the headline.

**Hard requirement:** Zeppelin is gated on `SPARK_SOURCE != disabled`. Picking `ZEPPELIN_SOURCE=container` without Spark surfaces an actionable error from the bootstrapper; the spec considers a Spark-less Zeppelin broken on purpose.

**Design update:** [Zeppelin Backend Decision](../../docs/strategy/zeppelin-spark-backend-decision.md) selects the standalone Spark interpreter path for Atlas Zeppelin. Spark Connect remains supported by JupyterHub and other Spark Connect clients. The stack should not require `%spark` Scala to use Spark Connect because Zeppelin's stock interpreter launches through `spark-submit` and Spark 4 rejects `spark.remote` mixed with master/deploy-mode configuration.

### 1.1. Spark backend posture

Atlas should treat Zeppelin as a Spark-submit/standalone Spark notebook surface:

- The selected backend is `spark.master=spark://spark-master:7077`.
- The implementation path for zero-touch lakehouse notebooks is a bundled or mounted `SPARK_HOME` plus seeded interpreter settings for MinIO S3A and the Iceberg REST `lakehouse` catalog.
- JupyterHub remains the Spark Connect notebook path for Python and Scala clients that use `SPARK_REMOTE`, `SparkSession.builder.remote(...)`, or Spark Connect client libraries directly.

The stack should not configure Spark Connect's remote property as the happy path for Zeppelin `%spark` on Spark 4.

### 1.2. Zero-touch Spark interpreter seeding

`zeppelin-init` waits for the Zeppelin REST API, updates the stock `spark` interpreter setting, restarts it, and exits. It is idempotent: rerunning it preserves user-owned properties while overwriting Atlas-owned Spark/lakehouse values.

Seeded values include:

- `SPARK_HOME=/opt/spark`
- `spark.master=spark://spark-master:7077`
- `zeppelin.spark.enableSupportedVersionCheck=false`
- `spark.submit.deployMode=client`
- `spark.driver.host=zeppelin` and `spark.driver.bindAddress=0.0.0.0`
- MinIO S3A settings for `s3a://` reads/writes and Spark event logs
- `spark.sql.catalog.lakehouse.uri=http://iceberg-rest:8181` and the rest of the Iceberg REST catalog settings for `lakehouse`

Manual recovery path: open Zeppelin at `http://localhost:${ZEPPELIN_PORT}`, go to top-right user menu → **Interpreter** → `spark`, and verify the values above. Click **Save**, then confirm the restart prompt if you make changes.

### 1.3. Verify it works

In a new notebook, run a `%spark` (Scala) cell:

```scala
%spark
println(spark.version)                 // prints the cluster's Spark version (4.1.x)
spark.range(5).selectExpr("id * id as sq").show()
```

…and a `%spark.pyspark` (Python) cell:

```python
%spark.pyspark
spark.sql("SELECT 1 + 1 AS result").show()
```

If both return values, the notebook is talking to the standalone Spark cluster. (The starter notebook `notebooks/spark_basics.zpln` in §5 does the same checks plus an s3a round-trip.)

With Iceberg REST enabled, a `%spark.sql` cell can validate the lakehouse catalog:

```sql
%spark.sql
SHOW NAMESPACES IN lakehouse
```

With Trino enabled, `zeppelin-init` creates a named JDBC interpreter profile called `trino`. Use `%trino` in Zeppelin 0.12.1:

```sql
%trino
SHOW CATALOGS;
SHOW SCHEMAS FROM lakehouse;
```

The generic Zeppelin JDBC docs describe multiple connections as `%jdbc(prefix)`, and data-eng-lab originally asked for `%jdbc(trino)`. Atlas seeds the named `trino` interpreter instead because Zeppelin 0.12.1 uses the interpreter name as the paragraph prefix for created JDBC profiles; `%jdbc(trino)` is not the documented happy path for this stack.

### 1.4. How MinIO (s3a) and Spark History work

For the intended zero-touch path, users should not configure storage credentials in the notebook. The seeded Spark interpreter should carry the same storage settings as the rest of the lakehouse stack:

- `s3a://` reads/writes use `spark.hadoop.fs.s3a.*` settings for MinIO endpoint `http://minio:9000`, credentials, and path-style addressing.
- `spark.eventLog.enabled=true` + `spark.eventLog.dir=s3a://spark-history/` send events to the Spark History Server automatically. Browse them at the Spark History UI (`SPARK_HISTORY_PORT`).
- Iceberg SQL should use `spark.sql.catalog.lakehouse.*` settings pointed at `http://iceberg-rest:8181` and the `s3a://lakehouse/` warehouse.

### 1.5. Reaching Spark Connect from outside the stack (host IDEs, remote/cloud)

The `spark-connect` sidecar is **backend-only by design** — it publishes no host port, so `sc://spark-connect:15002` resolves only from inside the Docker `backend-network`. JupyterHub is the in-stack notebook surface for that protocol. Publishing the gRPC port for host-side IDEs, and pointing `spark.remote` at a managed cloud Spark Connect endpoint instead, are both tracked as roadmap items — neither is enabled in the in-stack-only baseline.

### 1.6. Driving Zeppelin from VS Code

Zeppelin speaks its own REST + websocket protocol, not the Jupyter kernel protocol, so VS Code's built-in Jupyter extension cannot connect to it. The community **"Zeppelin Notebook"** extension ([`AllenLi1231.zeppelin-vscode`](https://marketplace.visualstudio.com/items?itemName=AllenLi1231.zeppelin-vscode)) renders `.zpln` files and runs paragraphs server-side against the same Spark interpreter as the web UI — point it at `http://localhost:${ZEPPELIN_PORT}` (no credentials; see §2), SSH-tunneling that port for a remote host. See the extension's Marketplace page for setup steps and caveats; the browser UI (§2) is the dependable fallback.

## 2. Access

| Surface | URL | Auth |
|---|---|---|
| Direct | `http://localhost:${ZEPPELIN_PORT}` | None; the published port is always bound to `127.0.0.1`. |

No authentication ships pre-configured, so Atlas does not publish Zeppelin through Kong and does not honor a wider `HOST_BIND_IP` for this service. Reach a remote Atlas host through the SSH tunnel documented in §1.6. Configure Zeppelin authentication before introducing any external reverse-proxy route.

## 3. Configuration

```bash
ZEPPELIN_SOURCE=disabled           # container | disabled
ZEPPELIN_IMAGE=apache/zeppelin:0.12.1
ZEPPELIN_INIT_IMAGE=python:3.12.13-alpine
ZEPPELIN_PORT=                     # auto-assigned (apps band)
```

## 4. Integration with the stack

- **Spark** (required) — `%spark` cells use the standalone Spark interpreter path selected in the Zeppelin backend decision: `SPARK_HOME` plus `spark.master=spark://spark-master:7077`.
- **MinIO** — `s3a://` credentials come from the generated `MINIO_SPARK_*` service account. It is limited to the Spark event-log and lakehouse workflow buckets; Zeppelin never receives MinIO root credentials.
- **Iceberg REST** (optional) — when `ICEBERG_REST_SOURCE=container`, the seeded `lakehouse` catalog points to `http://iceberg-rest:8181` and uses the scoped Iceberg MinIO credentials for S3FileIO.
- **Trino** (optional) — when `TRINO_SOURCE=container`, `zeppelin-init` waits for `http://trino:8080/v1/info` and creates or updates a named JDBC interpreter profile `trino` (group `jdbc`) with `default.driver=io.trino.jdbc.TrinoDriver`, `default.url=jdbc:trino://trino:8080/lakehouse`, `default.user=atlas`, and dependency `io.trino:trino-jdbc:482`. Then `%trino SHOW CATALOGS` works without manual UI setup. Trino still requires `MINIO_SOURCE=container` and `ICEBERG_REST_SOURCE=container`; if `TRINO_SOURCE=disabled`, the init script logs a skip and leaves existing JDBC settings alone.
- **Supabase Postgres** — JDBC connection details are exposed as env vars (`ZEPPELIN_JDBC_POSTGRES_URL` / `_USER` / `_PASSWORD`), but Zeppelin does not auto-bind them to a JDBC interpreter. One-time manual setup is required: create a `postgres` JDBC interpreter in the Zeppelin UI using those env var values. Zero-touch seeding (bind-mounting `conf/interpreter.json`) is tracked as a future improvement.
- **LiteLLM** (optional) — Python interpreter can call the LiteLLM gateway via `openai.OpenAI(base_url="http://litellm:4000/v1", api_key=...)`. No pre-configuration ships; users wire it themselves.

## 5. Starter notebook

`services/zeppelin/notebooks/spark_basics.zpln` ships pre-loaded. 5 cells:
1. Spark version check (`spark.version`)
2. Markdown intro
3. MinIO round-trip via S3A (`s3a://spark-history/...`)
4. Trino JDBC metadata smoke via `%trino` (`SHOW CATALOGS`; `SHOW SCHEMAS FROM lakehouse`) when `TRINO_SOURCE=container`
5. Postgres JDBC `SELECT version()` against supabase-db (requires the one-time `postgres` interpreter setup in §4; the cell will error with "Interpreter not properly configured" until you complete it)

Use it as a template for your own notebooks.

`services/zeppelin/notebooks/iceberg_advanced_sql.zpln` is the opt-in
standalone Spark counterpart to JupyterHub's Spark Connect advanced smoke,
covering Iceberg's MERGE/branching/streaming/maintenance surface. Run it
from the Zeppelin UI or from the repository root:

```bash
scripts/smoke-iceberg-advanced-sql.sh zeppelin
```

This `data-eng` / `all` track smoke adds no new service, SOURCE, or port. It
requires `SPARK_SOURCE`, `ICEBERG_REST_SOURCE`, `MINIO_SOURCE`, and
`ZEPPELIN_SOURCE` all set to `container`. See
[`docs/deployment/iceberg-advanced-smoke.md`](../../docs/deployment/iceberg-advanced-smoke.md)
for the full feature list this smoke exercises.

## 6. Dependencies & Integrations

### 6.1. Current — Upstream (this service calls)

| Service | Category |
|---|---|
| iceberg-rest | data |
| minio | data |
| redpanda | data |
| spark | data |
| supabase | data |
| trino | data |

### 6.2. Current — Downstream (services that call this)

_No downstream consumers._

### 6.3. Architecture diagram

![zeppelin architecture](./architecture.svg)

[Open the full-size diagram](./architecture.html) for a full-screen view.

### 6.4. Future — Missing pair integrations

_No high-confidence opportunities identified._

### 6.5. Future — Candidate new services

_No high-confidence opportunities identified._

### 6.6. Future — Unused features in this service

_No high-confidence opportunities identified._

## 7. Troubleshooting

- **Spark interpreter says "no master URL"** — `SPARK_MASTER` env var is missing from the container. Check the compose env block; the manifest's runtime_sc + compose.yml dual-write should ensure it. Restart the container after fixing.
- **First `%spark` cell after stack-up errors with "connection refused"** — Zeppelin's `depends_on` gates on `spark-master: service_healthy` and `spark-init: service_completed_successfully`, but a cold Spark worker or freshly restarted interpreter can still take a few seconds to accept driver/executor traffic. Confirm `spark.master=spark://spark-master:7077` in the `spark` interpreter settings, then re-run the cell once the Spark master UI shows a live worker.
- **S3A: "Access Denied" on s3a://...** — the generated `MINIO_SPARK_ACCESS_KEY` / `MINIO_SPARK_SECRET_KEY` or scoped policy is missing from the container. `docker exec ${PROJECT_NAME}-zeppelin env | grep -E 'MINIO|SPARK_SUBMIT_OPTIONS'` to confirm. Re-run `./start.sh` to provision the account and refresh the interpreter.
- **JDBC interpreter "Interpreter not properly configured"** — Zeppelin does not auto-bind the `ZEPPELIN_JDBC_POSTGRES_*` env vars to a JDBC interpreter profile. Walk through §4's one-time UI setup, then restart it (Interpreter → postgres → Restart). Supabase Postgres also must be running (it's a required dep of the stack).
- **`%trino` is missing or cannot load the driver** — confirm both `ZEPPELIN_SOURCE=container` and `TRINO_SOURCE=container`, then check `docker logs ${PROJECT_NAME}-zeppelin-init`. The init script should report either "trino JDBC interpreter created" or "already configured". The interpreter dependency must include `io.trino:trino-jdbc:482`.
- **"Notebook won't save"** — `/notebook` is bind-mounted from `services/zeppelin/notebooks/`. Confirm `services/zeppelin/notebooks/` exists and is writable by the host user. Zeppelin writes new .zpln files there.
