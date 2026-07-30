"""Atlas Spark utilities for the standalone cluster (#792).

The SparkSubmitOperator polls post-submit driver status via spark_default
(:7077 RPC) — which always fails with a protocol mismatch on the standalone
master (the REST endpoint is on :6066). After ``spark.standalone.submit.
waitAppCompletion=true`` makes ``spark-submit`` block+succeed, the poll is a
redundant false-negative.

This module provides ``confirm_driver_status_via_rest()`` — a lightweight
function that queries the master's REST endpoint (:6066) directly, bypassing
the broken poll. It is a pure Python callable (uses ``urllib``, no Airflow
import needed) so it can be unit-tested without a running cluster.
"""
from __future__ import annotations

import json
import urllib.request


def confirm_driver_status_via_rest(
    driver_id: str,
    rest_host: str = "spark-master",
    rest_port: int = 6066,
    timeout: int = 30,
) -> dict:
    """Confirm a standalone Spark driver's terminal status via the master's REST.

    Queries ``http://<rest_host>:<rest_port>/v1/submissions/status/<driver_id>``
    and returns the REST payload. Raises ``RuntimeError`` if the driver did not
    finish successfully — so a caller can ``submit()`` then ``confirm()`` and
    only succeed when the job genuinely finished, never masking a real failure.

    Args:
        driver_id: The Spark standalone submission ID (returned by
            ``SparkSubmitHook.submit()``).
        rest_host: The spark-master hostname (default ``spark-master`` — the
            Atlas compose DNS name, backend-network-only).
        rest_port: The standalone REST port (default 6066).
        timeout: HTTP timeout in seconds.

    Returns:
        The REST response payload (e.g. ``{"driverState": "FINISHED",
        "success": true}``).

    Raises:
        RuntimeError: The driver is not ``FINISHED + success``.
        URLError: The REST endpoint is unreachable (master not running, wrong
            host, etc.).
    """
    url = f"http://{rest_host}:{rest_port}/v1/submissions/status/{driver_id}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    driver_state = payload.get("driverState", "UNKNOWN")
    success = payload.get("success", False)
    if driver_state != "FINISHED" or not success:
        raise RuntimeError(
            f"Spark driver {driver_id} did not finish successfully: "
            f"driverState={driver_state}, success={success}. "
            f"This is a real failure, not the :7077 poll false-negative."
        )
    return payload
