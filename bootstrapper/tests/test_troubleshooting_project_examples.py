"""Run troubleshooting reader actions through real owners and owned executables."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from tests.test_three_surface_build import env_reference_config, _candidate_document
from tests.three_surface_test_utils import ROOT, surface_text

SOURCE = "docs/quick-start/troubleshooting.md"
DIAGNOSTIC_ARGS = [['logs', '--tail=100', '-f', 'litellm'], ['logs', '--tail=100', '-f', 'ollama-pull'], ['logs', '--tail=100', '-f', 'ollama'], ['logs', '--tail=100', '-f', 'comfyui-init'], ['logs', '--tail=100', '-f', 'comfyui'], ['logs', '--tail=100', '-f', 'n8n'], ['logs', '--tail=100', '-f', 'n8n-worker'], ['logs', '--tail=100', '-f', 'redis'], ['logs', '--tail=100', '-f', 'supabase-db'], ['logs', '--tail=100', '-f', 'supabase-auth'], ['logs', '--tail=100', '-f', 'supabase-api'], ['logs', '--tail=100', 'supabase-db-init'], ['exec', '-T', 'supabase-db', 'pg_isready'], ['exec', '-T', 'backend', 'python', '-c', "\nimport asyncio, os, asyncpg\nasync def main():\n    conn = await asyncio.wait_for(asyncpg.connect(os.environ['DATABASE_URL']), 5)\n    assert await asyncio.wait_for(conn.fetchval('SELECT 1'), 5) == 1\n    await conn.close()\n    print('database query succeeded')\nasyncio.run(main())"], ['logs', '--tail=100', '-f', 'kong-api-gateway'], ['ps'], ['ps'], ['logs', '--tail=100', '-f', 'redis'], ['logs', '--tail=100', '-f', 'supabase-db'], ['ps'], ['logs', '--tail=50'], ['logs', '--tail=100', '-f', 'ollama'], ['logs', '--tail=100', '-f', 'backend'], ['exec', '-T', 'backend', 'curl', '-sf', 'http://litellm:4000/health/liveliness'], ['exec', '-T', 'litellm', 'python', '-c', "import urllib.request; print(urllib.request.urlopen('http://ollama:11434/api/tags', timeout=5).status)"], ['exec', '-T', 'backend', 'curl', '-sf', 'http://supabase-api:3000/']]
VARIANTS = (
    "plain", "quoted", "export-spaced", "uppercase", "missing-project", "blank",
    "relative-selected", "absolute-selected", "legacy-selected", "selected-default-invalid",
    "missing-file", "missing-selected", "directory-selected", "invalid-selected", "invalid-project",
    "foreign-shell", "matching-shell", "empty-shell", "stale-compose", "overlays",
    "double-quoted", "padded", "invalid-file", "unsupported-command", "unsupported-expansion", "literal-data",
)
INVALID = {"missing-file", "missing-selected", "directory-selected", "invalid-selected", "invalid-project", "invalid-file", "unsupported-command", "unsupported-expansion"}
OWNER = '''from core.config_parser import ConfigParser
from utils.system import compose_env
import json
parser = ConfigParser(root_dir=".")
if not parser.env_file_path.is_file():
    raise ValueError("Selected configuration must be an existing file")
values = parser.parse_env_file()
project = parser.get_project_name()
command = ["docker", "compose", "-p", project, "--env-file=" + str(parser.env_file_path.resolve())]
prefix = compose_env(command).get("PROJECT_NAME", values.get("PROJECT_NAME") or project)
print(json.dumps({"project": project, "prefix": prefix, "selected": str(parser.env_file_path.resolve())}))
'''
RECORDER = '''import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
with Path(os.environ["PROJECT_GUIDE_CALLS"]).open("a") as stream:
    stream.write(json.dumps({"name":name,"args":sys.argv[1:],"project":os.environ.get("PROJECT_NAME")})+"\\n")
print("owned " + name + " output")
if name == "stop.sh":
    if os.environ.get("CORRUPT_AFTER_STOP"):
        Path(os.environ["ACTIVE_FILE"]).write_text("BROKEN: invalid\\n")
    sys.exit(int(os.environ.get("STOP_EXIT", "0")))
if name == "docker" and "volume" in sys.argv:
    sys.exit(int(os.environ.get("REMOVE_EXIT", "0")))
'''


def _blocks(surface, config):
    text = (ROOT / SOURCE).read_text() if surface == "repo" else surface_text(SOURCE, surface)
    document = _candidate_document(text, config)
    return ["".join(node.itertext()) for node in document.findall(".//pre/code")]


def _actions(blocks):
    setup = blocks[0]
    reset = next(block for block in blocks if "docker volume rm" in block)
    backfill = next(block for block in blocks if "./start.sh env backfill" in block
                    and ("Repair environment" in block or "Repair configuration" in block))
    return setup, reset, backfill


def _fixture(directory, variant):
    checkout = directory / "owned checkout"
    checkout.mkdir()
    (checkout / "bootstrapper").symlink_to(ROOT / "bootstrapper", target_is_directory=True)
    (checkout / "docker-compose.yml").write_text("services: {}\n")
    selected = _selected_file(checkout, variant)
    expected, shell = _settings(variant, selected)
    expected["files"] = ["docker-compose.yml"]
    if variant == "overlays":
        expected["files"] += _overlays(checkout, shell)
    _save_inputs(directory, checkout, expected)
    return checkout, expected, shell


def _selected_file(checkout, variant):
    rows = {"plain": "PROJECT_NAME=alpha\n", "quoted": "PROJECT_NAME='alpha'\n",
            "export-spaced": "export PROJECT_NAME = 'Exported'\n", "missing-project": "OTHER=value\n",
            "blank": "PROJECT_NAME=\n", "invalid-project": "PROJECT_NAME=bad.name\n"}
    rows.update({"double-quoted": 'PROJECT_NAME="MyStack"\n', "padded": "PROJECT_NAME='  MyStack  '\n",
                 "invalid-file": "BROKEN: invalid\n", "unsupported-command": "PROJECT_NAME=$(printf owned > shell-executed)\n",
                 "unsupported-expansion": "PROJECT_NAME=$INHERITED_EXPANSION\n",
                 "literal-data": "PROJECT_NAME=MyStack\nUNRELATED='$(printf owned > shell-executed)'\n"})
    text = rows.get(variant, "PROJECT_NAME=MyStack\n")
    selected = checkout / ("selected active.env" if "selected" in variant else ".env")
    if selected.name != ".env":
        (checkout / ".env").write_text("BROKEN: invalid\n" if variant == "selected-default-invalid"
                                        else "PROJECT_NAME=inactive\n")
    if variant not in {"missing-file", "missing-selected", "directory-selected"}:
        selected.write_text(text + ("BROKEN: invalid\n" if variant == "invalid-selected" else ""))
    if variant == "directory-selected":
        selected.mkdir()
    return selected


def _settings(variant, selected):
    expected = {"project": "mystack", "prefix": "MyStack", "valid": variant not in INVALID}
    if variant in {"plain", "quoted", "export-spaced", "missing-project", "blank"}:
        project = {"plain": "alpha", "quoted": "alpha", "export-spaced": "exported"}.get(variant, "atlas")
        expected.update(project=project, prefix="Exported" if variant == "export-spaced" else project)
    if variant == "padded":
        expected["prefix"] = "  MyStack  "
    expected["selected"] = str(selected.resolve())
    shell = {"COMPOSE_PROJECT_NAME": "foreign-project", "COMPOSE_FILE": "foreign.yml"}
    if variant in {"foreign-shell", "matching-shell", "empty-shell"}:
        shell["PROJECT_NAME"] = {"foreign-shell": "Foreign", "matching-shell": "MYStack", "empty-shell": ""}[variant]
        expected["prefix"] = "MYStack" if variant == "matching-shell" else "mystack"
    if selected.name != ".env":
        shell["GENAI_ENV_FILE" if variant == "legacy-selected" else "ATLAS_ENV_FILE"] = (
            str(selected) if variant == "absolute-selected" else selected.name)
    return expected, shell


def _overlays(checkout, shell):
    paths = ["services/_user/owned/compose.yml", "consumer/overlay.yml",
             "volumes/minio/consumer-storage.compose.yml"]
    for name in paths:
        path = checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("services: {}\n")
    manifest = checkout / "consumer/atlas.consumer.yml"
    manifest.write_text("name: owned\ncompose_overlays:\n  - overlay.yml\n")
    shell["ATLAS_CONSUMER_MANIFEST"] = str(manifest)
    return [paths[0], str(checkout / paths[1]), paths[2]]


def _save_inputs(directory, checkout, expected):
    (directory / "expected.json").write_text(json.dumps(expected, indent=2) + "\n")
    inputs = {str(path.relative_to(checkout)): path.read_text() for path in checkout.rglob("*")
              if path.is_file() and not path.is_symlink() and "bootstrapper" not in path.parts}
    (directory / "inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")


def _environment(directory, checkout, shell):
    tools = directory / "tools"
    tools.mkdir()
    for name in ("uv", "sed", "tail"):
        executable = shutil.which(name)
        assert executable, name
        (tools / name).symlink_to(Path(executable).resolve())
    for path in (tools / "docker", checkout / "start.sh", checkout / "stop.sh"):
        path.write_text("#!" + sys.executable + "\n" + RECORDER)
        path.chmod(0o700)
    environment = {"PATH": str(tools), "UV_PROJECT_ENVIRONMENT": sys.prefix,
                   "UV_PYTHON": sys.executable, "UV_NO_SYNC": "1", "UV_FROZEN": "1",
                   "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never", "VIRTUAL_ENV": sys.prefix,
                   "UV_CACHE_DIR": str(directory / "uv-cache"), "PYTHONDONTWRITEBYTECODE": "1",
                   "PROJECT_GUIDE_CALLS": str(directory / "calls.jsonl"), **shell}
    (directory / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    return environment


def _run(directory, name, script, execution):
    checkout, environment = execution
    (directory / (name + ".sh")).write_text(script)
    result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", script], cwd=checkout,
                            env=environment, capture_output=True, text=True, timeout=30)
    log = directory / "calls.jsonl"
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    record = {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "calls": calls}
    (directory / (name + "-result.json")).write_text(json.dumps(record, indent=2) + "\n")
    if log.exists():
        log.replace(directory / (name + "-calls.jsonl"))
    return record


def _owner(directory, execution, expected):
    script = "PYTHONPATH=bootstrapper uv run --project bootstrapper python -c " + shlex.quote(OWNER)
    record = _run(directory, "owner", script, execution)
    if not expected["valid"]:
        assert record["returncode"] != 0 and record["calls"] == [], record
        return
    assert record["returncode"] == 0 and record["calls"] == [], record
    actual = json.loads(record["stdout"])
    assert actual == {key: expected[key] for key in ("project", "prefix", "selected")}, record


def _assert_compose(record, expected, args):
    assert (record["returncode"] == 0) == expected["valid"], record
    if not expected["valid"]:
        assert record["calls"] == [], record
        return
    commands = [call for call in record["calls"] if "-p" in call["args"]]
    files = [part for path in expected["files"] for part in ("-f", path)]
    argv = ["compose", "-p", expected["project"], "--env-file=" + expected["selected"], *files, *args]
    assert len(commands) == 1 and commands[0]["args"] == argv, record
    _assert_output(commands[0], record, expected)


def _assert_output(command, record, expected):
    assert command["project"] in (None, expected["prefix"]), record
    assert record["calls"][0]["args"] == ["--version"], record
    assert record["calls"][1]["args"] == ["compose", "version"], record
    assert len(record["calls"]) == 3, record
    assert record["stdout"].count("owned docker output") == 1, record


@pytest.mark.parametrize("case", [(surface, variant) for surface in ["repo", "site", "wiki"] for variant in VARIANTS])
def test_diagnostics_resolve_existing_selected_project(case, tmp_path, env_reference_config):
    surface, variant = case
    checkout, expected, shell = _fixture(tmp_path, variant)
    execution = checkout, _environment(tmp_path, checkout, shell)
    _owner(tmp_path, execution, expected)
    setup, _, _ = _actions(_blocks(surface, env_reference_config))
    script = setup + '\n' + ("atlas_compose" if "atlas_compose()" in setup else "docker compose") + " ps\n"
    observed = _run(tmp_path, "diagnostic", script, execution)
    _assert_compose(observed, expected, ["ps"])
    assert not (checkout / "shell-executed").exists(), observed


@pytest.mark.parametrize("case", [(surface, variant) for surface in ["repo", "site", "wiki"]
                                  for variant in ["plain", "uppercase", "padded", "matching-shell", "foreign-shell", "empty-shell",
                                                  "relative-selected", "invalid-selected", "missing-selected"]])
def test_partial_reset_uses_effective_raw_volume_prefix(case, tmp_path, env_reference_config):
    surface, variant = case
    checkout, expected, shell = _fixture(tmp_path, variant)
    execution = checkout, _environment(tmp_path, checkout, shell)
    _owner(tmp_path, execution, expected)
    setup, reset, _ = _actions(_blocks(surface, env_reference_config))
    record = _run(tmp_path, "reset", setup + "\n" + reset, execution)
    removals = [call["args"] for call in record["calls"] if call["name"] == "docker"]
    prefix = expected["prefix"]
    assert removals == ([["volume", "rm", prefix + suffix] for suffix in ("-supabase-db-data", "-n8n-data")]
                        if expected["valid"] else []), record
    assert (record["returncode"] == 0) == expected["valid"], record
    assert all(call["name"] != "start.sh" for call in record["calls"]), record


@pytest.mark.parametrize("case", [(surface, failure) for surface in ["repo", "site", "wiki"]
                                  for failure in ["stop", "lookup", "remove"]])
def test_partial_reset_stops_on_failure(case, tmp_path, env_reference_config):
    surface, failure = case
    checkout, expected, shell = _fixture(tmp_path, "plain")
    shell.update(STOP_EXIT="7" if failure == "stop" else "0", ACTIVE_FILE=expected["selected"],
                 REMOVE_EXIT="9" if failure == "remove" else "0")
    if failure == "lookup":
        shell["CORRUPT_AFTER_STOP"] = "1"
    execution = checkout, _environment(tmp_path, checkout, shell)
    setup, reset, _ = _actions(_blocks(surface, env_reference_config))
    record = _run(tmp_path, "failure", setup + "\n" + reset, execution)
    removals = [call for call in record["calls"] if call["name"] == "docker"]
    assert len(removals) == (1 if failure == "remove" else 0), record
    assert record["returncode"] != 0, record


@pytest.mark.parametrize("surface", ["repo", "site", "wiki"])
def test_backfill_is_its_own_nondestructive_action(surface, tmp_path, env_reference_config):
    checkout, _, shell = _fixture(tmp_path, "plain")
    execution = checkout, _environment(tmp_path, checkout, shell)
    _, _, backfill = _actions(_blocks(surface, env_reference_config))
    record = _run(tmp_path, "backfill", backfill, execution)
    assert record["returncode"] == 0, record
    assert [(call["name"], call["args"]) for call in record["calls"]] == [("start.sh", ["env", "backfill"])], record


def _diagnostics(blocks):
    commands = []
    for block in blocks:
        commands.extend(_block_commands(block))
    return commands


def _block_commands(block):
    commands, pending = [], ""
    for line in block.splitlines():
        if not pending and not line.startswith(("docker compose logs", "docker compose exec", "docker compose ps",
                                                 "atlas_compose logs", "atlas_compose exec", "atlas_compose ps")):
            continue
        pending += line + "\n"
        try:
            shlex.split(pending, comments=True)
        except ValueError:
            continue
        commands.append(pending.split(" | grep")[0].rstrip())
        pending = ""
    assert not pending, pending
    return commands


@pytest.mark.parametrize("variant", VARIANTS)
def test_independent_owner_controls(variant, tmp_path):
    checkout, expected, shell = _fixture(tmp_path, variant)
    _owner(tmp_path, (checkout, _environment(tmp_path, checkout, shell)), expected)


@pytest.mark.parametrize("surface", ["repo", "site", "wiki"])
def test_diagnostic_fences_preserve_arguments_and_output(surface, tmp_path, env_reference_config):
    checkout, expected, shell = _fixture(tmp_path, "overlays")
    execution = checkout, _environment(tmp_path, checkout, shell)
    blocks = _blocks(surface, env_reference_config)
    setup, _, _ = _actions(blocks)
    commands = _diagnostics(blocks)
    argv = [shlex.split(command, comments=True)[1 if command.startswith("atlas_compose") else 2:]
            for command in commands]
    assert argv == DIAGNOSTIC_ARGS
    for number, command in enumerate(commands):
        record = _run(tmp_path, "command-" + str(number), setup + "\n" + command, execution)
        _assert_compose(record, expected, argv[number])


@pytest.mark.parametrize("surface", ["repo", "site", "wiki"])
def test_helper_reloads_configuration_on_each_call(surface, tmp_path, env_reference_config):
    checkout, expected, shell = _fixture(tmp_path, "uppercase")
    execution = checkout, _environment(tmp_path, checkout, shell)
    setup, _, _ = _actions(_blocks(surface, env_reference_config))
    command = "atlas_compose" if "atlas_compose()" in setup else "docker compose"
    script = setup + "\n" + command + " ps\nprintf 'PROJECT_NAME=NewTeam\\n' > .env\n" + command + " ps\n"
    record = _run(tmp_path, "repeat", script, execution)
    commands = [call for call in record["calls"] if "-p" in call["args"]]
    assert [call["args"][call["args"].index("-p") + 1] for call in commands] == [expected["project"], "newteam"], record
    assert record["returncode"] == 0, record


@pytest.mark.parametrize("surface", ["repo", "site", "wiki"])
def test_native_version_and_startup_descriptions_are_preserved(surface, env_reference_config):
    blocks = _blocks(surface, env_reference_config)
    assert any("docker --version\ndocker compose version\npython3 --version" in block for block in blocks)
    assert any("./start.sh --support-bundle ./atlas-support.tar.gz" in block for block in blocks)


# Only the new terminal tests use this recorder. This simulates the exact default
# and CheckTty boundary, not Compose execution, target lookup or daemon behavior.
# https://github.com/docker/compose/blob/v2.20.3/cmd/compose/exec.go#L71-L106
# go.mod pins github.com/docker/cli v24.0.5+incompatible:
# https://github.com/docker/compose/blob/v2.20.3/go.mod#L15
# https://github.com/docker/cli/blob/v24.0.5/cli/command/container/exec.go#L115
# https://github.com/docker/cli/blob/v24.0.5/cli/streams/in.go#L42-L53
TERMINAL_RECORDER = '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
row = {"name": "docker", "args": args, "project": os.environ.get("PROJECT_NAME"),
       "stdin_isatty": os.isatty(0), "stdout_isatty": os.isatty(1)}
rejected = False
if "exec" in args:
    options = args[args.index("exec") + 1:]
    tty = os.isatty(1) and options[0] != "-T"
    rejected = tty and not os.isatty(0)  # interactive defaults to true
    row.update(tty_requested=tty, attach_stdin=True, check_tty_rejected=rejected)
with Path(os.environ["PROJECT_GUIDE_CALLS"]).open("a") as stream:
    stream.write(json.dumps(row) + "\\n")
if rejected:
    print("the input device is not a TTY", file=sys.stderr)
    sys.exit(1)
print("owned docker output")
'''


def _drain_terminal(process, master, output):
    import os
    import select
    import time

    deadline = time.monotonic() + 30
    while True:
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(process.args, 30)
        if select.select([master], [], [], 0.05)[0]:
            try:
                data = os.read(master, 65536)
            except OSError as error:
                import errno
                if error.errno != errno.EIO:
                    raise
                break
            if not data:
                break
            output.write(data)
        elif process.poll() is not None:
            break
    process.wait(timeout=2)


def _run_context(directory, name, script, execution):
    import os
    import signal

    pty = pytest.importorskip("pty")
    checkout, environment, terminal = execution
    command = ["/bin/bash", "--noprofile", "--norc", "-c", script]
    (directory / (name + ".sh")).write_text(script)
    master, slave = pty.openpty()
    process = None
    try:
        with (directory / (name + ".stdout")).open("wb") as output, \
                (directory / (name + ".stderr")).open("wb") as errors:
            process = subprocess.Popen(command, cwd=checkout, env=environment, start_new_session=True,
                                       stdin=slave if terminal else subprocess.DEVNULL,
                                       stdout=slave if terminal else output, stderr=errors)
            os.close(slave)
            slave = None
            if terminal:
                _drain_terminal(process, master, output)
            else:
                process.wait(timeout=30)
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
        os.close(master)
        if slave is not None:
            os.close(slave)
    log = directory / "calls.jsonl"
    record = {"command": command, "returncode": process.returncode,
              "stdout": (directory / (name + ".stdout")).read_text(),
              "stderr": (directory / (name + ".stderr")).read_text(),
              "calls": [json.loads(line) for line in log.read_text().splitlines()]}
    log.replace(directory / (name + "-calls.jsonl"))
    (directory / (name + "-result.json")).write_text(json.dumps(record, indent=2) + "\n")
    return record


@pytest.mark.parametrize("case", [(surface, mode) for surface in ["repo", "site", "wiki"]
                                  for mode in ["terminal", "pipe"]])
def test_noninteractive_exec_terminal_context(case, tmp_path, env_reference_config):
    surface, mode = case
    checkout, expected, shell = _fixture(tmp_path, "plain")
    environment = _environment(tmp_path, checkout, shell)
    recorder = tmp_path / "tools/docker"
    recorder.write_text("#!" + sys.executable + "\n" + TERMINAL_RECORDER)
    blocks = _blocks(surface, env_reference_config)
    commands = [command for command in _diagnostics(blocks)
                if shlex.split(command, comments=True)[1:3] == ["compose", "exec"]
                or shlex.split(command, comments=True)[1] == "exec"]
    assert len(commands) == 5
    records = [_run_context(tmp_path, "exec-" + str(index), blocks[0] + "\n" + command,
                            (checkout, environment, mode == "terminal"))
               for index, command in enumerate(commands)]
    _assert_terminal_context(records, mode == "terminal", expected)


def _assert_terminal_context(records, terminal, expected):
    diagnostics = [record["calls"][-1] for record in records]
    assert [(row["stdin_isatty"], row["stdout_isatty"]) for row in diagnostics] == [(False, terminal)] * 5
    assert [record["returncode"] for record in records] == [0] * 5, records
    assert all(record["stdout"].count("owned docker output") == 1 and not record["stderr"] for record in records), records
    assert all(not row["check_tty_rejected"] and not row["tty_requested"] for row in diagnostics), records
    for row in diagnostics:
        args = row["args"]
        assert "-p" in args, row
        assert args[args.index("-p") + 1] == expected["project"]
