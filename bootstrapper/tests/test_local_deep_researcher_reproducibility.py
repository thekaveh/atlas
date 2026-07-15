"""Local Deep Researcher runtime inputs must be manifest-owned and pinned."""

from pathlib import Path
import re
import subprocess
import sys

from packaging.version import Version
import yaml


REPO = Path(__file__).resolve().parents[2]
EXPECTED_REF = "38f769f84380f2065de76021ac7c5215f88aa39e"
EXPECTED_CLI = "0.4.31"
EXPECTED_LOCK_SHA256 = "26fc35ac377836de6628e5f7b180944c4d4bd50a5e9f0200bd6e663f20e35c1a"
SECURITY_FLOORS = {
    "click": "8.3.3",
    "langchain-classic": "1.0.7",
    "langsmith": "0.8.18",
    "soupsieve": "2.8.4",
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
    assert "setuptools==83.0.0 \\" in lock
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

    for package, version in SECURITY_FLOORS.items():
        match = re.search(rf"^{re.escape(package)}==([^ ]+) \\$", lock, re.MULTILINE)
        assert match, package
        assert Version(match.group(1)) >= Version(version)
        assert f'"{package}>={version}"' in generator


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
