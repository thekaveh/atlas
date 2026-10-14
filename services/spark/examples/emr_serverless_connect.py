"""Connect a notebook / tool to Amazon EMR Serverless via Spark Connect.

This is the optional "cloud burst" path: instead of the in-stack ``spark-connect``
sidecar, point a Spark Connect client at a managed EMR Serverless interactive
session. Atlas already speaks the Spark Connect protocol (see
``09_spark_connect.ipynb`` / ``10_spark_scala.ipynb``), so only the endpoint +
auth differ.

IMPORTANT — version + environment
---------------------------------
EMR Serverless interactive runs **Spark 3.5.6** (emr-7.13.0), and a Spark Connect
client MUST match its server's Spark version. JupyterHub's default kernel carries
``pyspark-client==4.1.2`` (for the in-stack 4.1.2 sidecar), so this helper will
NOT run there. Use a SEPARATE environment::

    python -m venv emr-env && . emr-env/bin/activate
    pip install "pyspark[connect]==3.5.6" "boto3>=1.43.0"

Prerequisites (one-time, AWS side)
----------------------------------
* An EMR Serverless application created with interactive sessions enabled::

      aws emr-serverless create-application --type SPARK --name atlas-burst \\
        --release-label emr-7.13.0 --interactive-configuration '{"sessionEnabled": true}'

  …then ``start-application`` (or enable auto-start).
* An IAM execution role granting the session access to your data (S3 / Glue).
* The caller's IAM principal needs ``emr-serverless:StartSession``, ``GetSession``,
  ``GetSessionEndpoint``, ``TerminateSession``, ``GetResourceDashboard`` and
  ``iam:PassRole`` on the execution role.

Cost note: ``spark.stop()`` only disconnects the client — the EMR session keeps
billing until you ``terminate`` it (or it hits the idle/24h timeout). Always
terminate. The auth token also expires after ~1 hour; for longer work, re-fetch
the endpoint and rebuild the session.
"""

from __future__ import annotations

import logging
import time

import boto3
from pyspark.sql import SparkSession


def emr_serverless_session(
    application_id: str,
    execution_role_arn: str,
    region: str,
    *,
    poll_seconds: int = 5,
    timeout_seconds: int = 600,
):
    """Start an EMR Serverless session and return a connected SparkSession.

    Returns ``(spark, client, session_id)``. Pass ``client`` + ``session_id`` to
    :func:`terminate` when done so billing stops.
    Setup failures attempt termination before raising; successful callers own cleanup.
    """
    client = boto3.client("emr-serverless", region_name=region)
    session_id = client.start_session(
        applicationId=application_id,
        executionRoleArn=execution_role_arn,
    )["sessionId"]

    try:
        deadline = time.monotonic() + timeout_seconds
        while True:
            state = client.get_session(
                applicationId=application_id, sessionId=session_id
            )["session"]["state"]
            if state in ("STARTED", "IDLE"):
                break
            if state in ("FAILED", "TERMINATED"):
                raise RuntimeError(f"EMR Serverless session {session_id} -> {state}")
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"session {session_id} not ready after {timeout_seconds}s (state={state})"
                )
            time.sleep(poll_seconds)

        ep = client.get_session_endpoint(
            applicationId=application_id, sessionId=session_id
        )
        # The endpoint URL has no port; append :443 (the client defaults to 15002,
        # which is unreachable on EMR Serverless). Token is time-limited (~1h).
        connect_url = (
            ep["endpoint"].replace("https://", "sc://", 1)
            + ":443/;use_ssl=true;x-aws-proxy-auth="
            + ep["authToken"]
        )
        spark = SparkSession.builder.remote(connect_url).getOrCreate()
        return spark, client, session_id
    except BaseException:
        _terminate_after_failed_setup(client, application_id, session_id)
        raise


def terminate(client, application_id: str, session_id: str) -> None:
    """Terminate the EMR Serverless session to stop billing.

    ``spark.stop()`` only closes the local client connection; the remote session
    keeps running (and charging) until this is called or it times out.
    """
    client.terminate_session(applicationId=application_id, sessionId=session_id)


def _terminate_after_failed_setup(client, application_id: str, session_id: str) -> None:
    """Attempt owned-session cleanup without replacing the setup failure."""
    try:
        terminate(client, application_id, session_id)
    except Exception:
        logging.getLogger(__name__).warning(
            "Could not terminate EMR Serverless session %s after setup failed.", session_id,
        )


if __name__ == "__main__":
    import os

    spark, client, session_id = emr_serverless_session(
        application_id=os.environ["EMR_APPLICATION_ID"],
        execution_role_arn=os.environ["EMR_EXECUTION_ROLE_ARN"],
        region=os.environ.get("AWS_REGION", "us-east-1"),
    )
    try:
        print("connected | Spark", spark.version)
        spark.sql("SELECT 1 + 1 AS result").show()
    finally:
        try:
            spark.stop()
        finally:
            terminate(client, os.environ["EMR_APPLICATION_ID"], session_id)
        print(f"session {session_id} terminated")
