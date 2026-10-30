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
    # The replaced .env survives as a private backup (backup HMAC key etc.).
    backups = list(tmp_path.glob(".env.backup.cold.*"))  # separate retention slot
    assert len(backups) == 1
    assert "PROJECT_NAME=myshowcase" in backups[0].read_text(encoding="utf-8")
    assert backups[0].stat().st_mode & 0o077 == 0


def test_fresh_env_file_is_owner_only(tmp_path, monkeypatch):
    import start as start_module

    env = tmp_path / ".env"
    example = tmp_path / ".env.example"
    example.write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    example.chmod(0o644)
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))

    starter = start_module.AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = example

    assert starter.setup_env_file(cold_start=False) is True
    assert env.stat().st_mode & 0o777 == 0o600


def test_fresh_env_file_uses_atomic_private_writer(tmp_path, monkeypatch):
    import start as start_module

    env = tmp_path / ".env"
    example = tmp_path / ".env.example"
    example.write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))

    starter = start_module.AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = example
    writes = []

    def fake_private_write(path, text):
        writes.append((path, text))
        path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(start_module, "_write_private_text", fake_private_write)

    assert starter.setup_env_file(cold_start=False) is True
    assert writes == [(env, "PROJECT_NAME=atlas\n")]


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


def test_cold_stop_uses_project_override_without_host_wide_cleanup(monkeypatch):
    import stop as stop_module

    stopper = stop_module.AtlasStopper()
    calls = []

    def fake_execute_compose_command(args, *, use_env_file=True, project_name=None):
        calls.append((args, project_name))
        return 0

    monkeypatch.setattr(
        stopper.docker_manager,
        "execute_compose_command",
        fake_execute_compose_command,
    )

    def fake_project_volume_names(project_name):
        assert project_name == "myshowcase"
        return []

    monkeypatch.setattr(
        stopper.docker_manager, "_project_volume_names", fake_project_volume_names,
    )
    monkeypatch.setattr(
        stopper.docker_manager,
        "remove_project_networks",
        lambda project_name: pytest.fail("cold stop must not remove networks outside compose"),
    )
    assert not hasattr(stopper.docker_manager, "prune_system")

    assert stopper.stop_services(cold_stop=True, project_name="myshowcase") is True
    assert calls == [(["down", "--volumes", "--remove-orphans"], "myshowcase")]
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


def test_root_start_options_before_a_subcommand_are_flagged_not_fatal() -> None:
    """Every subcommand builds its own starter from .env, so `-p other doctor`
    silently checked the .env project. It must say so, without failing the
    invocations consumer scripts already make (`--no-tui ... doctor`)."""
    from click.testing import CliRunner

    import start as start_module

    flagged = CliRunner().invoke(start_module.main, ["-p", "other", "doctor", "--help"])
    assert flagged.exit_code == 0
    assert "--project" in flagged.output and "no effect on 'doctor'" in flagged.output

    quiet = CliRunner().invoke(start_module.main, ["--no-tui", "--json", "doctor", "--help"])
    assert quiet.exit_code == 0
    assert "no effect" not in quiet.output


def test_env_user_overlay_reads_export_lines_like_dotenv(tmp_path):
    """An `export KEY=` line used to become the key "export KEY", which then
    failed the merge and dropped the whole overlay."""
    from core.config_parser import parse_env_overlay_file

    overlay = tmp_path / ".env.user"
    overlay.write_text("export OPENAI_API_KEY=sk-1\nFOO='bar baz' # note\n", encoding="utf-8")
    assert parse_env_overlay_file(overlay) == {"OPENAI_API_KEY": "sk-1", "FOO": "bar baz"}


def test_project_name_via_env_values_or_env_file_is_validated(tmp_path):
    """Only `project_name:` was normalized; env.values / env.file wrote an
    invalid name to .env, after which ./stop.sh refused to run."""
    import pytest

    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    (tmp_path / "bad.env").write_text("PROJECT_NAME=Bad Name\n", encoding="utf-8")
    for body in ("name: c\nenv:\n  values:\n    PROJECT_NAME: \"My App\"\n",
                 "name: c\nenv:\n  file: bad.env\n"):
        manifest = tmp_path / "atlas.consumer.yml"
        manifest.write_text(body, encoding="utf-8")
        with pytest.raises(ConsumerManifestError, match="(?i)project"):
            load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


@pytest.mark.parametrize("side", ["start", "stop"])
def test_a_same_project_override_keeps_the_stored_spelling(tmp_path, monkeypatch, side):
    """`-p mystack` against `PROJECT_NAME=MyStack` must not rewrite .env:
    the volumes interpolate as `MyStack-*`, and a lower-cased name would
    start the next launch on new, empty ones (2026-10-08 run, cycle 77)."""
    env = tmp_path / ".env"
    env.write_text("PROJECT_NAME=MyStack\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))
    if side == "start":
        import start as start_module

        owner = start_module.AtlasStarter()
        persist = owner._persist_project_name
    else:
        import stop as stop_module

        owner = stop_module.AtlasStopper()
        persist = owner.persist_project_name
    owner.config_parser.env_file_path = env
    owner.config_parser.env_example_path = tmp_path / ".env.example"

    assert persist(normalize_project_name("MyStack")) is True
    assert env.read_text(encoding="utf-8") == "PROJECT_NAME=MyStack\n"
    # A different project is still persisted.
    assert persist("other") is True
    assert "PROJECT_NAME=other" in env.read_text(encoding="utf-8")


from types import SimpleNamespace
from core.docker_manager import DockerManager



def _owned_project_preflight_manager(tmp_path, file_project, project):
    from core.config_parser import ConfigParser

    env = tmp_path / "active.env"
    env.write_text(f"PROJECT_NAME={file_project}\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    overlay = tmp_path / "services" / "_user" / "owned" / "compose.yml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text('services: {owned: {environment: {OWNED_PROJECT: "${PROJECT_NAME:?selected project required}"}}}\n')
    manager = DockerManager.__new__(DockerManager)
    manager.root_dir = tmp_path
    manager.config_parser = ConfigParser(str(tmp_path))
    manager.config_parser.env_file_path = env
    manager.config_parser.load_consumer_config = lambda: SimpleNamespace(compose_overlays=())
    manager.project_name_override = project
    manager._compose_cmd = "docker compose"
    manager.output_sink = None
    manager._on_command = lambda _line: None
    return manager


def _owned_project_preflight_boundaries(manager, monkeypatch):
    import os
    from utils.system import compose_env

    observed = {"preflight": [], "execution": [], "queries": []}
    file_project = manager.config_parser.parse_env_file()["PROJECT_NAME"]

    def config(command, **kwargs):
        assert command[-2:] == ["config", "-q"]
        effective = kwargs.get("env", os.environ).get("PROJECT_NAME", file_project)
        observed["preflight"].append(effective)
        return SimpleNamespace(returncode=0 if effective else 17)

    def execute(command, *_args, **_kwargs):
        observed["execution"].append(compose_env(command).get("PROJECT_NAME", file_project))
        return 0

    monkeypatch.setattr("core.docker_manager.subprocess.run", config)
    monkeypatch.setattr("core.docker_manager.run_compose_child", execute)
    monkeypatch.setattr(manager, "_stream_compose_command", execute)
    monkeypatch.setattr(manager, "_project_volume_names", lambda project: observed["queries"].append(project) or [])
    return observed


@pytest.mark.parametrize("seam", ["execute-cold-stop", "stream-cold-start"])
@pytest.mark.parametrize("case", [
    (None, "old-owned", "new-owned", "new-owned"),
    ("", "old-owned", "new-owned", "new-owned"),
    ("foreign-owned", "old-owned", "new-owned", "new-owned"),
    ("new-owned", "old-owned", "new-owned", "new-owned"),
    (None, "MyStack", "mystack", "MyStack"),
    ("MYStack", "MyStack", "mystack", "MYStack"),
])
def test_cleanup_preflight_and_execution_use_the_same_selected_project(tmp_path, monkeypatch, seam, case):
    import os

    shell, file_project, project, expected = case
    if shell is None:
        monkeypatch.delenv("PROJECT_NAME", raising=False)
    else:
        monkeypatch.setenv("PROJECT_NAME", shell)
    manager = _owned_project_preflight_manager(tmp_path, file_project, project)
    observed = _owned_project_preflight_boundaries(manager, monkeypatch)
    success = (manager.perform_cold_stop_cleanup() if seam == "execute-cold-stop"
               else manager.perform_cold_start_cleanup(project_name=project))
    assert success is True and manager.teardown_overlays_dropped is False
    assert observed == {"preflight": [expected], "execution": [expected], "queries": [project]}
    assert (manager.project_name_override, os.environ.get("PROJECT_NAME")) == (project, shell)


@pytest.mark.parametrize("executable", [["docker", "compose"], ["docker-compose"]])
def test_redis_reference_probe_preserves_compose_inputs_and_cleans_model(tmp_path, monkeypatch, executable):
    import json
    from utils.system import _resolve_redis_password
    observed = []
    env = {"PROJECT_NAME": "selected", "OTHER": "synthetic-private"}
    model = tmp_path / "original" / "compose.yml"
    command = executable + ["--env-file=first.env", "--env-file", "second.env", "-p", "selected", "-f", str(model), "up"]

    def run(argv, **kwargs):
        probe = Path(argv[argv.index("-f") + 1])
        observed.append(probe)
        assert "synthetic-private" not in probe.read_text()
        assert json.loads(probe.read_text())["services"]["atlas-redis-env-probe"]["environment"] == {"VALUE": "${REDIS_PASSWORD}"}
        assert argv[:len(executable)] == executable
        assert argv[len(executable):-5] == ["--env-file", "first.env", "--env-file", "second.env", "-p", "selected", "--project-directory", str(model.parent)]
        assert argv[-3:] == ["config", "--format", "json"]
        assert kwargs == {"env": env, "timeout_seconds": 10, "max_output_bytes": 1024 * 1024}
        return SimpleNamespace(returncode=0, stdout=json.dumps({"services": {"atlas-redis-env-probe": {"environment": {"VALUE": " synthetic$${LITERAL} "}}}}))

    monkeypatch.setattr("core.process_runner.run_with_deadline", run)
    assert _resolve_redis_password(command, env) == " synthetic${LITERAL} "
    assert observed and not observed[0].exists()


@pytest.mark.parametrize("failure", ["timeout", "output-bound", "nonzero", "json", "shape", "interrupt"])
def test_redis_reference_probe_failure_is_value_free_and_removes_model(tmp_path, monkeypatch, failure):
    import subprocess
    from core.process_runner import CommandOutputTooLarge
    from utils.system import _resolve_redis_password
    observed = []

    def run(argv, **kwargs):
        observed.append(Path(argv[argv.index("-f") + 1]))
        if failure == "timeout":
            raise subprocess.TimeoutExpired(["synthetic-private"], 10, stderr="synthetic-private")
        if failure == "output-bound":
            raise CommandOutputTooLarge("synthetic-private")
        if failure == "interrupt":
            raise KeyboardInterrupt
        return SimpleNamespace(returncode=1 if failure == "nonzero" else 0, stdout="{}" if failure == "shape" else "synthetic-private")

    monkeypatch.setattr("core.process_runner.run_with_deadline", run)
    error = KeyboardInterrupt if failure == "interrupt" else RuntimeError
    with pytest.raises(error) as caught:
        _resolve_redis_password(["docker", "compose", "--env-file=active.env", "config"], {})
    if failure != "interrupt":
        assert str(caught.value) == "Could not resolve Redis password references with Compose"
        assert caught.value.__suppress_context__ is True
    assert observed and not observed[0].exists()


@pytest.mark.parametrize("assignment,expected", [
    ("REDIS_PASSWORD=${OTHER}", True),
    (' export REDIS_PASSWORD="${OTHER}" # comment', True),
    ("REDIS_PASSWORD='${OTHER}'", False),
    ("REDIS_PASSWORD=synthetic # ${COMMENT}", False),
    ("REDIS_PASSWORD=synthetic", False),
])
def test_redis_reference_detection_uses_winning_expanding_assignment(tmp_path, assignment, expected):
    from utils.system import _redis_password_expands
    first = tmp_path / "first.env"
    first.write_text("REDIS_PASSWORD=${EARLIER}\n")
    second = tmp_path / "second.env"
    second.write_text("\ufeff" + assignment + "\n")
    command = ["docker", "compose", "--env-file=" + str(first), "--env-file", str(second), "config"]
    assert _redis_password_expands(command) is expected
    assert _redis_password_expands(["docker", "compose", "config"]) is False


def test_redis_probe_keeps_explicit_project_directory_and_name(tmp_path):
    from utils.system import _redis_probe_command
    model = tmp_path / "probe.json"
    command = ["docker", "compose", "--project-directory=original", "--project-name=selected", "--env-file=relative.env", "config"]
    assert _redis_probe_command(command, model) == [
        "docker", "compose", "--project-directory", "original", "--project-name", "selected",
        "--env-file", "relative.env", "-f", str(model), "config", "--format", "json",
    ]


@pytest.mark.parametrize("shell", [{}, {"REDIS_PASSWORD": "synthetic${LITERAL}"}, {"REDIS_PASSWORD": ""}, {"REDIS_PASSWORD_URI": "synthetic-companion"}])
def test_redis_no_explicit_env_file_keeps_probe_fast_path(monkeypatch, shell):
    from utils.system import compose_env
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    monkeypatch.delenv("REDIS_PASSWORD_URI", raising=False)
    for key, value in shell.items():
        monkeypatch.setenv(key, value)

    def unexpected(*args, **kwargs):
        pytest.fail("The no-file or shell override fast path must not probe Compose")

    monkeypatch.setattr("core.process_runner.run_with_deadline", unexpected)
    child = compose_env(["docker", "compose", "config"])
    assert child.get("REDIS_PASSWORD") == shell.get("REDIS_PASSWORD")
    if "REDIS_PASSWORD" in shell:
        from urllib.parse import quote
        assert child["REDIS_PASSWORD_URI"] == quote(shell["REDIS_PASSWORD"], safe="")
