"""Keep AGENTS.md's architecture and test instructions grounded in source."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
GUIDANCE = ROOT / "AGENTS.md"


def _guidance() -> str:
    return GUIDANCE.read_text(encoding="utf-8")


def _backend_root_from_heading(text: str) -> Path:
    match = re.search(r"^### Backend \(`([^`]+)`\)$", text, re.MULTILINE)
    assert match, "AGENTS.md must name the Backend application root in its heading"
    return ROOT / match.group(1).strip("/")


def _backend_test_block(text: str) -> str:
    for language, body in re.findall(r"```([a-z]*)\n(.*?)\n```", text, re.DOTALL):
        if language in {"bash", "sh"} and "requirements-dev.txt" in body:
            return body
    raise AssertionError("AGENTS.md must provide an executable Backend test command")


def _attribute_path(node: ast.expr) -> tuple[str, ...]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return tuple(reversed(parts))


class _LiveGateEnvCollector(ast.NodeVisitor):
    """Resolve module helpers referenced by a pytest skip condition."""

    def __init__(self, definitions: dict[str, ast.AST]) -> None:
        self.definitions = definitions
        self.keys: set[str] = set()
        self._resolving: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        definition = self.definitions.get(node.id)
        if definition is None or node.id in self._resolving:
            return
        self._resolving.add(node.id)
        self.visit(definition)
        self._resolving.remove(node.id)

    def visit_Call(self, node: ast.Call) -> None:
        path = _attribute_path(node.func)
        if path in {("os", "getenv"), ("os", "environ", "get")} and node.args:
            key = node.args[0]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                self.keys.add(key.value)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _attribute_path(node.value) == ("os", "environ"):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                self.keys.add(key.value)
        self.generic_visit(node)


def _opt_in_live_endpoint_variables() -> set[str]:
    """Derive environment switches that actually gate self-skipping tests."""
    variables: set[str] = set()
    for path in (ROOT / "services/backend/app/app/tests").glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        definitions: dict[str, ast.AST] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                definitions[node.name] = node
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and value is not None:
                        definitions[target.id] = value

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if _attribute_path(node.func) != ("pytest", "mark", "skipif"):
                continue
            collector = _LiveGateEnvCollector(definitions)
            collector.visit(node.args[0])
            variables.update(collector.keys)

    assert variables, "expected opt-in live Backend tests with endpoint switches"
    return variables


@dataclass(frozen=True)
class _HarnessResult:
    returncode: int
    events: tuple[dict, ...]
    sentinel: Path
    test_root: Path
    fake_bin: Path
    stderr: str


_FAKE_PYTHON = '''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

event = {
    "tool": "venv-python",
    "path": sys.argv[0],
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "virtual_env": os.environ.get("VIRTUAL_ENV"),
    "live_env": {
        key: os.environ.get(key)
        for key in os.environ["HARNESS_LIVE_KEYS"].split(",")
        if key
    },
}
with Path(os.environ["HARNESS_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(event) + "\\n")
Path(os.environ["HARNESS_SENTINEL"]).write_text("pytest-executed\\n", encoding="utf-8")
raise SystemExit(int(os.environ["HARNESS_PYTEST_EXIT"]))
'''


_FAKE_UV = '''#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

argv = sys.argv[1:]
event = {
    "tool": "uv",
    "path": sys.argv[0],
    "argv": argv,
    "cwd": os.getcwd(),
    "virtual_env": os.environ.get("VIRTUAL_ENV"),
}
with Path(os.environ["HARNESS_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(event) + "\\n")

if argv[:1] == ["venv"]:
    target = Path(argv[-1])
    python312 = shutil.which("python3.12")
    if python312 is None:
        raise SystemExit(91)
    subprocess.run([python312, "--atlas-probe"], check=True)
    shim = target / "bin" / "python"
    shim.parent.mkdir(parents=True)
    shim.write_text(os.environ["HARNESS_FAKE_PYTHON"], encoding="utf-8")
    shim.chmod(0o755)
elif argv[:2] != ["pip", "install"]:
    raise SystemExit(92)
'''


_FAKE_PYTHON312 = '''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

event = {
    "tool": "python3.12",
    "path": sys.argv[0],
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
}
with Path(os.environ["HARNESS_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(event) + "\\n")
'''


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _execute_backend_command_harness(
    block: str,
    sandbox: Path,
    *,
    exit_code: int,
) -> _HarnessResult:
    fake_bin = sandbox / "bin"
    runtime_tmp = sandbox / "tmp"
    fake_bin.mkdir(parents=True)
    runtime_tmp.mkdir()
    log = sandbox / "events.jsonl"
    sentinel = sandbox / "pytest-executed"
    _write_executable(fake_bin / "uv", _FAKE_UV)
    _write_executable(fake_bin / "python3.12", _FAKE_PYTHON312)

    live_keys = sorted(_opt_in_live_endpoint_variables())
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "TMPDIR": str(runtime_tmp),
            "HARNESS_LOG": str(log),
            "HARNESS_SENTINEL": str(sentinel),
            "HARNESS_PYTEST_EXIT": str(exit_code),
            "HARNESS_FAKE_PYTHON": _FAKE_PYTHON,
            "HARNESS_LIVE_KEYS": ",".join(live_keys),
        }
    )
    env.update({key: "must-be-unset" for key in live_keys})
    completed = subprocess.run(
        ["bash", "-x", "-c", block],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    events = tuple(
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
    )
    create = next(
        event for event in events if event["tool"] == "uv" and event["argv"][:1] == ["venv"]
    )
    test_root = Path(create["argv"][-1]).parent
    return _HarnessResult(
        completed.returncode,
        events,
        sentinel,
        test_root,
        fake_bin,
        completed.stderr,
    )


def _assert_backend_command_execution(result: _HarnessResult) -> None:
    assert result.sentinel.is_file(), "the documented pytest command was not executed"
    assert result.sentinel.read_text(encoding="utf-8") == "pytest-executed\n"
    assert [event["tool"] for event in result.events] == [
        "uv",
        "python3.12",
        "uv",
        "venv-python",
    ]
    create, python_probe, install, pytest_run = result.events
    venv = Path(create["argv"][-1])
    assert create["argv"] == ["venv", "--python", "3.12", str(venv)]
    assert Path(python_probe["path"]) == result.fake_bin / "python3.12"
    assert python_probe["argv"] == ["--atlas-probe"]
    assert install["argv"] == [
        "pip",
        "install",
        "-r",
        "requirements.txt",
        "-r",
        "requirements-dev.txt",
        "-c",
        "requirements-test-locked.txt",
    ]
    assert install["virtual_env"] == str(venv)
    assert Path(pytest_run["path"]) == venv / "bin/python"
    assert pytest_run["argv"] == ["-m", "pytest", "tests/", "-q", "-W", "error"]
    assert set(pytest_run["live_env"].values()) == {None}
    backend_root = str(ROOT / "services/backend/app/app")
    assert create["cwd"] == install["cwd"] == pytest_run["cwd"] == backend_root
    for traced_command in (
        "cd services/backend/app/app",
        "mktemp -d",
        "uv venv --python 3.12",
        "uv pip install -r requirements.txt -r requirements-dev.txt",
        "env -u ATLAS_TEST_REDIS_URL",
        "-m pytest tests/ -q -W error",
        "rm -rf",
    ):
        assert traced_command in result.stderr


def _backend_ci_contract() -> tuple[str, str]:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/services-lint.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["lint"]["steps"]
    step = next(item for item in steps if item.get("name") == "Backend unit tests")
    return step["working-directory"], step["run"]


class _MigrationCallCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and re.fullmatch(
            r"_(?:needs|apply|stamp)_v\d+", node.func.id
        ):
            self.calls.append(
                (node.func.id, tuple(ast.unparse(arg) for arg in node.args))
            )
        self.generic_visit(node)


def _migration_execution_contract(source: str | None = None) -> dict[str, object]:
    start_path = ROOT / "bootstrapper/start.py"
    if source is None:
        source = start_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(start_path))
    atlas_starter = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AtlasStarter"
    )
    method = next(
        node
        for node in atlas_starter.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "run_port_migration"
    )

    migration_imports: dict[int, dict[str, str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        match = re.fullmatch(r"services\.migrations\.migration_v(\d+)", node.module)
        if match is None:
            continue
        migration_imports[int(match.group(1))] = {
            alias.name: alias.asname or alias.name for alias in node.names
        }

    collector = _MigrationCallCollector()
    collector.visit(method)
    migration_calls = tuple(collector.calls)
    top_level_gates = tuple(
        ast.unparse(statement.test)
        for statement in method.body
        if isinstance(statement, ast.If)
        and any(
            name.startswith("_needs_v")
            for name, _ in _calls_in_node(statement.test)
        )
    )
    execution_branches: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    for statement in method.body:
        if not isinstance(statement, ast.If):
            continue
        gate_calls = _calls_in_node(statement.test)
        if not any(name.startswith("_needs_v") for name, _ in gate_calls):
            continue
        no_migrate_gate = next(
            nested
            for nested in statement.body
            if isinstance(nested, ast.If)
            and ast.unparse(nested.test) == "no_port_migrate"
        )
        skipped = tuple(name for name, _ in _calls_in_nodes(no_migrate_gate.body))
        executed = tuple(name for name, _ in _calls_in_nodes(no_migrate_gate.orelse))
        execution_branches.append((ast.unparse(statement.test), skipped, executed))
    conditional_stamps = tuple(
        (
            ast.unparse(node.test),
            tuple(name for name, _ in _calls_in_nodes(node.body)),
            tuple(name for name, _ in _calls_in_nodes(node.orelse)),
        )
        for node in ast.walk(method)
        if isinstance(node, ast.If)
        and any(name.startswith("_apply_v") for name, _ in _calls_in_node(node.test))
    )
    return {
        "versions": tuple(sorted(migration_imports)),
        "imports": migration_imports,
        "calls": migration_calls,
        "gates": top_level_gates,
        "execution_branches": tuple(execution_branches),
        "conditional_stamps": conditional_stamps,
    }


def _calls_in_node(node: ast.AST) -> tuple[tuple[str, tuple[str, ...]], ...]:
    collector = _MigrationCallCollector()
    collector.visit(node)
    return tuple(collector.calls)


def _calls_in_nodes(nodes: list[ast.stmt]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    collector = _MigrationCallCollector()
    for node in nodes:
        collector.visit(node)
    return tuple(collector.calls)


def _assert_migration_execution_contract(contract: dict[str, object]) -> None:
    assert contract["versions"] == (1, 2, 3, 4, 5)
    imports = contract["imports"]
    imported_callable_aliases: set[str] = set()
    for version in contract["versions"]:
        expected = {
            "needs_migration": f"_needs_v{version}",
            "apply": f"_apply_v{version}",
            "stamp_version": f"_stamp_v{version}",
        }
        assert {name: imports[version][name] for name in expected} == expected
        imported_callable_aliases.update(expected.values())
    all_imported_contract_aliases = {
        alias
        for module_imports in imports.values()
        for alias in module_imports.values()
        if re.fullmatch(r"_(?:needs|apply|stamp)_v\d+", alias)
    }
    assert all_imported_contract_aliases == imported_callable_aliases

    expected_call_order = (
        "_needs_v1", "_apply_v1", "_stamp_v1",
        "_needs_v2", "_apply_v2", "_stamp_v2",
        "_needs_v3", "_apply_v3", "_stamp_v3",
        "_needs_v4", "_apply_v4", "_stamp_v4",
        "_needs_v4", "_needs_v5", "_apply_v5", "_stamp_v5",
    )
    calls = contract["calls"]
    assert tuple(name for name, _ in calls) == expected_call_order
    assert {name for name, _ in calls} == imported_callable_aliases
    assert all(arguments and arguments[0] == "env_path" for _, arguments in calls)

    expected_gates = (
        "_needs_v1(env_path)",
        "_needs_v2(env_path)",
        "_needs_v3(env_path)",
        "_needs_v4(env_path)",
        "not _needs_v4(env_path) and _needs_v5(env_path)",
    )
    assert contract["gates"] == expected_gates
    assert contract["execution_branches"] == tuple(
        (gate, (), (f"_apply_v{version}", f"_stamp_v{version}"))
        for version, gate in enumerate(expected_gates, start=1)
    )
    assert contract["conditional_stamps"] == (
        ("_apply_v4(env_path)", ("_stamp_v4",), ()),
    )


def _runtime_adaptive_examples(text: str) -> tuple[tuple[str, Path], ...]:
    bullet = re.search(
        r"^- `runtime_adaptive:`(?P<body>.*?)(?=\n- `|\n\n)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert bullet, "AGENTS.md must describe runtime_adaptive"
    examples = tuple(
        (adaptive_key, ROOT / relative_path)
        for adaptive_key, relative_path in re.findall(
            r"\[`([a-z0-9_-]+)`\]\((services/[a-z0-9_-]+/service\.yml)\)",
            bullet.group("body"),
        )
    )
    assert len(examples) >= 2, "cite current manifests instead of a freehand owner list"
    return examples


def _assert_multi_upstream_adaptation(adaptation: object) -> None:
    assert isinstance(adaptation, dict)
    adapts_to = adaptation.get("adapts_to")
    assert isinstance(adapts_to, list), "adaptive example must use a list of upstreams"
    assert len(adapts_to) >= 2, "adaptive example must demonstrate multiple upstreams"
    assert all(isinstance(item, str) and item.strip() for item in adapts_to)
    assert len(set(adapts_to)) == len(adapts_to), "adaptive upstreams must be distinct"
    assert isinstance(adaptation.get("environment_adaptation"), dict)
    assert isinstance(adaptation.get("extra_hosts_adaptation"), str)


def _assert_runtime_adaptive_synthesis(examples: tuple[tuple[str, Path], ...]) -> None:
    from services.manifests import load_manifests
    from services.sc_synthesizer import synthesize_legacy

    manifests = load_manifests(ROOT / "services")
    synthesized = synthesize_legacy(manifests)["adaptive_services"]
    expected_all = {
        adaptive_key: adaptation
        for manifest in manifests
        for adaptive_key, adaptation in manifest.runtime_adaptive.items()
    }
    assert synthesized == expected_all
    for adaptive_key, manifest_path in examples:
        assert manifest_path.is_file()
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        expected = raw["runtime_adaptive"][adaptive_key]
        _assert_multi_upstream_adaptation(expected)

        owners = [
            manifest
            for manifest in manifests
            if adaptive_key in manifest.runtime_adaptive
        ]
        assert len(owners) == 1, f"runtime_adaptive.{adaptive_key} must have one owner"
        owner = owners[0]
        assert owner.source_path is not None
        assert owner.source_path.resolve() == manifest_path.resolve()
        assert owner.runtime_adaptive[adaptive_key] == expected
        assert synthesized[adaptive_key] == expected


def test_backend_guidance_points_at_the_real_application_and_test_tree() -> None:
    backend_root = _backend_root_from_heading(_guidance())

    assert backend_root.is_dir()
    for relative in ("main.py", "tests", "requirements.txt", "requirements-dev.txt"):
        assert (backend_root / relative).exists(), (
            f"Backend guidance root {backend_root.relative_to(ROOT)} does not own {relative}"
        )


def test_backend_test_command_is_shell_valid_and_matches_the_ci_dependency_contract() -> None:
    text = _guidance()
    backend_root = _backend_root_from_heading(text)
    block = _backend_test_block(text)

    syntax = subprocess.run(
        ["bash", "-n"],
        input=block,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr

    logical_lines = re.sub(r"\\\n\s*", " ", block)
    cd_match = re.search(r"^cd ([^\s]+)$", logical_lines, re.MULTILINE)
    assert cd_match
    assert (ROOT / cd_match.group(1)).resolve() == backend_root.resolve()

    ci_workdir, ci_command = _backend_ci_contract()
    assert (ROOT / ci_workdir).resolve() == backend_root.resolve()
    ci_python = re.search(r"uv venv --python ([0-9.]+)", ci_command)
    assert ci_python
    assert re.search(
        rf"\buv venv --python {re.escape(ci_python.group(1))} ", logical_lines
    )
    install = re.search(r"^.*uv pip install (.+)$", logical_lines, re.MULTILINE)
    assert install, "the command must install Backend runtime and test dependencies"
    requirement_flags = re.compile(r"(?:^|\s)(-[rc])\s+([^\s]+)")
    assert set(requirement_flags.findall(install.group(1))) == set(
        requirement_flags.findall(re.sub(r"\\\n\s*", " ", ci_command))
    )
    assert re.search(r"-m pytest tests/ -q -W error\b", logical_lines)

    assert 'BACKEND_TEST_ROOT="$(mktemp -d)"' in block
    assert "BACKEND_TEST_VENV=\"$BACKEND_TEST_ROOT/venv\"" in block
    assert "trap 'rm -rf \"$BACKEND_TEST_ROOT\"' EXIT" in block

    pytest_command = next(
        line for line in logical_lines.splitlines() if "-m pytest tests/" in line
    )
    for opt_in_live_variable in _opt_in_live_endpoint_variables():
        assert f"-u {opt_in_live_variable}" in pytest_command, (
            "the default command must not inherit opt-in live-test endpoints"
        )

    for _, requirement in requirement_flags.findall(install.group(1)):
        assert (backend_root / requirement).is_file()


def test_backend_test_command_executes_the_ci_contract_and_cleans_up(
    tmp_path: Path,
) -> None:
    block = _backend_test_block(_guidance())

    success = _execute_backend_command_harness(block, tmp_path / "success", exit_code=0)
    _assert_backend_command_execution(success)
    assert success.returncode == 0
    assert not success.test_root.exists()

    failure = _execute_backend_command_harness(block, tmp_path / "failure", exit_code=19)
    _assert_backend_command_execution(failure)
    assert failure.returncode == 19
    assert not failure.test_root.exists()


def test_backend_command_harness_rejects_comment_masked_pytest(tmp_path: Path) -> None:
    block = _backend_test_block(_guidance())
    masked = block.replace(
        '  "$BACKEND_TEST_VENV/bin/python" -m pytest tests/ -q -W error',
        '  true # "$BACKEND_TEST_VENV/bin/python" -m pytest tests/ -q -W error',
    )
    assert masked != block

    result = _execute_backend_command_harness(masked, tmp_path, exit_code=0)
    with pytest.raises(AssertionError, match="pytest command was not executed"):
        _assert_backend_command_execution(result)


def test_live_endpoint_switches_are_derived_from_skip_conditions() -> None:
    assert _opt_in_live_endpoint_variables() == {
        "ATLAS_COMFYUI_LIVE_ENDPOINT",
        "ATLAS_TEI_RERANKER_LIVE_ENDPOINT",
        "ATLAS_TEST_REDIS_URL",
    }


def test_documented_migration_chain_reaches_the_current_frozen_module() -> None:
    text = _guidance()
    version_match = re.search(
        r"services/migrations/.*?currently runs v1 through v(\d+)",
        text,
        re.DOTALL,
    )
    assert version_match, "AGENTS.md must state the current migration-chain endpoint"

    migration_versions = {
        int(match.group(1))
        for path in (ROOT / "bootstrapper/services/migrations").glob("migration_v*.py")
        if (match := re.fullmatch(r"migration_v(\d+)\.py", path.name))
    }
    assert migration_versions == set(range(1, max(migration_versions) + 1))
    contract = _migration_execution_contract()
    assert set(contract["versions"]) == migration_versions
    assert int(version_match.group(1)) == max(contract["versions"])

    source_link = re.search(
        r"\[`start\.py::run_port_migration`\]\(([^)]+)\)", text
    )
    assert source_link
    assert (ROOT / source_link.group(1)).resolve() == (ROOT / "bootstrapper/start.py")


def test_migration_guidance_tracks_the_executed_method_contract() -> None:
    contract = _migration_execution_contract()
    _assert_migration_execution_contract(contract)


@pytest.mark.parametrize(
    ("before", "after"),
    (
        (
            "                _apply_v3(env_path)\n"
            "                _stamp_v3(env_path)",
            "                _stamp_v3(env_path)\n"
            "                _apply_v3(env_path)",
        ),
        (
            "                _stamp_v2(env_path)",
            "                pass  # removed stamp",
        ),
        (
            "from services.migrations.migration_v5 import (",
            "from services.migrations.migration_v4 import (",
        ),
    ),
    ids=("wrong-order", "missing-call-unused-import", "wrong-module"),
)
def test_migration_contract_rejects_execution_mutations(
    before: str,
    after: str,
) -> None:
    source = (ROOT / "bootstrapper/start.py").read_text(encoding="utf-8")
    mutated = source.replace(before, after, 1)
    assert mutated != source

    with pytest.raises((AssertionError, KeyError)):
        _assert_migration_execution_contract(_migration_execution_contract(mutated))


def test_runtime_adaptive_examples_are_real_manifest_owned_entries() -> None:
    for adaptive_key, manifest_path in _runtime_adaptive_examples(_guidance()):
        assert manifest_path.is_file()
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        assert adaptive_key in manifest.get("runtime_adaptive", {}), (
            f"{manifest_path.relative_to(ROOT)} does not own "
            f"runtime_adaptive.{adaptive_key}"
        )


def test_runtime_adaptive_examples_round_trip_through_manifest_synthesis() -> None:
    examples = _runtime_adaptive_examples(_guidance())
    _assert_runtime_adaptive_synthesis(examples)


def test_runtime_adaptive_example_rejects_an_empty_upstream_list() -> None:
    adaptive_key, manifest_path = _runtime_adaptive_examples(_guidance())[0]
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    mutated = dict(manifest["runtime_adaptive"][adaptive_key])
    mutated["adapts_to"] = []

    with pytest.raises(AssertionError, match="demonstrate multiple upstreams"):
        _assert_multi_upstream_adaptation(mutated)
