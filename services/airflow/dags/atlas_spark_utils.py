"""Atlas Spark utilities for the standalone cluster (#792, #876).

The shipped ``SparkSubmitHook`` (apache-airflow-providers-apache-spark 5.x)
invokes ``_start_driver_status_tracking()`` *inside* ``submit()`` for cluster
mode — polling the connection's RPC port (:7077), which is a protocol mismatch
against the standalone master's REST endpoint (:6066). The hook returns ``None``
(not a driver id) and the poll raises ``AirflowException`` (#876 live evidence).

This module provides:

- ``confirm_driver_status_via_rest()`` — pure-Python REST confirmation (urllib).
- ``submit_and_confirm_via_rest()`` — orchestrates submit + REST confirmation
  by **disabling the hook's poll** (``_should_track_driver_status = False``)
  before ``submit()``, then extracting ``hook._driver_id`` and confirming via
  the ``:6066`` REST endpoint.
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
    finish successfully.

    Args:
        driver_id: The Spark standalone submission ID (from ``hook._driver_id``
            after ``submit()`` — NOT returned by ``submit()`` itself, which is
            ``None`` in the shipped provider version).
        rest_host: The spark-master hostname (default ``spark-master``).
        rest_port: The standalone REST port (default 6066).
        timeout: HTTP timeout in seconds.

    Raises:
        RuntimeError: The driver is not ``FINISHED + success``.
        URLError: The REST endpoint is unreachable.
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


def submit_and_confirm_via_rest(
    hook,
    application: str,
    *,
    rest_host: str = "spark-master",
) -> str:
    """Submit a Spark job and confirm the driver's status via REST (#792, #876).

    The hook's ``_should_track_driver_status`` is set to ``False`` **before**
    ``submit()`` so the provider's ``_start_driver_status_tracking()`` (the
    incompatible :7077 RPC poll) never runs. After ``submit()`` returns (``None``
    in the shipped version), the driver id is read from ``hook._driver_id`` and
    confirmed via the master's ``:6066`` REST endpoint.

    **Never masks a genuine failure**: ``submit()`` raises on a real submission
    error (spark-submit exits non-zero) before the REST confirmation runs;
    ``confirm_driver_status_via_rest()`` raises ``RuntimeError`` if the driver
    is not ``FINISHED + success``.

    Args:
        hook: A ``SparkSubmitHook`` instance (constructed by the caller — this
            function is hook-agnostic so it can be unit-tested with a mock).
        application: The application JAR path (passed to ``hook.submit()`` —
            NOT to the hook's ``__init__``, which does not accept it in the
            shipped provider version).
        rest_host: The spark-master hostname for REST confirmation.

    Returns:
        The driver id on success.

    Raises:
        RuntimeError: No driver_id was set (submission failed before the driver
            launched), or the driver did not finish successfully.
        Exception: Any genuine submission error from ``hook.submit()``.
    """
    # Disable the provider's :7077 RPC poll — it runs inside submit() for
    # cluster mode and always fails with a protocol mismatch (#792, #876).
    hook._should_track_driver_status = False
    hook.submit(application)  # returns None; _driver_id is set on the hook.

    driver_id = hook._driver_id
    if not driver_id:
        raise RuntimeError(
            "SparkSubmitHook did not set a driver_id — the submission may "
            "have failed before the driver launched, or the log format changed."
        )
    confirm_driver_status_via_rest(driver_id, rest_host=rest_host)
    return driver_id
