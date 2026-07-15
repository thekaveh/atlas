# Zeppelin Spark Backend Decision

Decision issue: [#247](https://github.com/thekaveh/atlas/issues/247)
Implementation issue unblocked by this decision: [#211](https://github.com/thekaveh/atlas/issues/211)

## 1. Decision

Atlas should use Zeppelin's stock Spark interpreter in standalone Spark mode for
the zero-touch lakehouse notebook path in #211.

For Zeppelin, #211 should seed:

- `SPARK_HOME` inside the Zeppelin runtime.
- `spark.master=spark://spark-master:7077`.
- Iceberg REST catalog settings for `lakehouse`.
- MinIO S3A settings for event logs, warehouse reads/writes, and Iceberg's S3
  file IO.

Atlas should not require Zeppelin `%spark` Scala to use
`spark.remote=sc://spark-connect:15002` in this phase. Spark Connect remains the
right path for JupyterHub PySpark/Scala clients and other clients that use the
Spark Connect client API directly.

## 2. Rationale

The blocker in #211 showed that Apache Zeppelin 0.12.1 still starts the stock
Spark interpreter through `spark-submit`. With Spark 4, that launch path aborts
when `spark.remote` is present together with a master or deploy-mode value. The
failed live smoke also showed that removing `spark.master` from the saved
interpreter setting was not enough; Zeppelin still injected an empty or default
master into the Spark launch.

This matches the upstream support boundaries:

- Zeppelin's Spark interpreter documentation lists `%spark`, `%spark.pyspark`,
  `%spark.sql`, and related interpreters, and its supported execution modes are
  Local, Standalone, Yarn, and K8s. It documents `SPARK_HOME` plus
  `spark.master` as the normal way to connect Zeppelin to a Spark cluster:
  https://zeppelin.apache.org/docs/latest/interpreter/spark.html
- Spark's submit documentation treats `spark://HOST:PORT` as the standalone
  master URL for `spark-submit`, with client mode suitable for REPL-like
  interactive applications:
  https://spark.apache.org/docs/latest/submitting-applications.html
- Spark Connect is a separate client-server API configured through
  `SPARK_REMOTE`, `SparkSession.builder.remote(...)`, or shell `--remote`
  flows:
  https://spark.apache.org/docs/latest/spark-connect-overview.html

The practical conclusion is that Zeppelin's stock `%spark` interpreter should
stay on the Spark submit / standalone-cluster path unless Atlas admits a deeper
custom interpreter patch or a new service backend such as Livy.

## 3. Rejected Options

### 3.1. Strict Spark Connect for Zeppelin `%spark`

Rejected for #211. The observed Zeppelin launch path combines Spark Connect
configuration with Spark submit master handling, and Spark 4 rejects that mix.
Continuing on this path would require a custom Zeppelin interpreter/launcher
patch or a newer upstream-supported Zeppelin path that does not pass a master
when `spark.remote` is set.

### 3.2. Add Livy Now

Deferred. Livy is a legitimate Zeppelin Spark backend, and Zeppelin documents a
Livy interpreter with `zeppelin.livy.url` plus `livy.spark.*` settings:
https://github.com/apache/zeppelin/blob/master/docs/interpreter/livy.md

Atlas should not smuggle Livy into #211. Livy would be a new service admission:
track membership, category, image, source values, ports, Kong posture,
dependencies, health checks, init behavior, docs, and smoke tests. If Atlas
wants Livy, create a separate implementation issue before coding it.

### 3.3. Defer Zeppelin Spark

Rejected. Standalone Spark mode fits Zeppelin's documented model and unblocks a
fresh `%spark` notebook path without adding a new Atlas service.

## 4. Requirements For #211

#211 should be updated from "Spark Connect in Zeppelin" to "standalone Spark
interpreter with seeded lakehouse settings."

Service admission contract:

- Track: `data-eng`; no new track is required.
- Category: existing `zeppelin` service remains `apps`.
- Source values: keep `ZEPPELIN_SOURCE=container|disabled`; no new source value
  is needed.
- Default posture: keep Zeppelin disabled by default unless an existing track
  or explicit CLI/source choice enables it.
- Ports: no new host ports; keep `ZEPPELIN_PORT` bound to host loopback.
- Kong alias: none while Zeppelin ships without authentication.
- Dependencies: keep Spark and MinIO required; document optional/runtime edges
  to Supabase and Iceberg REST when the lakehouse catalog is seeded.
- Topology/data flow: `services/zeppelin/service.yml` should include
  `iceberg-rest` in `depends_on.optional` and `data_flow.calls` when the seeded
  catalog becomes part of the runtime contract.
- Init companion: #211 may add a `zeppelin-init` one-shot container inside the
  existing Zeppelin family. It should have no host port, should scale to zero
  when `ZEPPELIN_SOURCE=disabled`, and should update/restart interpreter
  settings idempotently through Zeppelin's REST API or an equivalent mounted
  config.
- Secrets: use scoped Spark/S3A and Iceberg MinIO credentials; do not expose
  MinIO root credentials to the notebook process.
- Wizard text: describe Zeppelin as a Spark-first notebook with zero-touch
  standalone Spark and lakehouse catalog setup when enabled. Do not present
  `spark.remote=sc://spark-connect:15002` as the happy path for Zeppelin.

Validation required by #211:

- Unit tests for manifest/runtime scales, init script idempotence, interpreter
  property generation, source gating, wizard copy, topology edges, docs, and
  generated baselines.
- A cold-start Compose smoke with Supabase DB, MinIO/init, Iceberg REST/init,
  Spark, Zeppelin, and the Zeppelin init companion.
- The smoke should create a fresh Zeppelin notebook/paragraph and run a cell
  equivalent to `SHOW NAMESPACES IN lakehouse` through `%spark` or `%spark.sql`.
- Existing starter notebooks should continue to load.
- Docs drift, link checks, source-permutation checks, Kong route checks, and
  full bootstrapper tests must pass.

## 5. Follow-Up Candidate

If Atlas later wants a server-side notebook/job gateway that is not tied to
Zeppelin's Spark submit interpreter, create a separate Livy service issue. That
issue should decide whether Livy belongs to `data-eng`, `data-ml`, or both, and
must include source values, port allocation, Kong exposure policy, dependencies,
health checks, init strategy, and smoke tests before implementation.
