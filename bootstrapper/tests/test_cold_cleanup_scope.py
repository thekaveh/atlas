"""Cold cleanup must stay project-scoped and gate secret rotation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.docker_manager import DockerManager
from start import AtlasStarter


REPO = Path(__file__).resolve().parents[2]


def test_cold_start_cleanup_uses_one_project_scoped_compose_down(tmp_path, monkeypatch):
    manager = DockerManager(str(tmp_path))
    calls: list[tuple[list[str], str | None]] = []

    monkeypatch.setattr(
        manager,
        "stream_compose",
        lambda args, **_kwargs: calls.append((args, manager.project_name_override)) or 0,
    )
    assert not hasattr(manager, "prune_system")
    monkeypatch.setattr(
        manager,
        "remove_project_networks",
        lambda _project: (_ for _ in ()).throw(
            AssertionError("compose down owns project-network cleanup")
        ),
    )

    assert manager.perform_cold_start_cleanup(project_name="new-project") is True
    assert calls == [(["down", "--volumes", "--remove-orphans"], "new-project")]
    assert manager.project_name_override is None


def test_cold_start_cleanup_propagates_compose_failure(tmp_path, monkeypatch):
    manager = DockerManager(str(tmp_path))
    monkeypatch.setattr(manager, "stream_compose", lambda _args, **_kwargs: 17)

    assert manager.perform_cold_start_cleanup() is False


def test_cold_stop_cleanup_does_not_prune_unrelated_projects(tmp_path, monkeypatch):
    manager = DockerManager(str(tmp_path))
    calls: list[tuple[list[str], str | None]] = []

    monkeypatch.setattr(
        manager,
        "execute_compose_command",
        lambda args, project_name=None: calls.append((args, project_name)) or 0,
    )
    assert not hasattr(manager, "prune_system")
    monkeypatch.setattr(manager.config_parser, "get_project_name", lambda: "atlas")

    assert manager.perform_cold_stop_cleanup() is True
    assert calls == [(["down", "--volumes", "--remove-orphans"], "atlas")]


def test_all_entry_paths_prepare_environment_before_secret_rotation():
    linear = (
        REPO / "bootstrapper" / "core" / "linear_startup.py"
    ).read_text(encoding="utf-8")
    tui = (
        REPO
        / "bootstrapper"
        / "ui"
        / "textual"
        / "screens"
        / "wizard_screen.py"
    ).read_text(encoding="utf-8")

    main_flow = linear[linear.index("def run_linear_startup(") :]
    linear_prepare = main_flow.index("starter.prepare_environment(")
    linear_rotation = main_flow.index(
        "starter.generate_encryption_keys(cold_start=options.cold)"
    )
    assert linear_prepare < linear_rotation
    assert main_flow.count("starter.prepare_environment(") == 1
    assert "starter.setup_env_file(" not in main_flow

    assert '("Cold-start cleanup"' not in tui
    assert 'docker", "system", "prune"' not in tui


def test_prepare_environment_does_not_replace_env_after_cleanup_failure():
    starter = object.__new__(AtlasStarter)
    starter.config_parser = SimpleNamespace(env_file_exists=lambda: True)
    calls: list[str] = []
    starter.backfill_missing_env_vars = lambda: calls.append("backfill") or True
    starter.perform_cold_start_cleanup = lambda **_kwargs: calls.append("cleanup") or False
    starter.setup_env_file = lambda **_kwargs: calls.append("setup") or True

    assert starter.prepare_environment(cold_start=True) is False
    assert calls == ["backfill", "cleanup"]


def test_prepare_environment_cleans_before_replacing_env():
    starter = object.__new__(AtlasStarter)
    starter.config_parser = SimpleNamespace(env_file_exists=lambda: True)
    calls: list[str] = []
    starter.backfill_missing_env_vars = lambda: calls.append("backfill") or True
    starter.perform_cold_start_cleanup = lambda **_kwargs: calls.append("cleanup") or True
    starter.setup_env_file = lambda **_kwargs: calls.append("setup") or True

    assert starter.prepare_environment(cold_start=True) is True
    assert calls == ["backfill", "cleanup", "setup"]


def test_prepare_environment_materializes_missing_env_before_cold_cleanup():
    starter = object.__new__(AtlasStarter)
    starter.config_parser = SimpleNamespace(env_file_exists=lambda: False)
    calls: list[str] = []
    starter.perform_cold_start_cleanup = lambda **_kwargs: calls.append("cleanup") or True
    starter.setup_env_file = lambda **_kwargs: calls.append("setup") or True

    assert starter.prepare_environment(cold_start=True) is True
    assert calls == ["setup", "cleanup"]


def test_prepare_environment_passes_cli_project_to_cleanup():
    starter = object.__new__(AtlasStarter)
    starter.config_parser = SimpleNamespace(env_file_exists=lambda: True)
    projects: list[str | None] = []
    starter.backfill_missing_env_vars = lambda: True
    starter.perform_cold_start_cleanup = (
        lambda **kwargs: projects.append(kwargs.get("project_name")) or True
    )
    starter.setup_env_file = lambda **_kwargs: True

    assert starter.prepare_environment(cold_start=True, project_name="new-project")
    assert projects == ["new-project"]


def test_perform_cold_start_cleanup_forwards_project_to_docker_manager():
    """Exercise the REAL AtlasStarter method (the tests above stub it out with a
    ``**_kwargs`` lambda, so they never catch a signature mismatch). It must
    accept the CLI project name and forward it to the docker manager. Regression
    guard for the crash `./start.sh --cold` hit when the method took no
    project_name yet prepare_environment passed one, and for the docker-manager
    call dropping the override.
    """
    starter = object.__new__(AtlasStarter)
    forwarded: list[str | None] = []
    starter.docker_manager = SimpleNamespace(
        perform_cold_start_cleanup=lambda project_name=None: (
            forwarded.append(project_name) or True
        )
    )
    starter.banner = SimpleNamespace(
        show_section_header=lambda *_a, **_k: None,
        show_status_message=lambda *_a, **_k: None,
    )

    assert starter.perform_cold_start_cleanup(project_name="new-project") is True
    assert forwarded == ["new-project"]
