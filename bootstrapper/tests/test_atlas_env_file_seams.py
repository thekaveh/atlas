"""ATLAS_ENV_FILE must be honored at every seam, not just validation.

Regression guards for the half-wired custom-env-path feature:
- docker compose argv used to hardcode `--env-file=.env` at all four
  construction sites, so compose silently ran against the repo-root .env
  (or failed) while the bootstrapper validated/migrated the custom file.
- KeyGenerator wrote generated secrets to repo-root .env regardless.
- A relative ATLAS_ENV_FILE resolved against CWD, which differs between
  the uv launcher (bootstrapper/) and the system-python fallback (root).
- GENAI_ENV_FILE is honored as a deprecated alias (stderr warning fires
  once per process); these tests also pin the alias behavior.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import core.config_parser as config_parser_module
from core.config_parser import ConfigParser
from core.docker_manager import DockerManager
from utils.key_generator import KeyGenerator


@pytest.fixture(autouse=True)
def _reset_deprecation_warned(monkeypatch):
    """Reset the module-level deprecation guard between tests so the
    one-shot stderr warning is testable in isolation."""
    monkeypatch.setattr(config_parser_module, "_DEPRECATION_WARNED", False)


def _custom_env(tmp_path, monkeypatch) -> Path:
    env = tmp_path / "custom.env"
    env.write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))
    return env


def test_build_compose_command_uses_resolved_env_file(tmp_path, monkeypatch):
    env = _custom_env(tmp_path, monkeypatch)
    dm = DockerManager()
    monkeypatch.setattr(dm, "detect_docker_compose_command", lambda: "docker compose")
    cmd = dm._build_compose_command(["up", "-d"])
    assert f"--env-file={env}" in cmd
    assert "--env-file=.env" not in cmd


def test_build_compose_command_default_env_file_is_root_anchored(monkeypatch):
    monkeypatch.delenv("ATLAS_ENV_FILE", raising=False)
    monkeypatch.delenv("GENAI_ENV_FILE", raising=False)
    dm = DockerManager()
    monkeypatch.setattr(dm, "detect_docker_compose_command", lambda: "docker compose")
    monkeypatch.setattr(dm.config_parser, "env_file_exists", lambda: True)
    cmd = dm._build_compose_command(["ps"])
    expected = f"--env-file={dm.root_dir / '.env'}"
    assert expected in cmd


def test_streaming_compose_command_honors_project_override(tmp_path, monkeypatch):
    env = _custom_env(tmp_path, monkeypatch)
    dm = DockerManager()
    monkeypatch.setattr(dm, "detect_docker_compose_command", lambda: "docker compose")
    dm.project_name_override = "customproj"

    cmd = dm._build_compose_command(["down"])

    assert cmd[cmd.index("-p") + 1] == "customproj"
    assert f"--env-file={env}" in cmd


def test_get_service_port_honors_project_override(tmp_path, monkeypatch):
    env = _custom_env(tmp_path, monkeypatch)
    dm = DockerManager()
    dm.project_name_override = "customproj"
    seen = {}

    class Result:
        returncode = 0
        stdout = "0.0.0.0:63080\n"
        stderr = ""

    monkeypatch.setattr(dm, "detect_docker_compose_command", lambda: "docker compose")

    def fake_run(cmd, **_kwargs):
        seen["cmd"] = cmd
        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)

    assert dm.get_service_port("backend", "8000") == "63080"
    assert seen["cmd"][seen["cmd"].index("-p") + 1] == "customproj"
    assert f"--env-file={env}" in seen["cmd"]


def test_key_generator_targets_custom_env_file(tmp_path, monkeypatch):
    env = _custom_env(tmp_path, monkeypatch)
    kg = KeyGenerator()
    assert kg.env_file_path == env


def test_relative_atlas_env_file_anchors_at_root_dir(tmp_path, monkeypatch):
    (tmp_path / "rel.env").write_text("", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", "rel.env")
    cp = ConfigParser(str(tmp_path))
    assert cp.env_file_path == (tmp_path / "rel.env").resolve()


def test_update_env_file_preserves_backslashes_in_values(tmp_path, monkeypatch):
    """Backslash sequences in env values (legal in JWT/base64 secrets)
    must be written verbatim. A plain re.sub(pattern, replacement) would
    interpret \\1 / \\g<name> in the replacement and corrupt silently —
    the lambda-replacement form in
    SourceOverrideManager.update_env_file is the guard this test pins."""
    from utils.source_override_manager import SourceOverrideManager

    env = tmp_path / ".env"
    env.write_text("SOME_SECRET=old\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))
    mgr = SourceOverrideManager(ConfigParser())
    tricky = r"abc\1def\g<name>\\end"
    assert mgr.update_env_file({"SOME_SECRET": tricky}) is True
    # Verbatim inside single quotes: Docker Compose expands backslash
    # escapes in unquoted and double-quoted values.
    assert f"SOME_SECRET='{tricky}'\n" in env.read_text(encoding="utf-8")


def test_service_config_update_preserves_backslashes_in_values(tmp_path, monkeypatch):
    """ServiceConfig.update_env_file is the PRIMARY start-path .env writer
    and a near-duplicate of SourceOverrideManager.update_env_file. It must
    carry the same backslash-safe lambda re.sub — a plain replacement
    string would interpret \\1 / \\g<name> and silently corrupt a JWT /
    base64 secret. Pins the second copy so the two can't drift."""
    from services.service_config import ServiceConfig

    env = tmp_path / ".env"
    env.write_text("SOME_SECRET=old\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))
    sc = ServiceConfig()
    tricky = r"abc\1def\g<name>\\end"
    assert sc.update_env_file({"SOME_SECRET": tricky}, create_backup=False) is True
    # Verbatim inside single quotes: Docker Compose expands backslash
    # escapes in unquoted and double-quoted values.
    assert f"SOME_SECRET='{tricky}'\n" in env.read_text(encoding="utf-8")


def test_service_config_update_preserves_file_mode(tmp_path, monkeypatch):
    """A user-chmod'd 0600 .env must not come back umask-default. The
    atomic writer clamps the tmp file's mode to the original before
    writing secrets."""
    import os

    env = tmp_path / ".env"
    env.write_text("SOME_SECRET=old\n", encoding="utf-8")
    os.chmod(env, 0o600)
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))
    from services.service_config import ServiceConfig

    sc = ServiceConfig()
    assert sc.update_env_file({"SOME_SECRET": "new"}, create_backup=False) is True
    assert (os.stat(env).st_mode & 0o777) == 0o600


def test_service_config_update_is_atomic_no_tmp_left(tmp_path, monkeypatch):
    """The tmp + os.replace write must not leave any secrets-bearing
    temporary file beside `.env` on success."""
    env = tmp_path / ".env"
    env.write_text("SOME_SECRET=old\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))
    from services.service_config import ServiceConfig

    sc = ServiceConfig()
    assert sc.update_env_file({"SOME_SECRET": "new"}, create_backup=False) is True
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


def test_parse_env_file_quote_and_hash_semantics(tmp_path, monkeypatch):
    """Inline-comment stripping must be quote-aware: a `#` inside quotes
    is data (PASSWORD="ab#cd" was silently read as `ab`), an unquoted
    hash is a comment only when preceded by whitespace."""
    env = tmp_path / ".env"
    env.write_text(
        'QUOTED="ab#cd"\n'
        "SINGLE='x#y'\n"
        "UNQUOTED_HASH=ab#cd\n"
        "WITH_COMMENT=value  # trailing note\n"
        "PLAIN=simple\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))
    parsed = ConfigParser().parse_env_file()
    assert parsed["QUOTED"] == "ab#cd"
    assert parsed["SINGLE"] == "x#y"
    assert parsed["UNQUOTED_HASH"] == "ab#cd"
    assert parsed["WITH_COMMENT"] == "value"
    assert parsed["PLAIN"] == "simple"


def test_genai_env_file_deprecated_alias_resolves_and_warns(tmp_path, monkeypatch, capsys):
    """Setting only GENAI_ENV_FILE (the deprecated alias) still resolves
    the custom path AND emits a stderr deprecation warning once per
    process."""
    env = tmp_path / "legacy.env"
    env.write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    monkeypatch.delenv("ATLAS_ENV_FILE", raising=False)
    monkeypatch.setenv("GENAI_ENV_FILE", str(env))
    cp = ConfigParser(str(tmp_path))
    assert cp.env_file_path == env.resolve()
    captured = capsys.readouterr()
    assert "GENAI_ENV_FILE is deprecated" in captured.err
    assert "ATLAS_ENV_FILE" in captured.err


def test_atlas_env_file_takes_precedence_over_genai_alias(tmp_path, monkeypatch, capsys):
    """When both env vars are set, ATLAS_ENV_FILE wins and the
    deprecation warning does NOT fire (the legacy alias is never read)."""
    atlas_env = tmp_path / "atlas.env"
    legacy_env = tmp_path / "legacy.env"
    atlas_env.write_text("", encoding="utf-8")
    legacy_env.write_text("", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(atlas_env))
    monkeypatch.setenv("GENAI_ENV_FILE", str(legacy_env))
    cp = ConfigParser(str(tmp_path))
    assert cp.env_file_path == atlas_env.resolve()
    captured = capsys.readouterr()
    assert "deprecated" not in captured.err


# --- every .env writer honours `export KEY=` (#1368) ------------------------


def _writers():
    from services.service_config import ServiceConfig
    from utils.supabase_keys import SupabaseKeyGenerator

    def key_generator(root):
        KeyGenerator(str(root)).update_env_key("SUPABASE_JWT_SECRET", "new-value")

    def supabase_keys(root):
        SupabaseKeyGenerator(str(root)).update_env_file("new-value", "anon", "service")

    def service_config(root):
        ServiceConfig(ConfigParser(str(root))).update_env_file(
            {"SUPABASE_JWT_SECRET": "new-value"}, create_backup=False
        )

    def source_override_manager(root):
        from utils.source_override_manager import SourceOverrideManager

        SourceOverrideManager(ConfigParser(str(root))).update_env_file(
            {"SUPABASE_JWT_SECRET": "new-value"}
        )

    return [key_generator, supabase_keys, service_config, source_override_manager]


@pytest.mark.parametrize("write", _writers(), ids=lambda w: w.__name__)
def test_each_writer_rewrites_an_export_line_instead_of_appending(tmp_path, write):
    env = tmp_path / ".env"
    env.write_text("PROJECT_NAME=atlas\nexport SUPABASE_JWT_SECRET=old-value\n", encoding="utf-8")

    write(tmp_path)

    text = env.read_text(encoding="utf-8")
    assignments = [line for line in text.splitlines() if "SUPABASE_JWT_SECRET=" in line]
    assert assignments == ["export SUPABASE_JWT_SECRET=new-value"]
    assert ConfigParser(str(tmp_path)).parse_env_file()["SUPABASE_JWT_SECRET"] == "new-value"


@pytest.mark.parametrize("write", _writers(), ids=lambda w: w.__name__)
def test_each_writer_leaves_env_owner_only(tmp_path, write):
    """The .env holds secrets, so every writer forces 0o600. Seeding at 0o644
    catches a writer that drops `mode=0o600`, since the atomic writer would
    otherwise keep the existing mode. The unterminated last line survives."""
    import stat

    env = tmp_path / ".env"
    env.write_text("SUPABASE_JWT_SECRET=old-value\nOPENAI_API_KEY=sk-x", encoding="utf-8")
    env.chmod(0o644)

    write(tmp_path)

    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert "OPENAI_API_KEY=sk-x" in env.read_text(encoding="utf-8").splitlines()


def _start_writers():
    def merge_overrides(starter):
        starter._merge_env_file_overrides({"X": "1"})

    def remove_by_prefix(starter):
        starter._remove_env_keys_by_prefix("ATLAS_STORE_")

    def migration_marker(starter):
        import start

        start._prepare_migration_marker(starter.config_parser.env_file_path)

    return [merge_overrides, remove_by_prefix, migration_marker]


@pytest.mark.parametrize("write", _start_writers(), ids=lambda w: w.__name__)
def test_each_start_writer_leaves_env_owner_only(tmp_path, monkeypatch, write):
    """`doctor`, `compose validate` and `env backfill` reach these writers
    without the start-time chmod, so each must force 0o600 itself."""
    import stat

    import start

    monkeypatch.delenv("ATLAS_ENV_FILE", raising=False)
    monkeypatch.delenv("GENAI_ENV_FILE", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "SUPABASE_JWT_SECRET=old-value\nATLAS_STORE_A=1\n"
        "BOOTSTRAPPER_PORT_LAYOUT_VERSION=1\nBOOTSTRAPPER_PORT_LAYOUT_VERSION=2\n",
        encoding="utf-8",
    )
    env.chmod(0o644)
    before = env.read_text(encoding="utf-8")
    starter = start.AtlasStarter.__new__(start.AtlasStarter)
    starter.config_parser = ConfigParser(str(tmp_path))

    write(starter)

    assert env.read_text(encoding="utf-8") != before  # the writer ran
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert "SUPABASE_JWT_SECRET=old-value" in env.read_text(encoding="utf-8").splitlines()


def test_set_env_assignment_appends_only_when_the_key_is_absent():
    from utils.atomic_write import set_env_assignment

    assert set_env_assignment("A=1", "B", "2") == "A=1\nB=2\n"
    assert set_env_assignment("  export B = 1\n# B=0\n", "B", "2") == "  export B=2\n# B=0\n"
    assert set_env_assignment("B=1\nexport B=9\n", "B", "2") == "B=2\nexport B=2\n"


def test_a_byte_order_mark_does_not_rename_the_first_key(tmp_path, monkeypatch):
    """`\\ufeffPROJECT_NAME` used to be the first key's name (#1391)."""
    monkeypatch.delenv("ATLAS_ENV_FILE", raising=False)
    (tmp_path / ".env").write_bytes("﻿PROJECT_NAME=atlas\nBASE_PORT=63000\n".encode("utf-8"))

    env = ConfigParser(str(tmp_path)).parse_env_file()

    assert list(env) == ["PROJECT_NAME", "BASE_PORT"]


def _database_orchestrator():
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[2] / "services/backup/database_orchestrator.py"
    spec = importlib.util.spec_from_file_location("atlas_env_seam_database_orchestrator", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_database_orchestrator_reads_the_custom_env_file(tmp_path, monkeypatch):
    """The host backup/restore entry points read only <repo>/.env, so a
    consumer whose config lives outside the checkout was refused or, with a
    stale checkout .env, silently aimed at the wrong project."""
    module = _database_orchestrator()
    (tmp_path / ".env").write_text("PROJECT_NAME=stale\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    custom = tmp_path / "config/prod.env"
    custom.write_text("PROJECT_NAME=prod\nBACKUP_SOURCE=container\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "config")
    monkeypatch.setenv("ATLAS_ENV_FILE", "config/prod.env")  # anchored at the checkout

    assert module._env_file_values(tmp_path) == {"PROJECT_NAME": "prod", "BACKUP_SOURCE": "container"}
    configuration = module.DatabaseConfiguration(tmp_path, {}, "a" * 32)
    assert configuration.env_file == custom.resolve()
    coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.compose_env_file = configuration.env_file
    assert coordinator.compose_argv("ps", "-q") == [
        "docker", "compose", "--env-file", str(custom.resolve()), "ps", "-q",
    ]


def test_database_orchestrator_default_env_file_adds_no_compose_flag(tmp_path, monkeypatch):
    module = _database_orchestrator()
    monkeypatch.delenv("ATLAS_ENV_FILE", raising=False)
    monkeypatch.delenv("GENAI_ENV_FILE", raising=False)
    (tmp_path / ".env").write_text("PROJECT_NAME=local\n", encoding="utf-8")

    assert module._env_file_values(tmp_path) == {"PROJECT_NAME": "local"}
    configuration = module.DatabaseConfiguration(tmp_path, {}, "a" * 32)
    assert configuration.env_file is None
    coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.compose_env_file = configuration.env_file
    assert coordinator.compose_argv("ps") == ["docker", "compose", "ps"]


def test_database_orchestrator_honors_the_genai_alias_and_refuses_a_missing_file(
    tmp_path, monkeypatch, capsys
):
    module = _database_orchestrator()
    monkeypatch.delenv("ATLAS_ENV_FILE", raising=False)
    custom = tmp_path / "legacy.env"
    custom.write_text("PROJECT_NAME=legacy\n", encoding="utf-8")
    monkeypatch.setenv("GENAI_ENV_FILE", str(custom))
    assert module._env_file_values(tmp_path) == {"PROJECT_NAME": "legacy"}
    assert "GENAI_ENV_FILE is deprecated" in capsys.readouterr().err

    monkeypatch.setenv("ATLAS_ENV_FILE", str(tmp_path / "missing.env"))
    with pytest.raises(module.ContractError, match="missing env file"):
        module._env_file_values(tmp_path)
