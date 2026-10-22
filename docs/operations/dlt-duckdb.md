# 7.8. Local JSON ingestion with dlt and DuckDB

This optional example loads small local JSON batches into a DuckDB file. Operators can inspect incremental updates, schema changes and recovery across process restarts.

The source lives in `examples/dlt-duckdb/`. It has its own Python environment and dependency lock. JupyterHub and the Atlas stack are unnecessary.

## 1. Requirements and limits

Use Python 3.12 and `uv` from the repository root. The optional project pins `dlt[duckdb]==1.31.0` and `duckdb==1.5.5`.
Its `uv.lock` fixes the complete dependency set. It does not install the example as a package.

The measured run used macOS 27.0.1 ARM64, CPython 3.12.8, uv 0.11.28 and 39 installed distributions. Section 6 describes its qualification method.

Each input file is limited to 64 KiB. A selected revision must contain one to eight records with unique positive integer IDs.
Required fields are `id`, `revision`, `label` and `amount`. `region` is optional text or null. Each batch uses one positive integer revision.

The example uses synthetic data and requires no credentials. The recipe clears inherited environment variables before importing dlt and disables dlt telemetry and third-party plugin discovery.
DuckDB disables automatic extension installation, automatic extension loading and external access. These controls do not impose an operating-system network boundary.

The measured qualification used an ephemeral native macOS sandbox profile that denied networking for the qualifier and its children.
Parent and child TCP, UDP and DNS controls confirmed kernel denial under that profile. The reader commands below do not create this boundary.

## 2. Create an isolated environment

1. Open a shell at the Atlas repository root.
2. Create a private work directory and install the locked optional dependencies:

    ```bash
    umask 077
    ATLAS_DLT_SOURCE="$(pwd -P)/examples/dlt-duckdb"
    ATLAS_DLT_WORK="$(mktemp -d "${TMPDIR:-/tmp}/atlas-dlt.XXXXXXXX")"
    ATLAS_DLT_WORK="$(cd "$ATLAS_DLT_WORK" && pwd -P)"
    ATLAS_DLT_VENV="$ATLAS_DLT_WORK/venv"
    UV_PROJECT_ENVIRONMENT="$ATLAS_DLT_VENV" \
      UV_CACHE_DIR="$ATLAS_DLT_WORK/uv-cache" \
      uv sync --project "$ATLAS_DLT_SOURCE" --python python3.12 \
      --locked --no-dev --no-build
    ATLAS_DLT_PYTHON="$ATLAS_DLT_VENV/bin/python"
    ATLAS_DLT_SITE="$ATLAS_DLT_VENV/lib/python3.12/site-packages"
    printf 'Work directory: %s\n' "$ATLAS_DLT_WORK"
    ```

    Dependency installation can use the configured package index. Runtime ingestion uses the local fixture and DuckDB file.
    Keep the printed work-directory path. It contains the environment and all example state.

3. Create the runtime directory and copy the fixture:

    ```bash
    ATLAS_DLT_RUN="$ATLAS_DLT_WORK/run"
    mkdir -m 700 "$ATLAS_DLT_RUN"
    mkdir -m 700 "$ATLAS_DLT_RUN/input"
    cp "$ATLAS_DLT_SOURCE/fixtures/revisions.json" \
      "$ATLAS_DLT_RUN/input/revisions.json"
    ```

    The runtime directory must be owned by your user, have mode `0700`, and contain no symlink components.
    Resolving the work directory with `pwd -P` avoids aliases such as macOS `/tmp`.
    Keep the same runtime directory for every subsequent revision and recovery attempt.

4. Define this shell function for the remaining commands:

    ```bash
    atlas_dlt() {
      "$ATLAS_DLT_PYTHON" -I -S -B -c '
    import runpy
    import sys
    from pathlib import Path

    source = sys.argv.pop(1)
    venv = Path(sys.executable).absolute().parent.parent
    site = venv / "lib/python3.12/site-packages"
    sys.path[:0] = [source, str(site)]
    runpy.run_module("recipe", run_name="__main__")
    ' "$ATLAS_DLT_SOURCE" --root "$ATLAS_DLT_RUN" "$@"
    }
    ```

    The launcher inserts only the example source and the owned environment's dependency directory. `-S` skips site initialization, including executable `.pth` files.
    Under Python 3.12 with `-S`, `sys.prefix` identifies the base interpreter. The launcher therefore derives the environment from the lexical executable path.
    Keep `-I -S -B` and the explicit paths when adapting the invocation.

## 3. Load and replay incremental batches

1. Load revision 1 twice, then apply the revision 2 delta:

    ```bash
    atlas_dlt --revision 1
    atlas_dlt --revision 1
    atlas_dlt --revision 2
    ```

    A successful invocation prints `{"status": "loaded", "pending": []}`. The fixed identities are pipeline `atlas_dlt_recipe`, dataset `recipe` and table `items`.
    The primary key is `id`. dlt applies the `merge/delete-insert` strategy and an integer `revision` cursor starting at zero.

    The fixture supplies these batches:

    | Revision | Input | Result to inspect |
    |---|---|---|
    | 1 | IDs 1 and 2, amounts 10 and 20 | Two rows; identical replay keeps one row per ID. |
    | 2 | ID 1 becomes `alpha2`/11; ID 3 is added | Three rows; ID 2 retains `beta`/20 and its original load marker. |
    | 3 | ID 2 adds `region="eu"` | Existing rows acquire the nullable `region` column. |
    | 4 | ID 1 changes `amount` to `"unknown"` | The numeric column remains; a text variant stores the incompatible value. |
    | 5 | ID 2 changes amount to 21; ID 4 is added | Four unique IDs after load and replay. |

2. Inspect the destination after revision 2:

    ```bash
    atlas_dlt_query() {
      "$ATLAS_DLT_PYTHON" -I -S -B -c '
    import sys
    from pathlib import Path

    venv = Path(sys.executable).absolute().parent.parent
    sys.path[:0] = [str(venv / "lib/python3.12/site-packages")]
    import duckdb

    with duckdb.connect(sys.argv[1], read_only=True,
                        config={"enable_external_access": False,
                                "autoinstall_known_extensions": False,
                                "autoload_known_extensions": False}) as connection:
        print(connection.execute(sys.argv[2]).fetchall())
    ' "$ATLAS_DLT_RUN/result.duckdb" "$1"
    }
    atlas_dlt_query 'SELECT id, revision, label, amount FROM recipe.items ORDER BY id'
    ```

    The qualified revision-2 snapshot contained these logical values:

    ```text
    (id, revision, label, amount)
    (1, 2, alpha2, 11)
    (2, 1, beta, 20)
    (3, 2, gamma, 30)
    ```

    ID 2 retained its initial `_dlt_load_id`; IDs 1 and 3 shared the new load marker. Local and committed cursors both reached 2.

    The cursor belongs to this persistent pipeline. Every changed batch requires a higher revision.
    Changing records under the current revision raises `revision-conflict`. Sending an older revision raises `stale-revision` and requires a separately designed backfill.
    The example provides no historical backfill or deletion operation. Rows absent from a delta remain in the destination.

## 4. Inspect schema evolution

Apply the next two revisions:

```bash
atlas_dlt --revision 3
atlas_dlt --revision 4
atlas_dlt_query "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema='recipe' AND table_name='items' ORDER BY ordinal_position"
atlas_dlt_query 'SELECT * FROM recipe.items ORDER BY id'
```

Revision 3 added `region` as nullable `VARCHAR` (`is_nullable=YES`). ID 2 contained `eu`; IDs 1 and 3 contained null.

Revision 4 retained nullable `amount BIGINT` and added nullable `amount__v_text VARCHAR`. The dlt schema recorded `bigint` and a `text` variant.
ID 1 contained null in `amount` and `unknown` in `amount__v_text`. IDs 2 and 3 retained numeric amounts 20 and 30, with null variants.

Schema evolution changes the destination schema even when a load succeeds. Keep both the original `amount` column and its text variant when examining this fixture.
Choose an explicit conversion or curated output schema before using mixed values downstream. The example does not silently convert the original numeric column to text.

Apply revision 5 and replay it without removing state:

```bash
atlas_dlt --revision 5
atlas_dlt --revision 5
```

The qualified final load and replay contained four unique IDs:

| id | revision | label | amount | region | amount__v_text |
|---|---|---|---|---|---|
| 1 | 4 | alpha2 | null | null | unknown |
| 2 | 5 | beta | 21 | eu | null |
| 3 | 2 | gamma | 30 | null | null |
| 4 | 5 | delta | 40 | null | null |

## 5. Qualify failure and recovery

`qualify.py` runs the complete fixture in separate child processes. Use a new, empty private runtime directory for this demonstration.
Keep the earlier `run` directory intact:

```bash
ATLAS_DLT_QUALIFY="$ATLAS_DLT_WORK/qualification"
mkdir -m 700 "$ATLAS_DLT_QUALIFY"
"$ATLAS_DLT_PYTHON" -I -S -B -c '
import runpy
import sys
from pathlib import Path

source = sys.argv.pop(1)
venv = Path(sys.executable).absolute().parent.parent
sys.path[:0] = [source, str(venv / "lib/python3.12/site-packages")]
runpy.run_module("qualify", run_name="__main__")
' "$ATLAS_DLT_SOURCE" --root "$ATLAS_DLT_QUALIFY" \
  --site "$ATLAS_DLT_SITE"
```

The qualifier intentionally damages one generated revision-5 SQL job inside its owned runtime directory. It saves the original bytes before appending SQL that references a missing table.
It exercises the real loader, restores the saved payload in the same retried job, and starts another process to resume loading.
This is deliberate payload repair. It does not demonstrate automatic correction of malformed input.

The qualified fault raised `PipelineStepFailed`, wrapping `LoadClientJobTerminalRetry`.
The loader retained `DatabaseUndefinedRelation` from DuckDB's missing-table `CatalogException`.
The retained package stayed pending with unchanged committed rows. Its local cursor was 5 while the committed cursor remained 4.

The job retained its `file_id` and load ID when its filename retry count advanced from 0 to 1.
Repair restored bytes with the original SHA-256, then a fresh process completed that same package and committed cursor 5.
Final replay retained four unique IDs, no pending packages or failed jobs, and the original exception history.

Inspect `job.json`, `fault.json`, `repair.json`, `resume.json` and `final-replay.json` under `artifacts/` for each run's generated identities and hashes.

For a normal recipe run with a retained pending package, diagnose and repair its cause before resuming.
Use the same runtime directory, database, pipeline identity and fixture. Resume only loading:

```bash
atlas_dlt --resume --revision 5
```

`--revision` remains required and validates that fixture batch. `--resume` does not extract it.
After the load succeeds, start any new extraction in a separate invocation with its new revision.
Do not remove pipeline state, recreate the database or reset the cursor to make a failed load appear successful.

Artifacts are local:

| Path below the runtime directory | Contents |
|---|---|
| `result.duckdb` | Destination tables and committed dlt state |
| `pipelines/atlas_dlt_recipe/` | Local pipeline state, schemas and load packages |
| `project/.dlt/config.toml` | Owned runtime configuration |
| `home/`, `data/`, `xdg/`, `temp/` | Owned runtime configuration, data and temporary files |
| `qualification.json` | Qualifier status, elapsed time and phase resource measurements |
| `artifacts/` | Qualifier rows, schemas, local/committed state, logs and generated-job evidence |

`qualification.json` and the detailed `artifacts/` are created by the qualifier. A normal recipe invocation prints its load status.
Installed dependencies and the acquisition cache remain in the sibling `venv/` and `uv-cache/` directories.

## 6. Resource bounds and measured result

Extraction, normalization and loading each use one worker. DuckDB uses one thread, a 256 MB memory setting and a 128 MB temporary-directory setting.
The qualifier checks the effective settings and caps each log and JSON artifact at 64 KiB.
It enforces 60 seconds per child phase and 180 seconds for the phase matrix, starting after initialization.
The one-shot recipe does not impose those qualifier deadlines.

DuckDB settings limit engine allocations and temporary storage. They are not operating-system memory or disk quotas.
Child peak RSS is measured separately. The maximum individual child RSS does not measure aggregate process memory.

The 2026-10-22 run completed all ten phases with status `qualified`. Its dependencies came from an offline uv installation of 39 exact reviewed wheels.
This qualification method differs from the index-backed `uv sync` setup command above.

| Observation | Measured value |
|---|---|
| Host | macOS 27.0.1 ARM64 |
| Interpreter and installer | CPython 3.12.8; uv 0.11.28 |
| Installed distributions | 39, including dlt 1.31.0 and DuckDB 1.5.5 |
| Ten-phase matrix elapsed time | 15.892700166 seconds |
| Maximum individual child peak RSS | 147,603,456 bytes, during `column` |
| Effective DuckDB threads | 1 |
| Effective DuckDB memory setting | `244.1 MiB` for requested 256 MB |
| Effective DuckDB temporary-storage setting | `122.0 MiB` for requested 128 MB |

The final size sample reported these regular-file byte totals:

| Group | Bytes |
|---|---|
| Database | 2,633,728 |
| WAL | 0 |
| Temporary files | 0 |
| Pipeline state | 103,258 |
| Logs | 8,024 |
| Artifacts | 181,123 |
| Other runtime files | 750 |

The sampler ran before writing `qualification.json`, so these totals exclude that summary file. They measure file lengths, not allocated filesystem blocks.
Environment installation, dependency caches and outer supervision receipts are outside this runtime sample. Their disk cost is not included.

These observations apply only to this tiny synthetic fixture. They establish no workload capacity or general performance claim.

The example covers one local writer and local JSON. Scheduling, live object-store access, remote credentials and larger workloads require separate qualification.
For a future remote source, use dlt's secret configuration or environment references. Keep real tokens out of source files and notebook output.
The existing [JupyterHub guide](../../services/jupyterhub/README.md) covers the stack's notebook integrations.
