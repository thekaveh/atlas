from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.three_surface_test_utils import surface_text


REPO_ROOT = Path(__file__).resolve().parents[2]
START_PY = REPO_ROOT / "bootstrapper" / "start.py"
LINEAR_STARTUP = REPO_ROOT / "bootstrapper" / "core" / "linear_startup.py"
REUSING_ATLAS = REPO_ROOT / "docs" / "operations" / "reusing-atlas.md"


@pytest.fixture(autouse=True)
def _rendered_model_boundary(monkeypatch):
    """Status-row doubles also own the external Compose configuration boundary."""
    import start as start_module

    monkeypatch.setattr(
        start_module.AtlasStarter,
        "_rendered_compose_services",
        lambda _self: {"backend": {}, "weaviate": {}},
    )


def test_start_cli_declares_detach_no_follow_and_json_options() -> None:
    src = START_PY.read_text(encoding="utf-8")

    assert "@click.option('--detach', '--no-follow'" in src
    assert "is_flag=True" in src
    assert "--no-follow" in src
    assert "@click.option('--json', 'json_output'" in src


def test_linear_detach_path_skips_following_logs() -> None:
    src = LINEAR_STARTUP.read_text(encoding="utf-8")

    detach_pos = src.find("if options.detach:")
    status_pos = src.find("show_detached_status_summary(json_output=True)")
    logs_pos = src.find("starter.show_container_logs()")

    assert detach_pos != -1, "linear start flow must branch on detach"
    assert status_pos != -1, "detach flow must print a terminal status summary"
    assert logs_pos != -1, "interactive/default flow should still follow logs"
    assert detach_pos < status_pos < logs_pos
    assert "show_container_logs()" in src[status_pos:]
    assert "assume_yes = options.detach or options.json_output" in src


def test_docker_start_services_can_wait_for_health(monkeypatch) -> None:
    from core.docker_manager import DockerManager

    manager = DockerManager(str(REPO_ROOT))
    captured: list[list[str]] = []

    def fake_execute(args, use_env_file=True, project_name=None):
        captured.append(args)
        return 0

    monkeypatch.setattr(manager, "execute_compose_command", fake_execute)
    # #504: targeting is fail-open — pin the projection to None here so this
    # test deterministically asserts the historical full-graph argv shape
    # (the targeted argv is covered by test_targeted_compose_up.py).
    monkeypatch.setattr(manager, "enabled_service_targets", lambda: None)
    # #506: neutralize source-drift so the argv is deterministic regardless of
    # this checkout's git HEAD / .atlas-build-state marker (the --build gate is
    # covered by test_source_drift_rebuild.py).
    monkeypatch.setattr(manager, "source_build_args", lambda _targets=None: [])

    assert manager.start_services(detached=True, wait=True, wait_timeout_seconds=123) == 0
    assert captured == [
        ["up", "-d", "--force-recreate", "--wait", "--wait-timeout", "123"]
    ]


def test_detached_json_status_summary_reports_unhealthy_service(monkeypatch, capsys) -> None:
    import start as start_module

    starter = start_module.AtlasStarter()
    monkeypatch.setattr(starter, "_rendered_compose_services", lambda: {
        "backend": {}, "n8n": {}, "minio-init": {"restart": "no"},
    })

    rows = [
        {
            "Service": "backend", "ID": "backend-1",
            "State": "running",
            "Health": "healthy",
            "Status": "Up 10 seconds (healthy)",
        },
        {
            "Service": "n8n", "ID": "n8n-1",
            "State": "running",
            "Health": "unhealthy",
            "Status": "Up 10 seconds (unhealthy)",
        },
        {
            "Service": "minio-init", "ID": "minio-init-1",
            "State": "exited",
            "ExitCode": 0,
            "Status": "Exited (0) 5 seconds ago",
        },
    ]

    monkeypatch.setattr(
        starter.docker_manager,
        "compose_ps_json",
        lambda: (rows, None),
    )

    assert starter.show_detached_status_summary(json_output=True) is False
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    by_service = {entry["service"]: entry for entry in payload["services"]}
    assert by_service["backend"]["ok"] is True
    assert by_service["minio-init"]["ok"] is True
    assert by_service["n8n"]["ok"] is False
    assert by_service["n8n"]["health"] == "unhealthy"


def test_automation_docs_name_detach_as_scripted_bring_up() -> None:
    for text in (
        REUSING_ATLAS.read_text(encoding="utf-8"),
        surface_text("docs/operations/index.md", "site"),
        surface_text("docs/operations/index.md", "wiki"),
    ):
        assert "--no-tui --detach" in text
        assert "--no-follow" in text
        assert "scripted" in text.lower() or "automation" in text.lower()


# ── #508: nonzero `up --wait` + converged stack = benign one-shot race ──────
def _race_starter(monkeypatch, *, up_result: int, ps_rows, ps_error=None):
    import start as start_module

    starter = start_module.AtlasStarter()
    desired = {"backend": {}, "litellm": {}, "n8n-init": {}, "litellm-init": {}}
    monkeypatch.setattr(starter, "_rendered_compose_services", lambda: desired)
    # A warm start whose images are current (#989): no build, one `up`.
    monkeypatch.setattr(
        starter.docker_manager, "enabled_service_targets", lambda: list(desired)
    )
    monkeypatch.setattr(
        starter.docker_manager, "prepare_build_args", lambda _cold, _targets: []
    )
    monkeypatch.setattr(
        starter.docker_manager, "start_services",
        lambda detached=True, wait=False, **_kw: up_result,
    )
    monkeypatch.setattr(
        starter.docker_manager, "compose_ps_json",
        lambda: (ps_rows, ps_error),
    )
    # One-shot verification is exercised separately; keep it green here.
    monkeypatch.setattr(starter, "verify_one_shot_init_containers", lambda: True)
    return starter


_HEALTHY_ROWS = [
    {"Service": "backend", "ID": "backend-1", "State": "running", "Health": "healthy"},
    {"Service": "litellm", "ID": "litellm-1", "State": "running", "Health": ""},
    {"Service": "n8n-init", "ID": "n8n-init-1", "State": "exited", "ExitCode": 0,
     "Status": "exited (0)"},
    {"Service": "litellm-init", "ID": "litellm-init-1", "State": "exited", "ExitCode": 0,
     "Status": "exited (0)"},
]


def test_up_wait_nonzero_with_converged_stack_is_benign(monkeypatch) -> None:
    """#508 repro: compose returns nonzero while ps shows only healthy/running
    services and exited-zero one-shots → startup continues (no false fail)."""
    starter = _race_starter(monkeypatch, up_result=1, ps_rows=list(_HEALTHY_ROWS))
    assert starter.start_docker_services(cold_start=False, wait=True) is True


def test_up_wait_race_recovery_is_repeatable_for_warm_starts(monkeypatch) -> None:
    """AC: verified for repeated warm starts — the classifier is stateless, so
    hitting the race on consecutive `up --wait` runs recovers each time."""
    starter = _race_starter(monkeypatch, up_result=1, ps_rows=list(_HEALTHY_ROWS))
    assert starter.start_docker_services(cold_start=False, wait=True) is True
    assert starter.start_docker_services(cold_start=False, wait=True) is True


def test_up_wait_nonzero_with_failed_one_shot_still_fails(monkeypatch, capsys) -> None:
    """AC: a genuine nonzero one-shot exit still fails and names service+code."""
    rows = list(_HEALTHY_ROWS) + [
        {"Service": "weaviate-init", "ID": "weaviate-init-1", "State": "exited", "ExitCode": 2,
         "Status": "exited (2)"},
    ]
    starter = _race_starter(monkeypatch, up_result=1, ps_rows=rows)
    starter._rendered_compose_services()["weaviate-init"] = {}
    assert starter.start_docker_services(cold_start=False, wait=True) is False
    out = capsys.readouterr().out
    assert "weaviate-init" in out
    assert "2" in out


def test_up_wait_nonzero_with_unhealthy_service_still_fails(monkeypatch, capsys) -> None:
    """AC: unhealthy long-lived services still fail startup."""
    rows = list(_HEALTHY_ROWS) + [
        {"Service": "weaviate", "ID": "weaviate-1", "State": "running", "Health": "unhealthy"},
    ]
    starter = _race_starter(monkeypatch, up_result=1, ps_rows=rows)
    starter._rendered_compose_services()["weaviate"] = {}
    assert starter.start_docker_services(cold_start=False, wait=True) is False
    assert "weaviate" in capsys.readouterr().out


def test_up_wait_nonzero_with_ps_error_or_empty_still_fails(monkeypatch) -> None:
    starter = _race_starter(monkeypatch, up_result=1, ps_rows=[],
                            ps_error="docker compose ps failed")
    assert starter.start_docker_services(cold_start=False, wait=True) is False
    starter = _race_starter(monkeypatch, up_result=1, ps_rows=[])
    assert starter.start_docker_services(cold_start=False, wait=True) is False


def test_nonzero_up_without_wait_never_recovers(monkeypatch) -> None:
    """The race only exists under --wait; the non-wait path keeps failing fast."""
    starter = _race_starter(monkeypatch, up_result=1, ps_rows=list(_HEALTHY_ROWS))
    assert starter.start_docker_services(cold_start=False, wait=False) is False


def test_zero_up_result_skips_race_inspection(monkeypatch) -> None:
    starter = _race_starter(monkeypatch, up_result=0, ps_rows=[])
    assert starter.start_docker_services(cold_start=False, wait=True) is True


# ── #677/#681: health=starting is convergent-pending, not a failure ─────────
class _FakeClock:
    """Deterministic monotonic clock — sleep advances it, so grace-window tests
    never touch the wall clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


def _sequence_poll(*snapshots):
    """poll_rows callable yielding each (rows, error) snapshot in turn, then
    repeating the last one forever."""
    state = {"i": 0}

    def _poll():
        i = state["i"]
        state["i"] = min(i + 1, len(snapshots) - 1)
        return snapshots[i]

    return _poll


_STARTING_THEN = [
    {"Service": "backend", "ID": "backend-1", "State": "running", "Health": "healthy"},
    {"Service": "weaviate", "ID": "weaviate-1", "State": "running", "Health": "starting",
     "ExitCode": 0, "Status": "Up 3 seconds (health: starting)"},
]
_ALL_HEALTHY_2 = [
    {"Service": "backend", "ID": "backend-1", "State": "running", "Health": "healthy"},
    {"Service": "weaviate", "ID": "weaviate-1", "State": "running", "Health": "healthy"},
]


def _starter():
    import start as start_module

    return start_module.AtlasStarter()


def test_compose_row_status_running_starting_is_pending_not_failed() -> None:
    """The row shape observed live (State=running, Health=starting, ExitCode=0)
    is convergent-pending — not ok, not a failure — and carries NO exit code."""
    import start as start_module

    entry = start_module.AtlasStarter._compose_row_status(_STARTING_THEN[1])
    assert entry["ok"] is False
    assert entry["pending"] is True
    assert entry["reason"] == "starting"
    assert entry["exit_code"] is None  # #677 AC#3: no exit code for a running container


def test_compose_row_status_running_healthy_has_no_exit_code() -> None:
    import start as start_module

    entry = start_module.AtlasStarter._compose_row_status(
        {"Service": "backend", "ID": "backend-1", "State": "running", "Health": "healthy", "ExitCode": 0}
    )
    assert entry["ok"] is True and entry["pending"] is False and entry["exit_code"] is None


def test_compose_row_status_exited_nonzero_still_fails_and_keeps_code() -> None:
    import start as start_module

    entry = start_module.AtlasStarter._compose_row_status(
        {"Service": "weaviate-init", "ID": "weaviate-init-1", "State": "exited", "ExitCode": 2, "Status": "exited (2)"}
    )
    assert entry["ok"] is False and entry["pending"] is False and entry["exit_code"] == "2"


def test_poll_until_converged_grace_resolves_starting_to_healthy() -> None:
    """#677/#681 AC#4: inject ps rows + a fake clock — a stack that is starting
    at the first snapshot and healthy at the next converges (converged=True),
    and reports that it waited."""
    clock = _FakeClock()
    poll = _sequence_poll((_STARTING_THEN, None), (_ALL_HEALTHY_2, None))
    services, converged, waited, error = _starter()._poll_until_converged(
        grace_seconds=60, poll_interval_seconds=2,
        poll_rows=poll, sleep=clock.sleep, monotonic=clock.monotonic,
    )
    assert error is None
    assert converged is True
    assert waited is True
    assert all(entry["ok"] for entry in services)


def test_poll_until_converged_genuine_failure_short_circuits() -> None:
    """An unhealthy (non-pending) row classifies immediately — no grace wait."""
    def _no_sleep(_seconds):
        raise AssertionError("must not grace-wait on a genuine failure")

    rows = _ALL_HEALTHY_2[:1] + [
        {"Service": "weaviate", "ID": "weaviate-1", "State": "running", "Health": "unhealthy"}
    ]
    services, converged, waited, error = _starter()._poll_until_converged(
        grace_seconds=60, poll_rows=lambda: (rows, None),
        sleep=_no_sleep, monotonic=lambda: 0.0,
    )
    assert converged is False and waited is False and error is None


def test_poll_until_converged_still_starting_after_grace_fails() -> None:
    """A row that stays starting past the whole grace window fails to converge."""
    clock = _FakeClock()
    poll = _sequence_poll((_STARTING_THEN, None))  # always starting
    services, converged, waited, error = _starter()._poll_until_converged(
        grace_seconds=6, poll_interval_seconds=2,
        poll_rows=poll, sleep=clock.sleep, monotonic=clock.monotonic,
    )
    assert converged is False and waited is True and error is None


def test_up_wait_grace_recovers_starting_then_healthy(capsys) -> None:
    """#677 AC#1 / #681 AC#2: a start that is merely starting → healthy within
    the grace window converges with NO error lines and flags the grace race."""
    clock = _FakeClock()
    poll = _sequence_poll((_STARTING_THEN, None), (_ALL_HEALTHY_2, None))
    starter = _starter()
    ok = starter._up_wait_race_converged(
        grace_seconds=60, poll_interval_seconds=2,
        poll_rows=poll, sleep=clock.sleep, monotonic=clock.monotonic,
    )
    assert ok is True
    out = capsys.readouterr().out
    assert "[ERROR]" not in out
    assert "grace" in out
    assert starter._up_converged_after_grace is True


def test_up_wait_starting_past_grace_fails_loudly_without_exit_code(capsys) -> None:
    """#677 AC#2/#3: a service still starting after the grace window fails and
    is named — with NO misleading `exit code 0` suffix for a running container."""
    clock = _FakeClock()
    poll = _sequence_poll((_STARTING_THEN, None))
    ok = _starter()._up_wait_race_converged(
        grace_seconds=6, poll_interval_seconds=2,
        poll_rows=poll, sleep=clock.sleep, monotonic=clock.monotonic,
    )
    assert ok is False
    out = capsys.readouterr().out
    assert "weaviate" in out
    assert "still not healthy after grace window" in out
    assert "exit code" not in out


def test_starting_then_healthy_start_succeeds_and_skips_rollback(monkeypatch) -> None:
    """#681 AC#3: the pending→healthy path returns success and NEVER invokes
    rollback_managed_host_processes() (which would kill a just-started managed
    ComfyUI on a fresh boot)."""
    import time

    import start as start_module

    starter = start_module.AtlasStarter()
    # A warm start whose images are current (#989): no build, one `up`.
    monkeypatch.setattr(
        starter.docker_manager, "enabled_service_targets", lambda: ["backend"]
    )
    monkeypatch.setattr(
        starter.docker_manager, "prepare_build_args", lambda _cold, _targets: []
    )
    monkeypatch.setattr(
        starter.docker_manager, "start_services",
        lambda detached=True, wait=False, **_kw: 1,  # nonzero → race path
    )
    monkeypatch.setattr(
        starter.docker_manager, "compose_ps_json",
        _sequence_poll((_STARTING_THEN, None), (_ALL_HEALTHY_2, None)),
    )
    # No real sleeping in the default-args grace loop.
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(starter, "verify_one_shot_init_containers", lambda: True)
    rollbacks: list[bool] = []
    monkeypatch.setattr(
        starter, "rollback_managed_host_processes",
        lambda: rollbacks.append(True) or True,
    )

    assert starter.start_docker_services(cold_start=False, wait=True) is True
    assert rollbacks == []  # pending is not a failure → no rollback


def test_detached_json_summary_flags_converged_after_grace(capsys) -> None:
    """#681 AC#5: --json distinguishes converged-after-grace from
    first-pass-healthy."""
    clock = _FakeClock()
    starter = _starter()
    ok = starter.show_detached_status_summary(
        json_output=True, grace_seconds=60, poll_interval_seconds=2,
        poll_rows=_sequence_poll((_STARTING_THEN, None), (_ALL_HEALTHY_2, None)),
        sleep=clock.sleep, monotonic=clock.monotonic,
    )
    assert ok is True
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["converged_after_grace"] is True


def test_detached_json_summary_first_pass_healthy_is_not_after_grace(capsys) -> None:
    starter = _starter()
    ok = starter.show_detached_status_summary(
        json_output=True,
        poll_rows=_sequence_poll((_ALL_HEALTHY_2, None)),
        sleep=lambda *_a: None, monotonic=lambda: 0.0,
    )
    assert ok is True
    payload = json.loads(capsys.readouterr().out)
    assert payload["converged_after_grace"] is False


def test_detached_json_summary_lists_services_left_out_by_failed_builds(capsys) -> None:
    # A failed image build drops that service from `up` (#989); the JSON said
    # ok with no trace of the gap.
    starter = _starter()
    starter.skipped_builds = ["jupyterhub"]
    starter.show_detached_status_summary(
        json_output=True,
        poll_rows=_sequence_poll((_ALL_HEALTHY_2, None)),
        sleep=lambda *_a: None, monotonic=lambda: 0.0,
    )
    assert json.loads(capsys.readouterr().out)["not_started"] == ["jupyterhub"]


def test_declined_launch_says_the_running_stack_was_stopped() -> None:
    from core.launch_outcome import launch_cancelled_notice

    assert "stopped to free its ports" in launch_cancelled_notice(stopped_previous=True)
    assert "stopped" not in launch_cancelled_notice()


def _base_compose_services():
    """Read real owned fragments and the generated seed, without running Compose."""
    import yaml

    from core.consumer_manifest import render_n8n_seed_overlay

    services = {}
    for path in sorted((REPO_ROOT / "services").glob("*/compose.yml")):
        services.update(yaml.safe_load(path.read_text())["services"])
    services.update(yaml.safe_load(render_n8n_seed_overlay([]))["services"])
    return services


_CUSTOM_JOB = {
    "prepare-index": {"restart": "on-failure", "healthcheck": {"test": ["CMD", "true"]}},
    "consumer": {"depends_on": {"prepare-index": {"condition": "service_completed_successfully"}}},
}
_STOPPED_CASES = [
    pytest.param(("backend", {"backend": {"restart": "unless-stopped"}}, 0, "Exited (0)", False), id="stopped-server"),
    pytest.param(("worker-init", {"worker-init": {"restart": "no"}}, 0, "Exited (0)", False), id="unknown-init-suffix"),
    pytest.param(("consumer-job", {"consumer-job": {"restart": "no"}}, 0, "Exited (0)", False), id="restart-no-is-not-role"),
    pytest.param(("n8n-init", None, 0, "Exited (0)", False), id="config-unavailable"),
    pytest.param(("n8n-init", {}, 0, "Exited (0)", False), id="service-absent"),
    pytest.param(("n8n-init", ["n8n-init"], 0, "Exited (0)", False), id="malformed-model"),
    pytest.param(("n8n-init", {"n8n-init": None}, 0, "Exited (0)", False), id="malformed-service"),
    pytest.param(("n8n-init", {"n8n-init": {"restart": "always"}}, 0, "Exited (0)", False), id="job-repurposed-as-daemon"),
    pytest.param(("n8n-init", {"n8n-init": {"restart": "on-failure"}}, 0, "Exited (0)", False), id="identity-only-ambiguous-retry"),
    pytest.param(("n8n-init", {"n8n-init": {"healthcheck": {"test": ["CMD", "true"]}}}, 0, "Exited (0)", False), id="identity-only-healthcheck"),
    pytest.param(("n8n-init", {"n8n-init": {"restart": "no"}}, 0, "Exited (0)", True), id="known-finite"),
    pytest.param(("prepare-index", _CUSTOM_JOB, 0, "Exited (0)", True), id="custom-completion-retrying-job"),
    pytest.param(("prepare-index", {**_CUSTOM_JOB, "prepare-index": {"restart": "always"}}, 0, "Exited (0)", False), id="completion-cannot-allow-daemon-restart"),
    pytest.param(("n8n-init", {"n8n-init": {"healthcheck": {"disable": True}}}, 0, "Exited (0)", True), id="disabled-healthcheck"),
    pytest.param(("n8n-init", {"n8n-init": {"healthcheck": {"test": ["NONE"]}}}, 0, "Exited (0)", True), id="none-healthcheck"),
    pytest.param(("n8n-init", {"n8n-init": {"depends_on": ["backend"]}}, 0, "Exited (0)", False), id="malformed-effective-dependencies"),
    pytest.param(("n8n-init", {"n8n-init": {}}, 2, "Exited (0)", False), id="exit-code-over-stale-status"),
    pytest.param(("n8n-init", {"n8n-init": {}}, None, "Exited (0)", True), id="status-proves-zero"),
    pytest.param(("n8n-init", {"n8n-init": {}}, "<nil>", "Exited (0)", True), id="nil-code-status-proves-zero"),
    pytest.param(("n8n-init", {"n8n-init": {}}, None, "Exited (unknown)", False), id="missing-code-no-zero-evidence"),
    pytest.param(("n8n-init", {"n8n-init": {}}, 0, "Exited", True), id="numeric-zero-authoritative"),
    pytest.param((None, {"n8n-init": {}}, 0, "Exited (0)", False), id="container-name-is-not-Service"),
]


@pytest.mark.parametrize("case", _STOPPED_CASES)
def test_detached_summary_requires_finite_role_and_zero_evidence(monkeypatch, capsys, case):
    name, model, code, status, expected = case
    starter = _starter()
    monkeypatch.setattr(starter, "_rendered_compose_services", lambda: model)
    starter._capture_readiness_targets([name] if name else ["n8n-init"])
    row = {"Service": name, "Name": "atlas-n8n-init", "State": "exited", "ExitCode": code, "Status": status}

    assert starter.show_detached_status_summary(
        json_output=True, poll_rows=lambda: ([row], None)
    ) is expected
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is expected
    if payload["services"]:
        assert payload["services"][0]["ok"] is expected
        assert payload["services"][0]["state"] == "exited"
    else:
        assert not expected and payload["error"]


@pytest.mark.parametrize("case", _STOPPED_CASES)
def test_wait_race_requires_finite_role_and_zero_evidence(monkeypatch, case):
    name, model, code, status, expected = case
    starter = _starter()
    monkeypatch.setattr(starter, "_rendered_compose_services", lambda: model)
    starter._capture_readiness_targets([name] if name else ["n8n-init"])
    row = {"Service": name, "Name": "atlas-n8n-init", "State": "exited", "ExitCode": code, "Status": status}

    assert starter._up_wait_race_converged(poll_rows=lambda: ([row], None)) is expected


@pytest.mark.parametrize("job", [
    "airflow-init", "backup", "comfyui-init", "hermes-init", "iceberg-rest-init",
    "label-studio-init", "langfuse-init", "lightrag-init", "litellm-init", "minio-init",
    "mlflow-init", "n8n-init", "n8n-seed", "ollama-pull", "open-webui-init",
    "openclaw-init", "redpanda-init", "spark-init", "supabase-db-init", "trueforge-init",
    "weaviate-init", "zeppelin-init",
])
def test_real_builtin_finite_jobs_keep_success_without_downstream_edges(monkeypatch, capsys, job):
    model = _base_compose_services()
    for service in model.values():
        service.pop("depends_on", None)
    model = {job: model[job]}
    model[job].setdefault("deploy", {})["replicas"] = 1
    starter = _starter()
    monkeypatch.setattr(starter, "_rendered_compose_services", lambda: model)
    starter._capture_readiness_targets([job])
    row = {"Service": job, "ID": job + "-1", "State": "exited", "ExitCode": 0, "Status": "Exited (0)"}

    assert starter.show_detached_status_summary(json_output=True, poll_rows=lambda: ([row], None))
    assert json.loads(capsys.readouterr().out)["services"][0]["ok"]
    assert starter._up_wait_race_converged(poll_rows=lambda: ([row], None))


def test_running_only_summary_resolves_desired_membership(monkeypatch, capsys):
    starter = _starter()
    calls = []
    def inspect():
        calls.append(True)
        return {"backend": {}, "weaviate": {}}
    monkeypatch.setattr(starter, "_rendered_compose_services", inspect)
    assert starter.show_detached_status_summary(
        json_output=True, poll_rows=lambda: (_ALL_HEALTHY_2, None)
    )
    assert json.loads(capsys.readouterr().out)["ok"]
    assert len(calls) == 1


def test_role_lookup_is_once_per_call_and_fresh_on_next_start(monkeypatch):
    starter = _starter()
    clock = _FakeClock()
    models = [{"backend": {}, "weaviate": {}, "n8n-init": {}},
              {"backend": {}, "weaviate": {}, "n8n-init": {"restart": "always"}}]
    calls = []

    def inspect():
        calls.append(True)
        return models[len(calls) - 1]

    monkeypatch.setattr(starter, "_rendered_compose_services", inspect)
    job = {"Service": "n8n-init", "ID": "n8n-init-1", "State": "exited", "ExitCode": 0}
    poll = _sequence_poll(
        (_STARTING_THEN + [job], None), (_ALL_HEALTHY_2 + [job], None)
    )
    assert starter._up_wait_race_converged(
        poll_rows=poll, sleep=clock.sleep, monotonic=clock.monotonic
    )
    assert len(calls) == 1
    assert not starter._up_wait_race_converged(poll_rows=lambda: ([job], None))
    assert len(calls) == 2


@pytest.mark.parametrize("model", [None, {}])
def test_unavailable_role_fails_promptly_and_retries_next_call(monkeypatch, model):
    starter = _starter()
    clock = _FakeClock()
    calls = []

    def inspect():
        calls.append(True)
        return model

    monkeypatch.setattr(starter, "_rendered_compose_services", inspect)
    # The unknown exit fails immediately; a second public call retries inspection.
    job = {"Service": "n8n-init", "ID": "n8n-init-1", "State": "exited", "ExitCode": 0}
    for _ in range(2):
        assert not starter._up_wait_race_converged(
            poll_rows=lambda: (_STARTING_THEN + [job], None),
            sleep=clock.sleep, monotonic=clock.monotonic,
        )
    assert len(calls) == 2
    assert clock.now == 0


def test_late_role_inspection_preserves_pending_health_grace(monkeypatch, capsys):
    starter = _starter()
    clock = _FakeClock()
    calls = []

    def inspect():
        calls.append(True)
        clock.sleep(60)  # existing configuration subprocess has a separate bound
        return {"backend": {}, "weaviate": {}, "n8n-init": {}}

    monkeypatch.setattr(starter, "_rendered_compose_services", inspect)
    job = {"Service": "n8n-init", "ID": "n8n-init-1", "State": "exited", "ExitCode": 0}
    poll = _sequence_poll(
        (_STARTING_THEN + [job], None), (_STARTING_THEN + [job], None),
        (_STARTING_THEN + [job], None), (_ALL_HEALTHY_2 + [job], None),
    )
    assert starter.show_detached_status_summary(
        json_output=True, grace_seconds=6, poll_interval_seconds=2,
        poll_rows=poll, sleep=clock.sleep, monotonic=clock.monotonic,
    )
    assert len(calls) == 1
    assert clock.now == 66
    assert json.loads(capsys.readouterr().out)["converged_after_grace"]


def test_stopped_zero_server_cannot_recover_nonzero_up(monkeypatch, capsys):
    rows = [{"Service": "backend", "ID": "backend-1", "State": "exited", "ExitCode": 0}]
    starter = _race_starter(monkeypatch, up_result=1, ps_rows=rows)
    assert not starter.start_docker_services(cold_start=False, wait=True)
    output = capsys.readouterr().out
    assert "backend" in output
    assert "one-shot lifecycle not declared" in output


# #1750: readiness requires the effective requested service/replica inventory.
def _desired_starter(monkeypatch, model):
    starter = _starter()
    monkeypatch.setattr(starter, "_rendered_compose_services", lambda: model)
    return starter


def _healthy(service, identity=None):
    return {"Service": service, "ID": identity or service + "-1", "State": "running", "Health": "healthy"}


def test_missing_expected_service_rejects_start_recovery_and_json(monkeypatch, capsys):
    starter = _race_starter(monkeypatch, up_result=1, ps_rows=[_healthy("redis")])
    monkeypatch.setattr(starter.docker_manager, "enabled_service_targets", lambda: ["backend", "redis"])
    monkeypatch.setattr(starter, "_rendered_compose_services", lambda: {"backend": {}, "redis": {}})
    assert starter.start_docker_services(wait=True) is False
    assert "backend" in capsys.readouterr().out
    assert starter.show_detached_status_summary(json_output=True) is False
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    _assert_replica_deficit(payload, "backend", 1, 0)


def _assert_replica_deficit(payload, name, expected, observed):
    deficit = next(entry for entry in payload["services"] if entry.get("missing_replicas"))
    assert deficit["service"] == name
    assert deficit["ok"] is False
    assert deficit["expected_replicas"] == expected
    assert deficit["observed_replicas"] == observed
    assert deficit["missing_replicas"] == expected - observed


@pytest.mark.parametrize("case", [
    ([], False, 0),
    ([_healthy("worker")], False, 1),
    ([_healthy("worker"), _healthy("worker")], False, 1),
    ([_healthy("orphan", "worker-1")], False, 0),
    ([_healthy("worker"), _healthy("orphan")], False, 1),
    ([_healthy("worker", "worker-1"), _healthy("worker", "worker-2")], True, 2),
    ([_healthy("worker", "worker-1"), _healthy("worker", "worker-2"), _healthy("orphan")], True, 2),
])
def test_desired_replicas_require_unique_expected_containers(monkeypatch, capsys, case):
    rows, expected, observed = case
    starter = _desired_starter(monkeypatch, {"worker": {"scale": 2}})
    assert starter._up_wait_race_converged(poll_rows=lambda: (rows, None)) is expected
    capsys.readouterr()
    assert starter.show_detached_status_summary(json_output=True, poll_rows=lambda: (rows, None)) is expected
    payload = json.loads(capsys.readouterr().out)
    if not expected:
        _assert_replica_deficit(payload, "worker", 2, observed)
        assert starter.show_detached_status_summary(poll_rows=lambda: (rows, None)) is False
        human = capsys.readouterr().out
        assert "worker" in human and "expected 2" in human and f"observed {observed}" in human


@pytest.mark.parametrize("model", [None, {}, [], {"worker": None}, {"worker": {"scale": -1}},
    {"worker": {"scale": "oops"}}, {"worker": {"scale": True}},
    {"worker": {"deploy": []}}, {"worker": {"deploy": {"replicas": 1.5}}},
    {"worker": {"scale": 2, "deploy": {"replicas": 1}}}])
def test_unknown_or_malformed_desired_model_cannot_prove_readiness(monkeypatch, capsys, model):
    starter = _desired_starter(monkeypatch, model)
    assert not starter.show_detached_status_summary(json_output=True, poll_rows=lambda: ([_healthy("worker")], None))
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] and payload["ok"] is False


def test_zero_scale_and_skipped_optional_builds_keep_exemptions(monkeypatch, capsys):
    starter = _desired_starter(monkeypatch, {"backend": {}, "disabled": {"scale": 0},
        "also-disabled": {"deploy": {"replicas": 0}}, "jupyterhub": {}})
    starter.skipped_builds = ["jupyterhub"]
    assert starter.show_detached_status_summary(json_output=True, poll_rows=lambda: ([_healthy("backend")], None))
    payload = json.loads(capsys.readouterr().out)
    assert payload["not_started"] == ["jupyterhub"]
    assert {entry["service"] for entry in payload["services"]} == {"backend"}


def test_declared_finite_job_needs_observed_completion(monkeypatch, capsys):
    starter = _desired_starter(monkeypatch, {"backend": {}, "n8n-init": {"restart": "no"}})
    rows = [_healthy("backend")]
    assert not starter._up_wait_race_converged(poll_rows=lambda: (rows, None))
    rows.append({"Service": "n8n-init", "ID": "init-1", "State": "exited", "ExitCode": 0})
    assert starter._up_wait_race_converged(poll_rows=lambda: (rows, None))
    capsys.readouterr()
    assert starter.show_detached_status_summary(json_output=True, poll_rows=lambda: (rows, None))


def test_launch_snapshot_survives_config_changes_and_excludes_unrequested_profiles(monkeypatch, capsys):
    starter = _race_starter(monkeypatch, up_result=1, ps_rows=[_healthy("backend"), _healthy("redis")])
    monkeypatch.setattr(starter.docker_manager, "enabled_service_targets", lambda: ["backend"])
    model = {"backend": {"depends_on": {"redis": {}}}, "redis": {}, "profile-other": {"profiles": ["other"]}}
    calls = []
    def render():
        calls.append(True)
        return model if len(calls) == 1 else {"changed": {}}
    monkeypatch.setattr(starter, "_rendered_compose_services", render)
    assert starter.start_docker_services(wait=True)
    capsys.readouterr()
    assert starter.show_detached_status_summary(json_output=True)
    assert len(calls) == 1


@pytest.mark.parametrize("bad_rows", [[None], [1], [{"Service": "backend", "State": "running"}, None],
    [{"Service": "backend", "State": "running"}], [{"ID": "x", "State": "running"}]])
def test_malformed_status_rows_cannot_prove_readiness(monkeypatch, capsys, bad_rows):
    starter = _desired_starter(monkeypatch, {"backend": {}})
    assert not starter.show_detached_status_summary(json_output=True, poll_rows=lambda: (bad_rows, None))
    assert json.loads(capsys.readouterr().out)["ok"] is False


@pytest.mark.parametrize("identity", [7, ["container"], {"container": "id"}])
def test_invalid_container_id_with_valid_name_is_inspection_error(monkeypatch, capsys, identity):
    starter = _desired_starter(monkeypatch, {"backend": {}})
    row = {**_healthy("backend"), "ID": identity, "Name": "atlas-backend-1"}
    assert not starter.show_detached_status_summary(json_output=True, poll_rows=lambda: ([row], None))
    assert json.loads(capsys.readouterr().out)["error"]


def test_whitespace_id_uses_unique_container_names(monkeypatch):
    starter = _desired_starter(monkeypatch, {"worker": {"scale": 2}})
    rows = [{**_healthy("worker"), "ID": " ", "Name": "worker-1"},
            {**_healthy("worker"), "ID": " ", "Name": "worker-2"}]
    assert starter._up_wait_race_converged(poll_rows=lambda: (rows, None))


@pytest.mark.parametrize("dependency", ["absent", "skipped", "zero"])
def test_optional_dependency_exemptions(monkeypatch, dependency):
    model = {"backend": {"depends_on": {"optional": {"required": False}}}}
    if dependency != "absent":
        model["optional"] = {"scale": 0} if dependency == "zero" else {}
    starter = _desired_starter(monkeypatch, model)
    starter.skipped_builds = ["optional"] if dependency == "skipped" else []
    starter._capture_readiness_targets(["backend"])
    assert starter._up_wait_race_converged(poll_rows=lambda: ([_healthy("backend")], None))


@pytest.mark.parametrize("dependency", ["absent", "skipped", "zero"])
def test_required_dependency_cannot_be_omitted_from_launch(monkeypatch, dependency):
    model = {"backend": {"depends_on": {"required": {}}}}
    if dependency != "absent":
        model["required"] = {"scale": 0} if dependency == "zero" else {}
    starter = _desired_starter(monkeypatch, model)
    starter.skipped_builds = ["required"] if dependency == "skipped" else []
    starter._capture_readiness_targets(["backend"])
    assert not starter._up_wait_race_converged(poll_rows=lambda: ([_healthy("backend")], None))


@pytest.mark.parametrize("field, value", [("Health", 0), ("Status", []), ("Name", ["container"])])
def test_malformed_optional_status_fields_fail_inspection(monkeypatch, capsys, field, value):
    starter = _desired_starter(monkeypatch, {"backend": {}})
    row = {**_healthy("backend"), field: value}
    assert not starter.show_detached_status_summary(json_output=True, poll_rows=lambda: ([row], None))
    assert json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("cold", [False, True])
def test_launch_captures_targets_after_optional_build_isolation(monkeypatch, capsys, cold):
    starter = _desired_starter(monkeypatch, {"backend": {}, "jupyterhub": {"build": {"context": "fixture"}}})
    manager = starter.docker_manager
    monkeypatch.setattr(manager, "enabled_service_targets", lambda: ["backend", "jupyterhub"])
    monkeypatch.setattr(manager, "prepare_build_args", lambda *_args: ["--build"])
    monkeypatch.setattr(manager, "capture_build_state", lambda *_args: None)
    monkeypatch.setattr(manager, "mark_source_built", lambda *_args: None)
    monkeypatch.setattr(manager, "execute_compose_command", lambda _args: 1)
    monkeypatch.setattr(manager, "compose_ps_json", lambda: ([_healthy("backend")], None))
    def isolate(targets, no_cache):
        assert targets == ["backend", "jupyterhub"]
        assert no_cache is cold
        starter.skipped_builds = ["jupyterhub"]
        return True, ["backend"]
    monkeypatch.setattr(starter, "_build_launch_images", isolate)
    monkeypatch.setattr(starter, "verify_one_shot_init_containers", lambda: True)
    monkeypatch.setattr(starter, "_reactivate_n8n_if_needed", lambda: True)
    assert starter.start_docker_services(cold_start=cold, wait=True)
    capsys.readouterr()
    assert starter.show_detached_status_summary(json_output=True)
    assert json.loads(capsys.readouterr().out)["not_started"] == ["jupyterhub"]


def test_new_start_refreshes_the_captured_desired_targets(monkeypatch):
    starter = _race_starter(monkeypatch, up_result=1, ps_rows=list(_HEALTHY_ROWS))
    assert starter.start_docker_services(wait=True)
    starter._rendered_compose_services()["redis"] = {}
    assert not starter.start_docker_services(wait=True)
