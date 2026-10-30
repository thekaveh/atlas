"""Local Deep Researcher runtime inputs must be manifest-owned and pinned."""

import importlib.util
import json
from pathlib import Path
import re
import runpy
import os
import subprocess
import sys
from types import SimpleNamespace

from packaging.requirements import Requirement
from packaging.version import Version
import pytest
import yaml

from scripts import audit_runtime_locks
from tests.test_runtime_lock_audit import _runtime_audit_spec


REPO = Path(__file__).resolve().parents[2]
EXPECTED_REF = "38f769f84380f2065de76021ac7c5215f88aa39e"
EXPECTED_CLI = "0.4.32"
EXPECTED_LOCK_SHA256 = "26fc35ac377836de6628e5f7b180944c4d4bd50a5e9f0200bd6e663f20e35c1a"
SECURITY_FLOORS = {
    "langgraph-sdk": "0.4.6",
    "aiohttp": "3.14.4",
    "certifi": "2026.10.19",
    "click": "8.3.3",
    "langchain-classic": "1.0.7",
    "langsmith": "0.8.18",
    "soupsieve": "2.8.4",
}
SERVING_SECURITY_FLOORS = {
    "langgraph-api": "0.15.9",
    "opentelemetry-api": "1.45.1",
}
RUNTIME_LIB = (
    REPO
    / "services/local-deep-researcher/build/scripts/runtime-lib.sh"
)


def test_manifest_pins_upstream_commit_and_langgraph_cli():
    manifest = yaml.safe_load(
        (REPO / "services/local-deep-researcher/service.yml").read_text(encoding="utf-8")
    )
    env = {entry["name"]: entry for entry in manifest["env"]}

    assert env["LOCAL_DEEP_RESEARCHER_REF"]["default"] == EXPECTED_REF
    assert env["LOCAL_DEEP_RESEARCHER_LANGGRAPH_CLI_VERSION"]["default"] == EXPECTED_CLI
    assert env["LOCAL_DEEP_RESEARCHER_UPSTREAM_LOCK_SHA256"]["default"] == (
        EXPECTED_LOCK_SHA256
    )


def test_compose_injects_all_runtime_pins():
    compose = yaml.safe_load(
        (REPO / "services/local-deep-researcher/compose.yml").read_text(encoding="utf-8")
    )
    environment = compose["services"]["local-deep-researcher"]["environment"]

    assert environment["LOCAL_DEEP_RESEARCHER_REF"] == "${LOCAL_DEEP_RESEARCHER_REF}"
    assert environment["LOCAL_DEEP_RESEARCHER_LANGGRAPH_CLI_VERSION"] == (
        "${LOCAL_DEEP_RESEARCHER_LANGGRAPH_CLI_VERSION}"
    )
    assert environment["LOCAL_DEEP_RESEARCHER_UPSTREAM_LOCK_SHA256"] == (
        "${LOCAL_DEEP_RESEARCHER_UPSTREAM_LOCK_SHA256}"
    )


def test_runtime_requirements_lock_pins_cli_and_build_tooling():
    lock = (
        REPO
        / "services/local-deep-researcher/build/config/runtime-requirements.lock"
    ).read_text(encoding="utf-8")

    assert f"langgraph-cli=={EXPECTED_CLI} \\" in lock
    assert "setuptools==84.1.0 \\" in lock
    assert "wheel==0.47.0 \\" in lock
    assert "--hash=sha256:" in lock
    assert f"# upstream-ref: {EXPECTED_REF}" in lock
    assert f"# upstream-lock-sha256: {EXPECTED_LOCK_SHA256}" in lock
    assert f"# langgraph-cli-version: {EXPECTED_CLI}" in lock

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lock.splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)

    assert blocks
    assert all("--hash=sha256:" in "\n".join(block) for block in blocks)


def test_runtime_requirements_clear_known_fixable_advisories():
    lock = (
        REPO
        / "services/local-deep-researcher/build/config/runtime-requirements.lock"
    ).read_text(encoding="utf-8")
    generator = (REPO / "scripts/refresh-local-deep-researcher-lock.py").read_text(
        encoding="utf-8"
    )
    project = (REPO / "services/local-deep-researcher/locks/runtime-pyproject.toml").read_text(
        encoding="utf-8"
    )

    for package, version in SECURITY_FLOORS.items():
        # A package can resolve per Python branch; every branch must clear the floor.
        pins = re.findall(
            rf"^{re.escape(package)}==([^ ;]+)(?: ;[^\\\n]*)? \\$", lock, re.MULTILINE
        )
        assert pins, package
        assert all(Version(pin) >= Version(version) for pin in pins), (package, pins)
        assert f'"{package}>={version}"' in generator
        assert f'"{package}>={version}"' in project


@pytest.mark.parametrize("package,version", SERVING_SECURITY_FLOORS.items())
def test_serving_security_floors_preserve_project_floor(package, version):
    module = runpy.run_path(str(REPO / "scripts/refresh-local-deep-researcher-lock.py"))
    declaration = f"{package}>={version} ; python_full_version >= '3.11'"
    assert declaration in module["SECURITY_DEPENDENCIES"]
    project = module["tomllib"].loads(
        (REPO / "services/local-deep-researcher/locks/runtime-pyproject.toml").read_text()
    )["project"]
    assert project["requires-python"] == ">=3.10"
    assert declaration in project["dependencies"]


@pytest.mark.parametrize("package,version", SERVING_SECURITY_FLOORS.items())
def test_serving_security_locks_preserve_python_branches(package, version):
    lock = REPO / "services/local-deep-researcher/build/config/runtime-requirements.lock"
    rows = [Requirement(line.rstrip(" \\")) for line in lock.read_text().splitlines()
            if line.startswith(package + "==")]
    assert len(rows) == 1
    row, = rows
    assert Version(next(iter(row.specifier)).version) >= Version(version)
    assert row.marker is not None
    assert row.marker.evaluate({"python_version": "3.11", "python_full_version": "3.11.0"})
    assert not row.marker.evaluate({"python_version": "3.10", "python_full_version": "3.10.99"})


@pytest.mark.parametrize("package,version", SERVING_SECURITY_FLOORS.items())
def test_combined_project_rejects_a_missing_serving_security_floor(tmp_path, package, version):
    module = runpy.run_path(str(REPO / "scripts/refresh-local-deep-researcher-lock.py"))
    canonical = REPO / "services/local-deep-researcher/locks/runtime-pyproject.toml"
    declaration = f"{package}>={version} ; python_full_version >= '3.11'"
    project = tmp_path / "pyproject.toml"
    project.write_text(canonical.read_text().replace(f'    "{declaration}",\n', ""))
    with pytest.raises(SystemExit, match=re.escape(declaration)):
        module["_validate_combined_project"](project, EXPECTED_CLI)


def test_combined_project_rejects_a_missing_certificate_security_floor(tmp_path):
    module = runpy.run_path(str(REPO / "scripts/refresh-local-deep-researcher-lock.py"))
    canonical = REPO / "services/local-deep-researcher/locks/runtime-pyproject.toml"
    module["_validate_combined_project"](canonical, EXPECTED_CLI)
    project = tmp_path / "pyproject.toml"
    project.write_text(canonical.read_text().replace('    "certifi>=2026.10.19",\n', ""))
    with pytest.raises(SystemExit, match=re.escape("certifi>=2026.10.19")):
        module["_validate_combined_project"](project, EXPECTED_CLI)


def test_combined_project_accepts_uv_rendered_security_floors(tmp_path):
    # refresh() validates the pyproject that `uv add` rewrites. uv renders
    # markers as `name>=x ; python_full_version >= ...`, so the declared
    # floors must use that exact form or every refresh fails validation.
    import shutil

    if not shutil.which("uv"):
        pytest.skip("uv is not installed")
    module = runpy.run_path(str(REPO / "scripts/refresh-local-deep-researcher-lock.py"))
    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[project]\nname = "uv-rendered"\nversion = "0"\n'
        'requires-python = ">=3.10"\ndependencies = []\n',
        encoding="utf-8",
    )
    subprocess.run(
        [
            "uv", "add", "--project", str(tmp_path), "--frozen",
            f"langgraph-cli[inmem]=={EXPECTED_CLI}",
            *module["BUILD_DEPENDENCIES"], *module["SECURITY_DEPENDENCIES"],
        ],
        capture_output=True, check=True, env={**os.environ, "UV_OFFLINE": "1"},
    )
    module["_validate_combined_project"](project, EXPECTED_CLI)


def test_runtime_helper_reuses_a_healthy_virtual_environment(tmp_path):
    venv = tmp_path / "venv"
    marker = venv / "preserved-on-reuse"

    subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; ensure_python_venv "$2" 3.11; touch "$3"; '
            'ensure_python_venv "$2" 3.11; test -f "$3"',
            "bash",
            str(RUNTIME_LIB),
            str(venv),
            str(marker),
        ],
        check=True,
    )


def test_runtime_helper_recreates_a_corrupt_virtual_environment(tmp_path):
    import shutil

    # The helper builds a 3.11 venv; uv would otherwise download a Python
    # into ~/.local/share/uv on a host without one (offline: a false failure).
    found = shutil.which("uv") and subprocess.run(
        ["uv", "python", "find", "3.11"], capture_output=True, check=False,
        env={**os.environ, "UV_PYTHON_DOWNLOADS": "never"},
    ).returncode == 0
    if not found:
        pytest.skip("no local Python 3.11 for uv; the test must not download one")
    venv = tmp_path / "venv"
    python = venv / "bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    python.chmod(0o755)
    stale = venv / "stale"
    stale.touch()

    subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; ensure_python_venv "$2" 3.11; '
            '"$2/bin/python" -c "import sys; assert sys.version_info[:2] == (3, 11)"; '
            'test ! -e "$3"',
            "bash",
            str(RUNTIME_LIB),
            str(venv),
            str(stale),
        ],
        check=True,
    )


def test_runtime_helper_recovers_invalid_git_repo_and_missing_origin(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)

    corrupt = tmp_path / "corrupt"
    (corrupt / ".git").mkdir(parents=True)
    (corrupt / "stale").touch()
    missing_origin = tmp_path / "missing-origin"
    subprocess.run(["git", "init", "-q", str(missing_origin)], check=True)

    for repo in (corrupt, missing_origin):
        subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; ensure_git_repo "$2" "$3"; '
                'git -C "$2" rev-parse --git-dir >/dev/null; '
                'test "$(git -C "$2" remote get-url origin)" = "$3"',
                "bash",
                str(RUNTIME_LIB),
                str(repo),
                str(origin),
            ],
            check=True,
        )


def test_runtime_lock_generator_is_byte_equivalent():
    subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts/refresh-local-deep-researcher-lock.py"),
            "--check",
        ],
        cwd=REPO,
        check=True,
    )


def test_runtime_lock_check_fails_on_byte_drift(tmp_path, monkeypatch):
    script_path = REPO / "scripts/refresh-local-deep-researcher-lock.py"
    spec = importlib.util.spec_from_file_location("refresh_ldr_lock", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    runtime_pyproject = tmp_path / "runtime-pyproject.toml"
    runtime_uv_lock = tmp_path / "runtime.uv.lock"
    runtime_requirements = tmp_path / "runtime-requirements.lock"
    runtime_pyproject.write_text("[project]\nname='test'\nversion='0'\n", encoding="utf-8")
    runtime_uv_lock.write_text("version = 1\n", encoding="utf-8")
    runtime_requirements.write_text("stale\n", encoding="utf-8")

    monkeypatch.setattr(module, "RUNTIME_PYPROJECT", runtime_pyproject)
    monkeypatch.setattr(module, "RUNTIME_UV_LOCK", runtime_uv_lock)
    monkeypatch.setattr(module, "RUNTIME_REQUIREMENTS", runtime_requirements)
    monkeypatch.setattr(module, "_pins", lambda: ("ref", "sha", "cli"))
    monkeypatch.setattr(
        module,
        "_stable_export",
        lambda *_args: "regenerated\n",
    )

    with pytest.raises(SystemExit, match="runtime lock drift"):
        module.check()


# The registered LDR runtime audit projects the shipped Linux Python 3.11 container
# before pip-audit collects pins.
_LDR_AUDIT_LOCK = "services/local-deep-researcher/build/config/runtime-requirements.lock"


def _audit_marker_host(version, system, machine):
    return {
        "implementation_name": "cpython", "implementation_version": version,
        "python_full_version": version, "python_version": ".".join(version.split(".")[:2]),
        "platform_python_implementation": "CPython", "os_name": "posix",
        "sys_platform": "linux" if system == "Linux" else "darwin",
        "platform_system": system, "platform_machine": machine,
        "platform_release": "", "platform_version": "",
    }


def _marker_audit_lock(root, text):
    lock = root / _LDR_AUDIT_LOCK
    lock.parent.mkdir(parents=True)
    lock.write_text(text, encoding="utf-8")
    return _runtime_audit_spec(_LDR_AUDIT_LOCK)


def _collector_marker_pairs(text, environment):
    pairs = {}
    for line in text.splitlines():
        if not re.match(r"^[A-Za-z0-9_.-]+(?:\[[^]]+\])?==", line):
            continue
        requirement = Requirement(line.removesuffix("\\").strip())
        if requirement.marker is None or requirement.marker.evaluate(environment):
            pairs[requirement.name] = str(next(iter(requirement.specifier)).version)
    return pairs


def _marker_collector(monkeypatch, environment, captured):
    def collect(command, **kwargs):
        text = Path(command[command.index("-r") + 1]).read_text()
        pairs = _collector_marker_pairs(text, environment)
        captured.update(text=text, pairs=pairs)
        payload = {"dependencies": [
            {"name": name, "version": version, "vulns": []}
            for name, version in pairs.items()
        ]}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(audit_runtime_locks, "run_bounded", collect)


@pytest.mark.parametrize("host", [
    ("3.10.12", "Darwin", "arm64"),
    ("3.11.17", "Darwin", "arm64"),
    ("3.12.9", "Linux", "x86_64"),
])
def test_registered_ldr_audit_projects_the_shipped_runtime_before_collection(tmp_path, monkeypatch, host):
    digest = "a" * 64
    rows = [
        "safe==1.0",
        "langgraph-api==0.13.5 ; python_full_version >= '3.11'",
        "opentelemetry-api==1.42.1 ; python_full_version >= '3.11'",
        "greenlet==3.2.3 ; sys_platform == 'linux' and (platform_machine == 'x86_64' or platform_machine == 'aarch64')",
        "cffi==1.17.1 ; python_full_version < '3.11' and platform_python_implementation == 'PyPy'",
        "cffi==2.1.1 ; python_full_version >= '3.11'",
        "numpy==2.2.6 ; python_full_version < '3.11'",
        "numpy==2.3.2 ; python_full_version >= '3.11'",
        "uuid-utils==0.14.1 ; python_full_version < '3.11'",
        "uuid-utils==0.17.1 ; python_full_version >= '3.11'",
    ]
    text = "# frozen export\n\n" + "".join(
        f"{row} \\\n    --hash=sha256:{digest} \\\n    --hash=sha256:{digest}\n# via serving family\n" for row in rows
    )
    spec = _marker_audit_lock(tmp_path, text)
    captured = {}
    _marker_collector(monkeypatch, _audit_marker_host(*host), captured)
    assert audit_runtime_locks.audit_spec(spec, root=tmp_path) == []
    assert captured["pairs"] == {
        "safe": "1.0", "langgraph-api": "0.13.5", "opentelemetry-api": "1.42.1",
        "greenlet": "3.2.3", "cffi": "2.1.1", "numpy": "2.3.2", "uuid-utils": "0.17.1",
    }
    assert ";" not in captured["text"] and "--hash" not in captured["text"]
    assert (tmp_path / _LDR_AUDIT_LOCK).read_text() == text


@pytest.mark.parametrize("dependencies", [
    [],
    [{"name": "safe", "version": "2.0", "vulns": []}],
    [{"name": "safe", "version": "1.0", "vulns": []}, {"name": "extra", "version": "1.0", "vulns": []}],
    [{"name": "safe", "version": "1.0", "skip_reason": "not collected"}],
    [{"name": "safe", "version": "1.0", "vulns": []}] * 2,
    [{"name": "safe", "vulns": []}],
    "not a dependency list",
])
def test_registered_ldr_audit_rejects_incomplete_collector_coverage(tmp_path, monkeypatch, dependencies):
    spec = _marker_audit_lock(tmp_path, "safe==1.0\n")
    monkeypatch.setattr(audit_runtime_locks, "run_bounded", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout=json.dumps({"dependencies": dependencies}), stderr="",
    ))
    failures = audit_runtime_locks.audit_spec(spec, root=tmp_path)
    assert failures and all("coverage" in failure for failure in failures)


@pytest.mark.parametrize("text", [
    "same==1.0 ; python_full_version >= '3.11'\nsame==2.0 ; python_full_version >= '3.11'\n",
    "safe==1.0 \\\n",
    "safe==1.0 ; python_full_version ??? '3.11'\n",
    "safe>=1.0\n",
    "safe @ https://user:secret@example.invalid/private.whl\n",
    "safe==1.0 \\\n    --hash=sha256:bad\n",
])
def test_registered_ldr_audit_rejects_ambiguous_or_malformed_projection(tmp_path, monkeypatch, text):
    spec = _marker_audit_lock(tmp_path, text)
    calls = []
    monkeypatch.setattr(audit_runtime_locks, "run_bounded", lambda *_args, **_kwargs: (
        calls.append(True) or SimpleNamespace(returncode=0, stdout='{"dependencies": []}', stderr="")
    ))
    failures = audit_runtime_locks.audit_spec(spec, root=tmp_path)
    assert failures and all("projection" in failure for failure in failures)
    assert "secret" not in " ".join(failures)
    assert calls == []


def test_registered_ldr_audit_coalesces_matching_active_duplicates(tmp_path, monkeypatch):
    spec = _marker_audit_lock(tmp_path, "safe==1.0\nsafe==1.0 ; python_full_version >= '3.11'\n")
    captured = {}
    _marker_collector(monkeypatch, _audit_marker_host("3.10.12", "Darwin", "arm64"), captured)
    assert audit_runtime_locks.audit_spec(spec, root=tmp_path) == []
    assert captured["text"].splitlines() == ["safe==1.0"]


def test_unprojected_audit_specs_keep_their_original_marker_input(tmp_path, monkeypatch):
    text = "safe==1.0 ; python_full_version >= '3.11'\n"
    (tmp_path / "other.txt").write_text(text)
    captured = {}
    _marker_collector(monkeypatch, _audit_marker_host("3.12.9", "Linux", "x86_64"), captured)
    assert audit_runtime_locks.audit_spec(audit_runtime_locks.AuditSpec("other.txt"), root=tmp_path) == []
    assert captured["text"] == text


def test_ldr_audit_marker_context_matches_the_shipped_container():
    root = Path(__file__).resolve().parents[2]
    build = root / "services/local-deep-researcher/build"
    dockerfile = (build / "Dockerfile").read_text()
    entrypoint = (build / "scripts/docker-entrypoint.sh").read_text()
    version = re.search(r"LOCAL_DEEP_RESEARCHER_IMAGE:-python:([\d.]+)-slim", dockerfile)[1]
    environments = _runtime_audit_spec(_LDR_AUDIT_LOCK).marker_environments
    assert {environment["platform_machine"] for environment in environments} == {"x86_64", "aarch64"}
    assert all(environment["python_full_version"] == version for environment in environments)
    assert all(environment["implementation_version"] == version for environment in environments)
    assert all(environment["python_version"] == "3.11" for environment in environments)
    assert all(environment["sys_platform"] == "linux" for environment in environments)
    assert all(environment["implementation_name"] == "cpython" for environment in environments)
    assert 'ensure_python_venv "$VENV_DIR" 3.11' in entrypoint
    assert 'uv pip sync --python "$VENV_PYTHON" --require-hashes "$RUNTIME_LOCK"' in entrypoint


@pytest.mark.parametrize("machine", ["x86_64", "aarch64"])
def test_registered_ldr_audit_collects_current_container_pins_on_another_host(monkeypatch, machine):
    root = Path(__file__).resolve().parents[2]
    spec = _runtime_audit_spec(_LDR_AUDIT_LOCK)
    text = (root / spec.lock).read_text()
    expected = _collector_marker_pairs(text, _audit_marker_host("3.11.15", "Linux", machine))
    captured = {}
    _marker_collector(monkeypatch, _audit_marker_host("3.10.12", "Darwin", "arm64"), captured)
    assert audit_runtime_locks.audit_spec(spec, root=root) == []
    assert captured["pairs"] == expected
    assert "greenlet" in captured["pairs"]
    assert "langgraph-api" in captured["pairs"]
    assert "opentelemetry-api" in captured["pairs"]
    assert ";" not in captured["text"] and "--hash" not in captured["text"]
    assert (root / spec.lock).read_text() == text
