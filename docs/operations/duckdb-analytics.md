# 7.9. Embedded DuckDB analytics over Parquet

This optional example runs one analytical SQL query inside the Python process with DuckDB. It reads a local Parquet file or the same file in MinIO.
It needs no query server. Spark, Trino and Airflow can stay disabled.

The source lives in `examples/duckdb-analytics/`. It has its own Python environment and dependency lock.
Its only dependency is `duckdb==1.5.5`, the version JupyterHub already ships.

## 1. Requirements and limits

Use Python 3.11, 3.12 or 3.13 and `uv` from the repository root. The recipe uses only DuckDB and the standard library.

The sample is synthetic and needs no credentials. The default sample has 30,000,000 rows in a 98,690,162-byte Parquet file.
Object-storage reads need the DuckDB `httpfs` extension and S3 credentials in the environment.

Each run uses a private in-memory database. The recipe sets the DuckDB memory limit, thread count and spill directory before it reads data.
It then allows only the named input or output path. Finally it disables other external access and locks the configuration.

The recipe never installs or autoloads extensions by itself. These controls are DuckDB settings, not operating-system quotas.

## 2. Create the environment

1. Open a shell at the Atlas repository root.
2. Create a work directory and install the locked dependency:

    ```bash
    ATLAS_DUCKDB_SOURCE="$(pwd -P)/examples/duckdb-analytics"
    ATLAS_DUCKDB_WORK="$(mktemp -d "${TMPDIR:-/tmp}/atlas-duckdb.XXXXXXXX")"
    UV_PROJECT_ENVIRONMENT="$ATLAS_DUCKDB_WORK/venv" \
      uv sync --project "$ATLAS_DUCKDB_SOURCE" --locked --no-dev
    atlas_duckdb() {
      "$ATLAS_DUCKDB_WORK/venv/bin/python" -I "$ATLAS_DUCKDB_SOURCE/recipe.py" \
        --temp-dir "$ATLAS_DUCKDB_WORK/spill" "$@"
    }
    ```

    Keep the work directory. It holds the environment, the sample, the spill directory and the result table.

## 3. Generate the sample

Write the deterministic sample to a new local path:

```bash
atlas_duckdb generate --output "$ATLAS_DUCKDB_WORK/sample.parquet"
```

The command prints `{"status": "generated", ...}`. It refuses to overwrite an existing local file.
`--rows` changes the row count. Every table in this page uses the default.

The generator derives every column from the row number. It uses no random function.
Two runs on the measured host produced byte-identical files with SHA-256 `795b9cd5fe2bedf67e22b1a282ac84ae520d06085233cb51270df86615669273`.
DuckDB versions can change the Parquet bytes, so the recipe checks the query result instead.

## 4. Run the checked query

Run the analysis and export the result table:

```bash
atlas_duckdb --memory-limit 512MB --threads 2 query \
  --input "$ATLAS_DUCKDB_WORK/sample.parquet" \
  --output "$ATLAS_DUCKDB_WORK/result.csv" \
  --expect-sha256 aad8641dda8567ebd2d0ebfaf2a26dbd1f73d856a67c5a1e747b4757dbe9f04d
```

The query totals orders and spend per customer, then summarizes the customers per region. It uses integer arithmetic and a fixed `ORDER BY`.
The recipe writes the eight result rows as CSV and prints the table's SHA-256 digest.

A matching digest prints `"status": "ok"`. A different digest prints `"status": "mismatch"` and exits 1.
The JSON line also reports startup time, query time, peak resident memory and peak spill.

The exported table starts with these rows:

| region | customers | orders | spend_cents | top_customer_cents |
|---|---|---|---|---|
| af | 500000 | 3750000 | 94143750000 | 400680 |
| ap | 500000 | 3750000 | 94162500000 | 400720 |

## 5. Query the same file in MinIO

DuckDB reads `s3://` paths through its `httpfs` extension. Install it once for the DuckDB version in use:

```bash
"$ATLAS_DUCKDB_WORK/venv/bin/python" -c 'import duckdb; duckdb.connect().install_extension("httpfs")'
```

The install downloads the extension from `extensions.duckdb.org` into `~/.duckdb/extensions`. Later runs load the local copy.

The recipe reads the endpoint and credentials from the standard AWS variables. JupyterHub already sets them for its scoped MinIO account:

| Variable | Use |
|---|---|
| `AWS_ENDPOINT_URL_S3` | MinIO URL, for example `http://minio:9000`. An `https` URL turns on TLS. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | MinIO access key and secret. |
| `AWS_REGION` or `AWS_DEFAULT_REGION` | Optional. The default is `us-east-1`. |

The recipe creates a temporary DuckDB secret with path-style URLs. It never prints the secret. Pass the same query an object path:

```bash
atlas_duckdb query --input s3://jupyter/duckdb/sample.parquet \
  --expect-sha256 aad8641dda8567ebd2d0ebfaf2a26dbd1f73d856a67c5a1e747b4757dbe9f04d
```

To publish the sample, run `generate --output s3://<bucket>/<key>.parquet` with credentials that can write the bucket.
The JupyterHub account writes only its own `jupyter` bucket. It can read `lakehouse` but not write it.

Inside JupyterHub, upload `recipe.py` and run it from a terminal with the image's Python. The image already pins `duckdb==1.5.5`.
The `httpfs` install step still applies there. The measured run below did not cover JupyterHub. The [JupyterHub guide](../../services/jupyterhub/README.md) lists the injected variables.

## 6. Measured cost, spill and concurrency

The measurements used the default sample on macOS 27.0.1 ARM64 with CPython 3.12.9, uv 0.11.19 and DuckDB 1.5.5.
The host had 24 cores and 192 GiB of memory. Resident memory is the process peak from `/usr/bin/time -l`.

A bare Python process that imported DuckDB and opened a connection took 0.09 seconds of wall time and 46 MiB of resident memory.
Inside the recipe, import and connection took about 0.04 seconds. Generating the sample took about 4 seconds.

Local query with two threads:

| `--memory-limit` | Query time | Peak resident memory | Peak spill |
|---|---|---|---|
| 2GB | 0.9 s | 1,024 MiB | 0 |
| 1GB | 1.9 s | 1,164 MiB | 986 MiB |
| 512MB | 2.0 s | 631 MiB | 1,428 MiB |
| 256MB | 2.6 s | 388 MiB | 1,491 MiB |
| 128MB | 2.8 s | 237 MiB | 1,584 MiB |

Every run returned the same digest. Threads changed time more than memory. At 512MB, one thread took 4.0 s and four threads took 1.3 s.
Eight threads were no faster than four on this query.

Spilling runs depend on host load and disk speed. A repeat 512MB run under a load average near 15 took 22 seconds. Its digest and memory were unchanged.

The MinIO runs used a disposable local MinIO container with the pinned Atlas `MINIO_IMAGE`. They returned the same digest:

| `--memory-limit` | Query time | Peak resident memory | Peak spill |
|---|---|---|---|
| 2GB | 1.4 s | 1,044 MiB | 0 |
| 512MB | 2.5 s | 695 MiB | 1,414 MiB |
| 256MB | 3.1 s | 381 MiB | 1,483 MiB |

A wrong secret failed with an HTTP 403 error. The error text did not contain the secret.

### 6.1. Limits to plan around

- **Memory.** Resident memory exceeded the DuckDB limit by about 115 to 250 MiB. Budget the limit plus 256 MiB per process.
- **Spill.** This query spilled 15 to 17 times the Parquet file size when memory was short. Keep that much free disk under `--temp-dir`.
- **Spill cap.** DuckDB 1.5.5 did not enforce `max_temp_directory_size` for this aggregate. A 128 MB setting still allowed about 1.5 GB of temporary files.
- **Recipe cap.** The recipe therefore samples the spill directory every 20 ms. Above `--max-temp`, default `4GB`, it interrupts the query and exits 2.
- **Concurrency.** Each process has its own database, memory and spill. Four parallel 512MB runs each took 3.6 s and used about 660 MiB. Eight took about 4.9 s each.
- **Database files.** The recipe uses no database file. A shared DuckDB file allows one writing process; other processes must open it read-only.

The cap check is approximate. A fast writer can pass the cap by one sampling interval before the interrupt lands. In the measured cap test, a 512MB cap stopped the run after 0.6 seconds.

### 6.2. When to move to Trino or Spark

These figures cover one 94 MiB file and one aggregate shape. They are not a general capacity claim.
Spill grows with the number of groups, so larger inputs need proportionally more disk.

Move the workload to a shared engine when any of these apply:

- The data no longer fits on one machine's disk with room for spill.
- Several people need the same query at once and per-process memory adds up beyond the host.
- The data lives in Iceberg tables that need snapshots, `MERGE` or catalog permissions.

[Trino](../../services/trino/README.md) serves SQL over the Iceberg lakehouse tables. [Spark](../../services/spark/README.md) handles distributed batch jobs.
The [Iceberg advanced smoke](iceberg-advanced-smoke.md) page shows the lakehouse write path. To port the query, replace the `read_parquet(...)` source with the table name.
