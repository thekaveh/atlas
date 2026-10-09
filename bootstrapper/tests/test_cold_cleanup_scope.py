"""Cold cleanup must stay project-scoped and gate secret rotation."""

from __future__ import annotations

import io
from pathlib import Path
import signal
from types import SimpleNamespace

import pytest

from core.docker_manager import DockerManager
from start import AtlasStarter


REPO = Path(__file__).resolve().parents[2]


def test_stream_compose_terminates_child_when_line_callback_fails(
    tmp_path, monkeypatch
):
    manager = DockerManager(str(tmp_path))
    manager._compose_cmd = "docker compose"
    monkeypatch.setattr(manager.config_parser, "get_project_name", lambda: "atlas")
    monkeypatch.setattr(manager.config_parser, "env_file_exists", lambda: False)

    class Process:
        def __init__(self):
            self.stdout = io.StringIO("compose output\n")
            self.signals = []

        def send_signal(self, sent):
            self.signals.append(sent)

        def wait(self, timeout=None):
            return 0

        def kill(self):
            raise AssertionError("graceful termination should succeed")

    process = Process()
    monkeypatch.setattr("core.docker_manager.subprocess.Popen", lambda *_a, **_k: process)

    with pytest.raises(RuntimeError, match="render failed"):
        manager.stream_compose(
            ["up"],
            on_line=lambda _line: (_ for _ in ()).throw(RuntimeError("render failed")),
        )

    assert process.signals == [signal.SIGINT]
    assert process.stdout.closed


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

    volume_queries = []
    monkeypatch.setattr(manager, "_project_volume_names", lambda project: volume_queries.append(project) or [])
    assert manager.perform_cold_start_cleanup(project_name="new-project") is True
    assert calls == [(["down", "--volumes", "--remove-orphans"], "new-project")]
    assert volume_queries == ["new-project"]  # the survivors check uses the overridden project
    assert manager.project_name_override is None


def test_cold_start_cleanup_propagates_compose_failure(tmp_path, monkeypatch):
    manager = DockerManager(str(tmp_path))
    monkeypatch.setattr(manager, "stream_compose", lambda _args, **_kwargs: 17)

    assert manager.perform_cold_start_cleanup() is False


def test_cold_start_cleanup_fails_when_consumer_overlays_were_dropped(tmp_path, monkeypatch):
    # Overlay-only volumes survive a base-file teardown; rotating secrets
    # after "success" would strand them with the old credentials.
    manager = DockerManager(str(tmp_path))

    def down(_args, **_kwargs):
        manager.teardown_overlays_dropped = True
        return 0

    monkeypatch.setattr(manager, "stream_compose", down)
    assert manager.perform_cold_start_cleanup() is False


def test_streamed_compose_down_survives_malformed_consumer_manifest(
    tmp_path, monkeypatch
):
    from core.consumer_manifest import ConsumerManifestError

    manager = DockerManager(str(tmp_path))
    manager._compose_cmd = "docker compose"
    monkeypatch.setattr(manager.config_parser, "get_project_name", lambda: "atlas")
    monkeypatch.setattr(manager.config_parser, "env_file_exists", lambda: False)
    monkeypatch.setattr(
        manager.config_parser,
        "load_consumer_config",
        lambda: (_ for _ in ()).throw(ConsumerManifestError("invalid yaml")),
    )
    commands = []

    class Process:
        stdout = io.StringIO("")

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(
        "core.docker_manager.subprocess.Popen",
        lambda command, **_kwargs: commands.append(command) or Process(),
    )

    assert manager.stream_compose(["down", "--remove-orphans"], lambda _line: None) == 0
    assert commands == [[
        "docker", "compose", "--ansi=always", "-p", "atlas",
        "-f", "docker-compose.yml", "down", "--remove-orphans",
    ]]


def test_streamed_compose_down_omits_an_overlay_rejected_by_preflight(
    tmp_path, monkeypatch
):
    manager = DockerManager(str(tmp_path))
    manager._compose_cmd = "docker compose"
    monkeypatch.setattr(manager.config_parser, "get_project_name", lambda: "atlas")
    monkeypatch.setattr(manager.config_parser, "env_file_exists", lambda: False)
    monkeypatch.setattr(
        manager, "_compose_file_args",
        lambda include_consumer=True: (["-f", "overlay.yml"] if include_consumer else []),
    )
    commands = []
    preflight_commands = []

    class Process:
        def __init__(self):
            self.stdout = io.StringIO("")
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(
        "core.docker_manager.subprocess.Popen",
        lambda command, **_kwargs: commands.append(command) or Process(),
    )
    monkeypatch.setattr(
        "core.docker_manager.subprocess.run",
        lambda command, **_kwargs: preflight_commands.append(command)
        or type("Result", (), {"returncode": 17})(),
    )

    assert manager.stream_compose(["down"], lambda _line: None) == 0
    assert preflight_commands[0][-4:] == ["-f", "overlay.yml", "config", "-q"]
    assert commands == [
        [
            "docker", "compose", "--ansi=always", "-p", "atlas",
            "-f", "docker-compose.yml", "down",
        ]
    ]


def test_streamed_compose_down_propagates_operational_failure(
    tmp_path, monkeypatch
):
    manager = DockerManager(str(tmp_path))
    manager._compose_cmd = "docker compose"
    monkeypatch.setattr(manager.config_parser, "get_project_name", lambda: "atlas")
    monkeypatch.setattr(manager.config_parser, "env_file_exists", lambda: False)
    monkeypatch.setattr(manager, "_compose_file_args", lambda: ["-f", "overlay.yml"])
    commands = []

    class Process:
        stdout = io.StringIO("")

        def wait(self, timeout=None):
            return 17

    monkeypatch.setattr(
        "core.docker_manager.subprocess.run",
        lambda *_args, **_kwargs: type("Result", (), {"returncode": 0})(),
    )
    monkeypatch.setattr(
        "core.docker_manager.subprocess.Popen",
        lambda command, **_kwargs: commands.append(command) or Process(),
    )

    assert manager.stream_compose(["down"], lambda _line: None) == 17
    assert commands[0][-3:] == ["-f", "overlay.yml", "down"]
    assert len(commands) == 1


@pytest.mark.parametrize(
    "overlay_path",
    (
        Path("services/_user/probe/compose.yml"),
        Path("volumes/minio/consumer-storage.compose.yml"),
    ),
)
def test_compose_down_falls_back_to_base_for_any_rejected_optional_overlay(
    tmp_path, monkeypatch, overlay_path
):
    manager = DockerManager(str(tmp_path))
    manager._compose_cmd = "docker compose"
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    target = tmp_path / overlay_path
    target.parent.mkdir(parents=True)
    target.write_text("services: {broken: [}\n")
    (tmp_path / "docker-compose.override.yml").write_text("services: {rogue: {}}\n")
    monkeypatch.setenv("COMPOSE_FILE", "docker-compose.override.yml")
    monkeypatch.setattr(manager.config_parser, "get_project_name", lambda: "atlas")
    monkeypatch.setattr(manager.config_parser, "env_file_exists", lambda: False)
    monkeypatch.setattr(
        manager.config_parser,
        "load_consumer_config",
        lambda: SimpleNamespace(compose_overlays=()),
    )
    preflights = []
    monkeypatch.setattr(
        "core.docker_manager.subprocess.run",
        lambda command, **_kwargs: preflights.append(command)
        or SimpleNamespace(returncode=17),
    )

    command = manager._build_compose_command(["down"])

    assert str(overlay_path) in preflights[0]
    assert command == [
        "docker", "compose", "-p", "atlas", "-f", "docker-compose.yml", "down",
    ]


def test_streamed_compose_down_reports_preflight_launch_failure(
    tmp_path, monkeypatch
):
    manager = DockerManager(str(tmp_path))
    manager._compose_cmd = "docker compose"
    monkeypatch.setattr(manager.config_parser, "get_project_name", lambda: "atlas")
    monkeypatch.setattr(manager.config_parser, "env_file_exists", lambda: False)
    monkeypatch.setattr(manager, "_compose_file_args", lambda: ["-f", "overlay.yml"])
    monkeypatch.setattr(
        "core.docker_manager.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("compose missing")),
    )
    monkeypatch.setattr(
        "core.docker_manager.subprocess.Popen",
        lambda *_args, **_kwargs: pytest.fail("down must not launch"),
    )
    lines = []

    assert manager.stream_compose(["down"], lines.append) == 1
    assert lines == ["❌ Error preparing docker compose command: compose missing"]


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
    monkeypatch.setattr(manager, "_project_volume_names", lambda _project: [])

    assert manager.perform_cold_stop_cleanup() is True
    assert calls == [(["down", "--volumes", "--remove-orphans"], "atlas")]


def test_cold_stop_is_not_reported_complete_while_project_volumes_remain(tmp_path, monkeypatch, capsys):
    """A bare --cold of a stack started with --consumer left the overlay's
    volumes on disk and still printed "All data volumes removed"."""
    manager = DockerManager(str(tmp_path))
    monkeypatch.setattr(manager, "execute_compose_command", lambda args, project_name=None: 0)
    monkeypatch.setattr(manager.config_parser, "get_project_name", lambda: "atlas")
    monkeypatch.setattr(manager, "_project_volume_names", lambda project: ["atlas-app-data"])

    assert manager.perform_cold_stop_cleanup() is False
    assert "atlas-app-data" in capsys.readouterr().out


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


def test_ctrl_c_during_cold_teardown_propagates(tmp_path, monkeypatch, capsys):
    """#1357: it read as "Cold cleanup failed; secrets were not rotated"."""
    manager = DockerManager(str(tmp_path))
    manager._compose_cmd = "docker compose"
    monkeypatch.setattr(manager.config_parser, "get_project_name", lambda: "atlas")
    monkeypatch.setattr(manager.config_parser, "env_file_exists", lambda: False)

    class Process:
        stdout = io.StringIO("")
        def __iter__(self):
            return self
        def send_signal(self, _sent):
            return None
        def wait(self, timeout=None):
            return 130

    class Lines:
        def __iter__(self):
            return self
        def __next__(self):
            raise KeyboardInterrupt
        def close(self):
            return None

    process = Process()
    process.stdout = Lines()
    monkeypatch.setattr("core.docker_manager.subprocess.Popen", lambda *_a, **_k: process)

    with pytest.raises(KeyboardInterrupt):
        manager.perform_cold_start_cleanup(project_name="atlas")
    assert manager._reraise_stream_interrupt is False  # only for the teardown


# ─── storage inventory and model cleanup (#1194) ─────────────────────────

REPO = Path(__file__).resolve().parents[2]


def test_volume_ownership_comes_from_the_declared_names():
    import start

    declared = start.declared_project_volumes(REPO)
    sizes = {"atlas-n8n-data": 10, "atlas-comfyui-models": 4_000, "atlas-dev-n8n-data": 7, "other-redis-data": 3}
    rows = {row["volume"]: row for row in start.volume_inventory("atlas", declared, sizes)}

    assert rows["atlas-n8n-data"]["ownership"] == "atlas"
    assert rows["atlas-comfyui-models"]["holds"] == "models/artifacts"
    assert rows["atlas-dev-n8n-data"]["ownership"] == "unknown"  # another project's look-alike
    assert "other-redis-data" not in rows
    assert start.parse_docker_size("1.5GB") == 1_500_000_000 and start.parse_docker_size("0B") == 0


class _FakeStack:
    """`docker exec` against an Atlas Ollama + ComfyUI container pair, backed
    by in-memory model stores; anything else (a host daemon) is a failure."""

    def __init__(self, ollama, comfyui, fail_on=None):
        self.ollama, self.comfyui, self.fail_on, self.calls = dict(ollama), dict(comfyui), fail_on, []

    def run(self, argv, **_kw):
        import subprocess

        self.calls.append(argv)
        assert argv[:2] == ["docker", "exec"], argv
        container, cmd = argv[2], argv[3:]
        out, code = "", 0
        if cmd[:2] == ["ollama", "list"]:
            out = "NAME ID SIZE MODIFIED\n" + "".join(f"{n} abc {s / 1e9} GB 2 days ago\n" for n, s in self.ollama.items())
        elif cmd[:1] == ["find"]:
            out = "".join(f"{size}\t{path}\0" for path, size in self.comfyui.items())
        elif cmd[:2] == ["ollama", "rm"] or cmd[:1] == ["rm"]:
            code = self._remove(cmd[0], cmd[-1])
        assert container in ("atlas-ollama", "atlas-comfyui"), container
        return subprocess.CompletedProcess(argv, code, out, "boom" if code else "")

    def _remove(self, tool, target):
        if self.fail_on and target.endswith(self.fail_on):
            return 1
        if tool == "ollama":
            self.ollama.pop(target)
        else:
            self.comfyui.pop(target.removeprefix("/opt/ComfyUI/models/"))
        return 0


def _stack_root(tmp_path):
    (tmp_path / "volumes" / "comfyui").mkdir(parents=True, exist_ok=True)
    (tmp_path / "volumes" / "comfyui" / "active-models.tsv").write_text(
        "sd15\tcheckpoint\tv1-5-pruned-emaonly.safetensors\thttps://x\tsha\tcheckpoints\tcurated\trequired\n"
    )
    return tmp_path


def _stack(fail_on=None):
    from utils import llm_catalog

    catalog = [e.name for e in llm_catalog.ollama_entries()]
    env = {"LLM_PROVIDER_SOURCE": "ollama-container-cpu", "OLLAMA_USER_MODELS": catalog[0], "COMFYUI_SOURCE": "container-cpu"}
    stack = _FakeStack({catalog[0]: 5_000_000_000, catalog[1]: 2_000_000_000, "someone/custom:7b": 1_000_000_000},
                       {"checkpoints/v1-5-pruned-emaonly.safetensors": 4, "loras/mystery.safetensors": 2}, fail_on)
    return env, catalog, stack


def test_no_cleanup_can_include_a_stateful_volume(tmp_path, monkeypatch):
    import start

    env, _catalog, stack = _stack()
    monkeypatch.setattr(start.subprocess, "run", stack.run)
    items = start._live_model_items(env, _stack_root(tmp_path), "atlas")
    planned = start.deletion_set(items, named=[item["path"] for item in items])
    assert {item["volume"] for item in planned} <= set(start.MODEL_VOLUMES)
    start.remove_items(planned, start._remove_live_item("atlas"))
    for argv in stack.calls:  # only model removals inside the two model containers
        assert "volume" not in argv and argv[3] in ("ollama", "find", "rm"), argv
    assert not set(start.MODEL_VOLUMES) & {"supabase-db-data", "n8n-data", "graph-db-data", "weaviate-data", "minio-data"}


def test_host_daemons_and_a_missing_active_list_are_never_cleaned(tmp_path, monkeypatch, capsys):
    import start

    env, _catalog, stack = _stack()
    monkeypatch.setattr(start.subprocess, "run", stack.run)
    for source in ("ollama-localhost", "none", "disabled"):
        assert start._ollama_volume_items({**env, "LLM_PROVIDER_SOURCE": source}, "atlas") == []
    for source in ("localhost", "managed-localhost-mps", "disabled"):
        assert start._comfyui_volume_items({**env, "COMFYUI_SOURCE": source}, _stack_root(tmp_path), "atlas") == []
    assert start._comfyui_volume_items(env, tmp_path / "fresh-checkout", "atlas") == []
    assert stack.calls == []  # nothing was even listed
    assert "active-models.tsv is missing" in capsys.readouterr().out


def test_active_models_are_retained_and_unknown_items_need_naming(tmp_path, monkeypatch):
    import start

    env, catalog, stack = _stack()
    monkeypatch.setattr(start.subprocess, "run", stack.run)
    items = start._live_model_items(env, _stack_root(tmp_path), "atlas")

    labels = {item["path"]: item["label"] for item in items}
    assert labels["checkpoints/v1-5-pruned-emaonly.safetensors"] == "retained"
    assert labels["loras/mystery.safetensors"] == labels["someone/custom:7b"] == "unknown"
    assert (labels[catalog[0]], labels[catalog[1]]) == ("retained", "removable")
    assert [item["path"] for item in start.deletion_set(items, named=[])] == [catalog[1]]
    named = {item["path"] for item in start.deletion_set(items, named=["someone/custom:7b", catalog[0]])}
    assert named == {catalog[1], "someone/custom:7b"}  # a retained item stays even when named


def test_the_preview_is_what_is_removed_and_an_interruption_stays_consistent(tmp_path, monkeypatch):
    import start
    from click.testing import CliRunner

    root = _stack_root(tmp_path)
    env, catalog, stack = _stack()
    monkeypatch.setattr(start.subprocess, "run", stack.run)
    monkeypatch.setattr(start, "_storage_context", lambda: (env, root, "atlas"))
    before = start._live_model_items(env, root, "atlas")
    result = CliRunner().invoke(start.main, ["storage", "clean", "--name", "loras/mystery.safetensors", "--yes"])
    previewed = {line.split()[3] for line in result.output.splitlines() if "will remove" in line}
    gone = {item["path"] for item in before} - {item["path"] for item in start._live_model_items(env, root, "atlas")}
    assert result.exit_code == 0 and previewed == gone == {catalog[1], "loras/mystery.safetensors"}

    env, catalog, stack = _stack(fail_on="mystery.safetensors")
    monkeypatch.setattr(start.subprocess, "run", stack.run)
    monkeypatch.setattr(start, "_storage_context", lambda: (env, root, "atlas"))
    result = CliRunner().invoke(start.main, ["storage", "clean", "--name", "loras/mystery.safetensors", "--yes"])
    assert result.exit_code == 1 and "stopped at loras/mystery.safetensors" in result.output
    after = {item["path"]: item["label"] for item in start._live_model_items(env, root, "atlas")}
    assert catalog[1] not in after and after["loras/mystery.safetensors"] == "unknown"  # whole items only


def test_host_model_directories_are_sized_not_cleaned(tmp_path):
    import start

    (tmp_path / "models" / "checkpoints").mkdir(parents=True)
    (tmp_path / "models" / "checkpoints" / "a.safetensors").write_bytes(b"x" * 10)
    rows = start.host_model_directories({"COMFYUI_LOCAL_MODELS_PATH": str(tmp_path / "models"),
                                         "COMFYUI_MPS_MODELS_PATH": str(tmp_path / "gone")})
    assert [(row["variable"], row["bytes"]) for row in rows] == [
        ("COMFYUI_LOCAL_MODELS_PATH", 10), ("COMFYUI_MPS_MODELS_PATH", None)]


def test_cold_start_does_not_rotate_secrets_while_project_volumes_remain(tmp_path, monkeypatch):
    """The cold stop names surviving overlay volumes; the cold start reported
    success and then regenerated the credentials those volumes hold."""
    manager = DockerManager(str(tmp_path))
    lines = []
    manager._on_command = lines.append
    monkeypatch.setattr(manager, "stream_compose", lambda args, on_line=None: 0)
    monkeypatch.setattr(manager.config_parser, "get_project_name", lambda: "atlas")
    monkeypatch.setattr(manager, "_project_volume_names", lambda project: ["atlas_consumer-pgdata"])

    assert manager.perform_cold_start_cleanup() is False
    assert any("atlas_consumer-pgdata" in line for line in lines)


def test_compose_children_name_resources_after_their_project(tmp_path, monkeypatch):
    """`--cold --project foo` ran `down --volumes` under `-p foo` while .env
    (or the shell) still said PROJECT_NAME=atlas, deleting atlas-* volumes."""
    import subprocess

    manager = DockerManager(str(tmp_path))
    monkeypatch.setenv("PROJECT_NAME", "other")
    seen = {}

    import io

    class Proc:
        stdout = io.StringIO("")

        def wait(self, timeout=None):
            return 0

        returncode = 0

    def popen(cmd, **kwargs):
        seen["env"] = kwargs["env"]
        seen["cmd"] = cmd
        return Proc()

    monkeypatch.setattr(subprocess, "Popen", popen)
    manager._stream_compose_command(["docker", "compose", "-p", "foo", "down", "--volumes"], lambda _l: None)
    assert seen["env"]["PROJECT_NAME"] == "foo"


@pytest.mark.parametrize("case", [
    (None, "MyStack", None),        # same project, raw case kept: volumes stay MyStack-*
    ("other", "atlas", "atlas"),    # a stray export naming another project is overridden
    (None, "atlas", None),          # consistent: nothing to change
])
def test_compose_env_keeps_the_projects_own_spelling(tmp_path, monkeypatch, case):
    """ccb79508 pinned PROJECT_NAME to the lowercased -p, renaming a
    hand-edited MyStack's volumes to mystack-* (empty databases, orphaned
    data); it now only replaces a value that names a different project."""
    from utils.system import compose_env

    shell, env_file, expected = case
    env = tmp_path / ".env"
    env.write_text(f"PROJECT_NAME={env_file}\n", encoding="utf-8")
    if shell is None:
        monkeypatch.delenv("PROJECT_NAME", raising=False)
    else:
        monkeypatch.setenv("PROJECT_NAME", shell)
    project = env_file.lower()
    result = compose_env(["docker", "compose", "-p", project, f"--env-file={env}", "up"])
    if expected is None:
        assert result.get("PROJECT_NAME") in (None, shell)
    else:
        assert result["PROJECT_NAME"] == expected


def test_the_wizard_compose_executor_pins_project_name_too():
    import inspect

    from ui.textual.screens import wizard_screen

    assert "compose_env(command)" in inspect.getsource(wizard_screen._ThreadedComposeExecutor)


def test_execute_compose_command_pins_project_name_for_cold_stop(tmp_path, monkeypatch):
    """`./stop.sh --cold` runs `down --volumes` through execute_compose_command;
    its child must name resources after -p, not a stray exported value."""
    import subprocess

    manager = DockerManager(str(tmp_path))
    monkeypatch.setenv("PROJECT_NAME", "other")
    seen = {}
    monkeypatch.setattr(manager, "_validated_compose_file_args", lambda args, prefix: ([], False))
    monkeypatch.setattr(manager, "detect_docker_compose_command", lambda: "docker compose")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: seen.update(kw) or subprocess.CompletedProcess(cmd, 0))
    assert manager.execute_compose_command(["down", "--volumes"], project_name="foo") == 0
    assert seen["env"]["PROJECT_NAME"] == "foo"


def test_an_ollama_model_the_stack_routes_is_retained(monkeypatch, tmp_path):
    """Retention read OLLAMA_USER_MODELS alone, so after a .env edit (or a
    start that failed before stopping the old stack) storage clean deleted
    the embedding model the running LiteLLM still routed (2026-10-08 run,
    cycle 9)."""
    import start

    rendered = tmp_path / "volumes" / "litellm" / "config.yaml"
    rendered.parent.mkdir(parents=True)
    rendered.write_text("model_list:\n- model_name: chat\n  litellm_params: {model: ollama_chat/routed-chat}\n")
    env = {"OLLAMA_USER_MODELS": "qwen3.8:latest", "LITELLM_EMBEDDING_MODEL": "ollama/nomic-embed-text"}
    items = start.label_ollama_models(
        [("qwen3.8:latest", 1), ("nomic-embed-text:latest", 1), ("routed-chat:latest", 1)], env, tmp_path)
    labels = {item["path"]: item["label"] for item in items}
    assert labels["nomic-embed-text:latest"] == labels["routed-chat:latest"] == "retained"
    assert start.deletion_set(items, named=[]) == []


def test_a_manager_refusal_exits_cleanly_instead_of_a_traceback(monkeypatch):
    """remove/managed-host commands let the manager's refusal escape as a
    traceback; siblings print '<op> failed: <why>' and exit 1 (cycle 9)."""
    from click.testing import CliRunner

    import start
    from services.comfyui_mps_manager import ComfyUiMpsError

    class Manager:
        state_dir = "/tmp/x"

        def remove(self):
            raise ComfyUiMpsError("models path is inside the state directory")

    monkeypatch.setattr(start, "_comfyui_mps_manager", lambda: Manager())
    result = CliRunner().invoke(start.main, ["comfyui-mps", "remove", "--yes"])
    assert result.exit_code == 1
    assert "Remove failed: models path is inside the state directory" in result.output
    assert isinstance(result.exception, SystemExit)
