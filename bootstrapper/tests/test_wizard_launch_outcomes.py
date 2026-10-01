"""Both front ends state the same launch result for the same probes (#1032).

The Textual launch screen and the ``--no-tui`` linear flow each run their
own pipeline (sharing one is #1043), so these tests drive BOTH real
pipelines end to end with the same injected post-start probes — passing,
failing, skipped and raising — and assert that the final result block and
the exit behaviour agree under the severity policy documented in
``core/launch_outcome.py``: readiness gates decide the exit code; probes
qualify the result but never fail a running launch.

The agreement is a contract rather than a coincidence: both front ends
render ``core.launch_outcome.summarize_launch`` for the same classified
probe outcomes, and only the place named in the next action differs.

They also pin the lifecycle copy: detach, cancel, stop and cold stop each
say what happens to services, configuration and data, and only cold stop
deletes anything.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from textual.app import App

from core import linear_startup
from core.launch_outcome import (
    CANCEL,
    COLD_STOP,
    DETACH,
    LIFECYCLE_ACTIONS,
    STOP,
    ProbeSkipped,
    classify_probe_result,
    run_probe,
    summarize_launch,
)
from ui.textual.screens.wizard_screen import WizardScreen
from ui.textual.widgets import PromptOption, PromptStep

_SKIP_REASON = "applies only to COMFYUI_SOURCE=localhost; this run uses container-cpu"


def _ports_ok(on_line=None):
    return 0


def _ports_mismatch(on_line=None):
    return 2


def _ports_raise(on_line=None):
    raise RuntimeError("port 63000 already bound")


def _models_found(on_line=None):
    return True


def _models_skipped(on_line=None):
    return ProbeSkipped(_SKIP_REASON)


# fixture -> (ports probe, comfyui-models probe, expected launch result)
FIXTURES = {
    "success": (_ports_ok, _models_found, "verified"),
    "failed": (_ports_mismatch, _models_found, "degraded"),
    "skipped": (_ports_ok, _models_skipped, "verified"),
    "exception": (_ports_raise, _models_skipped, "unverified"),
}


# ─── Fakes shared by both front ends ─────────────────────────────────


class _Docker:
    root_dir = "/nonexistent"
    project_name_override = None

    def set_command_echo_callback(self, _cb):
        pass

    def execute_compose_command(self, *_args, **_kwargs):
        return 0

    def enabled_service_targets(self):
        return None

    def prepare_build_args(self, _cold, _targets):
        return []

    def mark_source_built(self, _targets):
        pass


class _Starter:
    """Every pipeline step succeeds; only the two probes vary."""

    def __init__(self, fixture: str, healthy: bool = True):
        ports, models, _expected = FIXTURES[fixture]
        self.show_container_status_and_verify_ports = ports
        self.check_comfyui_models = models
        self.healthy = healthy
        self.support_bundle_path = None
        self.calls: list[str] = []
        self.config_parser = SimpleNamespace(
            root_dir="/nonexistent", get_project_name=lambda: "atlas"
        )
        self.docker_manager = _Docker()
        self.hosts_manager = SimpleNamespace(set_logger=lambda _logger: None)
        self.banner = SimpleNamespace(console=SimpleNamespace(print=self._banner))

    def _banner(self, text, *_args, **_kwargs):
        print(text)

    def __getattr__(self, name):
        def step(*_args, **_kwargs):
            self.calls.append(name)
            return True

        return step

    def show_detached_status_summary(self, *, json_output=False):
        if json_output:
            print(json.dumps({"ok": self.healthy, "services": []}))
        return self.healthy

    def show_container_logs(self):
        return 0


def _options(**overrides):
    values = dict(
        cold=False, base_port=63000, project_name="atlas", source_args={},
        profile="default", explicit_prometheus=None, explicit_grafana=None,
        cloud_api_keys={}, user_model_selections={}, no_port_migrate=False,
        setup_hosts=False, skip_hosts=True, track="gen-ai-rag", detach=False,
        json_output=False, no_splash=True,
    )
    values.update(overrides)
    return linear_startup.LinearStartupOptions(**values)


_STAGE_PREFIXES = ("  ✓ ", "  ✗ ", "  ? ", "  – ", "  Next: ")


def _result_block(lines):
    """The headline and the stage/next lines that follow it."""
    start = next(i for i, line in enumerate(lines) if "Launch result:" in line)
    block = [lines[start].strip()]
    for line in lines[start + 1:]:
        if not line.startswith(_STAGE_PREFIXES):
            break
        block.append(line)
    return block


def _same_result(block):
    """The block minus the next action, whose location is front-end specific."""
    return [line for line in block if not line.startswith("  Next: ")]


# ─── Driving each front end ──────────────────────────────────────────


def _run_linear(monkeypatch, capsys, fixture, **overrides):
    monkeypatch.setattr(linear_startup, "warn_if_submodule_pin_drifted", lambda *_: None)
    code = linear_startup.run_linear_startup(_Starter(fixture), _options(**overrides))
    captured = capsys.readouterr()
    return code, captured


def _run_tui(fixture, starter=None):
    codes: list[int] = []
    statuses: list[str] = []
    screen = WizardScreen(
        steps=[PromptStep("Dummy", 1, 1, "H", options=[PromptOption("a", "A")],
                          default_value="a")],
        services=[], no_splash=True, starter=starter or _Starter(fixture),
        prefilled_source_args={}, prefilled_stack_options={},
        on_launch_result=codes.append,
    )
    screen._write_status = lambda text, style="", source="": statuses.append(text)

    async def _compose(_args):
        return 0

    screen._run_compose = _compose

    class _App(App):
        def on_mount(self):
            self.push_screen(screen)

    async def scenario():
        async with _App().run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen._phase = "launch"
            await screen._run_pipeline_and_stream()
            await screen.app.workers.wait_for_complete()
            await pilot.pause()
            return screen._launch_succeeded

    try:
        succeeded = asyncio.run(scenario())
    finally:
        screen._close_launch_log_tee()
        if screen._launch_log_path is not None:
            screen._launch_log_path.unlink(missing_ok=True)
    return codes, statuses, succeeded


# ─── The two front ends agree ────────────────────────────────────────


@pytest.mark.parametrize("fixture", sorted(FIXTURES))
def test_tui_and_headless_state_the_same_result(fixture, monkeypatch, capsys):
    """AC2 + AC3 + AC4: same probes, same result block, same exit code."""
    code, captured = _run_linear(monkeypatch, capsys, fixture)
    tui_codes, statuses, succeeded = _run_tui(fixture)
    linear_block = _result_block(captured.out.splitlines())
    tui_block = _result_block(statuses)

    assert _same_result(tui_block) == _same_result(linear_block)
    expected = FIXTURES[fixture][2]
    assert f"Launch result: {expected}" in tui_block[0]
    # Exit behaviour: probes never fail a launch that converged.
    assert code == 0
    assert tui_codes == [] and succeeded is True
    # Readiness gates retained: both still ran to Compose convergence.
    assert "All services started" in " ".join(statuses)


@pytest.mark.parametrize("fixture", ["failed", "exception"])
def test_a_qualified_result_names_a_next_action_in_both_front_ends(
    fixture, monkeypatch, capsys
):
    _code, captured = _run_linear(monkeypatch, capsys, fixture)
    _codes, statuses, _ = _run_tui(fixture)
    assert "Next: check the output above" in captured.out
    assert "Next: check the Logs tab" in "\n".join(statuses)


def test_the_success_fixture_is_an_unqualified_success(monkeypatch, capsys):
    _code, captured = _run_linear(monkeypatch, capsys, "success")
    block = _result_block(captured.out.splitlines())
    assert block[0] == "✅ Launch result: verified — post-start verification passed"
    assert not any(line.startswith("  Next: ") for line in block)


def test_a_raising_probe_no_longer_crashes_the_headless_flow(monkeypatch, capsys):
    """Before #1032 the bare call let the exception escape as an
    "Unexpected error during startup" (exit 1) after the stack was up."""
    code, captured = _run_linear(monkeypatch, capsys, "exception")
    assert code == 0
    assert "[verify/ports] unverified — RuntimeError: port 63000 already bound" in captured.out
    assert "not verified: ports" in captured.out


def test_a_skipped_probe_is_labelled_with_its_reason(monkeypatch, capsys):
    _code, captured = _run_linear(monkeypatch, capsys, "skipped")
    block = "\n".join(_result_block(captured.out.splitlines()))
    assert f"comfyui-models skipped ({_SKIP_REASON})" in block
    assert "skipped: comfyui-models" in block


def test_a_failed_probe_degrades_rather_than_passes(monkeypatch, capsys):
    _code, captured = _run_linear(monkeypatch, capsys, "failed")
    block = _result_block(captured.out.splitlines())
    assert "degraded" in block[0]
    assert "verification failed: ports (2 checks did not pass)" in block[0]
    assert any(line.startswith("  ✗ Application verification") for line in block)


# ─── Readiness gates are retained ────────────────────────────────────


def test_a_failed_compose_start_still_fails_headless_before_any_result(
    monkeypatch, capsys
):
    starter = _Starter("success")
    starter.start_docker_services = lambda **_kwargs: False
    monkeypatch.setattr(linear_startup, "warn_if_submodule_pin_drifted", lambda *_: None)
    assert linear_startup.run_linear_startup(starter, _options()) == 1
    assert "Launch result:" not in capsys.readouterr().out


def test_a_failed_init_container_still_fails_the_tui_before_any_result():
    starter = _Starter("success")
    starter.verify_one_shot_init_containers = lambda *_args: False
    codes, statuses, succeeded = _run_tui("success", starter)
    assert codes == [1] and succeeded is False
    assert not any("Launch result:" in line for line in statuses)
    assert any("Required init container failed" in line for line in statuses)


# ─── Detached health is the readiness gate ───────────────────────────


def test_detached_unhealthy_stack_fails_with_a_failed_result(monkeypatch, capsys):
    monkeypatch.setattr(linear_startup, "warn_if_submodule_pin_drifted", lambda *_: None)
    starter = _Starter("success", healthy=False)
    code = linear_startup.run_linear_startup(starter, _options(detach=True))
    out = capsys.readouterr().out
    assert code == 1
    assert "❌ Launch result: failed — service health" in out
    assert "  ✗ Service health — failed" in out


def test_detached_healthy_stack_with_unverified_probe_still_exits_zero(
    monkeypatch, capsys
):
    code, captured = _run_linear(monkeypatch, capsys, "exception", detach=True)
    assert code == 0
    assert "Launch result: unverified" in captured.out
    assert "  ✓ Service health — verified" in captured.out


def test_json_mode_keeps_one_document_on_stdout_and_the_result_on_stderr(
    monkeypatch, capsys
):
    code, captured = _run_linear(
        monkeypatch, capsys, "failed", detach=True, json_output=True
    )
    assert code == 0
    assert json.loads(captured.out) == {"ok": True, "services": []}
    assert "Launch result: degraded" in captured.err


# ─── Probe classification ────────────────────────────────────────────


@pytest.mark.parametrize(("result", "outcome"), [
    (None, "verified"), (True, "verified"), (0, "verified"),
    (False, "failed"), (3, "failed"), (ProbeSkipped("why"), "skipped"),
])
def test_probe_results_classify(result, outcome):
    assert classify_probe_result("p", result).outcome == outcome


def test_an_absent_or_raising_probe_is_skipped_or_unverified():
    assert run_probe("p", None).outcome == "skipped"
    assert run_probe("p", _ports_raise).outcome == "unverified"


def test_the_port_probe_counts_mismatches_in_the_headless_branch(monkeypatch, capsys):
    import start as start_module

    starter = start_module.AtlasStarter()
    env = {"WEAVIATE_PORT": "63020", "WEAVIATE_SOURCE": "container",
           "WEAVIATE_SCALE": "1", "COMFYUI_SCALE": "0"}
    monkeypatch.setattr(starter.config_parser, "parse_env_file", lambda: env)
    monkeypatch.setattr(starter.docker_manager, "show_container_status", lambda: None)
    monkeypatch.setattr(starter.docker_manager, "get_service_port", lambda *_: "1")
    assert starter.show_container_status_and_verify_ports() == 1
    assert "Expected port 63020 but got 1" in capsys.readouterr().out


def test_the_comfyui_probe_skips_with_a_reason_off_localhost(monkeypatch):
    import start as start_module

    starter = start_module.AtlasStarter()
    starter.service_config.service_sources = {"COMFYUI_SOURCE": "container-gpu"}
    result = starter.check_comfyui_models()
    assert isinstance(result, ProbeSkipped)
    assert "COMFYUI_SOURCE=localhost" in result.reason
    assert "container-gpu" in result.reason


def test_the_comfyui_probe_fails_when_the_host_models_are_missing(tmp_path):
    import start as start_module

    starter = start_module.AtlasStarter()
    starter.service_config.service_sources = {"COMFYUI_SOURCE": "localhost"}
    starter.service_config.config_parser = SimpleNamespace(
        parse_env_file=lambda: {"COMFYUI_LOCAL_MODELS_PATH": str(tmp_path / "nope")}
    )
    assert starter.check_comfyui_models(on_line=lambda *_: None) is False


# ─── Prompted is not running (AC1) ───────────────────────────────────


def test_prometheus_and_grafana_rows_are_choices_not_locked_services():
    from core.config_parser import ConfigParser
    from ui.textual import integration as I

    class _Hosts:
        def __getattr__(self, _name):
            return lambda *a, **k: False

    _steps, rows, *_ = I._build_steps_and_rows(ConfigParser(), _Hosts())
    by_name = {row.name: row for row in rows}
    for name in ("Prometheus", "Grafana"):
        assert by_name[name].configurable, f"{name} must not render as locked"
    for name in ("Kong API Gateway", "Redis", "LiteLLM", "Backend API"):
        assert not by_name[name].configurable, name


# ─── Lifecycle actions (AC5) ─────────────────────────────────────────


def test_every_lifecycle_action_names_all_three_consequences():
    for action in LIFECYCLE_ACTIONS:
        assert "running" in action.services or "stop" in action.services
        assert "configuration" in action.configuration and "kept" in action.configuration
        assert "data" in action.data


def test_only_cold_stop_deletes_data():
    assert [a for a in LIFECYCLE_ACTIONS if a.destructive] == [COLD_STOP]
    assert "DELETED" in COLD_STOP.data
    for action in (DETACH, CANCEL, STOP):
        assert action.data.startswith("no data deleted"), action


def test_cancel_keeps_containers_and_deletes_nothing():
    assert "keep running" in CANCEL.services
    assert CANCEL.data == "no data deleted"


def test_a_cancelled_textual_run_says_what_it_left(capsys):
    import start as start_module

    assert start_module._report_tui_exit(130) == 130
    out = capsys.readouterr().out
    assert "keep running" in out and "no data deleted" in out
    assert start_module._report_tui_exit(0) == 0
    assert capsys.readouterr().out == ""


def test_the_tui_announces_each_way_out_once_the_stack_is_up():
    _codes, statuses, _ = _run_tui("success")
    text = "\n".join(statuses)
    assert DETACH.line("ctrl+q") in text
    assert STOP.line("ctrl+s", "press twice to confirm") in text
    assert COLD_STOP.line("ctrl+x", "press twice to confirm") in text


def test_the_headless_flow_explains_detach_stop_and_cold_stop(monkeypatch, capsys):
    _code, captured = _run_linear(monkeypatch, capsys, "success")
    assert DETACH.line("Ctrl+C", "stops following the logs only") in captured.out
    assert STOP.line("./stop.sh") in captured.out
    assert "./stop.sh --cold — Cold stop" in captured.out


def test_declining_the_summary_starts_nothing_and_says_so(monkeypatch, capsys):
    starter = _Starter("success")
    starter.show_pre_launch_summary = lambda **_kwargs: False
    monkeypatch.setattr(linear_startup, "warn_if_submodule_pin_drifted", lambda *_: None)
    assert linear_startup.run_linear_startup(starter, _options()) == 0
    out = capsys.readouterr().out
    assert "nothing was started" in out and "no data deleted" in out
    assert "start_docker_services" not in starter.calls


def test_interrupting_the_headless_log_stream_is_a_detach(monkeypatch, capsys):
    import start as start_module

    starter = start_module.AtlasStarter()

    def _interrupt(**_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(starter.docker_manager, "show_container_logs", _interrupt)
    assert starter.show_container_logs() == 130
    out = capsys.readouterr().out
    assert DETACH.consequences in out


def test_stop_and_cold_stop_both_report_configuration_kept(capsys):
    from stop import AtlasStopper

    stopper = AtlasStopper()
    stopper.show_final_status(False, False)
    warm = capsys.readouterr().out
    stopper.show_final_status(True, False)
    cold = capsys.readouterr().out
    assert "Configuration (.env) kept" in warm and "Data volumes preserved" in warm
    assert "Configuration (.env) kept" in cold and "All data volumes removed" in cold


def test_the_stop_confirmation_states_consequences():
    notes: list[str] = []
    screen = SimpleNamespace(
        _phase="launch", _pending_teardown=None, _pending_teardown_deadline=0.0,
        notify=lambda text, **_kwargs: notes.append(text),
    )
    WizardScreen._arm_or_commit_teardown(screen, cold=False)
    assert notes and STOP.consequences.capitalize() in notes[0]
    assert "ctrl+s again" in notes[0]


def test_summarize_launch_is_front_end_neutral_apart_from_where():
    probes = [("ports", "unverified", "boom")]
    tui = summarize_launch(probes, where="the Logs tab").lines
    cli = summarize_launch(probes).lines
    assert tui[:-1] == cli[:-1]
    assert "the Logs tab" in tui[-1] and "the output above" in cli[-1]
