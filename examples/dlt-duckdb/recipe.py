"""Load bounded local JSON revisions into DuckDB."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

PIPELINE = "atlas_dlt_recipe"
DATASET = "recipe"
INPUT_LIMIT = 64 * 1024
RECORD_LIMIT = 8


class RecipeError(ValueError):
    def __init__(self, category, detail):
        super().__init__(detail)
        self.category = category


def error_category(error, fallback):
    for _ in range(8):
        if isinstance(error, RecipeError):
            return error.category
        error = error.__cause__ or error.__context__
        if error is None:
            break
    return fallback


def private_root(value):
    root = Path(value).absolute()
    if root.is_symlink() or root.resolve() != root:
        raise RecipeError("private-path", "root must have no symlink components")
    try:
        info = root.stat()
    except OSError as error:
        raise RecipeError("private-path", "private root is unavailable") from error
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise RecipeError("private-path", "root must be owned and mode 0700")
    if not stat.S_ISDIR(info.st_mode):
        raise RecipeError("private-path", "root must be a directory")
    return root


def owned_path(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise RecipeError("private-path", "path leaves private root")
    current = path
    while current != root:
        if current.is_symlink():
            raise RecipeError("private-path", "owned paths cannot contain symlinks")
        current = current.parent
    return path


def validate_fields(row):
    required = {"id", "revision", "label", "amount"}
    if not isinstance(row, dict) or not required <= row.keys():
        raise RecipeError("malformed-input", "record lacks required fields")
    if row.keys() - required - {"region"}:
        raise RecipeError("malformed-input", "record has unsupported fields")


def validate_record(row, revision):
    validate_fields(row)
    integers = (row["id"], row["revision"])
    if any(type(value) is not int or value <= 0 for value in integers):
        raise RecipeError("malformed-input", "id and revision must be positive integers")
    if row["revision"] != revision or not isinstance(row["label"], str):
        raise RecipeError("malformed-input", "revision or label is invalid")
    if type(row["amount"]) not in (int, str):
        raise RecipeError("malformed-input", "amount must be integer or text")
    if row.get("region") is not None and not isinstance(row["region"], str):
        raise RecipeError("malformed-input", "region must be null or text")


def read_document(path):
    if path.is_symlink() or not path.is_file():
        raise RecipeError("private-path", "input must be a regular file")
    with path.open("rb") as handle:
        payload = handle.read(INPUT_LIMIT + 1)
    if len(payload) > INPUT_LIMIT:
        raise RecipeError("input-oversize", "input exceeds 64 KiB")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RecipeError("malformed-input", str(error)) from error
    if not isinstance(document, dict):
        raise RecipeError("malformed-input", "input must map revision numbers to batches")
    return document


def read_revision(path, revision):
    document = read_document(path)
    if str(revision) not in document:
        raise RecipeError("unknown-revision", "requested revision is absent")
    rows = document[str(revision)]
    if not isinstance(rows, list) or not 1 <= len(rows) <= RECORD_LIMIT:
        raise RecipeError("malformed-input", "batch must contain one to eight records")
    for row in rows:
        validate_record(row, revision)
    if len({row["id"] for row in rows}) != len(rows):
        raise RecipeError("malformed-input", "batch ids must be unique")
    return rows


def engine_settings(root):
    return {
        "threads": 1,
        "max_memory": "256MB",
        "temp_directory": str(owned_path(root, "temp")),
        "max_temp_directory_size": "128MB",
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "enable_external_access": False,
    }


def runtime_require(condition, detail):
    if not condition:
        raise RecipeError("runtime-isolation", detail)


def check_launcher():
    runtime_require(sys.version_info[:2] == (3, 12), "use the reviewed Python 3.12 interpreter")
    runtime_require(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode,
            "launch with -I -S -B before importing optional packages")
    venv = Path(sys.executable).absolute().parent.parent
    config = venv / "pyvenv.cfg"
    runtime_require(config.is_file() and not config.is_symlink(), "executable is not in the owned venv")
    content = config.read_text().lower().replace(" ", "")
    runtime_require("include-system-site-packages=false" in content, "venv exposes system site packages")
    expected = venv / "lib/python3.12/site-packages"
    visible = [Path(entry) for entry in sys.path if "site-packages" in entry]
    runtime_require(visible == [expected], "only the exact owned site path may be inserted")

    return expected


def configure_environment(root):
    for relative in ("home", "temp", "project/.dlt", "data", "pipelines", "artifacts",
                     "xdg/config", "xdg/data", "xdg/cache"):
        owned_path(root, relative).mkdir(mode=0o700, parents=True, exist_ok=True)
    config = owned_path(root, "project/.dlt/config.toml")
    config.write_text(
        "[runtime]\ndlthub_telemetry=false\nlog_level=\"ERROR\"\n"
        "[load]\nraise_on_max_retries=1\nauto_abort_on_terminal_error=false\n",
        encoding="utf-8",
    )
    environment = {
        "HOME": str(root / "home"), "TMPDIR": str(root / "temp"),
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "DLT_DISABLE_PLUGINS": "true",
        "DLT_PROJECT_DIR": str(root / "project"), "DLT_LOCAL_DIR": str(root / "project"),
        "DLT_DATA_DIR": str(root / "data"), "DLT_CONFIG_FOLDER": ".dlt",
        "XDG_CONFIG_HOME": str(root / "xdg/config"), "XDG_DATA_HOME": str(root / "xdg/data"),
        "XDG_CACHE_HOME": str(root / "xdg/cache"),
    }
    os.environ.clear()
    os.environ.update(environment)


def build_pipeline(root):
    check_launcher()
    configure_environment(root)
    import dlt
    from dlt.destinations import duckdb
    from dlt.destinations.impl.duckdb.configuration import DuckDbCredentials

    credentials = DuckDbCredentials(
        str(owned_path(root, "result.duckdb")),
        extensions=[], global_config=engine_settings(root), session_timezone=None,
    )
    return dlt.pipeline(
        pipeline_name=PIPELINE, pipelines_dir=str(owned_path(root, "pipelines")),
        destination=duckdb(credentials=credentials), dataset_name=DATASET,
    )


def check_source_revision(rows):
    import dlt

    revision = rows[0]["revision"]
    fingerprint = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    checkpoint = dlt.current.resource_state().setdefault("input_checkpoint", {})
    previous = checkpoint.get("revision", 0)
    if revision < previous:
        raise RecipeError("stale-revision", "older revisions require an explicit backfill")
    if revision == previous and fingerprint != checkpoint.get("sha256"):
        raise RecipeError("revision-conflict", "changed records require a higher revision")
    checkpoint.update({"revision": revision, "sha256": fingerprint})


def make_source(rows):
    import dlt

    @dlt.resource(
        name="items", primary_key="id",
        write_disposition={"disposition": "merge", "strategy": "delete-insert"},
        incremental=dlt.sources.incremental("revision", initial_value=0),
        schema_contract={"tables": "evolve", "columns": "evolve", "data_type": "evolve"},
    )
    def items():
        check_source_revision(rows)
        yield from rows

    @dlt.source(name=DATASET)
    def local_json():
        return items

    return local_json()


def prepare(pipeline, rows):
    if pipeline.list_normalized_load_packages():
        raise RecipeError("pending-load", "resume pending packages before extracting new data")
    pipeline.extract(make_source(rows), workers=1, max_parallel_items=1,
                     loader_file_format="insert_values")
    pipeline.normalize(workers=1)


def load(pipeline):
    return pipeline.load(workers=1, raise_on_failed_jobs=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=private_root)
    parser.add_argument("--revision", required=True, type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    rows = read_revision(owned_path(args.root, "input/revisions.json"), args.revision)
    pipeline = build_pipeline(args.root)
    if not args.resume:
        prepare(pipeline, rows)
    load(pipeline)
    print(json.dumps({"status": "loaded", "pending": pipeline.list_normalized_load_packages()}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "error", "category": error_category(error, "runtime-failure"), "detail": str(error)[:2048]}))
        raise SystemExit(2)
