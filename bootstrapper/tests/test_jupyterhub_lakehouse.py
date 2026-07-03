from __future__ import annotations

from pathlib import Path

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
    assert {"minio", "iceberg-rest", "spark"} <= set(manifest["data_flow"]["calls"])
    assert "lakehouse" in manifest["rows"][0]["description"].lower()


def test_jupyterhub_docs_cover_lakehouse_clients_and_validation() -> None:
    readme = (JUPYTERHUB_DIR / "README.md").read_text(encoding="utf-8")
    build_readme = (JUPYTERHUB_DIR / "build" / "README.md").read_text(encoding="utf-8")
    combined = f"{readme}\n{build_readme}"

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

    future_pairs = readme.split("### 15.4 Future", 1)[1].split("### 15.5", 1)[0]
    assert "jupyterhub ↔ minio" not in future_pairs


def test_jupyterhub_deployment_docs_describe_current_data_track_lakehouse_path() -> None:
    source_config = (ROOT / "docs" / "deployment" / "source-configuration.md").read_text(
        encoding="utf-8"
    )

    assert "JupyterHub" in source_config
    assert "PyIceberg" in source_config
    assert "JupyterHub + Backend wiring is a future spec" not in source_config
