from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DECISION = REPO_ROOT / "docs" / "strategy" / "zeppelin-spark-backend-decision.md"
README = REPO_ROOT / "services" / "zeppelin" / "README.md"
COMPOSE = REPO_ROOT / "services" / "zeppelin" / "compose.yml"
MANIFEST = REPO_ROOT / "services" / "zeppelin" / "service.yml"
KONG_README = REPO_ROOT / "services" / "kong" / "README.md"
PORTS_AND_ROUTES = REPO_ROOT / "docs" / "deployment" / "ports-and-routes.md"
SOURCE_CONFIGURATION = REPO_ROOT / "docs" / "deployment" / "source-configuration.md"
ROOT_README = REPO_ROOT / "README.md"


def _compact(text: str) -> str:
    return " ".join(text.split())


def test_zeppelin_backend_decision_selects_standalone_spark() -> None:
    text = DECISION.read_text()
    compact = _compact(text)

    assert "Decision issue: [#247]" in text
    assert "Implementation issue unblocked by this decision: [#211]" in text
    assert "spark.master=spark://spark-master:7077" in text
    assert "spark.remote=sc://spark-connect:15002" in text
    assert "Spark Connect remains the right path for JupyterHub" in compact
    assert "Rejected for #211" in text
    assert "Livy would be a new service admission" in text


def test_zeppelin_backend_decision_covers_service_admission_contract() -> None:
    text = DECISION.read_text()
    compact = _compact(text)

    required_fragments = [
        "Track: `data-eng`",
        "Category: existing `zeppelin` service remains `apps`",
        "keep `ZEPPELIN_SOURCE=container|disabled`",
        "no new host ports",
        "Kong alias: none",
        "`iceberg-rest` in `depends_on.optional` and `data_flow.calls`",
        "`zeppelin-init` one-shot container",
        "use scoped Spark/S3A and Iceberg MinIO credentials",
        "Do not present `spark.remote=sc://spark-connect:15002` as the happy path for Zeppelin",
    ]

    for fragment in required_fragments:
        assert fragment in compact


def test_zeppelin_manifest_currently_stays_existing_service_shape() -> None:
    manifest = yaml.safe_load(MANIFEST.read_text())

    assert manifest["category"] == "apps"
    assert manifest["sources"]["var"] == "ZEPPELIN_SOURCE"
    assert [option["id"] for option in manifest["sources"]["options"]] == [
        "container",
        "disabled",
    ]
    assert manifest["depends_on"]["required"] == ["spark", "minio"]
    assert "iceberg-rest" in manifest["depends_on"]["optional"]
    assert "alias" not in manifest["rows"][0]
    assert manifest["runtime_sc"]["zeppelin"]["container"]["environment"][
        "SPARK_MASTER"
    ] == "spark://spark-master:7077"
    assert manifest["runtime_sc"]["zeppelin-init"]["container"]["scale"] == 1


def test_zeppelin_is_loopback_only_and_has_no_root_minio_credentials() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    manifest = MANIFEST.read_text(encoding="utf-8")

    assert '"127.0.0.1:${ZEPPELIN_PORT}:8080"' in compose
    assert "zeppelin.localhost" not in compose
    assert "MINIO_ROOT_USER" not in compose
    assert "MINIO_ROOT_PASSWORD" not in compose
    assert "MINIO_SPARK_ACCESS_KEY" in compose
    assert "MINIO_SPARK_SECRET_KEY" in compose
    assert "MINIO_ROOT_USER" not in manifest
    assert "MINIO_ROOT_PASSWORD" not in manifest
    assert "Kong routes traffic TO Zeppelin" not in manifest


def test_current_operator_docs_do_not_advertise_a_zeppelin_kong_route() -> None:
    for path in (KONG_README, PORTS_AND_ROUTES, SOURCE_CONFIGURATION, ROOT_README):
        assert "zeppelin.localhost" not in path.read_text(encoding="utf-8")

    source_docs = SOURCE_CONFIGURATION.read_text(encoding="utf-8")
    assert "loopback-only direct UI" in source_docs


def test_zeppelin_readme_no_longer_claims_connect_is_happy_path() -> None:
    readme = README.read_text()
    compact = _compact(readme)

    assert "Zeppelin Backend Decision" in readme
    assert "standalone Spark interpreter path" in readme
    assert "JupyterHub remains the Spark Connect notebook path" in readme
    assert "The interpreter config is **not** seeded automatically" not in readme
    assert "The stack should not require `%spark` Scala to use Spark Connect" in compact


def test_zeppelin_compose_comments_follow_backend_decision() -> None:
    compose = COMPOSE.read_text()
    compact = _compact(compose)

    assert "SPARK_HOME: /opt/spark" in compose
    assert "SPARK_MASTER: spark://spark-master:7077" in compose
    assert "spark.sql.catalog.lakehouse.uri=http://iceberg-rest:8181" in compact
    assert "zeppelin-init" in compose
    assert "supported out-of-the-box route is Spark Connect" not in compose
    assert "Users who want to drive Connect from Zeppelin" not in compose
