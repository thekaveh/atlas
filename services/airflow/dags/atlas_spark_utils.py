"""Atlas Spark utilities for the standalone cluster (#792, #876, #880).

The shipped ``SparkSubmitHook`` (apache-airflow-providers-apache-spark 5.x)
invokes ``_start_driver_status_tracking()`` *inside* ``submit()`` for cluster
mode — polling the connection's RPC port (:7077), which is a protocol mismatch
against the standalone master's REST endpoint (:6066). The hook returns ``None``
(not a driver id) and the poll raises ``AirflowException`` (#876).

Worse (#880): the shipped provider populates ``hook._driver_id`` **only** on the
driver-tracking path — so when ``_should_track_driver_status`` is set to ``False``
(to avoid the :7077 poll), ``_driver_id`` is never set either. The driver ID must
therefore be extracted from the spark-submit log **independently** of the
provider's internal tracking state.

This module provides:

- ``confirm_driver_status_via_rest()`` — pure-Python REST confirmation (urllib).
- ``_extract_driver_id_from_log()`` — regex extraction of the standalone
  submission ID (``driver-YYYYMMDDHHMMSS-NNNN``) from spark-submit log lines.
- ``submit_and_confirm_via_rest()`` — orchestrates submit + REST confirmation
  by (1) disabling the :7077 poll, (2) wrapping ``_process_spark_submit_log``
  to capture the log, (3) extracting the driver ID from the captured log via
  regex, and (4) confirming via the :6066 REST endpoint.
"""
from __future__ import annotations

import json
import re
import urllib.request


# The Spark standalone master prints the submission ID in the format
# "driver-YYYYMMDDHHMMSS-NNNN" (e.g., "driver-20260730220946-0000").
_DRIVER_ID_RE = re.compile(r"(driver-\d+-\d+)")


def _extract_driver_id_from_log(lines: list[str]) -> str | None:
    """Extract a standalone Spark submission ID from spark-submit log lines.

    The master prints the ID during submission (e.g., "Driver successfully
    submitted as driver-20260730220946-0000"). This works independently of
    the provider's ``_driver_id`` attribute (#880), which is only populated
    on the tracking path we disable.
    """
    for line in lines:
        m = _DRIVER_ID_RE.search(line)
        if m:
            return m.group(1)
    return None


def confirm_driver_status_via_rest(
    driver_id: str,
    rest_host: str = "spark-master",
    rest_port: int = 6066,
    timeout: int = 30,
) -> dict:
    """Confirm a standalone Spark driver's terminal status via the master's REST.

    Raises ``RuntimeError`` if the driver is not ``FINISHED + success``.
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
    """Submit a Spark job and confirm the driver's status via REST (#792, #876, #880).

    1. Wraps ``hook._process_spark_submit_log`` to capture the spark-submit log.
    2. Sets ``_should_track_driver_status = False`` (the :7077 poll never runs).
    3. Calls ``hook.submit(application)`` (returns ``None`` in the shipped version).
    4. Extracts the driver ID from the captured log via regex (``driver-\\d+-\\d+``)
       — NOT from ``hook._driver_id``, which the shipped provider only sets on the
       tracking path (#880). Falls back to ``hook._driver_id`` if the regex misses.
    5. Confirms ``FINISHED + success`` via the master's ``:6066`` REST endpoint.

    **Never masks a genuine failure**: ``submit()`` raises on a real submission
    error before REST confirmation; ``confirm_driver_status_via_rest()`` raises
    if not ``FINISHED + success``; a missing driver ID raises.
    """
    # --- 1. Capture the spark-submit log (#880) ---
    captured_lines: list[str] = []
    original_log_processor = getattr(hook, "_process_spark_submit_log", None)

    def _capturing_log_processor(lines):
        captured_lines.extend(lines)
        if original_log_processor:
            original_log_processor(iter(captured_lines))

    if original_log_processor:
        hook._process_spark_submit_log = _capturing_log_processor

    # --- 2. Disable the :7077 RPC poll ---
    hook._should_track_driver_status = False

    # --- 3. Submit ---
    try:
        hook.submit(application)
    finally:
        if original_log_processor:
            hook._process_spark_submit_log = original_log_processor

    # --- 4. Extract the driver ID ---
    driver_id = _extract_driver_id_from_log(captured_lines) or getattr(
        hook, "_driver_id", None
    )
    if not driver_id:
        raise RuntimeError(
            "Could not extract a Spark driver_id — the spark-submit log did not "
            "contain a submission ID and hook._driver_id is unset. The provider "
            "may have changed its log format or the submission failed before the "
            "driver launched."
        )

    # --- 5. Confirm via REST ---
    confirm_driver_status_via_rest(driver_id, rest_host=rest_host)
    return driver_id


class RestConfirmingSparkHook:
    """Adapter that preserves the operator hook contract while using REST status.

    SparkSubmitOperator.execute still owns its normal configuration and
    OpenLineage injection. Only the hook submit boundary is replaced, so
    cluster-mode completion is confirmed through the standalone master's
    supported REST port instead of the RPC port.
    """

    def __init__(self, hook, *, rest_host: str = "spark-master") -> None:
        self._hook = hook
        self._rest_host = rest_host

    def submit(self, application: str):
        return submit_and_confirm_via_rest(
            self._hook,
            application,
            rest_host=self._rest_host,
        )

    def on_kill(self) -> None:
        self._hook.on_kill()

    def __getattr__(self, name):
        return getattr(self._hook, name)
