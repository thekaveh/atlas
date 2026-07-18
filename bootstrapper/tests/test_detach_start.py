from __future__ import annotations

import json
from pathlib import Path

from tests.three_surface_test_utils import surface_text


REPO_ROOT = Path(__file__).resolve().parents[2]
START_PY = REPO_ROOT / "bootstrapper" / "start.py"
REUSING_ATLAS = REPO_ROOT / "docs" / "deployment" / "reusing-atlas.md"


def test_start_cli_declares_detach_no_follow_and_json_options() -> None:
    src = START_PY.read_text(encoding="utf-8")

    assert "@click.option('--detach', '--no-follow'" in src
    assert "is_flag=True" in src
    assert "--no-follow" in src
    assert "@click.option('--json', 'json_output'" in src


def test_linear_detach_path_skips_following_logs() -> None:
    src = START_PY.read_text(encoding="utf-8")

    detach_pos = src.find("if detach:")
    status_pos = src.find("show_detached_status_summary(json_output=json_output)")
    logs_pos = src.find("starter.show_container_logs()")

    assert detach_pos != -1, "linear start flow must branch on detach"
    assert status_pos != -1, "detach flow must print a terminal status summary"
    assert logs_pos != -1, "interactive/default flow should still follow logs"
    assert detach_pos < status_pos < logs_pos
    assert "show_container_logs()" in src[status_pos:src.find("except click.ClickException")]
    assert "assume_yes=detach or json_output" in src


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

    assert manager.start_services(detached=True, wait=True, wait_timeout_seconds=123) == 0
    assert captured == [
        ["up", "-d", "--force-recreate", "--wait", "--wait-timeout", "123"]
    ]


def test_detached_json_status_summary_reports_unhealthy_service(monkeypatch, capsys) -> None:
    import start as start_module

    starter = start_module.AtlasStarter()

    rows = [
        {
            "Service": "backend",
            "State": "running",
            "Health": "healthy",
            "Status": "Up 10 seconds (healthy)",
        },
        {
            "Service": "n8n",
            "State": "running",
            "Health": "unhealthy",
            "Status": "Up 10 seconds (unhealthy)",
        },
        {
            "Service": "minio-init",
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
        surface_text("docs/operations.md", "site"),
        surface_text("docs/operations.md", "wiki"),
    ):
        assert "--no-tui --detach" in text
        assert "--no-follow" in text
        assert "scripted" in text.lower() or "automation" in text.lower()


# ── #508: nonzero `up --wait` + converged stack = benign one-shot race ──────
def _race_starter(monkeypatch, *, up_result: int, ps_rows, ps_error=None):
    import start as start_module

    starter = start_module.AtlasStarter()
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
    {"Service": "backend", "State": "running", "Health": "healthy"},
    {"Service": "litellm", "State": "running", "Health": ""},
    {"Service": "n8n-init", "State": "exited", "ExitCode": 0,
     "Status": "exited (0)"},
    {"Service": "litellm-init", "State": "exited", "ExitCode": 0,
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
        {"Service": "weaviate-init", "State": "exited", "ExitCode": 2,
         "Status": "exited (2)"},
    ]
    starter = _race_starter(monkeypatch, up_result=1, ps_rows=rows)
    assert starter.start_docker_services(cold_start=False, wait=True) is False
    out = capsys.readouterr().out
    assert "weaviate-init" in out
    assert "2" in out


def test_up_wait_nonzero_with_unhealthy_service_still_fails(monkeypatch, capsys) -> None:
    """AC: unhealthy long-lived services still fail startup."""
    rows = list(_HEALTHY_ROWS) + [
        {"Service": "weaviate", "State": "running", "Health": "unhealthy"},
    ]
    starter = _race_starter(monkeypatch, up_result=1, ps_rows=rows)
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
    {"Service": "backend", "State": "running", "Health": "healthy"},
    {"Service": "weaviate", "State": "running", "Health": "starting",
     "ExitCode": 0, "Status": "Up 3 seconds (health: starting)"},
]
_ALL_HEALTHY_2 = [
    {"Service": "backend", "State": "running", "Health": "healthy"},
    {"Service": "weaviate", "State": "running", "Health": "healthy"},
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
        {"Service": "backend", "State": "running", "Health": "healthy", "ExitCode": 0}
    )
    assert entry["ok"] is True and entry["pending"] is False and entry["exit_code"] is None


def test_compose_row_status_exited_nonzero_still_fails_and_keeps_code() -> None:
    import start as start_module

    entry = start_module.AtlasStarter._compose_row_status(
        {"Service": "weaviate-init", "State": "exited", "ExitCode": 2, "Status": "exited (2)"}
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
        {"Service": "weaviate", "State": "running", "Health": "unhealthy"}
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
