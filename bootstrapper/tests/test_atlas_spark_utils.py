"""Tests for the Atlas Spark standalone driver-status REST confirmation (#792, #876, #880).

Tests three functions:
- ``confirm_driver_status_via_rest()`` — pure-Python REST query (urllib mock).
- ``_extract_driver_id_from_log()`` — regex extraction from spark-submit log lines.
- ``submit_and_confirm_via_rest()`` — orchestration that disables the :7077 poll,
  captures the spark-submit log, extracts the driver ID via regex (NOT from
  ``hook._driver_id``, which the shipped provider only sets on the tracking path
  we disable — #880), and confirms via REST.

The mock hook faithfully represents the shipped SparkSubmitHook API (#876, #880):
- ``__init__`` does NOT accept ``application``.
- ``submit(application)`` returns None.
- ``_process_spark_submit_log(lines)`` is called during submit.
- ``_driver_id`` stays None when ``_should_track_driver_status`` is False.
- ``_should_track_driver_status`` is an attribute (overridable).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_MOD_PATH = (
    Path(__file__).resolve().parents[2]
    / "services" / "airflow" / "dags" / "atlas_spark_utils.py"
)
_spec = importlib.util.spec_from_file_location("atlas_spark_utils", _MOD_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
confirm_driver_status_via_rest = _mod.confirm_driver_status_via_rest
submit_and_confirm_via_rest = _mod.submit_and_confirm_via_rest
_extract_driver_id_from_log = _mod._extract_driver_id_from_log
RestConfirmingSparkHook = getattr(_mod, "RestConfirmingSparkHook", None)


def _mock_response(payload: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(payload).encode("utf-8")
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    return mock


_FINISHED = {"driverState": "FINISHED", "success": True}
_DRIVER_ID = "driver-20260730220946-0000"
_LOG_LINES = [
    "Running spark-submit using cluster deploy mode",
    "Driver successfully submitted as driver-20260730220946-0000",
    "spark-submit exited with code 0",
]


def _make_mock_hook(
    log_lines: list[str] | None = None,
    driver_id: str | None = None,
    submit_raises: Exception | None = None,
):
    """Mock SparkSubmitHook matching the shipped API (#876, #880):
    _should_track_driver_status starts True; _driver_id stays None (the provider
    only sets it on the tracking path, which we disable); submit(application)
    calls _process_spark_submit_log then returns None."""
    hook = MagicMock()
    hook._should_track_driver_status = True
    hook._driver_id = driver_id  # None by default — simulates #880.
    hook._process_spark_submit_log = MagicMock()

    lines = log_lines if log_lines is not None else _LOG_LINES

    def _submit(app):
        hook._process_spark_submit_log(iter(lines))
        if submit_raises:
            raise submit_raises
        return None

    hook.submit.side_effect = _submit
    return hook


# ─── confirm_driver_status_via_rest ────────────────────────────────────

def test_confirm_returns_payload_when_finished_and_success():
    with patch("urllib.request.urlopen", return_value=_mock_response(_FINISHED)):
        result = confirm_driver_status_via_rest(_DRIVER_ID)
    assert result["driverState"] == "FINISHED"


def test_confirm_raises_when_not_finished():
    with patch("urllib.request.urlopen", return_value=_mock_response({"driverState": "RUNNING", "success": False})):
        with pytest.raises(RuntimeError, match="did not finish"):
            confirm_driver_status_via_rest(_DRIVER_ID)


def test_confirm_raises_when_finished_but_not_success():
    with patch("urllib.request.urlopen", return_value=_mock_response({"driverState": "FINISHED", "success": False})):
        with pytest.raises(RuntimeError, match="did not finish"):
            confirm_driver_status_via_rest(_DRIVER_ID)


def test_confirm_uses_correct_rest_url():
    with patch("urllib.request.urlopen", return_value=_mock_response(_FINISHED)) as m:
        confirm_driver_status_via_rest(_DRIVER_ID, rest_host="spark-master")
    url = m.call_args[0][0]
    assert ":6066/" in url and ":7077" not in url and _DRIVER_ID in url


def test_confirm_propagates_http_errors():
    import urllib.error
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        with pytest.raises(urllib.error.URLError):
            confirm_driver_status_via_rest(_DRIVER_ID)


# ─── _extract_driver_id_from_log ───────────────────────────────────────

def test_extract_finds_driver_id_in_log():
    assert _extract_driver_id_from_log(_LOG_LINES) == _DRIVER_ID


def test_extract_returns_none_for_empty_log():
    assert _extract_driver_id_from_log([]) is None


def test_extract_returns_none_for_log_without_driver_id():
    assert _extract_driver_id_from_log(["some log line", "another line"]) is None


# ─── submit_and_confirm_via_rest (log-capture approach, #880) ──────────

def test_submit_extracts_driver_id_from_captured_log():
    """AC #880: the driver ID is obtained even when _should_track_driver_status
    is disabled (hook._driver_id stays None — the shipped provider only sets it
    on the tracking path). The ID is extracted from the spark-submit log."""
    hook = _make_mock_hook(driver_id=None)  # _driver_id stays None (#880).
    with patch("urllib.request.urlopen", return_value=_mock_response(_FINISHED)):
        result = submit_and_confirm_via_rest(hook, "s3a://jars/app.jar")
    assert result == _DRIVER_ID


def test_submit_disables_poll_before_submitting():
    """AC: the provider's :7077 poll is never invoked."""
    hook = _make_mock_hook()
    with patch("urllib.request.urlopen", return_value=_mock_response(_FINISHED)):
        submit_and_confirm_via_rest(hook, "s3a://jars/app.jar")
    assert hook._should_track_driver_status is False


def test_submit_confirms_via_rest_with_extracted_id():
    """AC: the helper queries :6066 using the extracted ID."""
    hook = _make_mock_hook()
    with patch("urllib.request.urlopen", return_value=_mock_response(_FINISHED)) as m:
        submit_and_confirm_via_rest(hook, "s3a://jars/app.jar")
    url = m.call_args[0][0]
    assert _DRIVER_ID in url and ":6066/" in url


def test_submit_preserves_genuine_errors():
    """AC: a genuine submission failure is not turned into success."""
    hook = _make_mock_hook(submit_raises=RuntimeError("spark-submit exited 1"))
    with pytest.raises(RuntimeError, match="spark-submit exited 1"):
        submit_and_confirm_via_rest(hook, "s3a://jars/app.jar")


def test_submit_raises_when_no_driver_id_in_log():
    """If neither the log nor hook._driver_id yields a driver ID, raise."""
    hook = _make_mock_hook(log_lines=["no driver id here"], driver_id=None)
    with patch("urllib.request.urlopen"):
        with pytest.raises(RuntimeError, match="Could not extract"):
            submit_and_confirm_via_rest(hook, "s3a://jars/app.jar")


def test_submit_falls_back_to_hook_driver_id_if_log_misses():
    """If the regex misses but hook._driver_id is set (e.g., on the tracking
    path), use it as a fallback."""
    hook = _make_mock_hook(log_lines=["no match"], driver_id="driver-fallback-0000")
    with patch("urllib.request.urlopen", return_value=_mock_response(_FINISHED)) as m:
        result = submit_and_confirm_via_rest(hook, "s3a://jars/app.jar")
    assert result == "driver-fallback-0000"
    assert "driver-fallback-0000" in m.call_args[0][0]


def test_rest_confirming_hook_routes_submit_and_kill(monkeypatch):
    assert RestConfirmingSparkHook is not None
    inner = MagicMock()
    monkeypatch.setattr(
        _mod,
        "submit_and_confirm_via_rest",
        lambda hook, application, *, rest_host: (
            hook,
            application,
            rest_host,
        ),
    )
    wrapped = RestConfirmingSparkHook(inner, rest_host="spark-master")

    assert wrapped.submit("s3a://jars/app.jar") == (
        inner,
        "s3a://jars/app.jar",
        "spark-master",
    )
    wrapped.on_kill()
    inner.on_kill.assert_called_once_with()


def test_lakehouse_dag_uses_rest_confirming_operator():
    dag_source = (
        Path(__file__).resolve().parents[2]
        / "services/airflow/dags/lakehouse_spark_submit_smoke.py"
    ).read_text(encoding="utf-8")

    assert "class AtlasSparkSubmitOperator(SparkSubmitOperator):" in dag_source
    assert "submit_lakehouse_job = AtlasSparkSubmitOperator(" in dag_source
