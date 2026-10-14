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


def test_log_lines_stream_to_the_provider_and_driver_id_is_recorded_early():
    seen_by_provider = []
    hook = _make_mock_hook(["a", "Driver successfully submitted as driver-20260730220946-0007", "b"])
    hook._process_spark_submit_log = MagicMock(side_effect=lambda lines: seen_by_provider.extend(lines))
    with patch("urllib.request.urlopen", return_value=_mock_response({"driverState": "FINISHED", "success": True})):
        assert submit_and_confirm_via_rest(hook, "app.jar") == "driver-20260730220946-0007"
    assert seen_by_provider[1].endswith("driver-20260730220946-0007")
    assert hook.__dict__["_atlas_driver_id"] == "driver-20260730220946-0007"


def test_on_kill_kills_the_cluster_driver_then_spark_submit():
    inner = MagicMock()
    inner.__dict__["_atlas_driver_id"] = "driver-1-2"
    wrapped = RestConfirmingSparkHook(inner)
    with patch("urllib.request.urlopen", return_value=_mock_response({})) as urlopen:
        wrapped.on_kill()
    request = urlopen.call_args.args[0]
    assert request.full_url == "http://spark-master:6066/v1/submissions/kill/driver-1-2"
    assert request.get_method() == "POST"
    inner.on_kill.assert_called_once_with()


@pytest.mark.parametrize("case", [
    ("submit", False, False), ("status", False, False),
    ("status", True, False), ("status", False, True),
])
def test_wrapped_submit_failure_cleans_up_and_preserves_original_error(case, monkeypatch):
    stage, remote_error, local_error = case
    original = RuntimeError("owned primary failure")
    hook = _make_mock_hook(submit_raises=original if stage == "submit" else None)
    processor = hook._process_spark_submit_log
    confirm = MagicMock(side_effect=original)
    kill = MagicMock(side_effect=OSError("owned kill unavailable") if remote_error else None)
    monkeypatch.setattr(_mod, "confirm_driver_status_via_rest", confirm)
    monkeypatch.setattr(_mod, "kill_driver_via_rest", kill)
    if local_error:
        hook.on_kill.side_effect = OSError("owned local cleanup unavailable")
    wrapped = RestConfirmingSparkHook(hook, rest_host="owned-spark")
    with pytest.raises(RuntimeError) as raised:
        wrapped.submit("owned.jar")
    assert raised.value is original
    kill.assert_called_once_with(_DRIVER_ID, rest_host="owned-spark")
    hook.on_kill.assert_called_once_with()
    assert hook._process_spark_submit_log is processor


def test_wrapped_success_keeps_driver_and_streamed_logs(monkeypatch):
    hook = _make_mock_hook()
    streamed = []
    hook._process_spark_submit_log.side_effect = lambda lines: streamed.extend(lines)
    monkeypatch.setattr(_mod, "confirm_driver_status_via_rest", lambda *args, **kwargs: _FINISHED)
    kill = MagicMock()
    monkeypatch.setattr(_mod, "kill_driver_via_rest", kill)
    result = RestConfirmingSparkHook(hook).submit("owned.jar")
    assert (result, streamed) == (_DRIVER_ID, _LOG_LINES)
    kill.assert_not_called()
    hook.on_kill.assert_not_called()


def test_reused_wrapper_failure_does_not_kill_previous_submission(monkeypatch):
    hook = _make_mock_hook()
    monkeypatch.setattr(_mod, "confirm_driver_status_via_rest", lambda *args, **kwargs: _FINISHED)
    kill = MagicMock()
    monkeypatch.setattr(_mod, "kill_driver_via_rest", kill)
    wrapped = RestConfirmingSparkHook(hook)
    assert wrapped.submit("first.jar") == _DRIVER_ID

    def fail_before_driver(application):
        hook._process_spark_submit_log(iter(["failed before driver registration"]))
        raise RuntimeError("owned pre-driver failure")

    hook.submit.side_effect = fail_before_driver
    with pytest.raises(RuntimeError, match="owned pre-driver failure"):
        wrapped.submit("second.jar")
    kill.assert_not_called()
    hook.on_kill.assert_called_once_with()


def test_fallback_driver_is_cleaned_up_after_status_failure(monkeypatch):
    hook = _make_mock_hook(log_lines=["no logged driver"], driver_id="driver-fallback-0000")
    monkeypatch.setattr(_mod, "confirm_driver_status_via_rest", MagicMock(side_effect=RuntimeError("owned status failure")))
    kill = MagicMock()
    monkeypatch.setattr(_mod, "kill_driver_via_rest", kill)
    with pytest.raises(RuntimeError, match="owned status failure"):
        RestConfirmingSparkHook(hook).submit("owned.jar")
    kill.assert_called_once_with("driver-fallback-0000", rest_host="spark-master")
    hook.on_kill.assert_called_once_with()


_EMR_PATH = _MOD_PATH.parents[2] / "spark" / "examples" / "emr_serverless_connect.py"


def _emr_example(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    client, spark, builder = MagicMock(), MagicMock(), MagicMock()
    client.start_session.return_value = {"sessionId": "owned-session"}
    client.get_session.return_value = {"session": {"state": "STARTED"}}
    client.get_session_endpoint.return_value = {
        "endpoint": "https://owned.invalid", "authToken": "OwnedSyntheticToken",
    }
    builder.remote.return_value.getOrCreate.return_value = spark
    boto = ModuleType("boto3")
    boto.client = MagicMock(return_value=client)
    pyspark, sql = ModuleType("pyspark"), ModuleType("pyspark.sql")
    pyspark.__path__ = []
    sql.SparkSession = SimpleNamespace(builder=builder)
    for name, module in [("boto3", boto), ("pyspark", pyspark), ("pyspark.sql", sql)]:
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location("owned_emr_example", _EMR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, client, spark, builder


@pytest.mark.parametrize("stage", ["state", "timeout", "endpoint", "connect", "interrupt"])
def test_emr_setup_failure_terminates_only_its_created_session(stage, monkeypatch):
    from types import SimpleNamespace

    module, client, _, builder = _emr_example(monkeypatch)
    original = KeyboardInterrupt() if stage == "interrupt" else RuntimeError("owned setup failure")
    if stage in ("state", "interrupt"):
        client.get_session.side_effect = original
    elif stage == "endpoint":
        client.get_session_endpoint.side_effect = original
    elif stage == "connect":
        builder.remote.return_value.getOrCreate.side_effect = original
    else:
        client.get_session.return_value = {"session": {"state": "STARTING"}}
        monkeypatch.setattr(module, "time", SimpleNamespace(
            monotonic=MagicMock(side_effect=[0, 601]), sleep=MagicMock(),
        ))
    with pytest.raises(TimeoutError if stage == "timeout" else type(original)) as raised:
        module.emr_serverless_session("owned-app", "owned-role", "owned-region")
    client.terminate_session.assert_called_once_with(applicationId="owned-app", sessionId="owned-session")
    if stage != "timeout":
        assert raised.value is original


def test_emr_cleanup_failure_preserves_setup_error(monkeypatch):
    module, client, _, _ = _emr_example(monkeypatch)
    original = RuntimeError("owned primary failure")
    client.get_session_endpoint.side_effect = original
    client.terminate_session.side_effect = OSError("owned termination unavailable")
    with pytest.raises(RuntimeError) as raised:
        module.emr_serverless_session("owned-app", "owned-role", "owned-region")
    assert raised.value is original
    client.terminate_session.assert_called_once_with(applicationId="owned-app", sessionId="owned-session")


def test_emr_success_transfers_handles_and_keeps_endpoint_contract(monkeypatch):
    module, client, spark, builder = _emr_example(monkeypatch)
    result = module.emr_serverless_session("owned-app", "owned-role", "owned-region")
    assert result == (spark, client, "owned-session")
    builder.remote.assert_called_once_with("sc://owned.invalid:443/;use_ssl=true;x-aws-proxy-auth=OwnedSyntheticToken")
    client.terminate_session.assert_not_called()


def test_emr_start_failure_has_no_owned_session_to_terminate(monkeypatch):
    module, client, _, _ = _emr_example(monkeypatch)
    client.start_session.side_effect = RuntimeError("owned start rejected")
    with pytest.raises(RuntimeError, match="owned start rejected"):
        module.emr_serverless_session("owned-app", "owned-role", "owned-region")
    client.terminate_session.assert_not_called()


def test_emr_script_terminates_when_client_stop_fails(monkeypatch):
    import runpy

    _, client, spark, _ = _emr_example(monkeypatch)
    monkeypatch.setenv("EMR_APPLICATION_ID", "owned-app")
    monkeypatch.setenv("EMR_EXECUTION_ROLE_ARN", "owned-role")
    spark.version = "owned-version"
    spark.stop.side_effect = RuntimeError("owned client stop failed")
    with pytest.raises(RuntimeError, match="owned client stop failed"):
        runpy.run_path(str(_EMR_PATH), run_name="__main__")
    client.terminate_session.assert_called_once_with(applicationId="owned-app", sessionId="owned-session")


@pytest.fixture
def daily_spark_smoke(monkeypatch):
    """Load the complete daily DAG with synthetic operator and Spark modules."""
    import sys
    import types

    providers = {
        "airflow": {"DAG": MagicMock()},
        "airflow.providers.amazon.aws.hooks.s3": {"S3Hook": MagicMock()},
        "airflow.providers.openai.hooks.openai": {"OpenAIHook": MagicMock()},
        "airflow.providers.common.sql.operators.sql": {
            "SQLExecuteQueryOperator": MagicMock()
        },
        "airflow.providers.standard.operators.python": {
            "PythonOperator": MagicMock()
        },
    }
    for name, attributes in providers.items():
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)

    builder = MagicMock()
    builder.remote.return_value = builder
    builder.appName.return_value = builder
    pyspark = types.ModuleType("pyspark")
    sql = types.ModuleType("pyspark.sql")
    sql.SparkSession = types.SimpleNamespace(builder=builder)
    monkeypatch.setitem(sys.modules, "pyspark", pyspark)
    monkeypatch.setitem(sys.modules, "pyspark.sql", sql)
    path = _MOD_PATH.with_name("example_etl_with_llm.py")
    spec = importlib.util.spec_from_file_location("atlas_daily_spark_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, builder


@pytest.mark.parametrize("failure", ["version-error", "version-cancel", "output-error"])
def test_daily_spark_smoke_stops_owned_session_after_failure(
    daily_spark_smoke, monkeypatch, failure
):
    module, builder = daily_spark_smoke
    original = {
        "version-error": RuntimeError("synthetic version RPC failed"),
        "version-cancel": KeyboardInterrupt("synthetic cancellation"),
        "output-error": OSError("synthetic output failed"),
    }[failure]

    class Session:
        stop = MagicMock()

        @property
        def version(self):
            if failure.startswith("version-"):
                raise original
            return "4.1.2"

    session = Session()
    builder.getOrCreate.return_value = session
    if failure == "output-error":
        monkeypatch.setattr(module, "print", MagicMock(side_effect=original), raising=False)
    with pytest.raises(type(original)) as raised:
        module.spark_smoke()
    assert raised.value is original
    session.stop.assert_called_once_with()


def test_daily_spark_smoke_preserves_success_output(daily_spark_smoke, capsys):
    module, builder = daily_spark_smoke
    session = MagicMock(version="4.1.2")
    builder.getOrCreate.return_value = session
    assert module.spark_smoke() is None
    builder.remote.assert_called_once_with("sc://spark-connect:15002")
    builder.appName.assert_called_once_with("airflow-smoke")
    assert capsys.readouterr().out == "Spark Connect connected; version=4.1.2\n"
    session.stop.assert_called_once_with()


def test_daily_spark_smoke_creation_error_has_no_owned_session(daily_spark_smoke):
    module, builder = daily_spark_smoke
    original = RuntimeError("synthetic creation failed")
    uncreated_session = MagicMock()
    builder.getOrCreate.return_value = uncreated_session
    builder.getOrCreate.side_effect = original
    with pytest.raises(RuntimeError) as raised:
        module.spark_smoke()
    assert raised.value is original
    uncreated_session.stop.assert_not_called()
