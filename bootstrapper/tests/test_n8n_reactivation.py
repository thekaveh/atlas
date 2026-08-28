"""#720: Atlas restarts n8n after seeding to register a consumer's production
webhook when the workflow is activated without an N8N_API_KEY.

The restart behavior was empirically verified on n8nio/n8n:2.28.2. Atlas keeps
the conservative restart on 2.36.7: the current image retains the
`publish:workflow --id` CLI contract, while an API-key activation still uses
the live public API and does not need the restart.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))


def test_effective_active_true_false_and_fromjson(tmp_path):
    import start

    assert start._n8n_workflow_effective_active(NS(active="true", source_path=tmp_path / "x")) is True
    assert start._n8n_workflow_effective_active(NS(active="false", source_path=tmp_path / "x")) is False
    f = tmp_path / "wf.json"
    f.write_text(json.dumps({"active": True}), encoding="utf-8")
    assert start._n8n_workflow_effective_active(NS(active="fromJson", source_path=f)) is True
    f.write_text(json.dumps({"active": False}), encoding="utf-8")
    assert start._n8n_workflow_effective_active(NS(active="fromJson", source_path=f)) is False
    # unreadable fromJson -> inactive (fail closed)
    assert start._n8n_workflow_effective_active(NS(active="fromJson", source_path=tmp_path / "missing")) is False


def test_needs_reactivation_restart_predicate(tmp_path):
    import start

    active = NS(active="true", source_path=tmp_path / "x")
    inactive = NS(active="false", source_path=tmp_path / "x")
    C = lambda wfs: NS(n8n_workflows=tuple(wfs))

    # active workflow + no key + n8n enabled -> restart needed
    assert start._n8n_needs_reactivation_restart({"N8N_SOURCE": "container"}, C([active])) is True
    # key present -> API path already registered -> no restart
    assert start._n8n_needs_reactivation_restart({"N8N_SOURCE": "container", "N8N_API_KEY": "k"}, C([active])) is False
    # n8n disabled / unset -> no restart
    assert start._n8n_needs_reactivation_restart({"N8N_SOURCE": "disabled"}, C([active])) is False
    assert start._n8n_needs_reactivation_restart({}, C([active])) is False
    # no active workflow -> no restart
    assert start._n8n_needs_reactivation_restart({"N8N_SOURCE": "container"}, C([inactive])) is False
    assert start._n8n_needs_reactivation_restart({"N8N_SOURCE": "container"}, C([])) is False


def test_reactivate_n8n_restarts_only_when_needed(tmp_path):
    import start

    active = NS(active="true", source_path=tmp_path / "x")

    class _CP:
        def __init__(self, env, wfs):
            self._env = env
            self._wfs = wfs

        def parse_env_file(self):
            return dict(self._env)

        def load_consumer_config(self):
            return NS(n8n_workflows=tuple(self._wfs))

    class _DM:
        def __init__(self):
            self.calls = []

        def execute_compose_command(self, args):
            self.calls.append(args)
            return 0

        def compose_service_ps_json(self, service):
            assert service == "n8n"
            return ([{"Service": "n8n", "State": "running", "Health": "healthy"}], None)

    class _Banner:
        def show_status_message(self, *a, **k):
            pass

    def make(env, wfs):
        s = start.AtlasStarter.__new__(start.AtlasStarter)
        s.config_parser = _CP(env, wfs)
        s.docker_manager = _DM()
        s.banner = _Banner()
        return s

    # needs restart -> restarts n8n
    s = make({"N8N_SOURCE": "container"}, [active])
    assert s._reactivate_n8n_if_needed() is True
    assert ["restart", "n8n"] in s.docker_manager.calls

    # key present -> no restart
    s = make({"N8N_SOURCE": "container", "N8N_API_KEY": "k"}, [active])
    assert s._reactivate_n8n_if_needed() is True
    assert s.docker_manager.calls == []


def test_reactivate_n8n_reports_restart_failure(tmp_path):
    import start

    active = NS(active="true", source_path=tmp_path / "x")
    starter = start.AtlasStarter.__new__(start.AtlasStarter)
    starter.config_parser = NS(
        load_consumer_config=lambda: NS(n8n_workflows=(active,)),
        parse_env_file=lambda: {"N8N_SOURCE": "container"},
    )
    starter.docker_manager = NS(execute_compose_command=lambda _args: 17)
    messages: list[tuple[str, str]] = []
    starter.banner = NS(
        show_status_message=lambda message, level: messages.append((message, level))
    )

    assert starter._reactivate_n8n_if_needed() is False
    assert any("failed" in message.lower() and level == "error" for message, level in messages)


@pytest.mark.parametrize("failure_source", ["consumer", "env"])
def test_reactivate_n8n_fails_closed_when_configuration_cannot_be_read(
    failure_source,
):
    import start

    def fail():
        raise OSError(f"cannot read {failure_source}")

    starter = start.AtlasStarter.__new__(start.AtlasStarter)
    starter.config_parser = NS(
        load_consumer_config=fail if failure_source == "consumer" else lambda: NS(),
        parse_env_file=fail if failure_source == "env" else lambda: {},
    )
    messages = []
    starter.banner = NS(
        show_status_message=lambda message, level: messages.append((message, level))
    )

    assert starter._reactivate_n8n_if_needed() is False
    assert any(f"cannot read {failure_source}" in message for message, _ in messages)
    assert any(level == "error" for _, level in messages)


@pytest.mark.parametrize(
    ("poll_result", "expected"),
    [
        (([{"service": "n8n", "ok": True}], True, True, None), True),
        (([{"service": "n8n", "ok": False, "reason": "unhealthy"}], False, True, None), False),
        (([], False, False, "inspection failed"), False),
        (([], False, False, None), False),
    ],
)
def test_reactivate_n8n_waits_for_healthy_container(tmp_path, poll_result, expected):
    import start

    active = NS(active="true", source_path=tmp_path / "x")
    starter = start.AtlasStarter.__new__(start.AtlasStarter)
    starter.config_parser = NS(
        load_consumer_config=lambda: NS(n8n_workflows=(active,)),
        parse_env_file=lambda: {"N8N_SOURCE": "container"},
    )
    starter.docker_manager = NS(
        execute_compose_command=lambda _args: 0,
        compose_service_ps_json=lambda _service: ([], None),
    )
    starter._poll_until_converged = lambda **_kwargs: poll_result
    messages = []
    starter.banner = NS(
        show_status_message=lambda message, level: messages.append((message, level))
    )

    assert starter._reactivate_n8n_if_needed() is expected
    if not expected:
        assert any(level == "error" for _, level in messages)


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"Service": "n8n", "State": "running", "Health": "healthy"}, True),
        ({"Service": "n8n", "State": "running", "Health": ""}, False),
        ({"Service": "n8n", "State": "running", "Health": "unhealthy"}, False),
        ({
            "Service": "n8n",
            "State": "exited",
            "ExitCode": 0,
            "Status": "Exited (0) 1 second ago",
        }, False),
    ],
)
def test_n8n_restart_requires_running_and_healthy(row, expected):
    import start

    starter = start.AtlasStarter.__new__(start.AtlasStarter)
    starter.docker_manager = NS(compose_service_ps_json=lambda _service: ([row], None))
    starter.banner = NS(show_status_message=lambda *_args, **_kwargs: None)
    real_poll = start.AtlasStarter._poll_until_converged.__get__(starter)
    starter._poll_until_converged = lambda **kwargs: real_poll(
        **{**kwargs, "grace_seconds": 0}
    )

    assert starter._n8n_restart_converged() is expected


def test_linear_start_rolls_back_when_n8n_reactivation_fails(monkeypatch):
    import start

    starter = start.AtlasStarter()
    monkeypatch.setattr(starter.docker_manager, "start_services", lambda **_kwargs: 0)
    monkeypatch.setattr(starter, "verify_one_shot_init_containers", lambda: True)
    monkeypatch.setattr(starter, "_reactivate_n8n_if_needed", lambda: False)
    actions: list[str] = []
    monkeypatch.setattr(
        starter, "rollback_managed_host_processes", lambda: actions.append("rollback") or True
    )
    monkeypatch.setattr(
        starter, "commit_managed_host_processes", lambda: actions.append("commit")
    )

    assert starter.start_docker_services() is False
    assert actions == ["rollback"]


def test_tui_marks_launch_failed_when_n8n_reactivation_fails():
    from ui.textual.screens.wizard_screen import WizardScreen

    events: list[str] = []
    screen = NS(
        _write_status=lambda *_args, **_kwargs: events.append("status"),
        _mark_launch_failed=lambda: events.append("failed"),
    )
    starter = NS(_reactivate_n8n_if_needed=lambda: False)

    assert asyncio.run(
        WizardScreen._reactivate_n8n_after_up(screen, starter)
    ) is False
    assert events == ["status", "failed"]


def test_seed_workflows_publishes_when_no_api_key():
    """The seed's no-key branch persists active=true via publish:workflow instead
    of the old passive 'registers on next restart' no-op."""
    seed = (REPO_ROOT / "services" / "n8n" / "init" / "scripts" / "seed-workflows.js").read_text(
        encoding="utf-8"
    )
    assert "publish:workflow" in seed
    assert "note: '${wf.id}' active but N8N_API_KEY unset" not in seed
