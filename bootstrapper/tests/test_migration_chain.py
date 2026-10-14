"""End-to-end .env migration chain (v0 -> v1 -> v2 -> v3 -> v4 -> v5).

Each migration step is unit-tested in isolation (test_port_migration,
test_migration_v2, test_migration_v3, test_migration_v4,
test_migration_v5), but their
*composition* on a single legacy v0 .env -- the exact upgrade path for
a user on an old checkout who hasn't run the bootstrapper since the
topology refactor -- was not. ``run_port_migration`` chains the steps
behind sequential ``_needs_vN`` gates; this test feeds one legacy file
through a single call and asserts every migration gate advances and
every schema rewrite lands.
"""

from __future__ import annotations

from pathlib import Path

from services.migrations.migration_v1 import V0_OFFSETS
from services.migrations.migration_v1 import needs_migration as needs_v1
from services.migrations.migration_v2 import needs_migration as needs_v2
from services.migrations.migration_v3 import needs_migration as needs_v3
from services.migrations.migration_v4 import needs_migration as needs_v4
from services.migrations.migration_v5 import needs_migration as needs_v5


def _legacy_v0_env(tmp_path: Path) -> Path:
    """A v0 .env: no layout sentinel, a port at its v0 default, a legacy
    ``*_LOCALHOST_URL`` line (v2 fodder), ``COMFYUI_MODEL_SET`` (v3),
    and a stale curated Ollama model reference (v4 fodder)."""
    port_var, offset = next(iter(V0_OFFSETS.items()))
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "BASE_PORT=63000",
                f"{port_var}={63000 + offset}",
                "COMFYUI_LOCALHOST_URL=http://localhost:11434",
                "COMFYUI_MODEL_SET=sdxl",
                "LITELLM_DEFAULT_MODEL=ollama/qwen3.6:latest",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return env


def test_v0_env_migrates_through_the_full_chain(tmp_path):
    env = _legacy_v0_env(tmp_path)
    (tmp_path / ".env.example").write_text("BASE_PORT=63000\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    # Sanity: a true v0 file needs every migration before we start.
    assert (
        needs_v1(env)
        and needs_v2(env)
        and needs_v3(env)
        and needs_v4(env)
        and needs_v5(env)
    )

    from start import AtlasStarter

    starter = AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = tmp_path / ".env.example"

    starter.run_port_migration(no_port_migrate=False)

    # The composition invariant: every step's sentinel advanced, so the
    # chain ran end to end in order.
    assert not needs_v1(env)
    assert not needs_v2(env)
    assert not needs_v3(env)
    assert not needs_v4(env)
    assert not needs_v5(env)

    text = env.read_text(encoding="utf-8")
    # v2: URL schema rewritten to the PORT schema.
    assert "COMFYUI_LOCALHOST_PORT" in text
    # v3: old enum translated and removed.
    assert "COMFYUI_MODEL_SET=" not in text
    assert "COMFYUI_USER_MODELS" in text
    # v4: retired curated Ollama model reference rewritten.
    assert "LITELLM_DEFAULT_MODEL=ollama/qwen3.8:latest" in text
    assert "WEAVIATE_ENABLE_MODULES=text2vec-openai,text2vec-ollama,multi2vec-clip,generative-openai,generative-ollama,backup-filesystem" in text


def test_chain_is_idempotent_on_second_run(tmp_path):
    """A second ``run_port_migration`` on the already-migrated file is a
    no-op: the sentinels stay at their terminal state."""
    env = _legacy_v0_env(tmp_path)
    (tmp_path / ".env.example").write_text("BASE_PORT=63000\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    from start import AtlasStarter

    starter = AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = tmp_path / ".env.example"

    starter.run_port_migration(no_port_migrate=False)
    first = env.read_text(encoding="utf-8")
    starter.run_port_migration(no_port_migrate=False)
    second = env.read_text(encoding="utf-8")

    assert first == second
    assert not (needs_v1(env) or needs_v2(env) or needs_v3(env) or needs_v4(env) or needs_v5(env))


def test_v4_catalog_failure_remains_retryable(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "BOOTSTRAPPER_PORT_LAYOUT_VERSION=3\n"
        "LITELLM_DEFAULT_MODEL=ollama/qwen3.6:latest\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.example").write_text("BASE_PORT=63000\n", encoding="utf-8")

    from start import AtlasStarter
    import utils.llm_catalog as llm_catalog

    starter = AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = tmp_path / ".env.example"

    original_loader = llm_catalog.ollama_entries

    def _broken_loader():
        raise ValueError("synthetic malformed catalog")

    monkeypatch.setattr(llm_catalog, "ollama_entries", _broken_loader)
    starter.run_port_migration(no_port_migrate=False)
    assert needs_v4(env) is True
    assert "qwen3.6:latest" in env.read_text(encoding="utf-8")

    monkeypatch.setattr(llm_catalog, "ollama_entries", original_loader)
    starter.run_port_migration(no_port_migrate=False)
    assert needs_v4(env) is False
    assert "qwen3.8:latest" in env.read_text(encoding="utf-8")


def test_chain_preserves_pristine_v1_backup(tmp_path):
    """Regression: in a full v1→v2→v3→v4 chain the per-step backups must
    not collide. v1 and v3 both named the backup ``.env.backup.<ts>`` at
    second precision, so v3 — writing post-v1+v2 content — silently
    overwrote v1's pristine snapshot, the one that matters for rollback.
    Version-stamped names keep them distinct; v4 follows the same
    convention."""
    env = _legacy_v0_env(tmp_path)
    original = env.read_text(encoding="utf-8")
    (tmp_path / ".env.example").write_text("BASE_PORT=63000\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    from start import AtlasStarter

    starter = AtlasStarter()
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = tmp_path / ".env.example"
    starter.run_port_migration(no_port_migrate=False)

    v1_backups = list(tmp_path.glob(".env.backup.v1.*"))
    v3_backups = list(tmp_path.glob(".env.backup.v3.*"))
    v4_backups = list(tmp_path.glob(".env.backup.v4.*"))
    assert len(v1_backups) == 1, "v1 must leave exactly one (un-clobbered) backup"
    assert len(v3_backups) == 1
    assert len(v4_backups) == 1, (
        "v4 has a stale LITELLM_DEFAULT_MODEL to rewrite in this fixture, "
        "so it must also leave its own version-stamped backup"
    )
    # The v1 backup holds the PRISTINE original, not post-migration content.
    assert v1_backups[0].read_text(encoding="utf-8") == original
    # Distinct files — no name collision.
    assert v1_backups[0] != v3_backups[0] != v4_backups[0] != v1_backups[0]


# --- migrations read and rewrite `export KEY=` lines (#1368) ----------------


def test_migration_helpers_treat_an_export_line_as_the_key():
    from services.migrations import migration_v3, migration_v4, migration_v5

    text = "export COMFYUI_MODEL_SET=sdxl\n  export OTHER=1\n"
    assert migration_v3._parse_env(text)["COMFYUI_MODEL_SET"] == "sdxl"
    assert migration_v4._parse_env(text)["OTHER"] == "1"
    for module in (migration_v3, migration_v4):
        assert module._replace_or_append(text, "OTHER", "2") == (
            "export COMFYUI_MODEL_SET=sdxl\n  export OTHER=2\n"
        )
    assert migration_v3._strip_old_var_lines("# old\nexport COMFYUI_MODEL_SET=x\nA=1\n",
                                              "COMFYUI_MODEL_SET") == "A=1\n"
    updated, changed = migration_v5._add_module("export WEAVIATE_ENABLE_MODULES=text2vec-ollama\n")
    assert changed and updated.startswith("export WEAVIATE_ENABLE_MODULES=")


def test_v1_port_layout_rewrite_keeps_the_export_prefix(tmp_path):
    from services.migrations import migration_v1

    old_port = 63000 + migration_v1.V0_OFFSETS["LITELLM_PORT"]
    env = tmp_path / ".env"
    env.write_text(f"BASE_PORT=63000\nexport LITELLM_PORT={old_port}\n", encoding="utf-8")
    result = migration_v1.apply(env, {"LITELLM_PORT": old_port + 1}, 63000)
    lines = [line for line in env.read_text(encoding="utf-8").splitlines() if "LITELLM_PORT=" in line]
    assert lines == [f"export LITELLM_PORT={old_port + 1}"]
    assert "LITELLM_PORT" in result.rewritten


def test_an_export_prefixed_version_line_is_current(tmp_path):
    """`export BOOTSTRAPPER_PORT_LAYOUT_VERSION=5` read as "no version": all
    five migrations re-ran and v1 appended a second version line."""
    from services.migrations import migration_v1

    env = tmp_path / ".env"
    env.write_text("export BOOTSTRAPPER_PORT_LAYOUT_VERSION=5\nexport A=1\n", encoding="utf-8")
    assert [needs(env) for needs in (needs_v1, needs_v2, needs_v3, needs_v4, needs_v5)] == [False] * 5
    env.write_text("export BOOTSTRAPPER_PORT_LAYOUT_VERSION=0\n", encoding="utf-8")
    migration_v1.stamp_version(env, 1)
    assert env.read_text(encoding="utf-8") == "export BOOTSTRAPPER_PORT_LAYOUT_VERSION=1\n"

# Duplicate sentinels must follow the canonical reader's last assignment.

import json
import stat
from types import SimpleNamespace

import click
import pytest

_MARKER = "BOOTSTRAPPER_PORT_LAYOUT_VERSION"
_COMMON = (
    "BASE_PORT=63000\nLITELLM_DEFAULT_MODEL=ollama/qwen3.6:latest\n"
    "WEAVIATE_ENABLE_MODULES=text2vec-ollama\n"
    "# retain this comment\nCUSTOM_LITERAL='a$z\\q # literal'\n"
)


def _marker_starter(tmp_path, markers):
    from core.config_parser import ConfigParser
    from start import AtlasStarter

    env = tmp_path / ".env"
    env.write_bytes((markers + _COMMON).encode("utf-8"))
    events = []

    def record(*args, **kwargs):
        events.append(tuple(map(str, args)))

    starter = AtlasStarter.__new__(AtlasStarter)
    starter.root_dir = Path(__file__).resolve().parents[2]
    starter.config_parser = ConfigParser(str(tmp_path))
    starter.config_parser.env_file_path = env
    starter.banner = SimpleNamespace(
        console=SimpleNamespace(print=record), show_status_message=record,
    )
    return starter, env, events


def _assert_marker_completed(starter, env):
    parsed = starter.config_parser.parse_env_file()
    assert (
        parsed[_MARKER], parsed["LITELLM_DEFAULT_MODEL"],
        parsed["WEAVIATE_ENABLE_MODULES"], parsed["CUSTOM_LITERAL"],
    ) == ("5", "ollama/qwen3.8:latest", "text2vec-ollama,backup-filesystem", "a$z\\q # literal")
    assert [needs(env) for needs in (needs_v1, needs_v2, needs_v3, needs_v4, needs_v5)] == [False] * 5
    from utils.atomic_write import decode_env_assignment, env_lines
    assignments = [decode_env_assignment(line, origin=str(env)) for line in env_lines(env.read_text(encoding="utf-8-sig"))]
    assert [item[1] for item in assignments if item is not None and item[0] == _MARKER] == ["5"]


@pytest.mark.parametrize("markers", [
    f"{_MARKER}=5\n{_MARKER}=3\n",
    f"{_MARKER}=3\n{_MARKER}=3\n",
    f"{_MARKER}=9\n{_MARKER}=5\n{_MARKER}=3\n",
    f"{_MARKER}=5\nexport {_MARKER}='3' # old version\n",
    f"{_MARKER}=5\n  export\t{_MARKER}\t=\t\" 003 \" # old version\n",
    f"{_MARKER}=5\n{_MARKER}=0\n",
    f"{_MARKER}=5\n{_MARKER}=\n",
    f"\ufeff{_MARKER}=5\r\n{_MARKER}=3\r\n",
    f"{_MARKER}=5\n\u00a0{_MARKER}=3\n",
    f"{_MARKER}=5\n{_MARKER}\u00a0=3\n",
    f"{_MARKER}=5\n\f{_MARKER}=3\n",
    f"{_MARKER}=5\n\v{_MARKER}=3\n",
    f"{_MARKER}=5\n{_MARKER}=4\n\u00a0{_MARKER}=3\n",
    f"{_MARKER}=5\n\u00a0export\t{_MARKER}\u00a0=\u00a0' 003 '\n",
])
def test_duplicate_legacy_marker_completes_and_preserves_pristine_backup(tmp_path, markers):
    starter, env, events = _marker_starter(tmp_path, markers)
    original = env.read_bytes()
    starter.config_parser.parse_env_file()  # Populate the real reader cache.
    starter.run_port_migration(False)
    _assert_marker_completed(starter, env)
    backups = list(tmp_path.glob(".env.backup.migration-marker.*"))
    assert len(backups) == 1
    assert (backups[0].read_bytes(), stat.S_IMODE(backups[0].stat().st_mode)) == (original, 0o600)
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in tmp_path.glob(".env.backup.*"))
    assert "Stale-model-reference migration complete (v4)." in str(events)
    assert "Weaviate backup-module migration complete (v5)." in str(events)
    first = env.read_bytes(), sorted(tmp_path.glob(".env.backup.*")), list(events)
    starter.run_port_migration(False)
    assert (env.read_bytes(), sorted(tmp_path.glob(".env.backup.*")), events) == first


@pytest.mark.parametrize("markers", [
    f"{_MARKER}=3\n{_MARKER}=5\n", f"{_MARKER}=3\n{_MARKER}=9\n",
    f"{_MARKER}=5\n{_MARKER}=5\n", f"{_MARKER}=9\n{_MARKER}=9\n",
    f"\ufeff{_MARKER}=3\r\nexport {_MARKER}=' 5 ' # current\r\n",
    f"{_MARKER}=3\n\u00a0{_MARKER}=5\n",
    f"{_MARKER}=3\n{_MARKER}\u00a0=9\n",
])
def test_duplicate_current_or_future_marker_is_exact_noop(tmp_path, monkeypatch, markers):
    import start

    starter, env, events = _marker_starter(tmp_path, markers)
    original = env.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("current/future effective marker must bypass the historical chain")

    for name in ("_needs_v1", "_needs_v2", "_needs_v3", "_needs_v4", "_needs_v5"):
        monkeypatch.setattr(start, name, forbidden)
    starter.run_port_migration(False)
    assert (env.read_bytes(), list(tmp_path.glob(".env.backup.*")), events) == (original, [], [])


@pytest.mark.parametrize("markers", [
    f"{_MARKER}=5\n{_MARKER}=3\n", f"{_MARKER}=3\n{_MARKER}=5\n",
    f"{_MARKER}=3\n{_MARKER}=3\n", f"{_MARKER}=5\n{_MARKER}=5\n",
])
def test_skip_duplicate_markers_preserves_bytes_backups_and_messages(tmp_path, markers):
    starter, env, events = _marker_starter(tmp_path, markers)
    original = env.read_bytes()
    starter.run_port_migration(True)
    assert (env.read_bytes(), list(tmp_path.glob(".env.backup.*"))) == (original, [])
    assert "complete" not in str(events)
    if markers.startswith(f"{_MARKER}=3"):
        assert "--no-port-migrate" in str(events)


@pytest.mark.parametrize("value", ["-1", "3.5", "PRIVATE_MARKER_VALUE", "٣", "'3' trailing", "\"3"])
def test_invalid_duplicate_marker_is_value_free_expected_error(tmp_path, value):
    starter, env, events = _marker_starter(tmp_path, f"{_MARKER}=5\n{_MARKER}={value}\n")
    original = env.read_bytes()
    with pytest.raises(click.ClickException) as caught:
        starter.run_port_migration(False)
    assert (env.read_bytes(), list(tmp_path.glob(".env.backup.*")), events) == (original, [], [])
    assert str(env) in str(caught.value) and _MARKER in str(caught.value)
    assert value not in str(caught.value)


@pytest.mark.parametrize("failure", ["backup", "before-write", "after-replace"])
def test_marker_persistence_failure_stops_before_migration(tmp_path, monkeypatch, failure):
    import start

    starter, env, events = _marker_starter(tmp_path, f"{_MARKER}=5\n{_MARKER}=3\n")
    original = env.read_bytes()
    real_write = start.atomic_write_text

    def fail(*args, **kwargs):
        if failure == "after-replace":
            real_write(*args, **kwargs)
        raise OSError("PRIVATE_FAILURE_VALUE")

    monkeypatch.setattr(start, "create_private_backup" if failure == "backup" else "atomic_write_text", fail)
    with pytest.raises(click.ClickException) as caught:
        starter.run_port_migration(False)
    backups = list(tmp_path.glob(".env.backup.migration-marker.*"))
    assert len(backups) == (0 if failure == "backup" else 1)
    assert events == [] and "PRIVATE_FAILURE_VALUE" not in str(caught.value)
    if backups:
        assert (backups[0].read_bytes(), stat.S_IMODE(backups[0].stat().st_mode)) == (original, 0o600)
    if failure == "after-replace":
        parsed = starter.config_parser.parse_env_file()
        assert (parsed[_MARKER], parsed["LITELLM_DEFAULT_MODEL"], parsed["WEAVIATE_ENABLE_MODULES"]) == (
            "3", "ollama/qwen3.6:latest", "text2vec-ollama",
        )
        monkeypatch.setattr(start, "atomic_write_text", real_write)
        starter.run_port_migration(False)
        _assert_marker_completed(starter, env)
    else:
        assert env.read_bytes() == original


def test_duplicate_marker_catalog_failure_does_not_leapfrog_and_retries(tmp_path, monkeypatch):
    from services.migrations import migration_v4

    starter, env, events = _marker_starter(tmp_path, f"{_MARKER}=5\n{_MARKER}=3\n")
    with monkeypatch.context() as scoped:
        scoped.setattr(migration_v4, "_current_ollama_catalog", lambda: None)
        starter.run_port_migration(False)
    parsed = starter.config_parser.parse_env_file()
    assert (parsed[_MARKER], parsed["WEAVIATE_ENABLE_MODULES"]) == ("3", "text2vec-ollama")
    assert "will retry" in str(events) and "complete (v5)" not in str(events)
    starter.run_port_migration(False)
    _assert_marker_completed(starter, env)


@pytest.mark.parametrize("modules", [
    "WEAVIATE_ENABLE_MODULES='text2vec-ollama' trailing\n",
    "WEAVIATE_ENABLE_MODULES=text2vec-ollama\nWEAVIATE_ENABLE_MODULES=generative-ollama\n",
])
def test_duplicate_marker_v5_module_failure_remains_retryable(tmp_path, modules):
    from utils.atomic_write import atomic_write_text

    starter, env, events = _marker_starter(tmp_path, f"{_MARKER}=5\n{_MARKER}=4\n")
    env.write_text(env.read_text().replace("WEAVIATE_ENABLE_MODULES=text2vec-ollama\n", modules))
    starter.run_port_migration(False)
    assert needs_v5(env) and not needs_v4(env)
    assert "migration (v5) skipped" in str(events) and "complete (v5)" not in str(events)
    text = env.read_text().replace(modules, "WEAVIATE_ENABLE_MODULES=text2vec-ollama\n")
    atomic_write_text(env, text, mode=0o600)
    starter.run_port_migration(False)
    assert starter.config_parser.parse_env_file()[_MARKER] == "5"


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r", ""])
def test_marker_consolidation_preserves_bom_literal_lines_and_history(tmp_path, monkeypatch, ending):
    import start
    from utils.atomic_write import env_lines

    starter, env, events = _marker_starter(tmp_path, "")
    lines = [f"{_MARKER}=5", "# retain comment", "OTHER='a$z\\q # literal'", f"export {_MARKER}='3' # marker"]
    original = ("\ufeff" + (ending or "\n").join(lines) + ending).encode()
    env.write_bytes(original)
    # Stop at v4's existing retry seam so historical newline rewrites cannot mask preflight bytes.
    monkeypatch.setattr(start, "_apply_v4", lambda _: False)
    starter.run_port_migration(False)
    raw = env.read_bytes().decode("utf-8")
    expected_lines = ["# " + line if _MARKER in line else line for line in env_lines(original.decode("utf-8-sig"), keepends=True)]
    expected = "\ufeff" + "".join(expected_lines)
    expected += ("" if expected.endswith(("\r", "\n")) else "\n") + f"{_MARKER}=3\n"
    assert raw == expected
    assert env.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "complete" not in str(events)


def test_exotic_separator_and_unrelated_duplicates_are_not_promoted(tmp_path, monkeypatch):
    import start

    starter, env, _ = _marker_starter(tmp_path, f"{_MARKER}=5\n{_MARKER}=3\n")
    tail = f"UNRELATED=first\nUNRELATED=last\nOTHER='literal\u2028{_MARKER}=9'\n"
    env.write_text(env.read_text() + tail)
    monkeypatch.setattr(start, "_apply_v4", lambda _: False)
    starter.run_port_migration(False)
    parsed = starter.config_parser.parse_env_file()
    assert (parsed[_MARKER], parsed["UNRELATED"], parsed["OTHER"]) == ("3", "last", f"literal\u2028{_MARKER}=9")
    assert tail in env.read_text()


@pytest.mark.parametrize("symlink", [False, True])
def test_marker_repair_uses_active_env_path_and_owned_symlink(tmp_path, monkeypatch, symlink):
    from core.config_parser import ConfigParser

    root = tmp_path / "root"
    root.mkdir()
    starter, decoy, _ = _marker_starter(root, f"{_MARKER}=5\n")
    original_decoy = decoy.read_bytes()
    target = tmp_path / "owned.env"
    target.write_text(f"{_MARKER}=5\n{_MARKER}=3\n" + _COMMON)
    original_target = target.read_bytes()
    active = tmp_path / "active.env"
    if symlink:
        active.symlink_to(target)
    else:
        active = target
    monkeypatch.setenv("ATLAS_ENV_FILE", str(active))
    starter.config_parser = ConfigParser(str(root))
    starter.run_port_migration(False)
    _assert_marker_completed(starter, active)
    assert decoy.read_bytes() == original_decoy
    assert active.is_symlink() is symlink
    selected = starter.config_parser.env_file_path
    snapshot = list(selected.parent.glob(selected.name + ".backup.migration-marker.*"))
    assert len(snapshot) == 1 and snapshot[0].read_bytes() == original_target


def test_marker_boundary_chain_end_matches_manifest_default():
    import yaml
    from start import _ENV_MIGRATION_VERSION

    manifest = yaml.safe_load((Path(__file__).resolve().parents[2] / "services/globals/service.yml").read_text())
    assert next(item["default"] for item in manifest["env"] if item["name"] == _MARKER) == _ENV_MIGRATION_VERSION


@pytest.mark.parametrize("flow", ["linear", "setup", "launch", "json"])
def test_marker_error_blocks_public_flow_before_dependent_work(tmp_path, monkeypatch, flow):
    from click.testing import CliRunner
    from core.linear_startup import LinearStartupOptions, json_cli_guard, run_linear_startup
    from ui.textual import integration

    starter, env, events = _marker_starter(tmp_path, f"{_MARKER}=5\n{_MARKER}=PRIVATE_MARKER_VALUE\n")
    monkeypatch.setattr(starter, "show_banner", lambda: None)
    monkeypatch.setattr(starter, "prepare_environment", lambda **_: True)
    monkeypatch.setattr(starter, "backfill_missing_env_vars", lambda: True)

    def forbidden(*args, **kwargs):
        pytest.fail("migration error must stop before dependent work")

    monkeypatch.setattr(starter, "apply_source_overrides", forbidden)
    monkeypatch.setattr(integration, "_build_steps_and_rows", forbidden)
    if flow == "linear":
        options = LinearStartupOptions(False, None, None, {}, None, None, None, {}, {}, False, False, True, None, True, False, True)
        with pytest.raises(click.ClickException):
            run_linear_startup(starter, options)
    elif flow == "setup":
        with pytest.raises(click.ClickException):
            integration.run_setup_flow(starter.config_parser, None, starter=starter)
    elif flow == "launch":
        with pytest.raises(click.ClickException):
            integration.run_launch_flow(starter.config_parser, None, starter=starter, source_args={}, stack_options={})
    else:
        @click.command()
        @click.option("--json", "json_output", is_flag=True)
        @json_cli_guard
        def command(json_output):
            starter.run_port_migration(False)
        result = CliRunner().invoke(command, ["--json"])
        assert (result.exit_code, json.loads(result.stdout), result.stdout.count("\n")) == (1, {"ok": False, "exit_code": 1}, 1)
        assert _MARKER in result.stderr and "PRIVATE_MARKER_VALUE" not in result.stderr
        assert "Traceback" not in result.output
    assert events == [] and list(tmp_path.glob(".env.backup.*")) == []
