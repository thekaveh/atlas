from __future__ import annotations


def test_compose_minimum_matches_include_contract():
    from core.docker_manager import DockerManager

    assert DockerManager.MIN_COMPOSE_VERSION == (2, 20, 3)


def test_ensure_dependencies_fails_below_minimum_compose(monkeypatch, tmp_path):
    import start as start_module

    starter = start_module.AtlasStarter()
    starter.root_dir = tmp_path
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    monkeypatch.setattr(starter.docker_manager, "check_docker_available", lambda: True)
    monkeypatch.setattr(starter.docker_manager, "get_compose_command_display", lambda: "docker compose")
    monkeypatch.setattr(
        starter.docker_manager,
        "check_compose_version",
        lambda: (False, "Docker Compose v2.19.0 is below the minimum"),
    )

    assert starter.ensure_dependencies_available() is False


def test_ensure_dependencies_allows_recommended_compose(monkeypatch, tmp_path):
    import start as start_module

    starter = start_module.AtlasStarter()
    starter.root_dir = tmp_path
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    monkeypatch.setattr(starter.docker_manager, "check_docker_available", lambda: True)
    monkeypatch.setattr(starter.docker_manager, "get_compose_command_display", lambda: "docker compose")
    monkeypatch.setattr(
        starter.docker_manager,
        "check_compose_version",
        lambda: (True, "Docker Compose v2.26.0 OK."),
    )

    assert starter.ensure_dependencies_available() is True


def test_wizard_preflight_checks_compose_version() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "start.py").read_text(
        encoding="utf-8"
    )
    wizard_branch = src[src.index("else:\n            if not starter.docker_manager.check_docker_available()"):]

    assert "check_compose_version()" in wizard_branch
