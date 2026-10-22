"""Wizard input acceptance, resolver and pipeline at owned offline boundaries."""
from __future__ import annotations

import asyncio
from functools import partial
from types import MethodType, SimpleNamespace as NS

import pytest
from textual.app import App
from textual.widgets import Input

from tests.test_final_project_auto_ports import (
    PORT_TITLE, PROJECT_TITLE, assert_result, env_text, forbidden, install_probes, owned,
    write_consumer,
)
from ui.textual import integration
from ui.textual.screens import wizard_screen as ws
from ui.textual.widgets.prompt_panel import PromptStep, PromptOption


def port_steps():
    return [PromptStep(title=PORT_TITLE, heading="Base port?", step_index=1,
                       step_total=2, kind="number", default_value="20000",
                       accepts_auto=True, number_min=1024, number_max=65000),
            PromptStep(title=PROJECT_TITLE, heading="Project?", step_index=2,
                       step_total=2, kind="text", default_value="consumer-a"),
            PromptStep(title="Confirm  ·  launch the stack", heading="Launch?",
                       step_index=3, step_total=3,
                       options=[PromptOption(value="yes", label="Yes")])]


def accept_port(tmp_path, monkeypatch, entries, launch=None):
    from ui import session_log
    original = session_log.SessionLogTee
    monkeypatch.setattr(session_log, "SessionLogTee", partial(
        original, config=session_log.SessionLogConfig(directory=str(tmp_path))))
    screen = ws.WizardScreen(steps=port_steps(), services=[], no_splash=True)

    class Wizard(App):
        def on_mount(self):
            self.push_screen(screen)

    async def interact():
        async with Wizard().run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            for raw in entries:
                if screen._step_index:
                    screen.action_back()
                    await pilot.pause()
                screen.query_one("#number-input", Input).value = raw
                screen.action_confirm()
                await pilot.pause()
            if launch is not None:
                await launch(screen, pilot)
            return dict(screen._selections)

    return asyncio.run(interact())


@pytest.mark.parametrize("entries,intent", [
    ([""], "inherited"), (["20000"], "numeric"), (["22000"], "numeric"),
    (["auto"], "auto"), (["", ""], "inherited"),
    (["20000", ""], "numeric"), (["", "20000"], "numeric"),
    (["auto", ""], "auto"), (["20000", "auto"], "auto"),
])
def test_actual_acceptance_preserves_three_port_intentions(tmp_path, monkeypatch, entries, intent):
    selections = accept_port(tmp_path, monkeypatch, entries)
    monkeypatch.setattr(integration, "_resolve_auto_base_port", lambda *a: 21000)
    _, options = integration._selections_to_args(selections, {}, 20000, {})
    assert options["base_port_intent"] == intent
    assert options["base_port_auto"] == (intent == "auto")


class InlineExecutor:
    def __init__(self, *args):
        pass

    def bridge_stream(self):
        return lambda: None

    def cancel_requested(self):
        return False

    async def run_in_thread(self, fn):
        return fn()


def pipeline_screen(s, options, monkeypatch):
    monkeypatch.setattr(ws, "_ThreadedComposeExecutor", InlineExecutor)
    s.hosts_manager = NS(set_logger=lambda *a: None)
    s.docker_manager.execute_compose_command = forbidden
    s.docker_manager.set_command_echo_callback = lambda *a: None
    for name in ("apply_source_overrides", "apply_profile_overrides", "apply_cloud_api_keys",
                 "apply_user_model_selections", "validate_source_configurations",
                 "reconcile_default_models", "preflight_managed_host_processes",
                 "unset_port_environment_variables"):
        setattr(s, name, lambda *a, **kw: True)
    for name in ("generate_encryption_keys", "generate_service_configuration",
                 "check_service_dependencies", "generate_kong_configuration",
                 "generate_litellm_configuration", "generate_comfyui_manifest",
                 "handle_hosts_configuration", "validate_localhost_services"):
        setattr(s, name, forbidden)
    s.validate_supabase_keys = lambda **kw: s.effects.append("ports-finished") or False
    screen = NS(_starter=s, _stack_options=options, _source_args={}, _safe_log=lambda *a, **kw: None,
                _write_status=lambda *a, **kw: None, _mark_launch_failed=lambda: None,
                _close_launch_log_tee=lambda: None, _on_base_port_change=None, _services=[])
    screen._show_launch_base_port = MethodType(ws.WizardScreen._show_launch_base_port, screen)
    screen._cold_cleanup_steps = MethodType(ws.WizardScreen._cold_cleanup_steps, screen)
    if hasattr(ws.WizardScreen, "_configure_launch_ports"):
        screen._configure_launch_ports = MethodType(ws.WizardScreen._configure_launch_ports, screen)
    return screen


@pytest.mark.parametrize("entry,base", [("", 21000), ("20000", 20000), ("22000", 22000), ("auto", 21000)])
def test_wizard_acceptance_through_mapper_and_pipeline(owned, monkeypatch, entry, base):
    s = owned
    assert s.setup_env_file(False)
    selections = accept_port(s.root_dir, monkeypatch, [entry])
    selections[PROJECT_TITLE] = "consumer-b"
    monkeypatch.setattr(integration, "_resolve_auto_base_port", lambda *a: 21000)
    _, options = integration._selections_to_args(selections, {}, 20000, s.config_parser.parse_env_file())
    # Use the production resolver from setup-flow's callback, without running its UI builder.
    if hasattr(s, "resolve_wizard_base_port"):
        s.resolve_wizard_base_port({}, options)
    assert options["base_port"] == base
    screen = pipeline_screen(s, options, monkeypatch)
    asyncio.run(ws.WizardScreen._run_pipeline_and_stream(screen))
    assert s.docker_manager.project_name_override == "consumer-b"
    if entry == "20000":
        assert "ports-finished" not in s.effects
        assert not s.base_port_auto_chosen
    else:
        assert "ports-finished" in s.effects
        assert_result(s, "consumer-b", base)
    assert s.immutable() == s.original


@pytest.mark.parametrize("docker", ["nonzero", "timeout", "oserror", "both"])
def test_wizard_final_identity_unknown_and_foreign_provenance(owned, monkeypatch, docker):
    s = owned
    assert s.setup_env_file(False)
    install_probes(s, monkeypatch, docker)
    selections = accept_port(s.root_dir, monkeypatch, [""])
    selections[PROJECT_TITLE] = "consumer-b"
    _, options = integration._selections_to_args(selections, {}, 20000, s.config_parser.parse_env_file())
    if hasattr(s, "resolve_wizard_base_port"):
        s.resolve_wizard_base_port({}, options)
    unknown = docker != "both"
    assert options["base_port"] == (20000 if unknown else 21000)
    assert s.probes[-1][-1].endswith("=consumer-b")
    assert bool(s.allocations) is not unknown


@pytest.mark.parametrize("success", [True, False])
def test_wizard_cold_cleanup_precedes_project_write(owned, monkeypatch, success):
    s = owned
    assert s.setup_env_file(False)
    original = s.config_parser.env_file_path.read_bytes()
    s.backfill_missing_env_vars = lambda: True

    def cleanup(**kwargs):
        s.effects.append(("cleanup", kwargs["project_name"]))
        assert s.config_parser.env_file_path.read_bytes() == original
        return success

    s.perform_cold_start_cleanup = cleanup
    screen = pipeline_screen(s, {"base_port": 21000, "project_name": "consumer-b",
                                 "cold": True, "cold_cleanup_pending": True}, monkeypatch)
    asyncio.run(ws.WizardScreen._run_pipeline_and_stream(screen))
    assert s.effects[0] == ("cleanup", "consumer-b")
    assert ("ports-finished" in s.effects) is success
    if not success:
        assert s.config_parser.env_file_path.read_bytes() == original


@pytest.mark.parametrize("free", [21000, None])
def test_mounted_transition_routes_allocation_warnings_and_updates_preview(owned, monkeypatch, free):
    s = owned
    assert s.setup_env_file(False)
    executor = ws._ThreadedComposeExecutor
    pipeline_screen(s, {}, monkeypatch)  # Install only the owned effect boundaries.
    monkeypatch.setattr(ws, "_ThreadedComposeExecutor", executor)
    s.free = free
    original_banner, original_messages = s.banner, list(s.messages)
    seen, pane_warnings = {}, []
    warnings = ["BASE_PORT=auto: persisted block 20000 is bound by another stack on this host "
                "— re-resolving to a free block (the prior block was not ours to keep)."]
    if free is None:
        warnings.append("BASE_PORT=auto could not find a free port block; keeping "
                        "20000 (expect bind conflicts).")

    async def launch(screen, pilot):
        screen.query_one("#number-input", Input).value = "consumer-b"
        screen.action_confirm()
        screen._starter = s
        screen._stack_options_resolver = lambda selections: integration._selections_to_args(
            selections, {}, 20000, s.config_parser.parse_env_file())
        write_log = screen._log_pane.write_log

        def capture(message, **kwargs):
            if message in warnings:
                pane_warnings.append((message, kwargs))
            return write_log(message, **kwargs)

        monkeypatch.setattr(screen._log_pane, "write_log", capture)
        await screen._transition_to_launch()
        await screen.app.workers.wait_for_complete()
        seen.update(screen._stack_options)
        seen["log"] = screen._launch_log_path.read_text()
        assert screen._preview_base == (free or 20000)
        assert s.banner is original_banner

    accept_port(s.root_dir, monkeypatch, [""], launch)
    assert pane_warnings == [(message, {"source": "pipeline", "level": "warn"}) for message in warnings]
    assert all(seen["log"].count(message) == 1 for message in warnings)
    assert s.messages == original_messages
    assert seen["base_port"] == (free or 20000)
    assert ("ports-finished" in s.effects) is (free is not None)
    assert_result(s, "consumer-b", free or 20000)


def test_mounted_transition_restores_banner_when_allocation_raises(owned, monkeypatch):
    s = owned
    assert s.setup_env_file(False)
    original_banner, original_messages = s.banner, list(s.messages)

    def unavailable(**kwargs):
        raise RuntimeError("owned allocation failure")

    monkeypatch.setattr(s.port_manager, "auto_base_port", unavailable)

    async def launch(screen, pilot):
        screen.query_one("#number-input", Input).value = "consumer-b"
        screen.action_confirm()
        screen._starter = s
        screen._stack_options_resolver = lambda selections: integration._selections_to_args(
            selections, {}, 20000, s.config_parser.parse_env_file())
        with pytest.raises(RuntimeError, match="owned allocation failure"):
            await screen._transition_to_launch()
        assert s.banner is original_banner
        assert "re-resolving" in screen._launch_log_path.read_text()
        assert not screen.app.workers

    accept_port(s.root_dir, monkeypatch, [""], launch)
    assert s.messages == original_messages
    assert s.config_parser.parse_env_file()["PROJECT_NAME"] == "consumer-a"


def test_final_sources_recheck_updates_options_and_preview(owned, monkeypatch):
    s = owned
    s.occupied = set()
    s.config_parser.env_file_path.write_text(env_text(s.port_manager, 63000))
    assert s.setup_env_file(False, project_name="consumer-b")
    options = {"base_port": 21000, "base_port_intent": "auto", "base_port_auto": True}
    s.resolve_wizard_base_port({}, options)
    s.occupied.add(21000)
    s.free = 22000
    screen = pipeline_screen(s, options, monkeypatch)
    assert screen._configure_launch_ports(21000)
    assert screen._stack_options["base_port"] == screen._preview_base == 22000
    assert_result(s, "consumer-b", 22000)


def test_inherited_numeric_without_manifest_auto_keeps_conflict(owned, monkeypatch):
    s = owned
    write_consumer(s.manifest, base="20000")
    s.original = s.immutable()
    assert s.setup_env_file(False)
    options = {"base_port": 20000, "base_port_intent": "inherited", "project_name": "consumer-b"}
    s.resolve_wizard_base_port({}, options)
    screen = pipeline_screen(s, options, monkeypatch)
    asyncio.run(ws.WizardScreen._run_pipeline_and_stream(screen))
    assert options["base_port"] == 20000
    assert "ports-finished" not in s.effects
    assert not s.allocations


@pytest.mark.parametrize("docker", ["nonzero", "timeout", "oserror"])
def test_unknown_final_ownership_cancels_early_auto_reallocation(owned, monkeypatch, docker):
    s = owned
    s.config_parser.env_file_path.write_text(env_text(s.port_manager, 63000))
    assert s.setup_env_file(False)
    assert s.base_port_auto_chosen
    s.occupied.add(21000)
    s.free = 22000
    install_probes(s, monkeypatch, docker)
    options = {"base_port": 21000, "base_port_intent": "inherited", "project_name": "consumer-b"}
    s.resolve_wizard_base_port({}, options)
    assert not s.handle_port_configuration(options["base_port"])
    assert not s.allocations
    assert s.config_parser.parse_env_file()["BASE_PORT"] == "21000"


def test_final_wizard_sources_are_probed_without_replaying_consumer_values(owned):
    s = owned
    assert s.setup_env_file(False)
    before = s.config_parser.env_file_path.read_bytes()
    options = {"base_port": 20000, "base_port_intent": "inherited", "project_name": "consumer-b"}
    s.resolve_wizard_base_port({"minio_source": "container"}, options)
    assert options["base_port"] == 21000
    assert s.allocations[-1] == {"source_overrides": {"MINIO_SOURCE": "container"}}
    assert s.config_parser.env_file_path.read_bytes() == before


def test_launch_preview_retains_an_explicit_localhost_port():
    from ui.textual.widgets.prompt_panel import SecondaryNumberInput
    row = NS(name="MinIO", port="9000")
    option = PromptOption(value="localhost", label="Host", secondary_number=SecondaryNumberInput(
        env_var="MINIO_LOCALHOST_PORT", default_value=9000, number_min=1024, number_max=65535))
    step = PromptStep(title="MinIO source", heading="Source?", step_index=1, step_total=1,
                      service_name="MinIO", options=[option])
    screen = NS(_services=[row], _steps=[step], _selections={
        "MinIO source": "localhost", "__secondary__:MINIO_LOCALHOST_PORT": "29999"},
        _on_base_port_change=lambda base, rows: [NS(name="MinIO", port="9000")],
        _service_table=NS(set_rows=lambda *a: None), _refresh_info_panel=lambda: None)
    screen._typed_host_port = MethodType(ws.WizardScreen._typed_host_port, screen)
    screen._reapply_typed_host_ports = MethodType(ws.WizardScreen._reapply_typed_host_ports, screen)
    ws.WizardScreen._show_launch_base_port(screen, 21000)
    assert screen._services[0].port == "29999"


def test_free_block_reassigned_to_final_project_is_owned_on_recheck(owned, monkeypatch):
    s = owned
    s.occupied = set()
    assert s.setup_env_file(False, project_name="consumer-b")
    s.occupied = {20000}
    install_probes(s, monkeypatch, "both")
    options = {"base_port": 20000, "base_port_intent": "inherited"}
    s.resolve_wizard_base_port({}, options)
    assert options["base_port"] == 20000
    assert not s.allocations


def test_pending_cold_wizard_preserves_auto_final_source_recheck(owned, monkeypatch):
    s = owned
    assert s.setup_env_file(False)
    s.base_port_auto_chosen = True
    s.backfill_missing_env_vars = lambda: True
    s.perform_cold_start_cleanup = lambda **kw: True
    screen = pipeline_screen(s, {"base_port": 21000, "base_port_auto": True,
                                 "base_port_intent": "auto", "project_name": "consumer-b",
                                 "cold": True, "cold_cleanup_pending": True}, monkeypatch)
    cleanup = screen._cold_cleanup_steps(s, 21000, "consumer-b")[0][1]
    assert cleanup()
    s.occupied.add(21000)
    s.free = 22000
    assert screen._configure_launch_ports(21000)
    assert screen._preview_base == screen._stack_options["base_port"] == 22000
    assert_result(s, "consumer-b", 22000)
