"""Supply-chain contracts for ComfyUI custom-node dependency locks."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

import yaml

from scripts.compile_comfyui_custom_node_locks import (
    LOCKS,
    _compile,
    base_package_names,
    compile_command,
    validate_catalog,
    write_constraints,
)
from scripts import check_comfyui_custom_node_overlays as overlay_checker
from scripts.check_comfyui_custom_node_overlays import IMAGE, overlay_commands


ROOT = Path(__file__).resolve().parents[2]
BASE_CONSTRAINTS = (
    ROOT / "services/comfyui/custom-node-locks/ai-dock-v2-cpu-22.04-v0.2.7.txt"
)


def test_custom_node_dependency_installs_are_hash_locked() -> None:
    catalog = yaml.safe_load(
        (ROOT / "services/comfyui/custom-nodes.yaml").read_text(encoding="utf-8")
    )["custom_nodes"]
    for node in catalog:
        if node.get("install_requirements"):
            lock = node.get("requirements_lock")
            digest = node.get("requirements_lock_sha256")
            assert isinstance(lock, str) and lock
            assert re.fullmatch(r"[0-9a-f]{64}", str(digest))
            lock_path = ROOT / "services/comfyui" / lock
            assert lock_path.is_file()
            assert hashlib.sha256(lock_path.read_bytes()).hexdigest() == digest
            assert "--hash=sha256:" in lock_path.read_text(encoding="utf-8")

    assert validate_catalog() == []

    provisioner = (
        ROOT / "services/comfyui/provisioning/provision_custom_nodes.sh"
    ).read_text(encoding="utf-8")
    assert "--require-hashes" in provisioner
    assert "--no-deps" in provisioner
    assert '"$dest/requirements.txt"' not in provisioner
    assert 'COMFYUI_MANIFEST_ROOT="${COMFYUI_MANIFEST_ROOT:-/comfyui-manifest}"' in provisioner
    assert 'expected_lock="${COMFYUI_MANIFEST_ROOT}/custom-node-locks/${requirements_lock_sha256}.txt"' in provisioner
    assert '[ "$requirements_lock" != "$expected_lock" ]' in provisioner

    mps = (ROOT / "bootstrapper/services/comfyui_mps_manager.py").read_text(
        encoding="utf-8"
    )
    assert '"--require-hashes"' in mps
    assert '"--no-deps"' in mps


def test_custom_node_lock_inputs_cover_only_secure_runtime_closures() -> None:
    catalog = yaml.safe_load(
        (ROOT / "services/comfyui/custom-nodes.yaml").read_text(encoding="utf-8")
    )["custom_nodes"]
    by_name = {node["name"]: node for node in catalog}
    assert by_name["ComfyUI_IPAdapter_plus"]["install_requirements"] is False
    assert by_name["ComfyUI-AnimateDiff-Evolved"]["install_requirements"] is False
    # 3D-Pack is intentionally absent from the auto-install allowlist. Its
    # pinned tree imports rembg (no secure Python 3.10 release) and BasicSR
    # (no fixed release), so Atlas must fail closed rather than clone it with
    # an incomplete or known-vulnerable dependency set.
    assert "ComfyUI-3D-Pack" not in by_name

    workflow = (ROOT / ".github/workflows/services-lint.yml").read_text(encoding="utf-8")
    assert "compile_comfyui_custom_node_locks.py --check" in workflow


def test_custom_node_locks_overlay_exact_ai_dock_runtime(tmp_path: Path) -> None:
    constraints = BASE_CONSTRAINTS.read_text(encoding="utf-8")
    assert "numpy==2.0.2" in constraints
    assert "numba==0.60.0" in constraints
    assert "urllib3==1.26.20" in constraints
    assert "matrix-client==0.4.0" in constraints

    compiler = (ROOT / "scripts/compile_comfyui_custom_node_locks.py").read_text(
        encoding="utf-8"
    )
    assert '"--constraint"' in compiler
    assert "ai-dock-v2-cpu-22.04-v0.2.7.txt" in compiler

    # The base constraints participate in resolution but are not emitted into
    # the overlay.  The exact base image is scanned independently; auditing a
    # second textual copy here would misattribute inherited packages to the
    # custom-node delta.
    base_packages = base_package_names(BASE_CONSTRAINTS)
    assert {"numpy", "urllib3", "numba", "matrix-client"} <= set(base_packages)
    instantid_constraints = tmp_path / "instantid-constraints.txt"
    write_constraints(LOCKS[0], instantid_constraints)
    assert "typing_extensions==" not in instantid_constraints.read_text(
        encoding="utf-8"
    )
    command = compile_command(
        LOCKS[0], ROOT / "instantid.txt", constraints=instantid_constraints
    )
    assert "--universal" not in command
    platform_index = command.index("--python-platform")
    assert command[platform_index + 1] == "x86_64-manylinux_2_28"
    assert command.count("--no-emit-package") == len(base_packages) - 1
    assert "typing_extensions" not in command
    for package in ("numpy", "urllib3", "numba", "matrix-client"):
        index = command.index(package)
        assert command[index - 1] == "--no-emit-package"

    for lock_name in ("instantid.txt", "gguf.txt"):
        lock = (
            ROOT / "services/comfyui/custom-node-locks" / lock_name
        ).read_text(encoding="utf-8")
        assert "numpy==" not in lock
        assert "urllib3==" not in lock
        assert "numba==" not in lock
        assert "matrix-client==" not in lock

    instantid_input = (
        ROOT / "services/comfyui/custom-node-locks/instantid.in"
    ).read_text(encoding="utf-8")
    instantid_lock = (
        ROOT / "services/comfyui/custom-node-locks/instantid.txt"
    ).read_text(encoding="utf-8")
    assert "onnx>=1.22.0" in instantid_input
    assert "onnx==1.22.0" in instantid_lock

    catalog = yaml.safe_load(
        (ROOT / "services/comfyui/custom-nodes.yaml").read_text(encoding="utf-8")
    )["custom_nodes"]
    instantid_node = next(node for node in catalog if node["name"] == "ComfyUI_InstantID")
    assert instantid_node["mps_unsafe"] is True

    workflow = (ROOT / ".github/workflows/services-lint.yml").read_text(
        encoding="utf-8"
    )
    assert "check_comfyui_custom_node_overlays.py" in workflow

    assert IMAGE.endswith(
        "@sha256:b47cd16007c309ebbb78b85f87bbc69ac9f7f3fc7596607e81940eeb7dca2421"
    )
    commands = overlay_commands(ROOT)
    assert {command[-1] for command in commands} == {"instantid.txt", "gguf.txt"}
    for command in commands:
        rendered = " ".join(command)
        assert "--no-deps --require-hashes" in rendered
        assert "pip check" in rendered


def test_overlay_runtime_checks_are_time_bounded(monkeypatch) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))

    monkeypatch.setattr(overlay_checker.subprocess, "run", fake_run)

    assert overlay_checker.main() == 0
    assert len(calls) == 2
    assert all(kwargs["timeout"] == 600 for _command, kwargs in calls)


def test_overlay_lock_compilation_is_time_bounded(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))

    monkeypatch.setattr("scripts.compile_comfyui_custom_node_locks.subprocess.run", fake_run)

    _compile(LOCKS[0], tmp_path / "instantid.txt")
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 600
