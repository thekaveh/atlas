from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
AIRFLOW_DIR = ROOT / "services" / "airflow"
SPARK_DIR = ROOT / "services" / "spark"
DAG = AIRFLOW_DIR / "dags" / "lakehouse_spark_submit_smoke.py"
DOCKERFILE = AIRFLOW_DIR / "build" / "Dockerfile"
INIT_SCRIPT = AIRFLOW_DIR / "init" / "scripts" / "init-airflow.sh"


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_airflow_image_has_real_spark_submit_client_and_lakehouse_jars() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "openjdk-17-jdk-headless" in dockerfile
    assert "JAVA_HOME=/opt/java/openjdk" in dockerfile
    assert "SPARK_HOME=/home/airflow/spark" in dockerfile
    assert "hadoop-aws-${HADOOP_AWS_VERSION}.jar" in dockerfile
    assert "bundle-${AWS_SDK_BUNDLE_VERSION}.jar" in dockerfile
    assert "${ICEBERG_SPARK_RUNTIME_ARTIFACT}-${ICEBERG_VERSION}.jar" in dockerfile
    assert "${ICEBERG_AWS_BUNDLE_ARTIFACT}-${ICEBERG_VERSION}.jar" in dockerfile
    assert "atlas-lakehouse-smoke.jar" in dockerfile
    assert "COPY --chown=airflow:0 lakehouse-smoke/LakehouseSmoke.java" in dockerfile
    assert "sha512sum -c -" in dockerfile


def test_airflow_compose_exposes_lakehouse_spark_submit_environment() -> None:
    compose = _yaml(AIRFLOW_DIR / "compose.yml")
    expected_env = {
        "SPARK_MASTER_URL": "spark://spark-master:7077",
        "ATLAS_LAKEHOUSE_SPARK_DEPLOY_MODE": "cluster",
        "ATLAS_LAKEHOUSE_SMOKE_JAR_PATH": "/opt/airflow/atlas-jars/atlas-lakehouse-smoke.jar",
        "MINIO_ENDPOINT": "http://minio:9000",
        "MINIO_BUCKET_ICEBERG_JARS": "${MINIO_BUCKET_ICEBERG_JARS:-jars}",
        "MINIO_BUCKET_ICEBERG_LANDING": "${MINIO_BUCKET_ICEBERG_LANDING:-landing}",
        "MINIO_BUCKET_ICEBERG_LAKEHOUSE": "${MINIO_BUCKET_ICEBERG_LAKEHOUSE:-lakehouse}",
        "MINIO_ICEBERG_ACCESS_KEY": "${MINIO_ICEBERG_ACCESS_KEY}",
        "MINIO_ICEBERG_SECRET_KEY": "${MINIO_ICEBERG_SECRET_KEY}",
        "ICEBERG_REST_URI": "http://iceberg-rest:8181",
    }

    for svc_name in ("airflow-webserver", "airflow-scheduler", "airflow-dag-processor"):
        env = compose["services"][svc_name]["environment"]
        for key, value in expected_env.items():
            assert env.get(key) == value, f"{svc_name}.{key}"

    init_env = compose["services"]["airflow-init"]["environment"]
    assert init_env["ICEBERG_REST_SOURCE"] == "${ICEBERG_REST_SOURCE:-disabled}"
    assert init_env["ATLAS_LAKEHOUSE_SPARK_DEPLOY_MODE"] == "cluster"


def test_airflow_init_seeds_spark_default_for_cluster_spark_submit() -> None:
    body = INIT_SCRIPT.read_text(encoding="utf-8")

    assert "for orphan in spark_default minio_default weaviate_default neo4j_default" in body
    assert "--conn-type spark" in body
    assert "--conn-host spark-master" in body
    assert "--conn-port 7077" in body
    assert 'deploy_mode="${ATLAS_LAKEHOUSE_SPARK_DEPLOY_MODE:-cluster}"' in body
    assert '\\"deploy-mode\\": \\"${deploy_mode}\\"' in body
    assert '\\"spark-binary\\": \\"spark-submit\\"' in body


def test_airflow_manifest_declares_lakehouse_spark_submit_topology() -> None:
    manifest = _yaml(AIRFLOW_DIR / "service.yml")
    tracks = _yaml(ROOT / "bootstrapper" / "tracks.yml")["tracks"]
    data_eng = next(track for track in tracks if track["key"] == "data-eng")

    assert manifest["category"] == "agents"
    assert {source["id"] for source in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert "airflow" in data_eng["services"]
    assert {"spark", "minio", "iceberg-rest"} <= set(manifest["depends_on"]["optional"])
    assert {"spark", "minio", "iceberg-rest"} <= set(manifest["data_flow"]["calls"])
    assert "SparkSubmit" in manifest["rows"][0]["description"]


def test_spark_master_keeps_backend_only_rest_submission_api_enabled() -> None:
    compose = _yaml(SPARK_DIR / "compose.yml")
    master = compose["services"]["spark-master"]
    master_env = master["environment"]
    worker = compose["services"]["spark-worker"]
    worker_env = worker["environment"]
    worker_command = "\n".join(worker["command"])

    assert "6066" not in "\n".join(master.get("ports", []))
    assert "SPARK_MASTER_OPTS" in master_env
    assert "spark.master.rest.enabled=true" in master_env["SPARK_MASTER_OPTS"]
    assert "spark.master.rest.host=spark-master" in master_env["SPARK_MASTER_OPTS"]
    assert "spark.master.rest.port=6066" in master_env["SPARK_MASTER_OPTS"]
    assert "spark.standalone.submit.waitAppCompletion=true" in master_env["SPARK_MASTER_OPTS"], (
        "spark-master must set spark.standalone.submit.waitAppCompletion=true (#792 option 3) "
        "so the standalone submit blocks to completion and reports the final driver state — "
        "the SparkSubmitOperator's post-submit poll via the :7077 RPC connection is a benign "
        "false-negative once the submit has reported success."
    )
    assert worker_env["AWS_ACCESS_KEY_ID"] == "${MINIO_ROOT_USER}"
    assert worker_env["AWS_SECRET_ACCESS_KEY"] == "${MINIO_ROOT_PASSWORD}"
    assert worker_env["AWS_ENDPOINT_URL_S3"] == "http://minio:9000"
    assert "HADOOP_CONF_DIR=/tmp/atlas-spark-hadoop-conf" in worker_command
    assert "fs.s3a.path.style.access" in worker_command
    assert "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider" in worker_command


def test_spark_master_rest_status_endpoint_is_documented() -> None:
    spark_readme = (SPARK_DIR / "README.md").read_text(encoding="utf-8")
    airflow_readme = (AIRFLOW_DIR / "README.md").read_text(encoding="utf-8")

    for expected in (
        "spark-master:6066",
        "SparkSubmitOperator",
        "backend-network-only",
        "driver status",
    ):
        assert expected in spark_readme

    assert "spark-master:6066" in airflow_readme
    assert "post-submit driver status" in airflow_readme


def test_lakehouse_spark_submit_smoke_dag_prepares_assets_and_submits_s3a_jar() -> None:
    body = DAG.read_text(encoding="utf-8")

    assert "SparkSubmitOperator" in body
    assert "S3Hook" in body
    assert "schedule=None" in body
    assert "s3a://" in body
    assert "ATLAS_LAKEHOUSE_SMOKE_JAR_PATH" in body
    assert "java_class=LAKEHOUSE_SMOKE_CLASS" in body
    assert "deploy_mode=DEPLOY_MODE" in body
    assert 'os.environ.get("MINIO_ROOT_USER", "")' in body
    assert '"MINIO_ICEBERG_ACCESS_KEY", ""' in body
    for expected in [
        "spark.eventLog.enabled",
        "spark.eventLog.dir",
        "spark.hadoop.fs.s3a.endpoint",
        "spark.driverEnv.AWS_ACCESS_KEY_ID",
        "spark.executorEnv.AWS_ENDPOINT_URL_S3",
        "spark.sql.catalog.lakehouse.uri",
        "spark.sql.catalog.lakehouse.warehouse",
        "spark.sql.catalog.lakehouse.s3.access-key-id",
        "spark.sql.catalog.lakehouse.s3.secret-access-key",
    ]:
        assert expected in body


def test_airflow_docs_describe_s3a_spark_submit_validation_path() -> None:
    readme = (AIRFLOW_DIR / "README.md").read_text(encoding="utf-8")
    source_docs = (ROOT / "docs" / "deployment" / "source-configuration.md").read_text(
        encoding="utf-8"
    )
    combined = f"{readme}\n{source_docs}"

    for expected in [
        "SparkSubmitOperator",
        "s3a://jars/",
        "deploy_mode=\"cluster\"",
        "lakehouse_spark_submit_smoke",
        "Spark History",
        "Iceberg REST",
    ]:
        assert expected in combined


def test_airflow_docs_describe_task_sdk_connection_context_boundary() -> None:
    readme = (AIRFLOW_DIR / "README.md").read_text(encoding="utf-8")
    source_docs = (ROOT / "docs" / "deployment" / "source-configuration.md").read_text(
        encoding="utf-8"
    )

    for docs in (readme, source_docs):
        for expected in (
            "outside a task execution context",
            "AirflowNotFoundException",
            "BaseHook.get_connection",
            'S3Hook(aws_conn_id="minio_default")',
            "airflow.settings.Session",
            "airflow.models.Connection",
            "minio_default",
            "spark_default",
            "DAG tasks should keep using hooks/operators",
        ):
            assert expected in docs
