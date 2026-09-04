from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from tests.seed_harness import (
    begin_reconciliation_after_interruption,
    cleanup_deadline_expired,
    defer_cleanup_failures,
    establish_cleanup_deadline,
    raise_deferred_cleanup_error,
    sleep_for_cleanup,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_CONFIG = REPO_ROOT / "services" / "otel-collector" / "config" / "config.yaml"
LOKI_CONFIG = REPO_ROOT / "services" / "loki" / "config" / "loki.yaml"
COLLECTOR_IMAGE = "otel/opentelemetry-collector-contrib:0.154.0"
LOKI_IMAGE = "grafana/loki:3.7.0"
GRAFANA_IMAGE = "grafana/grafana:11.4.3"
OTEL_SMOKE_OWNER_LABEL = "com.atlas.otel-smoke-token"
DOCKER_RECONCILE_SECONDS = 60
GRAFANA_DATASOURCE_CONFIG = (
    REPO_ROOT
    / "services"
    / "grafana"
    / "config"
    / "provisioning"
    / "datasources"
    / "tempo-loki.yml"
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("ATLAS_RUN_DOCKER_OTEL_SMOKE") != "1",
        reason="set ATLAS_RUN_DOCKER_OTEL_SMOKE=1 for the disposable Docker proof",
    ),
]


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _add_exception_note(exc: BaseException, note: str) -> None:
    if hasattr(exc, "add_note"):
        exc.add_note(note)
        return
    notes = getattr(exc, "__notes__", None)
    if notes is None:
        notes = []
        exc.__notes__ = notes
    notes.append(note)


def _inspect_owned_resource(kind: str, name: str) -> dict | None:
    inspected = _docker(kind, "inspect", name, check=False)
    if inspected.returncode == 0:
        records = json.loads(inspected.stdout)
        assert len(records) == 1 and isinstance(records[0], dict)
        return records[0]
    if kind == "container":
        listed = _docker(
            "ps", "-a", "--filter", f"name=^/{name}$",
            "--format", "{{.Names}}", check=False,
        )
    elif kind == "volume":
        listed = _docker(
            "volume", "ls", "-q", "--filter", f"name={name}", check=False,
        )
    else:
        listed = _docker(
            "network", "ls", "--filter", f"name=^{name}$",
            "--format", "{{.Name}}", check=False,
        )
    assert listed.returncode == 0
    assert name not in listed.stdout.splitlines()
    return None


def _remove_owned_resource(kind: str, name: str, token: str) -> None:
    record = _inspect_owned_resource(kind, name)
    if record is None:
        return
    labels = record.get("Config", {}).get("Labels") or record.get("Labels") or {}
    actual = record.get("Name", "").lstrip("/")
    assert actual == name
    if labels.get(OTEL_SMOKE_OWNER_LABEL) != token:
        return
    command = (
        ("rm", "-f", name)
        if kind == "container"
        else (kind, "rm", name)
    )
    removed = _docker(*command, check=False)
    assert removed.returncode == 0, removed.stderr or removed.stdout


def _assert_owned_resource_absent(kind: str, name: str, token: str) -> None:
    record = _inspect_owned_resource(kind, name)
    if record is None:
        return
    labels = record.get("Config", {}).get("Labels") or record.get("Labels") or {}
    assert labels.get(OTEL_SMOKE_OWNER_LABEL) != token


def _owned_resource_cleanup_pass(
    resources: tuple[tuple[str, str], ...], token: str,
) -> list[tuple[str, BaseException]]:
    failures: list[tuple[str, BaseException]] = []
    for kind, name in resources:
        try:
            _remove_owned_resource(kind, name, token)
        except BaseException as exc:
            failures.append((f"{kind} removal {name}", exc))
    for kind, name in resources:
        try:
            _assert_owned_resource_absent(kind, name, token)
        except BaseException as exc:
            failures.append((f"{kind} absence {name}", exc))
    return failures


def _cleanup_owned_resources(
    resources: tuple[tuple[str, str], ...], token: str, *, uncertain: bool | None = None,
) -> None:
    primary = sys.exc_info()[1]
    deferred_error = primary
    if uncertain is None:
        uncertain = primary is not None
    settle_until, deferred_error = establish_cleanup_deadline(
        DOCKER_RECONCILE_SECONDS if uncertain else None, deferred_error
    )
    while True:
        failures = _owned_resource_cleanup_pass(resources, token)
        deferred_error = defer_cleanup_failures(deferred_error, failures)
        settle_until, deferred_error = begin_reconciliation_after_interruption(
            settle_until,
            DOCKER_RECONCILE_SECONDS,
            deferred_error,
            failures,
        )
        expired, deferred_error = cleanup_deadline_expired(
            settle_until, deferred_error
        )
        if expired:
            if not failures:
                raise_deferred_cleanup_error(primary, deferred_error)
                return
            detail = "; ".join(
                f"{operation}: {type(exc).__name__}: {exc}"
                for operation, exc in failures
            )
            note = f"OTLP/Loki fixture cleanup could not be proven: {detail}"
            if deferred_error is not None:
                _add_exception_note(deferred_error, note)
                raise_deferred_cleanup_error(primary, deferred_error)
                return
            _add_exception_note(failures[0][1], note)
            raise failures[0][1]
        deferred_error = sleep_for_cleanup(0.1, deferred_error)


def _add_docker_diagnostics(
    primary: BaseException, names: tuple[str, ...],
) -> None:
    diagnostics: list[str] = []
    for name in names:
        try:
            logs = _docker("logs", name, check=False).stdout
            diagnostics.append(f"--- {name} ---\n{logs[-5000:]}")
        except BaseException as exc:
            diagnostics.append(
                f"--- {name} ---\nDocker diagnostics unavailable: "
                f"{type(exc).__name__}: {exc}"
            )
    _add_exception_note(primary, "\n".join(diagnostics))


def _free_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def _post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json_with_basic_auth(url: str) -> dict:
    token = base64.b64encode(b"admin:admin").decode("ascii")
    request = Request(url, headers={"Authorization": f"Basic {token}"})
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_until(description: str, probe, *, timeout: float = 45.0):
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = probe()
            if value:
                return value
        except (OSError, URLError, ValueError, KeyError) as exc:
            last_error = exc
        time.sleep(0.25)
    pytest.fail(f"timed out waiting for {description}: {last_error}")


def _query_url(port: int, query: str) -> str:
    now_ns = time.time_ns()
    params = urlencode(
        {
            "query": query,
            "start": str(now_ns - 120_000_000_000),
            "end": str(now_ns + 30_000_000_000),
            "limit": "100",
            "direction": "backward",
        }
    )
    return f"http://127.0.0.1:{port}/loki/api/v1/query_range?{params}"


def _has_result(response: dict) -> bool:
    return bool(response.get("data", {}).get("result"))


def _query_if_present(port: int, query: str) -> dict | None:
    response = _get_json(_query_url(port, query))
    return response if _has_result(response) else None


def _accepted(response: dict) -> bool:
    partial = response.get("partialSuccess", {})
    return partial.get("rejectedLogRecords", 0) in (0, "0", None)


def _start_loki(
    *, owned: tuple[str, str], network: str, port: int, volume: str,
) -> None:
    name, owner_token = owned
    _docker(
        "run",
        "-d",
        "--name",
        name,
        "--label",
        f"{OTEL_SMOKE_OWNER_LABEL}={owner_token}",
        "--network",
        network,
        "--network-alias",
        "loki",
        "-p",
        f"127.0.0.1:{port}:3100",
        "-e",
        "LOKI_RETENTION_PERIOD=24h",
        "-v",
        f"{LOKI_CONFIG}:/etc/loki/loki.yaml:ro",
        "-v",
        f"{volume}:/loki",
        LOKI_IMAGE,
        "-config.file=/etc/loki/loki.yaml",
        "-config.expand-env=true",
    )


def _wait_for_loki(port: int) -> None:
    _wait_until(
        "Loki readiness",
        lambda: urlopen(f"http://127.0.0.1:{port}/ready", timeout=3).read()
        == b"ready\n",
    )


def test_otlp_log_survives_loki_outage_with_correlation_and_redaction() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI unavailable")

    owner_token = uuid4().hex
    suffix = owner_token[:10]
    network = f"atlas-task19-{suffix}"
    collector = f"atlas-task19-collector-{suffix}"
    loki = f"atlas-task19-loki-{suffix}"
    loki_volume = f"atlas-task19-loki-data-{suffix}"
    collector_port = _free_port()
    loki_port = _free_port()

    service = f"atlas-task19-smoke-{suffix}"
    trace_id = "0123456789abcdef0123456789abcdef"
    span_id = "0123456789abcdef"
    bearer_secret = f"BEARER_SECRET_{suffix}"
    basic_secret = f"BASIC_SECRET_{suffix}"
    password_secret = f"PASSWORD_SECRET_{suffix}"
    json_bearer_secret = f"JSON_BEARER_SECRET_{suffix}"
    json_password_secret = f"JSON_PASSWORD_SECRET_{suffix}"
    single_quote_secret = f"SINGLE_QUOTE_SECRET_{suffix}"
    query_token_secret = f"QUERY_TOKEN_SECRET_{suffix}"
    underscore_key_secret = f"UNDERSCORE_KEY_SECRET_{suffix}"
    hyphen_key_secret = f"HYPHEN_KEY_SECRET_{suffix}"
    bracket_secret = f"BRACKET_SECRET_{suffix}"
    log_secret = f"LOG_SECRET_{suffix}"
    resource_secret = f"RESOURCE_SECRET_{suffix}"
    mixed_log_secret = f"MIXED_LOG_SECRET_{suffix}"
    mixed_resource_secret = f"MIXED_RESOURCE_SECRET_{suffix}"
    nonsecret_text = f"SECRETIVE_SETTING_{suffix}"
    suffix_authorization_text = f"XAUTHORIZATION_SETTING_{suffix}"
    suffix_token_text = f"NOT_TOKEN_SETTING_{suffix}"
    resources = (
        ("container", collector),
        ("container", loki),
        ("volume", loki_volume),
        ("network", network),
    )
    payload = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": service}},
                        {
                            "key": "authorization",
                            "value": {"stringValue": resource_secret},
                        },
                        {
                            "key": "Api-Key",
                            "value": {"stringValue": mixed_resource_secret},
                        },
                    ]
                },
                "scopeLogs": [
                    {
                        "scope": {"name": "atlas.task19.smoke"},
                        "logRecords": [
                            {
                                "timeUnixNano": str(time.time_ns()),
                                "traceId": trace_id,
                                "spanId": span_id,
                                "severityText": "INFO",
                                "body": {
                                    "stringValue": (
                                        f"Authorization: Bearer {bearer_secret}; "
                                        f"Proxy-Authorization: Basic {basic_secret}; "
                                        f"password: {password_secret}; "
                                        f'{{"authorization":"Bearer {json_bearer_secret}",'
                                        f'"password":"{json_password_secret}"}}; '
                                        f"['api_key':'{single_quote_secret}']; "
                                        f'?token="{query_token_secret}"&'
                                        f'api_key="{underscore_key_secret}"; '
                                        f"client-secret:'{hyphen_key_secret}'; "
                                        f"[password:{bracket_secret}]; "
                                        f"xauthorization: Bearer {suffix_authorization_text}; "
                                        f"not_token={suffix_token_text}; "
                                        f"secretive={nonsecret_text}; durable-smoke"
                                    )
                                },
                                "attributes": [
                                    {
                                        "key": "token",
                                        "value": {"stringValue": log_secret},
                                    },
                                    {
                                        "key": "Authorization",
                                        "value": {"stringValue": mixed_log_secret},
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }

    try:
        _docker(
            "network", "create", "--label",
            f"{OTEL_SMOKE_OWNER_LABEL}={owner_token}", network,
        )
        _docker(
            "volume", "create", "--label",
            f"{OTEL_SMOKE_OWNER_LABEL}={owner_token}", loki_volume,
        )
        _docker(
            "run",
            "-d",
            "--name",
            collector,
            "--label",
            f"{OTEL_SMOKE_OWNER_LABEL}={owner_token}",
            "--network",
            network,
            "--network-alias",
            "otel-collector",
            "-p",
            f"127.0.0.1:{collector_port}:4318",
            "-v",
            f"{COLLECTOR_CONFIG}:/etc/otelcol/config.yaml:ro",
            COLLECTOR_IMAGE,
            "--config=/etc/otelcol/config.yaml",
        )

        _wait_until(
            "Collector OTLP receiver",
            lambda: _accepted(
                _post_json(
                    f"http://127.0.0.1:{collector_port}/v1/logs",
                    {"resourceLogs": []},
                )
            ),
        )

        oversized = Request(
            f"http://127.0.0.1:{collector_port}/v1/logs",
            data=b"x" * 4_194_305,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as too_large:
            urlopen(oversized, timeout=5)
        assert too_large.value.code == 400
        assert "request body too large" in too_large.value.read().decode("utf-8")

        # Loki is deliberately absent here. A successful OTLP response proves
        # the event entered the Collector's bounded live retry queue.
        assert _accepted(
            _post_json(f"http://127.0.0.1:{collector_port}/v1/logs", payload)
        )
        time.sleep(1.0)

        _start_loki(
            owned=(loki, owner_token),
            network=network,
            port=loki_port,
            volume=loki_volume,
        )
        _wait_for_loki(loki_port)

        service_query = f'{{service_name="{service}"}}'
        response = _wait_until(
            "queued OTLP log in Loki",
            lambda: _query_if_present(loki_port, service_query),
        )
        encoded = json.dumps(response, sort_keys=True)
        assert "durable-smoke" in encoded
        assert trace_id in encoded
        assert span_id in encoded
        assert encoded.count("[REDACTED]") >= 3
        assert nonsecret_text in encoded
        secrets = (
            bearer_secret,
            basic_secret,
            password_secret,
            json_bearer_secret,
            json_password_secret,
            single_quote_secret,
            query_token_secret,
            underscore_key_secret,
            hyphen_key_secret,
            bracket_secret,
            log_secret,
            resource_secret,
            mixed_log_secret,
            mixed_resource_secret,
        )
        for secret in secrets:
            assert secret not in encoded
        assert "[password:[REDACTED]]" in encoded
        for nonsecret in (
            nonsecret_text,
            suffix_authorization_text,
            suffix_token_text,
        ):
            assert nonsecret in encoded

        correlated = _wait_until(
            "trace-correlated LogQL result",
            lambda: _query_if_present(
                loki_port,
                f'{service_query} | trace_id = "{trace_id}"',
            ),
        )
        assert _has_result(correlated)

        # Loki is the durable boundary. Remove and recreate only its container,
        # retaining the named volume, then prove the accepted record survives.
        first_loki_logs = _docker("logs", loki).stdout
        for secret in secrets:
            assert secret not in first_loki_logs
        _remove_owned_resource("container", loki, owner_token)
        _assert_owned_resource_absent("container", loki, owner_token)
        _start_loki(
            owned=(loki, owner_token),
            network=network,
            port=loki_port,
            volume=loki_volume,
        )
        _wait_for_loki(loki_port)
        persisted = _wait_until(
            "accepted OTLP log after Loki restart",
            lambda: _query_if_present(loki_port, service_query),
        )
        persisted_text = json.dumps(persisted, sort_keys=True)
        assert trace_id in persisted_text
        assert span_id in persisted_text
        for nonsecret in (
            nonsecret_text,
            suffix_authorization_text,
            suffix_token_text,
        ):
            assert nonsecret in persisted_text
        assert persisted_text.count("[REDACTED]") >= 3
        assert "[password:[REDACTED]]" in persisted_text
        for secret in secrets:
            assert secret not in persisted_text

        collector_logs = _docker("logs", collector).stdout
        restarted_loki_logs = _docker("logs", loki).stdout
        for secret in secrets:
            assert secret not in collector_logs
            assert secret not in first_loki_logs
            assert secret not in restarted_loki_logs
        assert '"otlphttp" alias is deprecated' not in collector_logs
        assert "paths were modified to include their context prefix" not in collector_logs
    except Exception as exc:
        _add_docker_diagnostics(exc, (collector, loki))
        raise
    finally:
        _cleanup_owned_resources(resources, owner_token)


def test_grafana_provisions_label_based_trace_link() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI unavailable")

    owner_token = uuid4().hex
    suffix = owner_token[:10]
    grafana = f"atlas-task19-grafana-{suffix}"
    grafana_port = _free_port()
    try:
        _docker(
            "run",
            "-d",
            "--name",
            grafana,
            "--label",
            f"{OTEL_SMOKE_OWNER_LABEL}={owner_token}",
            "-p",
            f"127.0.0.1:{grafana_port}:3000",
            "-e",
            "GF_SECURITY_ADMIN_USER=admin",
            "-e",
            "GF_SECURITY_ADMIN_PASSWORD=admin",
            "-e",
            "TEMPO_ENDPOINT=http://tempo:3200",
            "-e",
            "LOKI_ENDPOINT=http://loki:3100",
            "-v",
            f"{GRAFANA_DATASOURCE_CONFIG}:/etc/grafana/provisioning/datasources/atlas.yml:ro",
            GRAFANA_IMAGE,
        )
        datasource = _wait_until(
            "Grafana Loki datasource provisioning",
            lambda: _get_json_with_basic_auth(
                f"http://127.0.0.1:{grafana_port}/api/datasources/uid/Loki"
            ),
        )
        assert datasource["jsonData"]["derivedFields"] == [
            {
                "datasourceUid": "Tempo",
                "matcherType": "label",
                "matcherRegex": "trace_id",
                "name": "TraceID",
                # Grafana consumes the provisioning escape and persists the
                # expression with one dollar sign.
                "url": "${__value.raw}",
            }
        ]
    except Exception as exc:
        _add_docker_diagnostics(exc, (grafana,))
        raise
    finally:
        _cleanup_owned_resources((("container", grafana),), owner_token)
