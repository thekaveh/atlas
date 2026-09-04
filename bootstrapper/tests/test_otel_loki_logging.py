from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
import yaml

from tests import test_otel_loki_runtime as runtime


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES = REPO_ROOT / "services"


@pytest.mark.parametrize("kind", ("container", "volume", "network"))
def test_otel_smoke_cleanup_preserves_foreign_resource_collisions(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    name = f"foreign-{kind}"
    removals: list[tuple[str, ...]] = []
    ticks = iter((0.0, 61.0))
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    def docker(*args, **_kwargs):
        if args[:2] == (kind, "inspect"):
            labels_key = "Config" if kind == "container" else "Labels"
            labels = {runtime.OTEL_SMOKE_OWNER_LABEL: "foreign"}
            record = {
                "Name": f"/{name}" if kind == "container" else name,
                labels_key: {"Labels": labels} if kind == "container" else labels,
            }
            return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
        if args[:2] in (("rm", "-f"), (kind, "rm")):
            removals.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(runtime, "_docker", docker)
    runtime._cleanup_owned_resources(((kind, name),), "ours", uncertain=True)
    assert removals == []


@pytest.mark.parametrize("kind", ("container", "volume", "network"))
def test_otel_smoke_cleanup_reconciles_each_late_visible_resource_kind(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    name = f"late-{kind}"
    inspections = 0
    removed = False
    removals: list[tuple[str, ...]] = []
    ticks = iter((0.0, 2.0, 61.0))
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    def docker(*args, **_kwargs):
        nonlocal inspections, removed
        if args[:2] == (kind, "inspect"):
            inspections += 1
            if inspections >= 3 and not removed:
                labels = {runtime.OTEL_SMOKE_OWNER_LABEL: "ours"}
                record = {
                    "Name": f"/{name}" if kind == "container" else name,
                    **(
                        {"Config": {"Labels": labels}}
                        if kind == "container"
                        else {"Labels": labels}
                    ),
                }
                return subprocess.CompletedProcess(
                    args, 0, json.dumps([record]), ""
                )
            return subprocess.CompletedProcess(args, 1, "", "not found")
        removal = (
            args[:2] == ("rm", "-f")
            if kind == "container"
            else args[:2] == (kind, "rm")
        )
        if removal:
            removals.append(args)
            removed = True
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(runtime, "_docker", docker)
    runtime._cleanup_owned_resources(((kind, name),), "ours", uncertain=True)
    assert len(removals) == 1


@pytest.mark.parametrize(
    "diagnostic_error",
    (
        subprocess.TimeoutExpired(("docker", "logs"), 60),
        OSError("docker unavailable"),
    ),
)
def test_diagnostic_log_failure_preserves_primary_and_cleanup_note(
    monkeypatch: pytest.MonkeyPatch, diagnostic_error: BaseException,
) -> None:
    primary = RuntimeError("original live-test failure")
    ticks = iter((0.0, 61.0))
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    def docker(*_args, **_kwargs):
        raise diagnostic_error

    monkeypatch.setattr(runtime, "_docker", docker)
    with pytest.raises(RuntimeError) as caught:
        try:
            try:
                raise primary
            except RuntimeError as exc:
                runtime._add_docker_diagnostics(exc, ("collector", "loki"))
                raise
        finally:
            runtime._cleanup_owned_resources(
                (("container", "collector"),), "ours", uncertain=True
            )

    assert caught.value is primary
    notes = "\n".join(primary.__notes__)
    assert "Docker diagnostics unavailable" in notes
    assert "cleanup could not be proven" in notes


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_logs_pipeline_uses_native_loki_otlp_after_redaction() -> None:
    config = _yaml(SERVICES / "otel-collector" / "config" / "config.yaml")

    assert "debug" not in config["exporters"]
    assert config["exporters"]["otlp_http/loki"]["endpoint"] == (
        "http://loki:3100/otlp"
    )
    assert config["service"]["pipelines"]["logs"] == {
        "receivers": ["otlp"],
        "processors": ["memory_limiter", "transform/logs", "batch/logs"],
        "exporters": ["otlp_http/loki"],
    }
    protocols = config["receivers"]["otlp"]["protocols"]
    assert protocols["grpc"]["max_recv_msg_size_mib"] == 4
    assert protocols["http"]["max_request_body_size"] == 4_194_304


def test_loki_export_queue_is_bounded_in_memory_and_retried() -> None:
    config = _yaml(SERVICES / "otel-collector" / "config" / "config.yaml")
    exporter = config["exporters"]["otlp_http/loki"]
    queue = exporter["sending_queue"]

    assert queue["enabled"] is True
    assert "storage" not in queue
    assert queue["sizer"] == "requests"
    assert queue["num_consumers"] == 2
    assert queue["queue_size"] == 512
    assert queue["block_on_overflow"] is True
    assert exporter["retry_on_failure"] == {
        "enabled": True,
        "initial_interval": "1s",
        "max_interval": "30s",
        "max_elapsed_time": "0s",
    }

    batch = config["processors"]["batch/logs"]
    assert batch["timeout"] == "5s"
    assert 1 <= batch["send_batch_size"] <= batch["send_batch_max_size"] <= 1024

    assert config["service"]["extensions"] == ["health_check"]


def test_log_redaction_has_an_exact_attribute_and_body_scope() -> None:
    config = _yaml(SERVICES / "otel-collector" / "config" / "config.yaml")
    transform = config["processors"]["transform/logs"]

    assert transform["error_mode"] == "propagate"
    groups = {group["context"]: group["statements"] for group in transform["log_statements"]}
    assert set(groups) == {"log"}

    statements = "\n".join(groups["log"])
    for context in ("log", "resource"):
        assert f"delete_matching_keys({context}.attributes" in statements
    assert statements.count('delete_matching_keys(') == 2
    assert "(?i)^" in statements
    assert "authorization|proxy-authorization" in statements
    assert "api[_-]key|x-api-key|token" in statements

    log_statements = "\n".join(groups["log"])
    assert log_statements.count("replace_pattern(log.body") == 2
    assert "authorization|proxy-authorization" in log_statements
    assert "api[_-]key|x-api-key|token" in log_statements
    assert "where IsString(log.body)" in log_statements
    for body_statement in groups["log"][-2:]:
        value_terminator = body_statement.rsplit("[^", maxsplit=1)[1]
        assert r"\\]" in value_terminator


def test_collector_compose_waits_for_loki_health_without_storage_privilege() -> None:
    compose = _yaml(SERVICES / "otel-collector" / "compose.yml")
    collector = compose["services"]["otel-collector"]

    assert collector["depends_on"]["loki"] == {
        "condition": "service_healthy",
    }
    assert collector["volumes"] == [
        "./config/config.yaml:/etc/otelcol/config.yaml:ro"
    ]
    assert "volumes" not in compose


def test_loki_explicitly_enables_structured_metadata_with_bounded_retention() -> None:
    config = _yaml(SERVICES / "loki" / "config" / "loki.yaml")

    limits = config["limits_config"]
    assert limits["allow_structured_metadata"] is True
    assert limits["retention_period"] == "${LOKI_RETENTION_PERIOD:-24h}"
    assert config["compactor"] == {
        "working_directory": "/loki/retention",
        "compaction_interval": "10m",
        "retention_enabled": True,
        "retention_delete_delay": "2h",
        "delete_request_store": "filesystem",
    }
    assert limits["otlp_config"]["resource_attributes"]["ignore_defaults"] is True
    attribute_rules = limits["otlp_config"]["resource_attributes"][
        "attributes_config"
    ]
    assert attribute_rules == [
        {"action": "index_label", "attributes": ["service.name"]},
        {"action": "structured_metadata", "regex": ".*"},
    ]


def test_grafana_loki_datasource_links_trace_ids_to_tempo() -> None:
    datasource = _yaml(
        SERVICES
        / "grafana"
        / "config"
        / "provisioning"
        / "datasources"
        / "tempo-loki.yml"
    )
    loki = next(item for item in datasource["datasources"] if item["uid"] == "Loki")

    derived = loki["jsonData"]["derivedFields"]
    assert derived == [
        {
            "datasourceUid": "Tempo",
            "matcherType": "label",
            "matcherRegex": "trace_id",
            "name": "TraceID",
            "url": "$${__value.raw}",
        }
    ]


def test_observability_docs_bound_durability_and_redaction_claims() -> None:
    collector = (SERVICES / "otel-collector" / "README.md").read_text(
        encoding="utf-8"
    )
    loki = (SERVICES / "loki" / "README.md").read_text(encoding="utf-8")
    observability = (
        REPO_ROOT / "docs" / "architecture" / "observability-flow.md"
    ).read_text(encoding="utf-8")

    for exact_claim in (
        "bounded and in memory",
        "queued records do not survive a Collector restart",
        "keys case-insensitively match this exact allowlist",
        "does not recursively inspect nested attribute maps",
        "4,194,304 bytes",
        "400 Bad Request",
        "request body too large",
        "Body filtering is best-effort",
        "does not traverse structured nested or non-string bodies",
    ):
        assert exact_claim in collector
    assert "does not automatically scrape container stdout" in loki
    assert "persists accepted logs in Loki" in observability

    master = (
        REPO_ROOT / "docs" / "architecture" / "observability-flow.html"
    ).read_text(encoding="utf-8")
    assert 'data-source="OTel Collector" data-target="Loki"' in master
    assert "redacted logs" in master
