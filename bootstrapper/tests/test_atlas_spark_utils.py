"""Tests for the Atlas Spark standalone driver-status REST confirmation (#792, #876).

Tests two functions:
- ``confirm_driver_status_via_rest()`` — pure-Python REST query (urllib mock).
- ``submit_and_confirm_via_rest()`` — orchestration that disables the hook's
  :7077 poll, submits, extracts ``_driver_id``, and confirms via REST.

The ``submit_and_confirm_via_rest()`` tests use a mock hook that faithfully
represents the shipped SparkSubmitHook API (#876 live evidence):
- ``__init__`` does NOT accept ``application``.
- ``submit(application)`` takes the JAR path and returns None.
- ``_driver_id`` is set on the hook during submit.
- ``_should_track_driver_status`` is an attribute (overridable to False).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Load the utility by file path (it lives in services/airflow/dags/, not bootstrapper/).
_MOD_PATH = (
    Path(__file__).resolve().parents[2]
    / "services" / "airflow" / "dags" / "atlas_spark_utils.py"
)
_spec = importlib.util.spec_from_file_location("atlas_spark_utils", _MOD_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
confirm_driver_status_via_rest = _mod.confirm_driver_status_via_rest
submit_and_confirm_via_rest = _mod.submit_and_confirm_via_rest


# ─── helpers ───────────────────────────────────────────────────────────

def _mock_response(payload: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(payload).encode("utf-8")
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    return mock


_DRIVER_ID = "driver-202607301200-0001"


def _make_mock_hook(driver_id: str | None = _DRIVER_ID, submit_raises: Exception | None = None):
    """Build a mock SparkSubmitHook matching the shipped API (#876):
    _should_track_driver_status starts True; submit(application) sets
    _driver_id (or raises); submit returns None."""
    hook = MagicMock()
    hook._should_track_driver_status = True
    hook._driver_id = None

    def _submit(app):
        hook._driver_id = driver_id
        if submit_raises:
            raise submit_raises
        return None

    hook.submit.side_effect = _submit
    return hook


_FINISHED = {"driverState": "FINISHED", "success": True}


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


# ─── submit_and_confirm_via_rest (#876 corrected API) ──────────────────

def test_submit_disables_poll_before_submitting():
    """AC #876: prove the pattern does not call _start_driver_status_tracking()."""
    hook = _make_mock_hook()
    with patch("urllib.request.urlopen", return_value=_mock_response(_FINISHED)):
        submit_and_confirm_via_rest(hook, "s3a://jars/app.jar")
    assert hook._should_track_driver_status is False
    hook.submit.assert_called_once_with("s3a://jars/app.jar")


def test_submit_passes_application_to_submit_not_init():
    """AC #876: validate the documented call signature."""
    hook = _make_mock_hook()
    with patch("urllib.request.urlopen", return_value=_mock_response(_FINISHED)):
        submit_and_confirm_via_rest(hook, "s3a://jars/app.jar")
    hook.submit.assert_called_once_with("s3a://jars/app.jar")


def test_submit_extracts_driver_id_and_confirms():
    """AC #876: expose the submitted driver ID and confirm FINISHED+success."""
    hook = _make_mock_hook(driver_id="driver-abc-123")
    with patch("urllib.request.urlopen", return_value=_mock_response(_FINISHED)) as m:
        result = submit_and_confirm_via_rest(hook, "s3a://jars/app.jar")
    assert result == "driver-abc-123"
    assert "driver-abc-123" in m.call_args[0][0]


def test_submit_preserves_genuine_errors():
    """AC #876: preserve genuine submission failures."""
    hook = _make_mock_hook(submit_raises=RuntimeError("spark-submit exited 1"))
    with pytest.raises(RuntimeError, match="spark-submit exited 1"):
        submit_and_confirm_via_rest(hook, "s3a://jars/app.jar")


def test_submit_raises_when_no_driver_id():
    """If submit() set no driver_id (submission failed before launch), raise."""
    hook = _make_mock_hook(driver_id=None)
    with pytest.raises(RuntimeError, match="did not set a driver_id"):
        submit_and_confirm_via_rest(hook, "s3a://jars/app.jar")
