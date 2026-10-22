"""Offline final-project/durable-auto contracts; every effect belongs to a fixture."""
from __future__ import annotations

import asyncio
import copy
from pathlib import Path
import subprocess
from types import SimpleNamespace as NS

import pytest

from core.config_parser import ConfigParser
from core.port_manager import PortManager
from start import AtlasStarter
from utils.source_override_manager import SourceOverrideManager

ROOT = Path(__file__).resolve().parents[2]
PORT_TITLE = "Base port  ·  range"
PROJECT_TITLE = "Project name  ·  namespace"


def forbidden(*args, **kwargs):
    raise AssertionError("unexpected process/provider/lifecycle effect")


def env_text(manager, base, project="consumer-a"):
    ports = manager.calculate_port_assignments(base)
    return (f"PROJECT_NAME={project}\nBASE_PORT={base}\nSECRET=owned-fixture-only\n"
            + "".join(f"{key}={value}\n" for key, value in ports.items()))


def write_consumer(path, project="consumer-a", base="auto"):
    path.write_text("name: synthetic-consumer\n"
                    + (f"project_name: {project}\n" if project else "")
                    + f"env:\n  values:\n    BASE_PORT: {base}\n")


def install_probes(starter, monkeypatch, docker):
    starter.probes = []
    starter.allocations = []
    pm = starter.port_manager
    monkeypatch.setattr(pm, "check_port_range_availability",
                        lambda base, *a, **kw: [base] if base in starter.occupied else [])
    monkeypatch.setattr(pm, "auto_base_port", lambda **kw: starter.allocations.append(kw) or starter.free)
    monkeypatch.setattr(pm, "get_port_conflicts", lambda base: {"KONG_HTTP_PORT": base}
                        if base in starter.occupied else {})
    monkeypatch.setattr(pm, "suggest_available_base_port", lambda: starter.free)

    def probe(argv, **kwargs):
        expected = ["docker", "ps", "-q", "--filter",
                    "label=com.docker.compose.project=" + argv[-1].split("=", 2)[-1]]
        assert argv == expected
        starter.probes.append(argv)
        if docker == "timeout":
            raise subprocess.TimeoutExpired(argv, 10)
        if docker == "oserror":
            raise OSError("fixture unavailable")
        project = argv[-1].split("=", 2)[-1]
        running = project == "consumer-a" or docker == "both"
        return NS(returncode=1 if docker == "nonzero" else 0,
                  stdout="owned-id\n" if running else "")

    monkeypatch.setattr("start.subprocess.run", probe)
    monkeypatch.setattr("subprocess.Popen", forbidden)


@pytest.fixture
def owned(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_ENV_USER_FILE", raising=False)
    monkeypatch.setenv("ATLAS_ENV_FILE", str(tmp_path / ".env"))
    manifest = tmp_path / "atlas.consumer.yml"
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    write_consumer(manifest)
    (tmp_path / "services").mkdir()
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    s = AtlasStarter.__new__(AtlasStarter)
    s.root_dir = tmp_path
    s.config_parser = ConfigParser(str(tmp_path))
    s.port_manager = PortManager(str(ROOT))
    s.port_manager.config_parser = s.config_parser
    s.source_override_manager = SourceOverrideManager(s.config_parser)
    s.messages, s.effects = [], []
    s.banner = NS(show_status_message=lambda *a, **kw: s.messages.append(a),
                  show_section_header=lambda *a, **kw: None)
    s.docker_manager = NS(project_name_override=None,
                          are_project_containers_running=lambda: False, stop_services=forbidden)
    s.base_port_auto_chosen = False
    s.stopped_previous_instance = s.project_stopped_this_run = False
    s.occupied, s.free = {20000}, 21000
    s.config_parser.env_example_path.write_text(env_text(s.port_manager, 20000))
    s.config_parser.env_file_path.write_text(env_text(s.port_manager, 20000))
    independent = tmp_path / "independent-a.env"
    independent.write_text(env_text(s.port_manager, 20000))
    s.inventory = {"containers": ["consumer-a-db"], "volumes": ["consumer-a-data"]}
    s.manifest = manifest
    s.immutable = lambda: (manifest.read_bytes(), independent.read_bytes(), copy.deepcopy(s.inventory))
    s.original = s.immutable()
    install_probes(s, monkeypatch, "normal")
    return s


def assert_result(s, project, base):
    env = s.config_parser.parse_env_file()
    assert env["PROJECT_NAME"] == project
    assert s.docker_manager.project_name_override == project
    assert env["BASE_PORT"] == str(base)
    assert all(env[key] == str(value) for key, value in
               s.port_manager.calculate_port_assignments(base).items())
    assert s.immutable() == s.original


@pytest.mark.parametrize("state", ["existing", "fresh", "cold"])
@pytest.mark.parametrize("manifest_project", ["consumer-a", None])
def test_cli_final_project_owns_allocation(owned, state, manifest_project):
    s = owned
    write_consumer(s.manifest, manifest_project)
    s.original = s.immutable()
    parsed_config = s.config_parser.load_consumer_config()
    parsed = copy.deepcopy(parsed_config.env_overrides)
    if state == "fresh":
        s.config_parser.env_file_path.unlink()
    assert s.setup_env_file(cold_start=state == "cold", project_name="consumer-b")
    assert s.handle_port_configuration(None)
    assert s.probes[0][-1] == "label=com.docker.compose.project=consumer-b"
    assert parsed_config.env_overrides == parsed
    assert s.config_parser.load_consumer_config().env_overrides == parsed
    assert_result(s, "consumer-b", 21000)
    assert ("PROJECT_NAME" in s._env_user_keys) == bool(manifest_project)


@pytest.mark.parametrize("docker", ["normal", "both", "nonzero", "timeout", "oserror"])
def test_changed_project_provenance_and_unknown_ownership(owned, monkeypatch, docker):
    s = owned
    install_probes(s, monkeypatch, docker)
    assert s.setup_env_file(False, project_name="consumer-b")
    unknown = docker in {"nonzero", "timeout", "oserror"}
    assert s.handle_port_configuration(None) is not unknown
    assert s.probes[0][-1].endswith("=consumer-b")
    assert s.config_parser.parse_env_file()["BASE_PORT"] == ("20000" if unknown else "21000")
    assert bool(s.allocations) is not unknown
    assert s.immutable() == s.original


@pytest.mark.parametrize("occupied", [False, True])
def test_same_project_durable_block_stays(owned, occupied):
    s = owned
    s.occupied = {20000} if occupied else set()
    assert s.setup_env_file(False, project_name="consumer-a")
    assert s.config_parser.parse_env_file()["BASE_PORT"] == "20000"
    assert not s.allocations


@pytest.mark.parametrize("base,occupied", [(22000, False), (22000, True), (20000, True)])
def test_explicit_numeric_is_never_reallocated(owned, base, occupied):
    s = owned
    s.occupied = {20000, base} if occupied else {20000}
    s.config_parser.env_file_path.write_text(env_text(s.port_manager, 63000))
    assert s.setup_env_file(False, base_port=base, project_name="consumer-b")
    assert s.handle_port_configuration(base) is not occupied
    assert not s.base_port_auto_chosen
    if not occupied:
        assert_result(s, "consumer-b", base)
    else:
        assert any("Port conflicts detected" in message[0] for message in s.messages)


@pytest.mark.parametrize("overlay,manifest,expected", [
    ("overlay-project", None, "overlay-project"),
    ("overlay-project", "consumer-c", "consumer-c"),
    (None, None, "consumer-a"),
])
def test_cold_fallback_respects_overlay_precedence(owned, overlay, manifest, expected):
    s = owned
    write_consumer(s.manifest, manifest)
    if overlay:
        (s.root_dir / ".env.user").write_text(f"PROJECT_NAME={overlay}\n")
    s.original = s.immutable()
    assert s.setup_env_file(True)
    assert s.config_parser.get_project_name() == expected
    assert s.probes[0][-1].endswith("=" + expected)


@pytest.mark.parametrize("fresh,success", [(False, True), (False, False), (True, True)])
def test_cold_cleanup_gates_replacement_and_targets_b(owned, fresh, success):
    s = owned
    original = s.config_parser.env_file_path.read_bytes()
    if fresh:
        s.config_parser.env_file_path.unlink()
    s.backfill_missing_env_vars = lambda: s.effects.append("backfill") or True

    def cleanup(**kwargs):
        s.effects.append(("cleanup", kwargs["project_name"]))
        assert s.config_parser.env_file_path.exists()
        if not fresh:
            assert s.config_parser.env_file_path.read_bytes() == original
        return success

    s.perform_cold_start_cleanup = cleanup
    assert s.prepare_environment(True, project_name="consumer-b") is success
    assert ("cleanup", "consumer-b") in s.effects
    if success:
        assert s.handle_port_configuration(None)
        assert_result(s, "consumer-b", 21000)
    else:
        assert s.config_parser.env_file_path.read_bytes() == original
        assert not s.allocations


@pytest.mark.parametrize("failure", ["project", "ports"])
def test_failures_stop_at_existing_boundary(owned, monkeypatch, failure):
    s = owned
    if failure == "project":
        monkeypatch.setattr(s.source_override_manager, "update_env_file", lambda *a: False)
        assert not s.setup_env_file(False, project_name="consumer-b")
        assert s.docker_manager.project_name_override is None
    elif failure == "ports":
        assert s.setup_env_file(False, project_name="consumer-b")
        monkeypatch.setattr(s.port_manager, "update_env_ports", lambda *a: False)
        assert not s.handle_port_configuration(None)
    assert s.immutable() == s.original


def test_no_free_auto_block_warns_then_refuses_conflicts(owned):
    s = owned
    s.free = None
    assert s.setup_env_file(False, project_name="consumer-b")
    assert not s.handle_port_configuration(None)
    assert any("could not find a free port block" in m[0] for m in s.messages)
    assert s.immutable() == s.original


def test_fresh_template_provenance_with_both_projects_running(owned, monkeypatch):
    s = owned
    s.config_parser.env_file_path.unlink()
    install_probes(s, monkeypatch, "both")
    assert s.setup_env_file(False, project_name="consumer-b")
    assert s.handle_port_configuration(None)
    assert_result(s, "consumer-b", 21000)


def test_repeat_b_warm_start_keeps_new_owned_block(owned, monkeypatch):
    s = owned
    assert s.setup_env_file(False, project_name="consumer-b")
    assert s.handle_port_configuration(None)
    s.allocations.clear()
    s.occupied = {20000, 21000}
    install_probes(s, monkeypatch, "both")
    assert s.setup_env_file(False, project_name="consumer-b")
    assert s.config_parser.parse_env_file()["BASE_PORT"] == "21000"
    assert not s.allocations


def test_cli_auto_integer_retains_final_source_recheck(owned, monkeypatch):
    s = owned
    s._cli_base_port_auto = s.base_port_auto_chosen = True
    s.occupied.add(21000)
    s.free = 22000
    assert s.setup_env_file(False, base_port=21000, project_name="consumer-b")
    assert s.handle_port_configuration(21000)
    assert_result(s, "consumer-b", 22000)


def test_cli_override_beats_all_overlay_identities_without_mutation(owned, monkeypatch):
    s = owned
    sibling = s.root_dir / ".env.user"
    external = s.root_dir / "external.env"
    sibling.write_text("PROJECT_NAME=sibling-project\n")
    external.write_text("PROJECT_NAME=external-project\n")
    monkeypatch.setenv("ATLAS_ENV_USER_FILE", str(external))
    original = (sibling.read_bytes(), external.read_bytes())
    assert s.setup_env_file(False, project_name="consumer-b")
    assert s.handle_port_configuration(None)
    assert s.probes[0][-1].endswith("=consumer-b")
    assert_result(s, "consumer-b", 21000)
    assert (sibling.read_bytes(), external.read_bytes()) == original
