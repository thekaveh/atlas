"""Contracts for the optional embedded DuckDB analytics recipe (#1210).

The default suite doubles DuckDB. The last test runs the real engine only
where ``duckdb`` is importable, such as the recipe's own environment.
"""

from __future__ import annotations

import builtins
from collections import defaultdict
import hashlib
import importlib.util
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "examples/duckdb-analytics"
MINIO = {"AWS_ENDPOINT_URL_S3": "http://minio:9000", "AWS_ACCESS_KEY_ID": "reader",
         "AWS_SECRET_ACCESS_KEY": "pa'ss"}


def _load(name="atlas_duckdb_analytics_recipe"):
    spec = importlib.util.spec_from_file_location(name, SOURCE / "recipe.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recipe(monkeypatch):
    original_import = builtins.__import__

    def no_engine(name, *args, **kwargs):
        if name.partition(".")[0] == "duckdb":
            raise AssertionError("duckdb imported while loading the recipe")
        return original_import(name, *args, **kwargs)

    with monkeypatch.context() as imports:
        imports.setattr(builtins, "__import__", no_engine)
        return _load()


def _category(recipe, text, action):
    with pytest.raises(recipe.RecipeError) as caught:
        action()
    assert text in str(caught.value)
    return caught.value


def test_project_pins_the_jupyterhub_duckdb_and_nothing_heavier():
    project = (SOURCE / "pyproject.toml").read_text()
    hub = (ROOT / "services/jupyterhub/build/requirements.txt").read_text()
    assert 'dependencies = ["duckdb==1.5.5"]' in project
    assert "\nduckdb==1.5.5\n" in hub
    lock = (SOURCE / "uv.lock").read_text()
    assert lock.count("[[package]]") == 2
    for engine in ("pyspark", "trino", "apache-airflow"):
        assert engine not in project + lock


def test_sql_is_deterministic_integer_only_and_ordered(recipe):
    query = recipe.query_sql("/data/sample.parquet")
    assert "read_parquet('/data/sample.parquet')" in query
    assert query.rstrip().endswith("ORDER BY region")
    for drifting in ("random(", "now(", "avg(", "DOUBLE", "FLOAT", "LIMIT"):
        assert drifting not in query
    generated = recipe.generate_sql("s3://bucket/sample.parquet", 10)
    assert "FROM range(10)" in generated
    assert "TO 's3://bucket/sample.parquet'" in generated
    assert "random(" not in generated
    for rows in (0, -1, True, 1.5, 10**9 + 1):
        _category(recipe, "positive integer", lambda: recipe.generate_sql("x", rows))


def test_literals_escape_quotes(recipe):
    assert recipe.literal("o'neil") == "'o''neil'"
    assert recipe.list_literal(["a", "b'c"]) == "['a', 'b''c']"
    assert recipe.query_sql("/x'y.parquet").count("'/x''y.parquet'") == 1


@pytest.mark.parametrize("location", ["s3://", "s3://bucket", "s3://bucket/", "s3://b/k?x=1"])
def test_object_location_needs_bucket_and_key(recipe, location):
    _category(recipe, "s3://bucket/key", lambda: recipe.check_location(location, True))


def test_local_input_must_be_regular_file_and_output_new(recipe, tmp_path):
    data = tmp_path / "data.parquet"
    data.write_bytes(b"PAR1")
    assert recipe.check_location(str(data), True) == str(data.absolute())
    assert recipe.check_location("s3://b/k.parquet", True) == "s3://b/k.parquet"
    link = tmp_path / "link.parquet"
    link.symlink_to(data)
    for bad in (tmp_path, link, tmp_path / "missing.parquet"):
        _category(recipe, "regular Parquet file", lambda: recipe.check_location(str(bad), True))
    _category(recipe, "already exists", lambda: recipe.check_location(str(data), False))
    assert recipe.check_location(str(tmp_path / "new.parquet"), False).endswith("new.parquet")


def test_engine_config_bounds_memory_threads_and_spill(recipe, tmp_path):
    config = recipe.engine_config("256MB", 2, tmp_path, "4GB")
    assert config == {
        "memory_limit": "256MB", "threads": 2, "temp_directory": str(tmp_path),
        "max_temp_directory_size": "4GB", "preserve_insertion_order": False,
        "autoinstall_known_extensions": False, "autoload_known_extensions": False,
    }


def test_lockdown_allows_only_named_paths_then_freezes(recipe):
    assert recipe.lockdown_sql(["/in.parquet", "s3://b/k"]) == [
        "SET allowed_paths = ['/in.parquet', 's3://b/k']",
        "SET enable_external_access = false",
        "SET lock_configuration = true",
    ]


def test_s3_secret_uses_minio_environment(recipe):
    sql = recipe.s3_secret_sql(MINIO)
    assert "ENDPOINT 'minio:9000'" in sql and "USE_SSL false" in sql
    assert "URL_STYLE 'path'" in sql and "REGION 'us-east-1'" in sql
    assert "SECRET 'pa''ss'" in sql and "KEY_ID 'reader'" in sql
    secure = dict(MINIO, AWS_ENDPOINT_URL_S3="https://s3.example:443", AWS_REGION="eu-west-1")
    assert "USE_SSL true" in recipe.s3_secret_sql(secure)
    assert "REGION 'eu-west-1'" in recipe.s3_secret_sql(secure)
    hub = dict(MINIO, AWS_DEFAULT_REGION="ap-south-1")
    assert "REGION 'ap-south-1'" in recipe.s3_secret_sql(hub)


@pytest.mark.parametrize("changes, text", [
    ({"AWS_ENDPOINT_URL_S3": ""}, "set AWS_ENDPOINT_URL_S3"),
    ({"AWS_ENDPOINT_URL_S3": "minio:9000"}, "http(s) URL"),
    ({"AWS_ENDPOINT_URL_S3": "http://minio:9000/bucket"}, "must not carry"),
    ({"AWS_ENDPOINT_URL_S3": "http://u:p@minio:9000"}, "must not carry"),
    ({"AWS_SECRET_ACCESS_KEY": ""}, "AWS_SECRET_ACCESS_KEY"),
])
def test_s3_secret_rejects_incomplete_settings(recipe, changes, text):
    _category(recipe, text, lambda: recipe.s3_secret_sql(dict(MINIO, **changes)))


@pytest.mark.parametrize("text, expected", [
    ("512MB", 512 * 10**6), ("4GB", 4 * 10**9), ("4 GiB", 4 * 2**30), ("1024", 1024),
])
def test_parse_size_accepts_duckdb_units(recipe, text, expected):
    assert recipe.parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "0MB", "1.5GB", "4XB", "-1GB"])
def test_parse_size_rejects_other_text(recipe, text):
    _category(recipe, "size must look like", lambda: recipe.parse_size(text))


def test_spill_sampler_tracks_peak_and_fires_limit_once(recipe, tmp_path):
    readings = iter([0, 40, 120, 90, 150])
    calls = []
    sampler = recipe.SpillSampler(tmp_path, 100, lambda: calls.append(1),
                                  measure=lambda _path: next(readings, 0))
    for _ in range(5):
        sampler.sample()
    assert (sampler.peak, sampler.exceeded, calls) == (150, True, [1])
    (tmp_path / "block.tmp").write_bytes(b"x" * 8192)
    assert recipe.directory_bytes(tmp_path) >= 8192


class FakeEngine(ModuleType):
    """A minimal duckdb stand-in that records statements."""

    def __init__(self, rows, fail_on=None, peak=0):
        super().__init__("duckdb")
        self.Error = type("Error", (Exception,), {})
        self.InterruptException = type("InterruptException", (self.Error,), {})
        self.rows, self.fail_on, self.peak = rows, fail_on, peak
        self.statements, self.configs = [], []

    def connect(self, database, config):
        self.configs.append((database, config))
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, engine):
        self.engine, self.interrupted = engine, False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def interrupt(self):
        self.interrupted = True

    def execute(self, statement):
        engine = self.engine
        engine.statements.append(statement)
        if engine.fail_on and engine.fail_on in statement:
            raise engine.Error(f"rejected {statement}")
        if "read_parquet" in statement and engine.peak:
            (Path(engine.configs[-1][1]["temp_directory"]) / "spill.tmp").write_bytes(
                b"x" * engine.peak)
            deadline = time.monotonic() + 5
            while not self.interrupted and time.monotonic() < deadline:
                time.sleep(0.01)
            raise engine.InterruptException("interrupted")
        return SimpleNamespace(fetchall=lambda: engine.rows)


ROWS = [("af", 2, 3, 300, 200), ("eu", 1, 1, 50, 50)]


def _args(recipe, tmp_path, *extra):
    return recipe.parser().parse_args(["--temp-dir", str(tmp_path / "spill"), *extra])


def test_query_locks_engine_before_reading_and_checks_digest(recipe, tmp_path, monkeypatch):
    engine = FakeEngine(ROWS)
    monkeypatch.setitem(sys.modules, "duckdb", engine)
    data = tmp_path / "sample.parquet"
    data.write_bytes(b"PAR1")
    table = recipe.table_csv(ROWS)
    assert table == ("region,customers,orders,spend_cents,top_customer_cents\n"
                     "af,2,3,300,200\neu,1,1,50,50\n")
    digest = hashlib.sha256(table.encode()).hexdigest()
    out = tmp_path / "result.csv"
    args = _args(recipe, tmp_path, "query", "--input", str(data), "--output", str(out),
                 "--expect-sha256", digest)
    result = recipe.run_query(args)
    assert result["status"] == "ok" and result["sha256"] == digest and result["rows"] == 2
    assert out.read_text() == table
    assert engine.configs[0][0] == ":memory:"
    assert engine.configs[0][1]["memory_limit"] == "512MB"
    assert engine.statements[:3] == recipe.lockdown_sql([str(data)])
    assert "read_parquet" in engine.statements[3]
    assert not any("httpfs" in statement for statement in engine.statements)
    args.expect_sha256 = "0" * 64
    assert recipe.run_query(args)["status"] == "mismatch"


def test_object_query_loads_httpfs_and_redacts_secret_errors(recipe, tmp_path, monkeypatch):
    for name in ("AWS_ENDPOINT_URL", "AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(name, raising=False)
    for name, value in MINIO.items():
        monkeypatch.setenv(name, value)
    engine = FakeEngine(ROWS)
    monkeypatch.setitem(sys.modules, "duckdb", engine)
    args = _args(recipe, tmp_path, "query", "--input", "s3://lakehouse/sample.parquet")
    assert recipe.run_query(args)["status"] == "ok"
    assert engine.statements[0] == "LOAD httpfs"
    assert engine.statements[1].startswith("CREATE TEMPORARY SECRET")
    assert engine.statements[2] == "SET allowed_paths = ['s3://lakehouse/sample.parquet']"
    failing = FakeEngine(ROWS, fail_on="CREATE TEMPORARY SECRET")
    monkeypatch.setitem(sys.modules, "duckdb", failing)
    error = _category(recipe, "rejected the S3 secret", lambda: recipe.run_query(args))
    assert "pa''ss" not in str(error) and error.__cause__ is None
    missing = FakeEngine(ROWS, fail_on="LOAD httpfs")
    monkeypatch.setitem(sys.modules, "duckdb", missing)
    _category(recipe, "INSTALL httpfs", lambda: recipe.run_query(args))


def test_spill_past_max_temp_stops_the_query(recipe, tmp_path, monkeypatch):
    engine = FakeEngine(ROWS, peak=4096)
    monkeypatch.setitem(sys.modules, "duckdb", engine)
    data = tmp_path / "sample.parquet"
    data.write_bytes(b"PAR1")
    args = _args(recipe, tmp_path, "--max-temp", "1KB", "query", "--input", str(data))
    _category(recipe, "spill passed --max-temp 1KB", lambda: recipe.run_query(args))


def _reference(rows, customers):
    """Pure-Python twin of GENERATE_SQL + QUERY_SQL for a tiny sample."""
    regions = ("af", "ap", "eu", "me", "na", "oc", "sa", "un")
    spend, orders = defaultdict(int), defaultdict(int)
    for i in range(rows):
        customer = (i * 2654435761) % customers
        spend[customer] += 100 + (i * 1103515245 + 12345) % 50000
        orders[customer] += 1
    table = defaultdict(lambda: [0, 0, 0, 0])
    for customer, total in spend.items():
        entry = table[regions[customer % 8]]
        entry[0] += 1
        entry[1] += orders[customer]
        entry[2] += total
        entry[3] = max(entry[3], total)
    return [(region, *table[region]) for region in sorted(table)]


def test_real_engine_matches_pure_python_reference(tmp_path, monkeypatch):
    duckdb = pytest.importorskip("duckdb")
    assert duckdb.__version__ == "1.5.5"
    recipe = _load("atlas_duckdb_analytics_live")
    monkeypatch.setattr(recipe, "CUSTOMERS", 50_000)
    sample = tmp_path / "sample.parquet"
    base = ["--temp-dir", str(tmp_path / "spill"), "--threads", "1"]
    assert recipe.main([*base, "generate", "--output", str(sample), "--rows", "200000"]) == 0
    expected = hashlib.sha256(recipe.table_csv(_reference(200_000, 50_000)).encode()).hexdigest()
    for memory in ("1GB", "32MB"):
        args = recipe.parser().parse_args(
            [*base, "--memory-limit", memory, "query", "--input", str(sample),
             "--expect-sha256", expected])
        assert recipe.run_query(args)["status"] == "ok"
