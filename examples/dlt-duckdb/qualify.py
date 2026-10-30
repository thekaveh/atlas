"""Qualify incremental loads, schema evolution and retry recovery with local fixtures."""

import argparse
from contextlib import ExitStack
import errno
import gzip
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
import traceback

import recipe

REPORT_CAP = 64 * 1024
PHASE_SECONDS = 60
TOTAL_SECONDS = 180
FAULT_TABLE = "atlas_recipe_missing_1220"
PHASES = [("initial", 1), ("replay", 1), ("delta", 2), ("column", 3),
          ("variant", 4), ("prepare-poison", 5), ("fault", 5),
          ("repair", 5), ("resume", 5), ("final-replay", 5)]


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    payload = json.dumps(value, default=str, sort_keys=True, separators=(",", ":")).encode()
    if len(payload) > REPORT_CAP:
        raise recipe.RecipeError("report-cap", "JSON artifact exceeds 64 KiB")
    path.write_bytes(payload + b"\n")


def cursor(state):
    return state["sources"]["recipe"]["resources"]["items"]["incremental"]["revision"]["last_value"]


def bounded_bytes(value):
    number, unit = re.fullmatch(r"([0-9.]+)\s*([KMGT]?i?B)", value).groups()
    factors = {"B": 1, "KB": 1000, "MB": 10**6, "GB": 10**9,
               "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
    return float(number) * factors[unit]


def effective_settings(sql, root):
    desired = recipe.engine_settings(root)
    settings = {key: sql.execute_sql("SELECT current_setting(%s)", key)[0][0]
                for key in desired}
    for key in ("threads", "temp_directory", "autoinstall_known_extensions",
                "autoload_known_extensions", "enable_external_access"):
        require(settings[key] == desired[key], "effective setting differs: " + key)
    for key, ceiling in (("max_memory", 256 * 10**6), ("max_temp_directory_size", 128 * 10**6)):
        require(0.99 * ceiling <= bounded_bytes(settings[key]) <= ceiling,
                "effective memory/temp setting differs: " + key)
    return settings


def package_record(info):
    jobs = [job.asdict() for group in info.jobs.values() for job in group]
    for job in jobs:
        job["failed_message"] = (job["failed_message"] or "")[:2048]
    exceptions = sorted(Path(info.package_path).glob(".exceptions/*"))
    return {"load_id": info.load_id, "state": info.state, "jobs": jobs,
            "pending_transitions": info.has_pending_transitions,
            "exceptions": [{"name": p.name, "sha256": digest(p.read_bytes()),
                            "bytes": p.stat().st_size} for p in exceptions]}


def snapshot(pipeline, root, phase):
    from dlt.pipeline.state_sync import load_pipeline_state_from_destination

    with pipeline.sql_client() as sql:
        settings = effective_settings(sql, root)
        columns = sql.execute_sql(
            "SELECT column_name,data_type,is_nullable FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position", "recipe", "items")
        names = [column[0] for column in columns]
        projection = ",".join('"' + name.replace('"', '""') + '"' for name in names)
        rows = sql.execute_sql("SELECT " + projection + " FROM recipe.items ORDER BY id")
        loads = sql.execute_sql("SELECT load_id,status FROM recipe._dlt_loads ORDER BY load_id")
    with pipeline.destination_client() as client:
        committed = load_pipeline_state_from_destination(recipe.PIPELINE, client)
    state, schema = pipeline.state, pipeline.default_schema.to_dict(bump_version=False)
    write_json(root / "artifacts" / (phase + ".local-state.json"), state)
    write_json(root / "artifacts" / (phase + ".committed-state.json"), committed)
    write_json(root / "artifacts" / (phase + ".schema.json"), schema)
    packages = list(pipeline.list_completed_load_packages()) + list(pipeline.list_normalized_load_packages())
    return {"rows": [dict(zip(names, row)) for row in rows], "columns": columns,
            "loads": loads, "settings": settings, "local_cursor": cursor(state),
            "committed_cursor": cursor(committed),
            "pending": list(pipeline.list_normalized_load_packages()),
            "packages": [package_record(pipeline.get_load_package_info(key)) for key in packages],
            "dlt_columns": schema["tables"]["items"]["columns"]}


def item_job(pipeline, load_id, file_id=None):
    info = pipeline.get_load_package_info(load_id)
    candidates = [job for job in info.jobs["new_jobs"]
                  if job.job_file_info.table_name == "items"
                  and job.job_file_info.file_format == "insert_values"]
    if file_id is not None:
        candidates = [job for job in candidates if job.job_file_info.file_id == file_id]
    require(len(candidates) == 1, "expected exactly one owned pending items insert-values job")
    return candidates[0]


def poison(pipeline, root):
    pending = pipeline.list_normalized_load_packages()
    require(len(pending) == 1, "expected one normalized package")
    load_id = pending[0]
    job = item_job(pipeline, load_id)
    path = recipe.owned_path(root, Path(job.file_path).relative_to(root))
    original = path.read_bytes()
    require(len(original) <= REPORT_CAP, "generated job exceeds artifact cap")
    compressed = job.job_file_info.is_compressed
    text = gzip.decompress(original) if compressed else original
    require(len(text) <= REPORT_CAP, "decoded generated job exceeds cap")
    require(text.splitlines()[1] == b"VALUES", "unexpected insert-values header")
    require(text.rstrip().endswith(b";"), "unexpected insert-values terminator")
    damaged = text + ("\nSELECT * FROM " + FAULT_TABLE + ";\n").encode()
    poisoned = gzip.compress(damaged, mtime=0) if compressed else damaged
    (root / "artifacts" / "job.original").write_bytes(original)
    (root / "artifacts" / "job.poisoned").write_bytes(poisoned)
    path.write_bytes(poisoned)
    manifest = {"load_id": load_id, "file_id": job.job_file_info.file_id,
                "job_id": job.job_file_info.job_id(), "original_sha256": digest(original),
                "poison_sha256": digest(poisoned), "compressed": compressed}
    write_json(root / "artifacts" / "job.json", manifest)
    return manifest


def repair(pipeline, root):
    manifest = json.loads((root / "artifacts" / "job.json").read_text())
    load_id = manifest["load_id"]
    job = item_job(pipeline, load_id, manifest["file_id"])
    require(job.job_file_info.job_id() == manifest["job_id"], "retry changed immutable job id")
    require(job.job_file_info.retry_count >= 1, "real loader retry count did not advance")
    require(job.failed_message and FAULT_TABLE in job.failed_message, "missing genuine fault evidence")
    path = recipe.owned_path(root, Path(job.file_path).relative_to(root))
    require(digest(path.read_bytes()) == manifest["poison_sha256"], "pending job is not poisoned payload")
    original = (root / "artifacts" / "job.original").read_bytes()
    require(digest(original) == manifest["original_sha256"], "original payload changed")
    path.write_bytes(original)
    (root / "artifacts" / "job.restored").write_bytes(path.read_bytes())
    return {"load_id": load_id, "file_id": manifest["file_id"],
            "retry_count": job.job_file_info.retry_count, "restored_sha256": digest(path.read_bytes())}


def child_phase(root, phase, revision):
    rows = recipe.read_revision(recipe.owned_path(root, "input/revisions.json"), revision)
    pipeline = recipe.build_pipeline(root)
    if phase in ("fault", "resume"):
        return load_phase(pipeline, root, phase)
    if phase == "repair":
        result = repair(pipeline, root)
    else:
        recipe.prepare(pipeline, rows)
        if phase == "prepare-poison":
            result = poison(pipeline, root)
        else:
            recipe.load(pipeline)
            result = {}
    result["snapshot"] = snapshot(pipeline, root, phase)
    return result


def load_phase(pipeline, root, phase):
    if phase == "resume":
        recipe.load(pipeline)
        return {"snapshot": snapshot(pipeline, root, phase)}
    try:
        recipe.load(pipeline)
    except Exception as error:
        detail = traceback.format_exc()
        require(FAULT_TABLE in detail, "load failed for an unrelated cause")
        require(len(detail.encode()) <= REPORT_CAP, "exception artifact exceeds cap")
        (root / "artifacts" / "fault.traceback.txt").write_text(detail)
        result = {"category": "retry-failure", "exception": type(error).__name__,
                  "detail": str(error)[:2048], "snapshot": snapshot(pipeline, root, phase)}
        require(result["snapshot"]["pending"], "fault did not retain a pending package")
        return result
    raise AssertionError("poisoned real job unexpectedly loaded")


def child(root_value, phase, revision):
    root = recipe.private_root(root_value)
    try:
        result = child_phase(root, phase, int(revision))
        result["status"] = "observed"
        write_json(root / "artifacts" / (phase + ".json"), result)
    except Exception as error:
        result = {"status": "error", "category": recipe.error_category(error, "qualification-failure"),
                  "exception": type(error).__name__, "detail": str(error)[:2048]}
        write_json(root / "artifacts" / (phase + ".json"), result)
        traceback.print_exc()
        raise SystemExit(2)


def disk_bytes(root):
    groups = {"database": 0, "wal": 0, "temp": 0, "state": 0, "logs": 0, "artifacts": 0, "other": 0}
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        group = {"result.duckdb": "database", "result.duckdb.wal": "wal"}.get(str(relative))
        if group is None:
            group = {"temp": "temp", "pipelines": "state", "artifacts": "artifacts"}.get(relative.parts[0], "other")
        if group == "artifacts" and path.suffix == ".log":
            group = "logs"
        groups[group] += path.stat().st_size
    return groups


def read_ready(selector, log, remaining):
    size = 0
    for key, _ in selector.select(timeout=0.02):
        chunk = os.read(key.fd, 4096)
        if chunk:
            log.write(chunk[:max(0, remaining - size)])
            size += len(chunk)
        else:
            selector.unregister(key.fileobj)
    return size


def exited_darwin_group(pid):
    import ctypes

    # Establish exit first: a live singleton can fork after its group snapshot.
    try:
        os.getpgid(pid)
    except ProcessLookupError as error:
        if error.errno != errno.ESRCH:
            return False
    else:
        return False
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    listing = library.proc_listpids
    listing.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
    listing.restype = ctypes.c_int
    members = (ctypes.c_int * 2)()
    ctypes.set_errno(0)
    size = listing(2, pid, members, ctypes.sizeof(members))  # PROC_PGRP_ONLY
    if ctypes.get_errno() or size != ctypes.sizeof(ctypes.c_int) or members[0] != pid:
        return False  # Zero/unknown/truncated results never prove absence.
    return True  # Exited unreaped leader cannot fork or release its reserved PGID.


def stop_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError as error:
        if error.errno != errno.ESRCH:
            raise
    except PermissionError as error:
        try:
            exited = (error.errno == errno.EPERM and sys.platform == "darwin"
                      and exited_darwin_group(pid))
        except (OSError, AttributeError) as query_error:
            raise error from query_error
        if not exited:
            raise


def record_error(measurement, error):
    detail = {"type": type(error).__name__, "detail": str(error)[:2048]}
    if error.__cause__ is not None:
        detail["cause"] = {"type": type(error.__cause__).__name__, "detail": str(error.__cause__)[:2048]}
    if "error" not in measurement:
        measurement["error"] = detail
        measurement["category"] = "supervisor-error"
    else:
        measurement.setdefault("cleanup_errors", []).append(detail)


def close_resource(resource, measurement):
    try:
        resource.close()
    except Exception as error:
        record_error(measurement, error)


def wait_child(selector, log, deadline, measurement):
    # Do not reap here: the unreaped leader reserves its owned process-group id.
    while selector.get_map():
        measurement["output_bytes"] += read_ready(selector, log, REPORT_CAP - measurement["output_bytes"])
        if time.monotonic() >= deadline or measurement["output_bytes"] > REPORT_CAP:
            measurement["category"] = "deadline" if time.monotonic() >= deadline else "report-cap"
            break


def finish_child(process, measurement):
    flags = 0
    try:
        stop_group(process.pid)
    except Exception as error:
        record_error(measurement, error)
        flags = os.WNOHANG
    try:
        pid, status, usage = os.wait4(process.pid, flags)
        if pid:
            process.returncode = os.waitstatus_to_exitcode(status)
            measurement["exit_code"] = process.returncode
            measurement["peak_rss_bytes"] = usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    except Exception as error:
        record_error(measurement, error)


def supervised(command, log_path, deadline):
    measurement = {"exit_code": None, "peak_rss_bytes": None, "output_bytes": 0, "category": None}
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True, env=dict(os.environ))
    with ExitStack() as resources:
        resources.callback(close_resource, process.stdout, measurement)
        resources.callback(close_resource, process.stderr, measurement)
        try:
            selector = selectors.DefaultSelector()
            resources.callback(close_resource, selector, measurement)
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            log = log_path.open("wb")
            resources.callback(close_resource, log, measurement)
            wait_child(selector, log, deadline, measurement)
        except Exception as error:
            record_error(measurement, error)
        finally:
            # Also kill descendants after normal EOF, before the one direct-child reap.
            finish_child(process, measurement)
    return measurement


def run_phase(root, phase, site, total_deadline):
    name, revision = phase
    bootstrap = ("import sys; sys.path[:0]=[sys.argv[1],sys.argv[2]]; "
                 "import qualify; qualify.child(*sys.argv[3:])")
    command = [sys.executable, "-I", "-S", "-B", "-c", bootstrap,
               str(Path(__file__).parent), str(site), str(root), name, str(revision)]
    started = time.monotonic()
    measurement = supervised(command, root / "artifacts" / (name + ".log"),
                             min(total_deadline, started + PHASE_SECONDS))
    measurement.update({"phase": name, "elapsed_seconds": time.monotonic() - started,
                        "disk_bytes": disk_bytes(root)})
    result_path = root / "artifacts" / (name + ".json")
    if result_path.exists() and result_path.stat().st_size <= REPORT_CAP:
        measurement["result"] = json.loads(result_path.read_text())
    write_json(root / "artifacts" / (name + ".measurement.json"),
               {key: value for key, value in measurement.items() if key != "result"})
    return measurement


def logical_rows(snapshot):
    return [{key: value for key, value in row.items() if key not in {"_dlt_id", "_dlt_load_id"}}
            for row in snapshot["rows"]]


def assert_complete(snapshot):
    jobs = [job for package in snapshot["packages"] for job in package["jobs"]]
    require(all(job["state"] == "completed_jobs" for job in jobs), "successful phase retained failed jobs")


def assert_batch(snapshot, expected, revision):
    assert_complete(snapshot)
    actual = logical_rows(snapshot)
    require(len({row["id"] for row in actual}) == len(actual), "duplicate destination id")
    require([row["id"] for row in actual] == sorted(expected), "destination key set differs")
    for row in actual:
        require(row == expected[row["id"]],
                "destination business values differ for id " + str(row["id"]))
    require(snapshot["local_cursor"] == revision, "local cursor differs")
    require(snapshot["committed_cursor"] == revision, "committed cursor differs")
    require(not snapshot["pending"], "successful phase retained pending packages")


def assert_variant(changed):
    variants = [name for name, column in changed["dlt_columns"].items()
                if column.get("variant") and name.startswith("amount")]
    require(len(variants) == 1, "expected one observed amount variant")
    variant = variants[0]
    require(changed["dlt_columns"]["amount"]["data_type"] == "bigint", "original amount type changed")
    require(changed["dlt_columns"][variant]["data_type"] == "text", "variant is not text")
    actual_types = {column[0]: column[1] for column in changed["columns"]}
    require(actual_types["amount"] == "BIGINT" and actual_types[variant] == "VARCHAR",
            "destination original/variant types differ")
    require(changed["rows"][0]["amount"] is None, "variant did not preserve original nullable amount")
    require(changed["rows"][0][variant] == "unknown", "variant lost incompatible source value")
    require(all(row[variant] is None for row in changed["rows"][1:]), "variant changed unrelated ids")
    return variant


def assert_evolution(results):
    before, added = [results[name]["snapshot"] for name in ("delta", "column")]
    require("region" not in {column[0] for column in before["columns"]}, "region existed before addition")
    require([column for column in added["columns"] if column[0] == "region"] == [["region", "VARCHAR", "YES"]],
            "nullable text region was not added")
    require(added["rows"][0]["region"] is None and added["rows"][2]["region"] is None,
            "added region changed absent source keys")


def assert_recovery(results):
    before = results["variant"]["snapshot"]
    failed = results["fault"]["snapshot"]
    for phase in ("prepare-poison", "repair"):
        require(logical_rows(results[phase]["snapshot"]) == logical_rows(before),
                phase + " changed committed business rows")
    require(logical_rows(failed) == logical_rows(before), "failed package changed committed business rows")
    require(failed["local_cursor"] == 5 and failed["committed_cursor"] == 4, "pending/committed cursors not distinct")
    load_id = results["prepare-poison"]["load_id"]
    require(load_id not in {row[0] for row in failed["loads"] if row[1] == 0}, "failed load marked complete")
    require(results["fault"]["category"] == "retry-failure", "missing distinct retry failure")
    restored = results["repair"]
    require(restored["load_id"] == load_id and restored["retry_count"] >= 1, "repair changed load identity")
    recovered = results["resume"]["snapshot"]
    package = next(package for package in recovered["packages"] if package["load_id"] == load_id)
    require(package["exceptions"], "recovery lost exception history")
    require([load_id, 0] in recovered["loads"], "recovered package lacks completed destination marker")
    require(logical_rows(recovered)[0] == logical_rows(before)[0], "recovery changed absent id1")
    require(logical_rows(recovered)[2] == logical_rows(before)[2], "recovery changed absent id3")
    require(package["state"] == "loaded", "recovery did not load original package")
    require(not any(job["state"] != "completed_jobs" for job in package["jobs"]), "recovery left unfinished jobs")
    require(logical_rows(recovered) == logical_rows(results["final-replay"]["snapshot"]), "replay changed logical contents")


def expected_evolution(expected, results):
    for row in expected.values():
        row["region"] = "eu" if row["id"] == 2 else None
    expected[2]["revision"] = 3
    assert_batch(results["column"]["snapshot"], expected, 3)
    changed = results["variant"]["snapshot"]
    variant = assert_variant(changed)
    for row in expected.values():
        row[variant] = "unknown" if row["id"] == 1 else None
    expected[1].update(amount=None, revision=4)
    assert_batch(changed, expected, 4)
    return variant


def assert_matrix(results):
    expected = {1: {"id": 1, "revision": 1, "label": "alpha", "amount": 10},
                2: {"id": 2, "revision": 1, "label": "beta", "amount": 20}}
    for name in ("initial", "replay"):
        assert_batch(results[name]["snapshot"], expected, 1)
    expected.update({1: {"id": 1, "revision": 2, "label": "alpha2", "amount": 11},
                     3: {"id": 3, "revision": 2, "label": "gamma", "amount": 30}})
    assert_batch(results["delta"]["snapshot"], expected, 2)
    initial_id2 = results["initial"]["snapshot"]["rows"][1]["_dlt_load_id"]
    require(results["delta"]["snapshot"]["rows"][1]["_dlt_load_id"] == initial_id2,
            "delta rewrote absent id2")
    variant = expected_evolution(expected, results)
    expected[2].update(amount=21, revision=5)
    expected[4] = {"id": 4, "revision": 5, "label": "delta", "amount": 40,
                   "region": None, variant: None}
    for name in ("resume", "final-replay"):
        assert_batch(results[name]["snapshot"], expected, 5)
    assert_evolution(results)
    assert_recovery(results)


def initialize(root, fixture):
    recipe.private_root(root)
    require(not any(root.iterdir()), "qualification root must be initially empty")
    (root / "input").mkdir(mode=0o700)
    shutil.copyfile(fixture, root / "input/revisions.json")
    recipe.configure_environment(root)
    versions = {distribution.metadata["Name"]: distribution.version
                for distribution in importlib.metadata.distributions()}
    write_json(root / "artifacts" / "dependencies.json", versions)
    require(importlib.metadata.version("dlt") == "1.31.0", "dlt version differs")
    require(importlib.metadata.version("duckdb") == "1.5.5", "DuckDB version differs")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--site", required=True, type=Path)
    args = parser.parse_args()
    root, site = recipe.private_root(args.root), args.site.resolve(strict=True)
    require(site == recipe.check_launcher(), "site path differs from approved executable venv")
    initialize(root, Path(__file__).parent / "fixtures/revisions.json")
    measurements, results = [], {}
    started = time.monotonic()
    try:
        for phase in PHASES:
            measurement = run_phase(root, phase, site, started + TOTAL_SECONDS)
            measurements.append(measurement)
            require(measurement["category"] is None, str(measurement))
            require(measurement["exit_code"] == 0, str(measurement))
            results[phase[0]] = measurement["result"]
        assert_matrix(results)
        require(time.monotonic() <= started + TOTAL_SECONDS, "total qualification deadline exceeded")
        status, detail = "qualified", None
    except Exception as error:
        status, detail = "failed", str(error)[:2048]
    summary = {"status": status, "detail": detail, "elapsed_seconds": time.monotonic() - started,
               "measurements": [{key: value for key, value in item.items() if key != "result"}
                                for item in measurements], "disk_bytes": disk_bytes(root)}
    write_json(root / "qualification.json", summary)
    print(json.dumps(summary, separators=(",", ":")))
    return 0 if status == "qualified" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except recipe.RecipeError as error:
        print(json.dumps({"status": "error", "category": error.category, "detail": str(error)}))
        raise SystemExit(2)
