"""Project-name override (--project / -p + PROJECT_NAME in .env + wizard step).

The container-family namespace is set by `docker compose -p <PROJECT_NAME>`.
start.sh and stop.sh must agree on it so stop tears down exactly what start
launched, and a submodule consumer must be able to isolate from a base Atlas
stack by setting their own name. These tests pin:

  * normalize_project_name() validation (Docker Compose project-name rules),
  * the persist→read loop (writing PROJECT_NAME to .env is what get_project_name
    — and therefore every `-p` and a later bare ./stop.sh — reads back),
  * the wizard's "Project name" step (default from .env, normalized on change,
    no-op when unchanged).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Repo root (parent of bootstrapper/) — so the wizard test finds services/
# regardless of the pytest working directory (CI runs from bootstrapper/).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

from core.config_parser import (
    ConfigParser,
    DEFAULT_PROJECT_NAME,
    normalize_project_name,
)


# ── normalize_project_name ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("atlas", "atlas"),
        ("MyShowcase", "myshowcase"),   # lower-cased like Docker Compose
        ("rag-showcase", "rag-showcase"),
        ("proj_1", "proj_1"),
        ("  Foo  ", "foo"),             # trimmed
    ],
)
def test_normalize_valid(raw, expected):
    assert normalize_project_name(raw) == expected


@pytest.mark.parametrize("bad", ["", "   ", "bad name", "has.dot", "-leading", "a/b", "x*y"])
def test_normalize_rejects_invalid(bad):
    with pytest.raises(ValueError):
        normalize_project_name(bad)


def test_default_project_name_constant():
    assert DEFAULT_PROJECT_NAME == "atlas"


# ── persist → read loop (the start/stop agreement mechanism) ─────────────────

def _cp(tmp_path, body: str) -> ConfigParser:
    env = tmp_path / ".env"
    env.write_text(body, encoding="utf-8")
    cp = ConfigParser(str(tmp_path))
    cp.env_file_path = env
    return cp


def test_get_project_name_reads_env(tmp_path):
    cp = _cp(tmp_path, "PROJECT_NAME=myshowcase\n")
    assert cp.get_project_name() == "myshowcase"


def test_get_project_name_normalizes_env_value(tmp_path):
    cp = _cp(tmp_path, "PROJECT_NAME=MyShowcase\n")
    assert cp.get_project_name() == "myshowcase"


def test_get_project_name_blank_env_value_defaults_to_atlas(tmp_path):
    cp = _cp(tmp_path, "PROJECT_NAME=   \n")
    assert cp.get_project_name() == "atlas"


def test_get_project_name_rejects_invalid_env_value(tmp_path):
    cp = _cp(tmp_path, "PROJECT_NAME=bad.name\n")
    with pytest.raises(ValueError, match="invalid project name"):
        cp.get_project_name()


def test_get_project_name_defaults_to_atlas(tmp_path):
    cp = _cp(tmp_path, "BASE_PORT=63000\n")  # no PROJECT_NAME
    assert cp.get_project_name() == "atlas"


def test_persist_then_read_round_trip(tmp_path):
    """Writing PROJECT_NAME to .env (as --project does) is what every compose
    -p and a later bare ./stop.sh read back — this is the agreement guarantee."""
    from utils.source_override_manager import SourceOverrideManager

    cp = _cp(tmp_path, "PROJECT_NAME=atlas\n")
    assert cp.get_project_name() == "atlas"
    SourceOverrideManager(cp).update_env_file({"PROJECT_NAME": "myshowcase"})
    # A FRESH ConfigParser reading the same .env (mimicking the next process —
    # e.g. a later bare ./stop.sh) sees the persisted name.
    assert ConfigParser(str(tmp_path)).get_project_name() == "myshowcase"
    assert cp.get_project_name() == "myshowcase"


def test_start_setup_env_aborts_when_project_name_persist_fails(tmp_path, monkeypatch):
    import start as start_module

    env = tmp_path / ".env"
    env.write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))

    starter = start_module.AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = tmp_path / ".env.example"
    monkeypatch.setattr(
        starter.source_override_manager,
        "update_env_file",
        lambda _overrides: False,
    )

    assert starter.setup_env_file(False, project_name="myshowcase") is False


def test_start_setup_env_rejects_invalid_persisted_project_before_mutation(tmp_path, monkeypatch):
    import start as start_module

    env = tmp_path / ".env"
    env.write_text("PROJECT_NAME=bad.name\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))

    starter = start_module.AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = tmp_path / ".env.example"

    def fail_if_called(_overrides):
        raise AssertionError("invalid persisted PROJECT_NAME should fail before .env mutation")

    monkeypatch.setattr(starter.source_override_manager, "update_env_file", fail_if_called)

    assert starter.setup_env_file(False) is False
    assert env.read_text(encoding="utf-8") == "PROJECT_NAME=bad.name\n"


def test_cold_start_applies_env_user_overlay_and_preserves_project_name(tmp_path, monkeypatch):
    import start as start_module

    env = tmp_path / ".env"
    example = tmp_path / ".env.example"
    overlay = tmp_path / ".env.user"
    env.write_text(
        "PROJECT_NAME=myshowcase\n"
        "DOWNSTREAM_ONLY=old-value\n",
        encoding="utf-8",
    )
    example.write_text(
        "PROJECT_NAME=atlas\n"
        "BASE_PORT=63000\n",
        encoding="utf-8",
    )
    overlay.write_text(
        "# consumer-owned overlay\n"
        "DOWNSTREAM_ONLY=kept-value\n"
        "QUOTED_HASH=\"a#b\"\n"
        "INLINE_COMMENT=kept  # comment\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))

    starter = start_module.AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = example

    assert starter.setup_env_file(cold_start=True) is True

    env_text = env.read_text(encoding="utf-8")
    parsed = starter.config_parser.parse_env_file()
    assert "BASE_PORT=63000" in env_text
    assert parsed["PROJECT_NAME"] == "myshowcase"
    assert parsed["DOWNSTREAM_ONLY"] == "kept-value"
    assert parsed["QUOTED_HASH"] == "a#b"
    assert parsed["INLINE_COMMENT"] == "kept"


def test_cold_start_project_flag_overrides_env_user_project_name(tmp_path, monkeypatch):
    import start as start_module

    env = tmp_path / ".env"
    example = tmp_path / ".env.example"
    overlay = tmp_path / ".env.user"
    env.write_text("PROJECT_NAME=oldshowcase\n", encoding="utf-8")
    example.write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    overlay.write_text("PROJECT_NAME=overlayshowcase\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))

    starter = start_module.AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = example

    assert starter.setup_env_file(cold_start=True, project_name="clishowcase") is True

    assert starter.config_parser.parse_env_file()["PROJECT_NAME"] == "clishowcase"


def test_existing_env_applies_external_env_user_file_on_every_start(tmp_path, monkeypatch):
    import start as start_module

    env_dir = tmp_path / "atlas"
    consumer_dir = tmp_path / "consumer"
    env_dir.mkdir()
    consumer_dir.mkdir()
    env = env_dir / ".env"
    example = env_dir / ".env.example"
    external_overlay = consumer_dir / "atlas.env.user"
    env.write_text(
        "PROJECT_NAME=myshowcase\n"
        "DOWNSTREAM_ONLY=old-value\n",
        encoding="utf-8",
    )
    example.write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    external_overlay.write_text(
        "DOWNSTREAM_ONLY=external-value\n"
        "EXTERNAL_ONLY=enabled\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))
    monkeypatch.setenv("ATLAS_ENV_USER_FILE", str(external_overlay))

    starter = start_module.AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = example

    assert starter.setup_env_file(cold_start=False) is True

    parsed = starter.config_parser.parse_env_file()
    assert parsed["PROJECT_NAME"] == "myshowcase"
    assert parsed["DOWNSTREAM_ONLY"] == "external-value"
    assert parsed["EXTERNAL_ONLY"] == "enabled"


def test_cold_start_applies_external_env_user_file_after_sibling_overlay(tmp_path, monkeypatch):
    import start as start_module

    env_dir = tmp_path / "atlas"
    consumer_dir = tmp_path / "consumer"
    env_dir.mkdir()
    consumer_dir.mkdir()
    env = env_dir / ".env"
    example = env_dir / ".env.example"
    sibling_overlay = env_dir / ".env.user"
    external_overlay = consumer_dir / "atlas.env.user"
    env.write_text("PROJECT_NAME=oldshowcase\n", encoding="utf-8")
    example.write_text(
        "PROJECT_NAME=atlas\n"
        "BASE_PORT=63000\n"
        "DOWNSTREAM_ONLY=example-value\n",
        encoding="utf-8",
    )
    sibling_overlay.write_text(
        "PROJECT_NAME=siblingshowcase\n"
        "DOWNSTREAM_ONLY=sibling-value\n"
        "SIBLING_ONLY=kept\n",
        encoding="utf-8",
    )
    external_overlay.write_text(
        "PROJECT_NAME=externalshowcase\n"
        "DOWNSTREAM_ONLY=external-value\n"
        "EXTERNAL_ONLY=enabled\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))
    monkeypatch.setenv("ATLAS_ENV_USER_FILE", str(external_overlay))

    starter = start_module.AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = example

    assert starter.setup_env_file(cold_start=True) is True

    parsed = starter.config_parser.parse_env_file()
    assert parsed["BASE_PORT"] == "63000"
    assert parsed["PROJECT_NAME"] == "externalshowcase"
    assert parsed["DOWNSTREAM_ONLY"] == "external-value"
    assert parsed["SIBLING_ONLY"] == "kept"
    assert parsed["EXTERNAL_ONLY"] == "enabled"


def test_cold_start_project_flag_overrides_external_env_user_project_name(tmp_path, monkeypatch):
    import start as start_module

    env = tmp_path / ".env"
    example = tmp_path / ".env.example"
    external_overlay = tmp_path / "consumer.env"
    env.write_text("PROJECT_NAME=oldshowcase\n", encoding="utf-8")
    example.write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    external_overlay.write_text("PROJECT_NAME=externalshowcase\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))
    monkeypatch.setenv("ATLAS_ENV_USER_FILE", str(external_overlay))

    starter = start_module.AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = example

    assert starter.setup_env_file(cold_start=True, project_name="clishowcase") is True

    assert starter.config_parser.parse_env_file()["PROJECT_NAME"] == "clishowcase"


def test_relative_external_env_user_file_resolves_against_invoker_cwd(tmp_path, monkeypatch):
    import start as start_module

    env_dir = tmp_path / "atlas"
    consumer_dir = tmp_path / "consumer"
    env_dir.mkdir()
    consumer_dir.mkdir()
    env = env_dir / ".env"
    example = env_dir / ".env.example"
    external_overlay = consumer_dir / "atlas.env.user"
    env.write_text("PROJECT_NAME=myshowcase\n", encoding="utf-8")
    example.write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    external_overlay.write_text("DOWNSTREAM_ONLY=relative-value\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))
    monkeypatch.setenv("ATLAS_ENV_USER_FILE", "atlas.env.user")
    monkeypatch.setenv("ATLAS_INVOKER_CWD", str(consumer_dir))

    starter = start_module.AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = example

    assert starter.setup_env_file(cold_start=False) is True

    assert starter.config_parser.parse_env_file()["DOWNSTREAM_ONLY"] == "relative-value"


def test_missing_external_env_user_file_warns_without_crashing(tmp_path, monkeypatch):
    import start as start_module

    env = tmp_path / ".env"
    example = tmp_path / ".env.example"
    missing_overlay = tmp_path / "missing.env.user"
    env.write_text("PROJECT_NAME=myshowcase\n", encoding="utf-8")
    example.write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))
    monkeypatch.setenv("ATLAS_ENV_USER_FILE", str(missing_overlay))

    starter = start_module.AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = example
    messages = []
    monkeypatch.setattr(
        starter.banner,
        "show_status_message",
        lambda message, status="info": messages.append((message, status)),
    )

    assert starter.setup_env_file(cold_start=False) is True

    assert starter.config_parser.parse_env_file() == {"PROJECT_NAME": "myshowcase"}
    assert any(status == "warning" and "ATLAS_ENV_USER_FILE" in message for message, status in messages)


# ── stop.py override ─────────────────────────────────────────────────────────

def test_stop_show_configuration_info_honors_override(tmp_path, monkeypatch):
    import stop as stop_module

    monkeypatch.setenv("ATLAS_ENV_FILE", str(tmp_path / ".env"))
    (tmp_path / ".env").write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    stopper = stop_module.AtlasStopper()
    # No override → the .env value; override → wins.
    assert stopper.show_configuration_info(False, False) == "atlas"
    assert stopper.show_configuration_info(False, False,
                                           project_name_override="myshowcase") == "myshowcase"


def test_stop_services_uses_project_override_for_compose(monkeypatch):
    import stop as stop_module

    stopper = stop_module.AtlasStopper()
    calls = []

    def fake_stop_services(*, remove_volumes=False, remove_orphans=True):
        calls.append(stopper.docker_manager.project_name_override)
        return 0

    monkeypatch.setattr(stopper.docker_manager, "stop_services", fake_stop_services)

    assert stopper.stop_services(cold_stop=False, project_name="myshowcase") is True
    assert calls == ["myshowcase"]
    assert stopper.docker_manager.project_name_override is None


def test_cold_stop_uses_project_override_for_compose_and_networks(monkeypatch):
    import stop as stop_module

    stopper = stop_module.AtlasStopper()
    calls = []
    networks = []

    def fake_execute_compose_command(args, *, use_env_file=True, project_name=None):
        calls.append((args, project_name))
        return 0

    monkeypatch.setattr(
        stopper.docker_manager,
        "execute_compose_command",
        fake_execute_compose_command,
    )
    monkeypatch.setattr(
        stopper.docker_manager,
        "remove_project_networks",
        lambda project_name: networks.append(project_name) or True,
    )
    monkeypatch.setattr(stopper.docker_manager, "prune_system", lambda **kwargs: 0)

    assert stopper.stop_services(cold_stop=True, project_name="myshowcase") is True
    assert calls == [(["down", "--volumes", "--remove-orphans"], "myshowcase")]
    assert networks == ["myshowcase"]
    assert stopper.docker_manager.project_name_override is None


# ── wizard "Project name" step ───────────────────────────────────────────────

def test_wizard_project_name_step_and_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_ENV_FILE", str(tmp_path / ".env"))
    (tmp_path / ".env").write_text("PROJECT_NAME=myshowcase\nBASE_PORT=63000\n", encoding="utf-8")

    from utils.hosts_manager import HostsManager
    from ui.textual.integration import _build_steps_and_rows, _selections_to_args

    # Repo root so service discovery finds services/ (CI runs from bootstrapper/);
    # ATLAS_ENV_FILE (set above) still points parse_env_file at the temp .env.
    cp = ConfigParser(str(REPO_ROOT))
    steps, _rows, info, cbp, _state, _cs = _build_steps_and_rows(cp, HostsManager())
    proj_steps = [s for s in steps if "Project name" in s.title]
    assert len(proj_steps) == 1, "expected exactly one Project name step"
    step = proj_steps[0]
    # Default pre-fills from the current .env value (not the hardcoded default).
    assert step.default_value == "myshowcase"
    assert step.kind == "text"

    env_vars = cp.parse_env_file()
    title = step.title
    # Changing the name → normalized value in stack_options.
    _, opts_changed = _selections_to_args({title: "NewName"}, info, cbp, env_vars=env_vars)
    assert opts_changed.get("project_name") == "newname"
    # Re-confirming the SAME name → no-op (None), so no spurious .env write.
    _, opts_same = _selections_to_args({title: "myshowcase"}, info, cbp, env_vars=env_vars)
    assert opts_same.get("project_name") is None
    # An invalid entry → no-op (None) rather than corrupting PROJECT_NAME.
    _, opts_bad = _selections_to_args({title: "bad name!"}, info, cbp, env_vars=env_vars)
    assert opts_bad.get("project_name") is None
