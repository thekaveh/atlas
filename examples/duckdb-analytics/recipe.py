"""Analyze Parquet with embedded DuckDB under fixed memory, thread and spill limits.

Run it with the commands in docs/operations/duckdb-analytics.md.
"""

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import resource
import sys
import threading
import time
from urllib.parse import urlsplit

DEFAULT_ROWS = 30_000_000
CUSTOMERS = 4_000_000
REGIONS = ("af", "ap", "eu", "me", "na", "oc", "sa", "un")
CATEGORIES = ("books", "games", "garden", "grocery", "music", "tools")
COLUMNS = ("region", "customers", "orders", "spend_cents", "top_customer_cents")

GENERATE_SQL = """
COPY (
  SELECT i AS order_id,
         (i * 2654435761) % {customers} AS customer_id,
         {regions}[((i * 2654435761) % {customers}) % 8 + 1] AS region,
         {categories}[(i * 31) % 6 + 1] AS category,
         DATE '2026-01-01' + CAST((i * 48271) % 365 AS INTEGER) AS order_day,
         100 + (i * 1103515245 + 12345) % 50000 AS amount_cents
  FROM range({rows}) AS t(i)
) TO {target} (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 122880)
"""

QUERY_SQL = """
WITH per_customer AS (
  SELECT customer_id, region, count(*) AS orders, sum(amount_cents) AS spend_cents
  FROM read_parquet({source})
  GROUP BY customer_id, region
)
SELECT region, count(*) AS customers, sum(orders) AS orders,
       sum(spend_cents) AS spend_cents, max(spend_cents) AS top_customer_cents
FROM per_customer
GROUP BY region
ORDER BY region
"""


class RecipeError(ValueError):
    pass


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def list_literal(values):
    return "[" + ", ".join(literal(value) for value in values) + "]"


def generate_sql(target, rows):
    if type(rows) is not int or not 0 < rows <= 10**9:
        raise RecipeError("rows must be a positive integer up to 1,000,000,000 (BIGINT-safe)")
    return GENERATE_SQL.format(
        customers=CUSTOMERS, regions=list_literal(REGIONS),
        categories=list_literal(CATEGORIES), rows=rows, target=literal(target),
    )


def query_sql(source):
    return QUERY_SQL.format(source=literal(source))


def is_object_path(location):
    return location.startswith("s3://")


def check_location(location, must_exist):
    if is_object_path(location):
        parts = urlsplit(location)
        if not parts.netloc or parts.path in ("", "/") or parts.query or parts.fragment:
            raise RecipeError("object paths must look like s3://bucket/key.parquet")
        return location
    path = Path(location).absolute()
    if must_exist and (path.is_symlink() or not path.is_file()):
        raise RecipeError("local input must be a regular Parquet file")
    if not must_exist and (path.exists() or path.is_symlink()):
        raise RecipeError("local output already exists; choose a new path")
    return str(path)


def engine_config(memory_limit, threads, temp_directory, max_temp):
    return {
        "memory_limit": memory_limit,
        "threads": threads,
        "temp_directory": str(temp_directory),
        "max_temp_directory_size": max_temp,
        "preserve_insertion_order": False,
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
    }


def s3_endpoint(environ):
    url = environ.get("AWS_ENDPOINT_URL_S3") or environ.get("AWS_ENDPOINT_URL")
    if not url:
        raise RecipeError("set AWS_ENDPOINT_URL_S3, for example http://minio:9000")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise RecipeError("AWS_ENDPOINT_URL_S3 must be an http(s) URL")
    if parts.path not in ("", "/") or parts.query or parts.username:
        raise RecipeError("AWS_ENDPOINT_URL_S3 must not carry a path, query or user")
    return parts.netloc, parts.scheme == "https"


def s3_secret_sql(environ):
    endpoint, use_ssl = s3_endpoint(environ)
    key_id = environ.get("AWS_ACCESS_KEY_ID")
    secret = environ.get("AWS_SECRET_ACCESS_KEY")
    if not key_id or not secret:
        raise RecipeError("set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY")
    region = environ.get("AWS_REGION") or environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    return (
        "CREATE TEMPORARY SECRET recipe_s3 (TYPE s3, PROVIDER config, "
        f"KEY_ID {literal(key_id)}, SECRET {literal(secret)}, REGION {literal(region)}, "
        f"ENDPOINT {literal(endpoint)}, URL_STYLE 'path', USE_SSL {str(use_ssl).lower()})"
    )


UNITS = {"B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12,
         "KIB": 2**10, "MIB": 2**20, "GIB": 2**30, "TIB": 2**40}


def parse_size(text):
    number = text.strip().rstrip("bBiIkKmMgGtT ")
    unit = text.strip()[len(number):].strip().upper() or "B"
    if not number.isdigit() or unit not in UNITS or int(number) <= 0:
        raise RecipeError(f"size must look like 512MB or 4GiB, not {text!r}")
    return int(number) * UNITS[unit]


def directory_bytes(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_blocks * 512
            except OSError:
                pass
    return total


class SpillSampler:
    """Track peak temp-directory bytes; call on_limit once if they pass limit."""

    def __init__(self, path, limit, on_limit, interval=0.02, measure=directory_bytes):
        self.path, self.limit, self.on_limit = path, limit, on_limit
        self.interval, self.measure = interval, measure
        self.peak, self.exceeded = 0, False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def sample(self):
        self.peak = max(self.peak, self.measure(self.path))
        if self.peak > self.limit and not self.exceeded:
            self.exceeded = True
            self.on_limit()

    def _run(self):
        while True:
            self.sample()
            if self._stop.wait(self.interval):
                return

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join()


def peak_rss_bytes():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def table_csv(rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return buffer.getvalue()


def lockdown_sql(locations):
    """Allow only the named input/output, then freeze the engine settings."""
    return [
        f"SET allowed_paths = {list_literal(locations)}",
        "SET enable_external_access = false",
        "SET lock_configuration = true",
    ]


def connect(args, locations):
    import duckdb

    temp = Path(args.temp_dir).absolute()
    temp.mkdir(mode=0o700, parents=True, exist_ok=True)
    config = engine_config(args.memory_limit, args.threads, temp, args.max_temp)
    connection = duckdb.connect(":memory:", config=config)
    if any(is_object_path(location) for location in locations):
        try:
            connection.execute("LOAD httpfs")
        except duckdb.Error as error:
            raise RecipeError("httpfs is not installed; run INSTALL httpfs once "
                              "(see docs/operations/duckdb-analytics.md)") from error
        statement = s3_secret_sql(os.environ)
        try:
            connection.execute(statement)
        except duckdb.Error:
            raise RecipeError("DuckDB rejected the S3 secret settings") from None
    for statement in lockdown_sql(locations):
        connection.execute(statement)
    return connection, temp


def run_generate(args):
    target = check_location(args.output, must_exist=False)
    sql = generate_sql(target, args.rows)
    started = time.perf_counter()
    connection, _temp = connect(args, [target])
    with connection:
        connection.execute(sql)
    return {"status": "generated", "output": target, "rows": args.rows,
            "elapsed_s": round(time.perf_counter() - started, 2)}


def run_query(args):
    source = check_location(args.input, must_exist=True)
    limit = parse_size(args.max_temp)
    started = time.perf_counter()
    import duckdb

    connection, temp = connect(args, [source])
    startup = time.perf_counter() - started
    with connection, SpillSampler(temp, limit, connection.interrupt) as spill:
        try:
            rows = connection.execute(query_sql(source)).fetchall()
        except duckdb.InterruptException:
            if not spill.exceeded:
                raise
            raise RecipeError(f"spill passed --max-temp {args.max_temp}; "
                              "raise it or --memory-limit") from None
    elapsed = time.perf_counter() - started - startup
    table = table_csv(rows)
    digest = hashlib.sha256(table.encode()).hexdigest()
    if args.output:
        Path(args.output).write_text(table, encoding="utf-8")
    result = {
        "status": "ok", "rows": len(rows), "sha256": digest,
        "startup_s": round(startup, 3), "query_s": round(elapsed, 2),
        "peak_rss_mb": round(peak_rss_bytes() / 2**20), "peak_spill_mb": round(spill.peak / 2**20),
    }
    if args.expect_sha256 and digest != args.expect_sha256:
        result["status"] = "mismatch"
    return result


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--memory-limit", default="512MB")
    root.add_argument("--threads", type=int, default=2)
    root.add_argument("--temp-dir", default="duckdb-spill")
    root.add_argument("--max-temp", default="4GB")
    commands = root.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="write the deterministic sample")
    generate.add_argument("--output", required=True)
    generate.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    query = commands.add_parser("query", help="run the checked analysis query")
    query.add_argument("--input", required=True)
    query.add_argument("--output")
    query.add_argument("--expect-sha256")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    result = run_generate(args) if args.command == "generate" else run_query(args)
    print(json.dumps(result))
    return 0 if result["status"] in ("ok", "generated") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "error", "type": type(error).__name__,
                          "detail": str(error)[:2048]}))
        raise SystemExit(2)
