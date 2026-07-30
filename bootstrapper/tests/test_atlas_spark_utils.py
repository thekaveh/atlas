"""Tests for the Atlas Spark standalone driver-status REST confirmation (#792).

Tests ``confirm_driver_status_via_rest()`` — the utility that bypasses the
SparkSubmitOperator's broken :7077 RPC poll by querying the master's :6066 REST
endpoint directly. No Airflow import needed; the function is pure Python
(``urllib``) so it can be fully tested with a mock.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# The utility lives in services/airflow/dags/ (not under bootstrapper/), so
# load it by file path rather than a package import.
_MOD_PATH = (
    Path(__file__).resolve().parents[2]
    / "services" / "airflow" / "dags" / "atlas_spark_utils.py"
)
_spec = importlib.util.spec_from_file_location("atlas_spark_utils", _MOD_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
confirm_driver_status_via_rest = _mod.confirm_driver_status_via_rest


def _mock_response(payload: dict):
    """Build a mock urllib response that yields the given JSON payload."""
    mock = MagicMock()
    mock.read.return_value = json.dumps(payload).encode("utf-8")
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    return mock


_DRIVER_ID = "driver-202607291200-0001"


def test_confirm_returns_payload_when_finished_and_success():
    """FINISHED + success → returns the payload (no exception)."""
    payload = {"driverState": "FINISHED", "success": True}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        result = confirm_driver_status_via_rest(_DRIVER_ID)
    assert result["driverState"] == "FINISHED"
    assert result["success"] is True


def test_confirm_raises_when_not_finished():
    """RUNNING → RuntimeError (real failure, not the poll false-negative)."""
    payload = {"driverState": "RUNNING", "success": False}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        with pytest.raises(RuntimeError, match="did not finish successfully"):
            confirm_driver_status_via_rest(_DRIVER_ID)


def test_confirm_raises_when_finished_but_not_success():
    """FINISHED + success=False → RuntimeError (the driver failed)."""
    payload = {"driverState": "FINISHED", "success": False}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        with pytest.raises(RuntimeError, match="did not finish successfully"):
            confirm_driver_status_via_rest(_DRIVER_ID)


def test_confirm_raises_when_killed():
    """KILLED → RuntimeError."""
    payload = {"driverState": "KILLED", "success": False}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        with pytest.raises(RuntimeError, match="KILLED"):
            confirm_driver_status_via_rest(_DRIVER_ID)


def test_confirm_uses_correct_rest_url():
    """The function hits :6066 (the REST endpoint), not :7077 (the RPC port)."""
    payload = {"driverState": "FINISHED", "success": True}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)) as mock_urlopen:
        confirm_driver_status_via_rest(_DRIVER_ID, rest_host="spark-master")
    called_url = mock_urlopen.call_args[0][0]
    assert ":6066/" in called_url, f"must use the REST port :6066, got {called_url}"
    assert ":7077" not in called_url, f"must NOT use the RPC port :7077"
    assert _DRIVER_ID in called_url


def test_confirm_propagates_http_errors():
    """If the REST endpoint is unreachable, the urllib error propagates
    (does NOT silently return success)."""
    import urllib.error
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        with pytest.raises(urllib.error.URLError):
            confirm_driver_status_via_rest(_DRIVER_ID)
