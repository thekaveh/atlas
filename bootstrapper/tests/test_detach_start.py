from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
START_PY = REPO_ROOT / "bootstrapper" / "start.py"
REUSING_ATLAS = REPO_ROOT / "docs" / "deployment" / "reusing-atlas.md"
SITE_OPERATIONS = REPO_ROOT / "docs" / "site" / "operations.md"
WIKI_OPERATIONS = REPO_ROOT / "docs" / "wiki" / "Operations.md"


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
    for path in (REUSING_ATLAS, SITE_OPERATIONS, WIKI_OPERATIONS):
        text = path.read_text(encoding="utf-8")
        assert "--no-tui --detach" in text
        assert "--no-follow" in text
        assert "scripted" in text.lower() or "automation" in text.lower()
