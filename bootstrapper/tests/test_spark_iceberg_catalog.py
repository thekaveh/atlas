"""Contract tests for Spark's baked Iceberg lakehouse integration."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SPARK_DIR = REPO_ROOT / "services" / "spark"
COMPOSE = SPARK_DIR / "compose.yml"
DOCKERFILE = SPARK_DIR / "build" / "Dockerfile"
MANIFEST = SPARK_DIR / "service.yml"
README = SPARK_DIR / "README.md"
SOURCE_DOC = REPO_ROOT / "docs" / "deployment" / "source-configuration.md"

ICEBERG_VERSION = "1.11.0"
ICEBERG_RUNTIME = "iceberg-spark-runtime-4.1_2.13"
ICEBERG_RUNTIME_SHA512 = (
    "f4620bb2d20777146a769a8a939a5bed658e1d5708b6a5155e1f6e831c0bfd29"
    "bd1f82618dc59bc0f30399ffcb03add3e66656286692dc6c2be94e4e1f4e7479"
)
ICEBERG_AWS_BUNDLE = "iceberg-aws-bundle"
ICEBERG_AWS_BUNDLE_SHA512 = (
    "bb50f7dc5f36a001efecf15e4d03eff955466f06557261fbacdd7fe0d88113c82"
    "e0b9b2af96cd554cef4a1d4c2348fc950270bc3f51cf6bf1e4526f46ce336a0"
)


def test_spark_image_bakes_iceberg_runtime_and_aws_bundle_with_sha512():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert f"ARG ICEBERG_VERSION={ICEBERG_VERSION}" in dockerfile
    assert f"ARG ICEBERG_SPARK_RUNTIME_ARTIFACT={ICEBERG_RUNTIME}" in dockerfile
    assert f"ARG ICEBERG_AWS_BUNDLE_ARTIFACT={ICEBERG_AWS_BUNDLE}" in dockerfile
    assert f"ARG ICEBERG_SPARK_RUNTIME_SHA512={ICEBERG_RUNTIME_SHA512}" in dockerfile
    assert f"ARG ICEBERG_AWS_BUNDLE_SHA512={ICEBERG_AWS_BUNDLE_SHA512}" in dockerfile

    assert (
        "https://repo.maven.apache.org/maven2/org/apache/iceberg/"
        "${ICEBERG_SPARK_RUNTIME_ARTIFACT}/${ICEBERG_VERSION}/"
        "${ICEBERG_SPARK_RUNTIME_ARTIFACT}-${ICEBERG_VERSION}.jar"
    ) in dockerfile
    assert (
        "https://repo.maven.apache.org/maven2/org/apache/iceberg/"
        "${ICEBERG_AWS_BUNDLE_ARTIFACT}/${ICEBERG_VERSION}/"
        "${ICEBERG_AWS_BUNDLE_ARTIFACT}-${ICEBERG_VERSION}.jar"
    ) in dockerfile
    assert "sha512sum -c -" in dockerfile
    assert (
        "/opt/spark/jars/${ICEBERG_SPARK_RUNTIME_ARTIFACT}-${ICEBERG_VERSION}.jar"
        in dockerfile
    )
    assert (
        "/opt/spark/jars/${ICEBERG_AWS_BUNDLE_ARTIFACT}-${ICEBERG_VERSION}.jar"
        in dockerfile
    )


def test_spark_connect_defaults_include_lakehouse_rest_catalog():
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    command = doc["services"]["spark-connect"]["command"]
    joined = " ".join(command)

    expected_confs = {
        "spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        "spark.sql.catalog.lakehouse=org.apache.iceberg.spark.SparkCatalog",
        "spark.sql.catalog.lakehouse.type=rest",
        "spark.sql.catalog.lakehouse.uri=http://iceberg-rest:8181",
        "spark.sql.catalog.lakehouse.warehouse=s3a://lakehouse/",
        "spark.sql.catalog.lakehouse.io-impl=org.apache.iceberg.aws.s3.S3FileIO",
        "spark.sql.catalog.lakehouse.s3.endpoint=http://minio:9000",
        "spark.sql.catalog.lakehouse.s3.path-style-access=true",
        "spark.sql.catalog.lakehouse.s3.access-key-id=${MINIO_ICEBERG_ACCESS_KEY}",
        "spark.sql.catalog.lakehouse.s3.secret-access-key=${MINIO_ICEBERG_SECRET_KEY}",
        "spark.sql.catalog.lakehouse.client.region=us-east-1",
    }
    for conf in expected_confs:
        assert conf in joined

    assert "ports" not in doc["services"]["spark-connect"], (
        "Spark Connect should stay backend-network-only for this slice."
    )
    assert "iceberg-rest" not in doc["services"]["spark-connect"].get("depends_on", {}), (
        "Spark should start for ML-only users even when ICEBERG_REST_SOURCE=disabled."
    )


def test_spark_manifest_marks_iceberg_rest_as_optional_runtime_upstream():
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))

    assert "iceberg-rest" in manifest["depends_on"]["optional"]
    assert "iceberg-rest" in manifest["data_flow"]["calls"]
    assert manifest["category"] == "data"
    assert [option["id"] for option in manifest["sources"]["options"]] == [
        "container",
        "disabled",
    ]
    assert "lakehouse" in manifest["sources"]["options"][0]["label"].lower()


def test_spark_docs_explain_lakehouse_catalog_without_new_ports_or_source():
    readme = README.read_text(encoding="utf-8")
    source_doc = SOURCE_DOC.read_text(encoding="utf-8")

    for text in (readme, source_doc):
        assert "Iceberg REST" in text
        assert "lakehouse" in text
        assert "iceberg-spark-runtime-4.1_2.13:1.11.0" in text
        assert "sc://spark-connect:15002" in text

    assert "SPARK_SOURCE=container" in readme
    assert "spark-lakehouse" not in readme
    assert "Spark Connect | `sc://spark-connect:15002`" in readme
