"""Behavioral tests for the AI-Dock custom-node provisioning shell hook."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "services/comfyui/provisioning/provision_custom_nodes.sh"
REF = "a" * 40


def _executable(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _run_plan(
    tmp_path: Path,
    row: str,
    *,
    cached: bool = False,
    git_fails: bool = False,
    symlink_nodes_to: Path | None = None,
    snapshot_fails: bool = False,
    prior_ready: bool = False,
    final_newline: bool = True,
    destination_symlink_to: Path | None = None,
    current_ref: str = REF,
    swap_root_during_clone: bool = False,
    lock_as_symlink: bool = False,
    swap_lock_before_pip: bool = False,
    swap_tmp_after_checkout: bool = False,
    swap_original_in_pip: bool = False,
    workspace_chmod_fails: bool = False,
    source_mode: bool = False,
    status_failure: str | None = None,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    mutations = tmp_path / "mutations.log"
    nodes = tmp_path / "nodes"
    manifest = tmp_path / "manifest"
    lock_dir = manifest / "custom-node-locks"
    lock_dir.mkdir(parents=True)
    payload = b"example==1.0 --hash=sha256:" + b"b" * 64 + b"\n"
    digest = hashlib.sha256(payload).hexdigest()
    lock = lock_dir / f"{digest}.txt"
    if lock_as_symlink:
        outside_lock = tmp_path / "outside-lock.txt"
        outside_lock.write_bytes(payload)
        lock.symlink_to(outside_lock)
    else:
        lock.write_bytes(payload)
    plan = manifest / "active-custom-nodes.tsv"
    rendered_row = row.format(lock=lock, digest=digest, ref=REF)
    plan.write_text(
        rendered_row + ("\n" if rendered_row and final_newline else ""),
        encoding="utf-8",
    )

    _executable(
        bin_dir / "git",
        'printf "git %s\\n" "$*" >> "$MUTATION_LOG"\n'
        'if [ "${TEST_GIT_FAIL:-0}" = "1" ]; then exit 1; fi\n'
        'if [ "$1" = "clone" ] && [ "${SWAP_ROOT_DURING_CLONE:-0}" = "1" ]; then '
        'mv "$NODE_ROOT" "$NODE_ROOT.before-swap"; '
        'ln -s "$OUTSIDE_ROOT" "$NODE_ROOT"; fi\n'
        'if [ "$1" = "clone" ]; then mkdir -p "$3/.git"; fi\n'
        'if [ "$1" = "-C" ]; then : > "$2/git-mutated"; fi\n'
        'if [ "$1" = "-C" ] && [ "$3" = "checkout" ] '
        '&& [ "${SWAP_LOCK_BEFORE_PIP:-0}" = "1" ]; then '
        'rm -f "$LOCK_PATH"; ln -s "$OUTSIDE_LOCK" "$LOCK_PATH"; fi\n'
        'if [ "$1" = "-C" ] && [ "$3" = "checkout" ] '
        '&& [ "${SWAP_TMP_AFTER_CHECKOUT:-0}" = "1" ]; then '
        'rm -rf "$2"; ln -s "$OUTSIDE_NODE" "$2"; fi\n'
        'if [ "$1" = "-C" ] && [ "$3" = "rev-parse" ]; then printf "%s\\n" "$CURRENT_REF"; fi\n',
    )
    fake_pip = _executable(
        bin_dir / "pip",
        'printf "pip %s\\n" "$*" >> "$MUTATION_LOG"\n'
        'if [ "${SWAP_ORIGINAL_IN_PIP:-0}" = "1" ]; then '
        'rm -f "$LOCK_PATH"; printf "malicious\\n" > "$LOCK_PATH"; fi\n'
        'last_arg=""; for arg in "$@"; do last_arg="$arg"; done\n'
        'sha256sum "$last_arg" | cut -d " " -f1 >> "$PIP_INPUT_LOG"\n',
    )
    if workspace_chmod_fails or status_failure == "chmod":
        _executable(
            bin_dir / "chmod",
            'if [ "${WORKSPACE_CHMOD_FAILS:-0}" = "1" ] && [ "$1" = "700" ]; then exit 1; fi\n'
            'if [ "${STATUS_CHMOD_FAILS:-0}" = "1" ]; then '
            'case "${2:-}" in ./.atlas-node-provisioning.tsv.*) exit 1 ;; esac; fi\n'
            '/bin/chmod "$@"\n',
        )
    if status_failure == "safety":
        _executable(
            bin_dir / "realpath",
            'case "$1" in ./.atlas-node-provisioning.tsv.*) printf "/unsafe-status-temp\\n"; exit 0 ;; esac\n'
            '/usr/bin/realpath "$@"\n',
        )
    if status_failure == "mv":
        _executable(
            bin_dir / "mv",
            'case "${3:-}" in ./.atlas-node-provisioning.tsv) exit 1 ;; esac\n'
            '/bin/mv "$@"\n',
        )
    if cached:
        (nodes / "node" / ".git").mkdir(parents=True)
    elif destination_symlink_to is not None:
        nodes.mkdir()
        (nodes / "node").symlink_to(destination_symlink_to, target_is_directory=True)
    elif symlink_nodes_to is not None:
        nodes.symlink_to(symlink_nodes_to, target_is_directory=True)
    if prior_ready:
        nodes.mkdir(exist_ok=True)
        plan_sha = hashlib.sha256(plan.read_bytes()).hexdigest()
        (nodes / ".atlas-node-provisioning.tsv").write_text(
            f"v1\t{plan_sha}\tready\t0\t0\n", encoding="utf-8"
        )

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "COMFYUI_CUSTOM_NODES_PATH": str(nodes),
        "COMFYUI_CUSTOM_NODES_TSV": str(plan),
        "COMFYUI_MANIFEST_ROOT": str(manifest),
        "COMFYUI_VENV_PIP": str(fake_pip),
        "MUTATION_LOG": str(mutations),
        "EXPECTED_REF": REF,
        "CURRENT_REF": current_ref,
        "TEST_GIT_FAIL": "1" if git_fails else "0",
        "SWAP_ROOT_DURING_CLONE": "1" if swap_root_during_clone else "0",
        "NODE_ROOT": str(nodes),
        "OUTSIDE_ROOT": str(tmp_path / "race-outside"),
        "LOCK_PATH": str(lock),
        "OUTSIDE_LOCK": str(tmp_path / "outside-lock.txt"),
        "SWAP_LOCK_BEFORE_PIP": "1" if swap_lock_before_pip else "0",
        "SWAP_TMP_AFTER_CHECKOUT": "1" if swap_tmp_after_checkout else "0",
        "OUTSIDE_NODE": str(tmp_path / "outside-node"),
        "SWAP_ORIGINAL_IN_PIP": "1" if swap_original_in_pip else "0",
        "PIP_INPUT_LOG": str(tmp_path / "pip-input.log"),
        "WORKSPACE_CHMOD_FAILS": "1" if workspace_chmod_fails else "0",
        "STATUS_CHMOD_FAILS": "1" if status_failure == "chmod" else "0",
    }
    if snapshot_fails:
        env["TMPDIR"] = str(tmp_path / "missing-tmpdir")
    command = ["/bin/bash", str(SCRIPT)]
    if source_mode:
        command = [
            "/bin/bash",
            "-c",
            'umask 022; source "$1"; printf "caller-umask=%s\\n" "$(umask)"',
            "atlas-source-test",
            str(SCRIPT),
        ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return result, nodes, mutations


@pytest.mark.parametrize(
    "row",
    [
        "node\thttps://github.com/o/node.git\t{ref}\ttrue\t{lock}\t0000000000000000000000000000000000000000000000000000000000000000",
        "node\thttps://github.com/o/node.git\t{ref}\ttrue\t{lock}/../escape.txt\t{digest}",
        "node\thttps://github.com/o/node.git",
    ],
)
def test_invalid_plan_never_mutates_git_pip_or_node_tree(tmp_path: Path, row: str) -> None:
    result, nodes, mutations = _run_plan(tmp_path, row)

    assert not (nodes / "node").exists()
    assert _node_status_fields(nodes)[2] == "failed"
    assert not mutations.exists()
    assert "failed" in result.stdout or "skipping" in result.stdout


def test_verified_lock_uses_production_hash_flags(tmp_path: Path) -> None:
    row = "node\thttps://github.com/o/node.git\t{ref}\ttrue\t{lock}\t{digest}"
    _result, nodes, mutations = _run_plan(tmp_path, row)

    assert (nodes / "node" / ".git").is_dir()
    log = mutations.read_text(encoding="utf-8")
    assert "git clone https://github.com/o/node.git" in log
    assert "pip install --no-cache-dir --no-deps --require-hashes -r" in log


def test_cached_node_still_applies_verified_lock(tmp_path: Path) -> None:
    row = "node\thttps://github.com/o/node.git\t{ref}\ttrue\t{lock}\t{digest}"
    result, _nodes, mutations = _run_plan(tmp_path, row, cached=True)

    log = mutations.read_text(encoding="utf-8")
    assert "git clone" not in log
    assert "pip install --no-cache-dir --no-deps --require-hashes -r" in log
    assert "cached at" in result.stdout


def _node_status_fields(nodes: Path) -> list[str]:
    status = nodes / ".atlas-node-provisioning.tsv"
    assert status.is_file()
    return status.read_text(encoding="utf-8").strip().split("\t")


def test_required_node_failure_writes_failed_plan_status(tmp_path: Path) -> None:
    row = "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired"
    result, nodes, _ = _run_plan(tmp_path, row, git_fails=True)

    assert result.returncode == 0
    assert _node_status_fields(nodes)[2:] == ["failed", "1", "0"]


def test_legacy_six_column_node_failure_defaults_to_required(tmp_path: Path) -> None:
    row = "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t"

    _result, nodes, _ = _run_plan(tmp_path, row, git_fails=True)

    assert _node_status_fields(nodes)[2:] == ["failed", "1", "0"]


def test_optional_node_failure_warns_but_plan_is_ready(tmp_path: Path) -> None:
    row = "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\toptional"
    result, nodes, _ = _run_plan(tmp_path, row, git_fails=True)

    assert result.returncode == 0
    assert "optional" in result.stdout.lower()
    assert _node_status_fields(nodes)[2:] == ["ready", "0", "1"]


@pytest.mark.parametrize(
    ("policy", "expected_status"),
    [
        ("required", ["failed", "1", "0"]),
        ("optional", ["ready", "0", "1"]),
    ],
)
def test_git_failure_is_counted_once_and_skips_dependency_phase(
    tmp_path: Path, policy: str, expected_status: list[str]
) -> None:
    row = (
        "node\thttps://github.com/o/node.git\t{ref}\ttrue\t{lock}\t{digest}\t"
        + policy
    )

    _result, nodes, mutations = _run_plan(tmp_path, row, git_fails=True)

    assert _node_status_fields(nodes)[2:] == expected_status
    operations = mutations.read_text(encoding="utf-8")
    assert "git clone" in operations
    assert "pip " not in operations


def test_cached_node_refreshes_ready_status_for_exact_plan(tmp_path: Path) -> None:
    row = "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired"
    result, nodes, mutations = _run_plan(tmp_path, row, cached=True)

    assert result.returncode == 0
    assert "git clone" not in mutations.read_text(encoding="utf-8")
    assert _node_status_fields(nodes)[2:] == ["ready", "0", "0"]


def test_symlinked_custom_node_root_is_rejected_without_outside_mutation(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    row = "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired"

    result, _nodes, mutations = _run_plan(tmp_path, row, symlink_nodes_to=outside)

    assert result.returncode == 0
    assert "unsafe" in result.stdout.lower()
    assert not mutations.exists()
    assert list(outside.iterdir()) == []


def test_empty_node_plan_publishes_digest_bound_ready_status(tmp_path: Path) -> None:
    result, nodes, mutations = _run_plan(tmp_path, "")

    assert result.returncode == 0
    fields = _node_status_fields(nodes)
    assert fields[1] == hashlib.sha256(b"").hexdigest()
    assert fields[2:] == ["ready", "0", "0"]
    assert not mutations.exists()


def test_node_snapshot_failure_invalidates_prior_ready_status(tmp_path: Path) -> None:
    row = "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired"
    result, nodes, mutations = _run_plan(
        tmp_path, row, snapshot_fails=True, prior_ready=True
    )

    assert result.returncode == 0
    assert not (nodes / ".atlas-node-provisioning.tsv").exists()
    assert not mutations.exists()


def test_final_required_row_without_newline_fails_closed_before_effects(tmp_path: Path) -> None:
    row = "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired"

    result, nodes, mutations = _run_plan(tmp_path, row, final_newline=False)

    assert "invalid custom-node plan" in result.stdout
    assert _node_status_fields(nodes)[2:] == ["failed", "1", "0"]
    assert not (nodes / "node").exists()
    assert not mutations.exists()


@pytest.mark.parametrize(
    "invalid_row",
    [
        "unsafe/name\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired",
        "node\thttp://github.com/o/node.git\t{ref}\tfalse\t\t\trequired",
        "node\thttps://github.com/o/node\t{ref}\tfalse\t\t\trequired",
        "node\thttps://github.com/o/node.git\tnot-a-ref\tfalse\t\t\trequired",
        "node\thttps://github.com/o/node.git\t{ref}\tyes\t\t\trequired",
        "node\thttps://github.com/o/node.git\t{ref}\tfalse\t{lock}\t{digest}\trequired",
        "node\thttps://github.com/o/node.git\t{ref}\ttrue\t{lock}\t\trequired",
        "node\thttps://github.com/o/node.git\t{ref}\ttrue\t{lock}\t{digest}\tadvisory",
        "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired\r",
        "other\x01\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired",
    ],
)
def test_whole_plan_preflight_rejects_malformed_later_row_before_any_effect(
    tmp_path: Path, invalid_row: str
) -> None:
    first = "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired"
    second = invalid_row.replace("node\t", "other\t", 1)

    result, nodes, mutations = _run_plan(tmp_path, first + "\n" + second)

    assert "invalid custom-node plan" in result.stdout
    assert _node_status_fields(nodes)[2:] == ["failed", "1", "0"]
    assert not (nodes / "node").exists()
    assert not (nodes / "other").exists()
    assert not mutations.exists()


def test_conflicting_duplicate_destination_is_rejected_before_any_effect(tmp_path: Path) -> None:
    row = "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired"
    conflict = "node\thttps://github.com/o/other.git\t{ref}\tfalse\t\t\trequired"

    result, nodes, mutations = _run_plan(tmp_path, row + "\n" + conflict)

    assert "invalid custom-node plan" in result.stdout
    assert _node_status_fields(nodes)[2:] == ["failed", "1", "0"]
    assert not (nodes / "node").exists()
    assert not mutations.exists()


@pytest.mark.parametrize("current_ref", [REF, "b" * 40], ids=["cached", "update"])
def test_destination_symlink_is_rejected_without_outside_git_or_pip_mutation(
    tmp_path: Path, current_ref: str
) -> None:
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    row = "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired"

    result, nodes, mutations = _run_plan(
        tmp_path,
        row,
        destination_symlink_to=outside,
        current_ref=current_ref,
    )

    assert "unsafe" in result.stdout.lower()
    assert _node_status_fields(nodes)[2:] == ["failed", "1", "0"]
    assert not (outside / "git-mutated").exists()
    assert not mutations.exists()


def test_optional_destination_symlink_is_a_security_failure_not_a_warning(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    row = "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\toptional"

    _result, nodes, mutations = _run_plan(
        tmp_path,
        row,
        destination_symlink_to=outside,
    )

    assert _node_status_fields(nodes)[2] == "failed"
    assert not (outside / "git-mutated").exists()
    assert not mutations.exists()


def test_root_swap_during_git_fails_closed_without_publishing_outside_status(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "race-outside"
    outside.mkdir()
    outside_status = outside / ".atlas-node-provisioning.tsv"
    outside_status.write_text("sentinel\n", encoding="utf-8")
    row = "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired"

    result, nodes, _mutations = _run_plan(
        tmp_path,
        row,
        swap_root_during_clone=True,
    )

    assert result.returncode == 0
    assert nodes.is_symlink()
    assert outside_status.read_text(encoding="utf-8") == "sentinel\n"
    assert list(outside.iterdir()) == [outside_status]
    original_root = tmp_path / "nodes.before-swap"
    assert (original_root / "node" / ".git").is_dir()
    assert _node_status_fields(original_root)[2:] == ["ready", "0", "0"]


def test_symlinked_dependency_lock_fails_whole_plan_preflight(tmp_path: Path) -> None:
    row = "node\thttps://github.com/o/node.git\t{ref}\ttrue\t{lock}\t{digest}\trequired"

    result, nodes, mutations = _run_plan(tmp_path, row, lock_as_symlink=True)

    assert "invalid custom-node plan" in result.stdout
    assert _node_status_fields(nodes)[2:] == ["failed", "1", "0"]
    assert not mutations.exists()


def test_dependency_lock_is_rechecked_before_pip(tmp_path: Path) -> None:
    outside_lock = tmp_path / "outside-lock.txt"
    outside_lock.write_bytes(b"example==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    row = "node\thttps://github.com/o/node.git\t{ref}\ttrue\t{lock}\t{digest}\trequired"

    result, nodes, mutations = _run_plan(tmp_path, row, swap_lock_before_pip=True)

    assert "requirements install failed" in result.stdout
    assert _node_status_fields(nodes)[2:] == ["failed", "1", "0"]
    assert "pip " not in mutations.read_text(encoding="utf-8")


def test_pip_consumes_private_verified_lock_when_original_changes_on_entry(
    tmp_path: Path,
) -> None:
    row = "node\thttps://github.com/o/node.git\t{ref}\ttrue\t{lock}\t{digest}\trequired"

    _result, nodes, mutations = _run_plan(
        tmp_path,
        row,
        swap_original_in_pip=True,
    )

    expected = hashlib.sha256(
        b"example==1.0 --hash=sha256:" + b"b" * 64 + b"\n"
    ).hexdigest()
    assert (tmp_path / "pip-input.log").read_text(encoding="utf-8").strip() == expected
    lock = next((tmp_path / "manifest/custom-node-locks").iterdir())
    assert lock.read_text(encoding="utf-8") == "malicious\n"
    pip_line = next(
        line
        for line in mutations.read_text(encoding="utf-8").splitlines()
        if line.startswith("pip ")
    )
    private_snapshot = Path(pip_line.rsplit(" ", 1)[-1])
    assert "comfy-node-locks." in str(private_snapshot.parent)
    assert not private_snapshot.parent.exists(), "private workspace must be trap-cleaned"
    assert _node_status_fields(nodes)[2:] == ["ready", "0", "0"]


def test_all_git_work_finishes_before_any_dependency_install(tmp_path: Path) -> None:
    first = "node\thttps://github.com/o/node.git\t{ref}\ttrue\t{lock}\t{digest}\trequired"
    second = "other\thttps://github.com/o/other.git\t{ref}\ttrue\t{lock}\t{digest}\trequired"

    _result, _nodes, mutations = _run_plan(tmp_path, first + "\n" + second)

    operations = mutations.read_text(encoding="utf-8").splitlines()
    last_git = max(index for index, line in enumerate(operations) if line.startswith("git "))
    first_pip = min(index for index, line in enumerate(operations) if line.startswith("pip "))
    assert last_git < first_pip


def test_private_lock_workspace_permission_failure_blocks_pip_and_readiness(
    tmp_path: Path,
) -> None:
    row = "node\thttps://github.com/o/node.git\t{ref}\ttrue\t{lock}\t{digest}\trequired"

    _result, nodes, mutations = _run_plan(
        tmp_path,
        row,
        workspace_chmod_fails=True,
    )

    assert _node_status_fields(nodes)[2] == "failed"
    assert "pip " not in mutations.read_text(encoding="utf-8")


@pytest.mark.parametrize("policy", ["required", "optional"])
def test_leading_hyphen_node_reaches_private_lock_pip_phase(
    tmp_path: Path, policy: str
) -> None:
    row = (
        "-v\thttps://github.com/o/node.git\t{ref}\ttrue\t{lock}\t{digest}\t"
        + policy
    )

    _result, nodes, mutations = _run_plan(tmp_path, row)

    assert (nodes / "-v" / ".git").is_dir()
    assert "pip install" in mutations.read_text(encoding="utf-8")
    expected = hashlib.sha256(
        b"example==1.0 --hash=sha256:" + b"b" * 64 + b"\n"
    ).hexdigest()
    assert (tmp_path / "pip-input.log").read_text(encoding="utf-8").strip() == expected
    assert _node_status_fields(nodes)[2:] == ["ready", "0", "0"]


@pytest.mark.parametrize(
    ("row", "kwargs"),
    [
        ("", {}),
        ("node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired", {}),
        (
            "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired",
            {"git_fails": True},
        ),
    ],
    ids=["early-empty", "success", "failure"],
)
def test_sourced_hook_preserves_caller_umask(
    tmp_path: Path, row: str, kwargs: dict[str, bool]
) -> None:
    result, _nodes, _mutations = _run_plan(
        tmp_path,
        row,
        source_mode=True,
        **kwargs,
    )

    assert "caller-umask=0022" in result.stdout


@pytest.mark.parametrize("failure", ["chmod", "safety", "mv"])
def test_status_publication_failure_removes_atomic_temp(
    tmp_path: Path, failure: str
) -> None:
    row = "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired"

    _result, nodes, _mutations = _run_plan(tmp_path, row, status_failure=failure)

    assert not list(nodes.glob(".atlas-node-provisioning.tsv.*"))
    assert not (nodes / ".atlas-node-provisioning.tsv").exists()


def test_temporary_clone_symlink_is_rejected_before_destination_replacement(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-node"
    (outside / ".git").mkdir(parents=True)
    row = "node\thttps://github.com/o/node.git\t{ref}\tfalse\t\t\trequired"

    result, nodes, _mutations = _run_plan(
        tmp_path,
        row,
        swap_tmp_after_checkout=True,
    )

    assert "clone failed" in result.stdout
    assert _node_status_fields(nodes)[2:] == ["failed", "1", "0"]
    assert not (nodes / "node").exists()
