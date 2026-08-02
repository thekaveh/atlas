"""Manual smoke DAG for Airflow -> SparkSubmitOperator -> S3A -> Iceberg.

This DAG is intentionally unscheduled. Trigger it after starting Atlas with
Airflow, Spark, MinIO, and Iceberg REST enabled to validate the lakehouse
submit path end-to-end.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from airflow.providers.standard.operators.python import PythonOperator
from atlas_spark_utils import RestConfirmingSparkHook


class AtlasSparkSubmitOperator(SparkSubmitOperator):
    """Spark submit operator with standalone-driver REST confirmation."""

    def __init__(self, *, rest_host: str = "spark-master", **kwargs):
        super().__init__(**kwargs)
        self.rest_host = rest_host

    def _get_hook(self):
        return RestConfirmingSparkHook(
            super()._get_hook(),
            rest_host=self.rest_host,
        )


JARS_BUCKET = os.environ.get("MINIO_BUCKET_ICEBERG_JARS", "jars")
LANDING_BUCKET = os.environ.get("MINIO_BUCKET_ICEBERG_LANDING", "landing")
LAKEHOUSE_BUCKET = os.environ.get("MINIO_BUCKET_ICEBERG_LAKEHOUSE", "lakehouse")
REGION = os.environ.get("MINIO_REGION", "us-east-1")
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://minio:9000")
ICEBERG_REST_URI = os.environ.get("ICEBERG_REST_URI", "http://iceberg-rest:8181")
DEPLOY_MODE = os.environ.get("ATLAS_LAKEHOUSE_SPARK_DEPLOY_MODE", "cluster")
SMOKE_JAR_PATH = Path(
    os.environ.get(
        "ATLAS_LAKEHOUSE_SMOKE_JAR_PATH",
        "/opt/airflow/atlas-jars/atlas-lakehouse-smoke.jar",
    )
)
LAKEHOUSE_SMOKE_CLASS = "com.atlas.spark.LakehouseSmoke"
JAR_KEY = "atlas/lakehouse-smoke/latest/atlas-lakehouse-smoke.jar"
LANDING_KEY = "airflow-smoke/input/latest.txt"
ICEBERG_TABLE = "lakehouse.bronze.airflow_spark_submit_smoke"


default_args = {
    "owner": "atlas",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
}


def prepare_s3a_assets(**_ctx) -> None:
    if not SMOKE_JAR_PATH.is_file():
        raise FileNotFoundError(f"Smoke JAR missing: {SMOKE_JAR_PATH}")

    s3 = S3Hook(aws_conn_id="minio_default").get_conn()
    s3.upload_file(str(SMOKE_JAR_PATH), JARS_BUCKET, JAR_KEY)
    s3.put_object(
        Bucket=LANDING_BUCKET,
        Key=LANDING_KEY,
        Body=b"atlas airflow spark-submit smoke\n",
        ContentType="text/plain",
    )


spark_conf = {
    "spark.master": os.environ.get("SPARK_MASTER_URL", "spark://spark-master:7077"),
    "spark.app.name": "atlas-airflow-lakehouse-smoke",
    "spark.cores.max": "1",
    "spark.executor.memory": "1g",
    "spark.driver.memory": "1g",
    "spark.standalone.submit.waitAppCompletion": "true",
    "spark.hadoop.fs.s3a.endpoint": MINIO_ENDPOINT,
    "spark.hadoop.fs.s3a.endpoint.region": REGION,
    "spark.hadoop.fs.s3a.access.key": os.environ.get("MINIO_ROOT_USER", ""),
    "spark.hadoop.fs.s3a.secret.key": os.environ.get("MINIO_ROOT_PASSWORD", ""),
    "spark.hadoop.fs.s3a.path.style.access": "true",
    "spark.hadoop.fs.s3a.connection.ssl.enabled": "false",
    "spark.hadoop.fs.s3a.aws.credentials.provider": (
        "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider"
    ),
    "spark.driverEnv.AWS_ACCESS_KEY_ID": os.environ.get("MINIO_ROOT_USER", ""),
    "spark.driverEnv.AWS_SECRET_ACCESS_KEY": os.environ.get("MINIO_ROOT_PASSWORD", ""),
    "spark.driverEnv.AWS_REGION": REGION,
    "spark.driverEnv.AWS_ENDPOINT_URL_S3": MINIO_ENDPOINT,
    "spark.executorEnv.AWS_ACCESS_KEY_ID": os.environ.get("MINIO_ROOT_USER", ""),
    "spark.executorEnv.AWS_SECRET_ACCESS_KEY": os.environ.get("MINIO_ROOT_PASSWORD", ""),
    "spark.executorEnv.AWS_REGION": REGION,
    "spark.executorEnv.AWS_ENDPOINT_URL_S3": MINIO_ENDPOINT,
    "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
    "spark.sql.catalog.lakehouse": "org.apache.iceberg.spark.SparkCatalog",
    "spark.sql.catalog.lakehouse.type": "rest",
    "spark.sql.catalog.lakehouse.uri": ICEBERG_REST_URI,
    "spark.sql.catalog.lakehouse.warehouse": f"s3a://{LAKEHOUSE_BUCKET}/",
    "spark.sql.catalog.lakehouse.io-impl": "org.apache.iceberg.aws.s3.S3FileIO",
    "spark.sql.catalog.lakehouse.s3.endpoint": MINIO_ENDPOINT,
    "spark.sql.catalog.lakehouse.s3.path-style-access": "true",
    "spark.sql.catalog.lakehouse.s3.access-key-id": os.environ.get(
        "MINIO_ICEBERG_ACCESS_KEY", ""
    ),
    "spark.sql.catalog.lakehouse.s3.secret-access-key": os.environ.get(
        "MINIO_ICEBERG_SECRET_KEY", ""
    ),
    "spark.sql.catalog.lakehouse.client.region": REGION,
    "spark.eventLog.enabled": "true",
    "spark.eventLog.dir": "s3a://spark-history/",
}


with DAG(
    "lakehouse_spark_submit_smoke",
    description="Manual smoke: upload a JAR to MinIO, submit it with SparkSubmitOperator, write Iceberg.",
    default_args=default_args,
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["smoke", "lakehouse", "spark-submit"],
) as dag:
    prepare_assets = PythonOperator(
        task_id="prepare_s3a_assets",
        python_callable=prepare_s3a_assets,
    )

    submit_lakehouse_job = AtlasSparkSubmitOperator(
        task_id="submit_lakehouse_s3a_jar",
        conn_id="spark_default",
        application=f"s3a://{JARS_BUCKET}/{JAR_KEY}",
        java_class=LAKEHOUSE_SMOKE_CLASS,
        deploy_mode=DEPLOY_MODE,
        conf=spark_conf,
        application_args=[
            f"s3a://{LANDING_BUCKET}/{LANDING_KEY}",
            ICEBERG_TABLE,
        ],
        verbose=True,
    )

    prepare_assets >> submit_lakehouse_job
