from __future__ import annotations

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
JUPYTERHUB_DIR = ROOT / "services" / "jupyterhub"


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _requirement_names(path: Path) -> set[str]:
    names: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("--"):
            continue
        names.add(line.split("==", 1)[0].split(">=", 1)[0].split("<", 1)[0].lower())
    return names


def test_jupyterhub_image_includes_lakehouse_python_libraries() -> None:
    names = _requirement_names(JUPYTERHUB_DIR / "build" / "requirements.txt")

    assert {"boto3", "s3fs", "pyiceberg[s3fs]", "pyarrow", "duckdb"} <= names


def test_jupyterhub_compose_exposes_lakehouse_runtime_environment() -> None:
    compose = _yaml(JUPYTERHUB_DIR / "compose.yml")
    env = compose["services"]["jupyterhub"]["environment"]

    assert env["SPARK_REMOTE"] == "${SPARK_REMOTE:-sc://spark-connect:15002}"
    assert env["MINIO_ENDPOINT"] == "http://minio:9000"
    assert env["AWS_ENDPOINT_URL_S3"] == "http://minio:9000"
    assert env["AWS_ACCESS_KEY_ID"] == "${MINIO_JUPYTER_ACCESS_KEY}"
    assert env["AWS_SECRET_ACCESS_KEY"] == "${MINIO_JUPYTER_SECRET_KEY}"
    assert env["AWS_DEFAULT_REGION"] == "${MINIO_REGION:-us-east-1}"
    assert env["ICEBERG_REST_URI"] == "http://iceberg-rest:8181"
    assert env["ICEBERG_WAREHOUSE"] == "s3://${MINIO_BUCKET_ICEBERG_LAKEHOUSE:-lakehouse}/"

    assert env["PYICEBERG_CATALOG__REST__TYPE"] == "rest"
    assert env["PYICEBERG_CATALOG__REST__URI"] == "http://iceberg-rest:8181"
    assert (
        env["PYICEBERG_CATALOG__REST__WAREHOUSE"]
        == "s3://${MINIO_BUCKET_ICEBERG_LAKEHOUSE:-lakehouse}/"
    )
    assert env["PYICEBERG_CATALOG__REST__S3__ENDPOINT"] == "http://minio:9000"
    assert env["PYICEBERG_CATALOG__REST__S3__ACCESS_KEY_ID"] == "${MINIO_JUPYTER_ACCESS_KEY}"
    assert env["PYICEBERG_CATALOG__REST__S3__SECRET_ACCESS_KEY"] == "${MINIO_JUPYTER_SECRET_KEY}"
    assert env["PYICEBERG_CATALOG__REST__S3__REGION"] == "${MINIO_REGION:-us-east-1}"
    assert env["PYICEBERG_CATALOG__REST__S3__FORCE_VIRTUAL_ADDRESSING"] == "false"


def test_jupyterhub_manifest_declares_lakehouse_topology() -> None:
    manifest = _yaml(JUPYTERHUB_DIR / "service.yml")
    tracks = _yaml(ROOT / "bootstrapper" / "tracks.yml")["tracks"]
    track_services = {track["key"]: track["services"] for track in tracks}

    assert manifest["category"] == "apps"
    assert "jupyterhub" in track_services["data-eng"]
    assert "jupyterhub" in track_services["ml-eng"]
    assert set(manifest["sources"]["options"][0].keys()) >= {"id", "label"}
    assert {source["id"] for source in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert set(manifest["depends_on"]["required"]) == {"supabase", "redis", "litellm"}
    assert {"minio", "iceberg-rest", "spark"} <= set(manifest["depends_on"]["optional"])
    assert {"minio", "iceberg-rest", "spark"} <= set(
        manifest["runtime_deps"]["jupyterhub"]["optional"]
    )
    # A calls entry is a name or a qualified object (#1273).
    targets = {
        entry["target"] if isinstance(entry, dict) else entry
        for entry in manifest["data_flow"]["calls"]
    }
    assert {"minio", "iceberg-rest", "spark"} <= targets
    assert "lakehouse" in manifest["rows"][0]["description"].lower()


def test_jupyterhub_docs_cover_lakehouse_clients_and_validation() -> None:
    readme = (JUPYTERHUB_DIR / "README.md").read_text(encoding="utf-8")
    combined = readme

    for expected in [
        "boto3",
        "s3fs",
        "pyiceberg",
        "pyarrow",
        "duckdb",
        "ICEBERG_REST_URI",
        "pyiceberg.catalog",
        "load_catalog",
        "list_namespaces",
        "AWS_ENDPOINT_URL_S3",
        "docker exec ${PROJECT_NAME}-jupyterhub",
    ]:
        assert expected in combined

    future_pairs = readme.split("### 15.4. Future", 1)[1].split("### 15.5.", 1)[0]
    assert "jupyterhub ↔ minio" not in future_pairs


def test_jupyterhub_deployment_docs_describe_current_data_track_lakehouse_path() -> None:
    # The dedicated SOURCE matrix row that used to spell out "PyIceberg lakehouse
    # notebooks" in prose was removed as duplication (docs IA restructure, Task 3);
    # the fact still lives in this file's SPARK_SOURCE section (§4.11), which names
    # JupyterHub explicitly and references the `pyiceberg` package (its actual,
    # lowercase PyPI/import name — used consistently across the manifest,
    # requirements.txt, and services/jupyterhub/README.md) as part of the lakehouse
    # client stack.
    source_config = (ROOT / "docs" / "operations" / "source-configuration.md").read_text(
        encoding="utf-8"
    )

    assert "JupyterHub" in source_config
    assert "pyiceberg" in source_config
    assert "JupyterHub + Backend wiring is a future spec" not in source_config


def _spark_notebook_connection(monkeypatch, notebook, address, failure=None):
    """Compile the unchanged connection cell against local Spark doubles."""
    import json
    import sys
    import types
    from unittest.mock import MagicMock

    path = JUPYTERHUB_DIR / "build" / "notebooks" / f"{notebook}.ipynb"
    data = json.loads(path.read_text())
    source = "".join(next(c for c in data["cells"] if c["cell_type"] == "code")["source"])
    original = RuntimeError(f"Synthetic Spark failure at {address}")

    class Session:
        @property
        def version(self):
            if failure == "version":
                raise original
            return "4.1.2"

    builder = MagicMock()
    builder.remote.return_value = builder
    builder.getOrCreate.return_value = Session()
    if failure == "creation":
        builder.getOrCreate.side_effect = original
    package = types.ModuleType("pyspark")
    sql = types.ModuleType("pyspark.sql")
    sql.SparkSession = types.SimpleNamespace(builder=builder)
    kinds = types.ModuleType("pyspark.sql.types")
    for name in ["LongType", "StringType", "StructField", "StructType"]:
        setattr(kinds, name, type(name, (), {}))
    for name, module in [("pyspark", package), ("pyspark.sql", sql), ("pyspark.sql.types", kinds)]:
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("SPARK_REMOTE", address)
    return compile(source, str(path), "exec"), builder


@pytest.mark.parametrize("notebook", ["09_spark_connect", "12_iceberg_advanced_sql"])
@pytest.mark.parametrize(
    "connection",
    [
        ("sc://spark-connect:15002", "sc://spark-connect:15002"),
        (
            "sc://owned.invalid:443/;use_ssl=true;x-aws-proxy-auth=OwnedSyntheticSparkProxyToken",
            "sc://owned.invalid:443",
        ),
        (
            "sc://[::1]:15002/;token=OwnedSyntheticSparkProxyToken",
            "sc://[::1]:15002",
        ),
    ],
)
def test_spark_notebook_passes_raw_address_but_hides_connection_options(
    monkeypatch, capsys, notebook, connection
):
    address, display = connection
    code, builder = _spark_notebook_connection(monkeypatch, notebook, address)
    exec(code, {})
    builder.remote.assert_called_once_with(address)
    output = capsys.readouterr().out
    assert display in output and "4.1.2" in output
    assert "OwnedSyntheticSparkProxyToken" not in output
    assert "use_ssl" not in output


@pytest.mark.parametrize("notebook", ["09_spark_connect", "12_iceberg_advanced_sql"])
@pytest.mark.parametrize("failure", ["creation", "version"])
def test_spark_notebook_connection_error_display_hides_proxy_token(
    monkeypatch, capsys, notebook, failure
):
    import traceback

    address = "sc://owned.invalid:443/;x-aws-proxy-auth=OwnedSyntheticSparkProxyToken"
    code, builder = _spark_notebook_connection(monkeypatch, notebook, address, failure)
    with pytest.raises(RuntimeError) as raised:
        exec(code, {})
    builder.remote.assert_called_once_with(address)
    displayed = "".join(traceback.format_exception(raised.value)) + capsys.readouterr().out
    assert "OwnedSyntheticSparkProxyToken" not in displayed
    assert "sc://owned.invalid:443" in str(raised.value)


@pytest.mark.parametrize("notebook", ["09_spark_connect", "12_iceberg_advanced_sql"])
@pytest.mark.parametrize(
    "case",
    [
        (address, failure)
        for address in [
            "sc://owned.invalid;x-aws-proxy-auth=OwnedSyntheticSparkProxyToken:443",
            "sc://[::1%25;x-aws-proxy-auth=OwnedSyntheticSparkProxyToken]:443",
        ]
        for failure in [None, "creation", "version"]
    ],
)
def test_spark_notebook_malformed_authority_does_not_disclose_credentials(
    monkeypatch, capsys, notebook, case
):
    import traceback

    address, failure = case
    code, builder = _spark_notebook_connection(monkeypatch, notebook, address, failure)
    displayed = ""
    if failure:
        with pytest.raises(RuntimeError) as raised:
            exec(code, {})
        displayed = "".join(traceback.format_exception(raised.value))
    else:
        exec(code, {})
    builder.remote.assert_called_once_with(address)
    displayed += capsys.readouterr().out
    assert "ownedsyntheticsparkproxytoken" not in displayed.casefold()
