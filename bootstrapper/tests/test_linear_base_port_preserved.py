"""Linear-flow port handling must preserve a previously configured BASE_PORT.

Regression guard: handle_port_configuration(None) used to fall straight to
DEFAULT_BASE_PORT, so any `--no-tui` / non-TTY run silently rewrote every
*_PORT in .env back to the 63000 layout for users who had configured a
custom base port (and left BASE_PORT itself inconsistent with the rewritten
ports). The TUI path already read BASE_PORT from .env; the linear path now
mirrors it.
"""
from __future__ import annotations

from core.config_parser import DEFAULT_BASE_PORT
from start import AtlasStarter


def _starter_with_env(tmp_path, monkeypatch, env_text: str):
    env = tmp_path / ".env"
    env.write_text(env_text, encoding="utf-8")
    starter = AtlasStarter()
    starter.config_parser.env_file_path = env
    monkeypatch.setattr(starter.port_manager, "get_port_conflicts", lambda bp: {})
    monkeypatch.setattr(starter.docker_manager, "are_project_containers_running", lambda: False)
    captured = {}

    def _capture(bp):
        captured["base_port"] = bp
        return True

    monkeypatch.setattr(starter.port_manager, "update_env_ports", _capture)
    return starter, captured


def test_none_base_port_preserves_env_value(tmp_path, monkeypatch):
    starter, captured = _starter_with_env(tmp_path, monkeypatch, "BASE_PORT=64000\n")
    assert starter.handle_port_configuration(None) is True
    assert captured["base_port"] == 64000


def test_none_base_port_falls_back_to_default_when_blank(tmp_path, monkeypatch):
    starter, captured = _starter_with_env(tmp_path, monkeypatch, "BASE_PORT=\n")
    assert starter.handle_port_configuration(None) is True
    assert captured["base_port"] == DEFAULT_BASE_PORT


def test_explicit_flag_still_wins(tmp_path, monkeypatch):
    starter, captured = _starter_with_env(tmp_path, monkeypatch, "BASE_PORT=64000\n")
    assert starter.handle_port_configuration(65000) is True
    assert captured["base_port"] == 65000


def test_update_env_ports_persists_base_port_itself(tmp_path, monkeypatch):
    """A --base-port run must rewrite BASE_PORT in .env, or the next
    flagless run (which preserves .env's BASE_PORT) reads the STALE
    anchor and silently reverts every *_PORT to the old layout —
    exactly the motivating case of the preserve fix."""
    from start import AtlasStarter

    env = tmp_path / ".env"
    env.write_text("BASE_PORT=63000\nKONG_HTTP_PORT=63000\n", encoding="utf-8")
    starter = AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.port_manager.config_parser = starter.config_parser
    monkeypatch.setattr(starter.port_manager, "get_port_conflicts", lambda bp: {})
    # Point the port manager's writer at the tmp env file.
    if hasattr(starter.port_manager, "env_file_path"):
        starter.port_manager.env_file_path = env
    assert starter.handle_port_configuration(64000) is True
    text = env.read_text(encoding="utf-8")
    assert "BASE_PORT=64000" in text, text
    assert "KONG_HTTP_PORT=64000" in text, text
    # And the follow-up flagless run now resolves the NEW anchor.
    captured = {}
    monkeypatch.setattr(
        starter.port_manager, "update_env_ports",
        lambda bp: captured.setdefault("bp", bp) or True,
    )
    assert starter.handle_port_configuration(None) is True
    assert captured["bp"] == 64000


def test_a_moved_port_block_stops_the_running_stack_first(tmp_path, monkeypatch):
    """No port conflicts, but the block moves: the running stack is stopped,
    so a service disabled in the same run does not keep its old ports."""
    starter, captured = _starter_with_env(tmp_path, monkeypatch, "BASE_PORT=63000\n")
    stopped = []
    monkeypatch.setattr(starter.docker_manager, "are_project_containers_running", lambda: True)
    monkeypatch.setattr(starter.docker_manager, "stop_services",
                        lambda **kw: stopped.append(kw) or 0)
    assert starter.handle_port_configuration(64000) is True
    assert stopped == [{"remove_volumes": False, "remove_orphans": True}]
    assert captured["base_port"] == 64000
    stopped.clear()
    assert starter.handle_port_configuration(63000) is True  # unchanged block: no stop
    assert stopped == []


def test_a_hand_edited_base_port_is_a_move_too(tmp_path, monkeypatch):
    """BASE_PORT edited in .env while *_PORT still name the old block: the
    running stack publishes the old ports and must be stopped first."""
    from core.config_parser import DEFAULT_BASE_PORT

    starter, _captured = _starter_with_env(tmp_path, monkeypatch, "BASE_PORT=64000\n")
    old_block = starter.port_manager.calculate_port_assignments(DEFAULT_BASE_PORT)
    (tmp_path / ".env").write_text("BASE_PORT=64000\n" + "".join(f"{k}={v}\n" for k, v in old_block.items()))
    assert starter._port_block_moves(64000) is True
    new_block = starter.port_manager.calculate_port_assignments(64000)
    (tmp_path / ".env").write_text("BASE_PORT=64000\n" + "".join(f"{k}={v}\n" for k, v in new_block.items()))
    assert starter._port_block_moves(64000) is False


def test_a_port_pinned_by_an_overlay_is_not_a_move(tmp_path, monkeypatch):
    """An overlay pin of one *_PORT is merged into .env every start and then
    reset; counting it stopped the running stack on every warm start."""
    starter, _captured = _starter_with_env(tmp_path, monkeypatch, "BASE_PORT=63000\n")
    block = starter.port_manager.calculate_port_assignments(63000)
    pinned = dict(block, KONG_HTTP_PORT=8000)
    (tmp_path / ".env").write_text("BASE_PORT=63000\n" + "".join(f"{k}={v}\n" for k, v in pinned.items()))
    starter._env_user_keys = {"KONG_HTTP_PORT"}
    assert starter._port_block_moves(63000) is False
    starter._env_user_keys = set()
    assert starter._port_block_moves(63000) is True
