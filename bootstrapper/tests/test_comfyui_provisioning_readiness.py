"""ComfyUI readiness is bound to the exact selected provisioning plan."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]
HEALTH_SCRIPT = ROOT / "services/comfyui/provisioning/healthcheck.sh"
COMPOSE = ROOT / "services/comfyui/compose.yml"


def _write_status(root: Path, name: str, plan: Path, state: str, required: int, optional: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(plan.read_bytes()).hexdigest()
    (root / name).write_text(
        f"v1\t{digest}\t{state}\t{required}\t{optional}\n",
        encoding="utf-8",
    )


def _run_health(tmp_path: Path, *, model_state: str = "ready", node_state: str = "ready"):
    manifest = tmp_path / "manifest"
    manifest.mkdir(parents=True)
    models_plan = manifest / "active-models.tsv"
    nodes_plan = manifest / "active-custom-nodes.tsv"
    models_plan.write_text("", encoding="utf-8")
    nodes_plan.write_text("", encoding="utf-8")
    models = tmp_path / "models"
    nodes = tmp_path / "nodes"
    _write_status(models, ".atlas-model-provisioning.tsv", models_plan, model_state, 1 if model_state != "ready" else 0, 0)
    _write_status(nodes, ".atlas-node-provisioning.tsv", nodes_plan, node_state, 1 if node_state != "ready" else 0, 0)
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    curl = fakebin / "curl"
    curl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    curl.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fakebin}:/opt/homebrew/bin:/usr/bin:/bin",
        "COMFYUI_MANIFEST_ROOT": str(manifest),
        "COMFYUI_MODELS_PATH": str(models),
        "COMFYUI_CUSTOM_NODES_PATH": str(nodes),
        "COMFYUI_HEALTH_URL": "http://localhost:18188/system_stats",
    }
    return subprocess.run(
        ["sh", str(HEALTH_SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    ), manifest, models, nodes


def test_exact_ready_manifests_and_reachable_api_are_healthy(tmp_path: Path) -> None:
    result, _manifest, _models, _nodes = _run_health(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_required_model_or_node_failure_is_never_healthy(tmp_path: Path) -> None:
    model_failed, *_ = _run_health(tmp_path / "model", model_state="failed")
    node_failed, *_ = _run_health(tmp_path / "node", node_state="failed")
    assert model_failed.returncode != 0
    assert node_failed.returncode != 0


def test_ready_status_with_required_failures_is_rejected_as_corrupt(tmp_path: Path) -> None:
    result, manifest, models, nodes = _run_health(tmp_path)
    assert result.returncode == 0
    _write_status(models, ".atlas-model-provisioning.tsv", manifest / "active-models.tsv", "ready", 1, 0)

    corrupt = subprocess.run(
        result.args,
        env={
            **os.environ,
            "PATH": f"{tmp_path / 'bin'}:/opt/homebrew/bin:/usr/bin:/bin",
            "COMFYUI_MANIFEST_ROOT": str(manifest),
            "COMFYUI_MODELS_PATH": str(models),
            "COMFYUI_CUSTOM_NODES_PATH": str(nodes),
            "COMFYUI_HEALTH_URL": "http://localhost:18188/system_stats",
        },
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert corrupt.returncode != 0


def test_stale_success_for_a_different_plan_is_never_healthy(tmp_path: Path) -> None:
    result, manifest, _models, _nodes = _run_health(tmp_path)
    assert result.returncode == 0
    (manifest / "active-models.tsv").write_text("new selected plan\n", encoding="utf-8")

    stale = subprocess.run(
        result.args,
        env={
            **os.environ,
            "PATH": f"{tmp_path / 'bin'}:/opt/homebrew/bin:/usr/bin:/bin",
            "COMFYUI_MANIFEST_ROOT": str(manifest),
            "COMFYUI_MODELS_PATH": str(tmp_path / "models"),
            "COMFYUI_CUSTOM_NODES_PATH": str(tmp_path / "nodes"),
            "COMFYUI_HEALTH_URL": "http://localhost:18188/system_stats",
        },
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert stale.returncode != 0


def _health_env(tmp_path: Path, manifest: Path, models: Path, nodes: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}:/opt/homebrew/bin:/usr/bin:/bin",
        "COMFYUI_MANIFEST_ROOT": str(manifest),
        "COMFYUI_MODELS_PATH": str(models),
        "COMFYUI_CUSTOM_NODES_PATH": str(nodes),
        "COMFYUI_HEALTH_URL": "http://localhost:18188/system_stats",
    }


def test_health_rejects_symlinked_roots_plans_and_statuses(tmp_path: Path) -> None:
    for target in ("manifest-root", "model-root", "node-root", "model-plan", "model-status"):
        case = tmp_path / target
        result, manifest, models, nodes = _run_health(case)
        assert result.returncode == 0
        if target == "manifest-root":
            real = case / "manifest-real"
            manifest.rename(real)
            manifest.symlink_to(real, target_is_directory=True)
        elif target == "model-root":
            real = case / "models-real"
            models.rename(real)
            models.symlink_to(real, target_is_directory=True)
        elif target == "node-root":
            real = case / "nodes-real"
            nodes.rename(real)
            nodes.symlink_to(real, target_is_directory=True)
        elif target == "model-plan":
            plan = manifest / "active-models.tsv"
            real = manifest / "active-models-real.tsv"
            plan.rename(real)
            plan.symlink_to(real)
        else:
            status = models / ".atlas-model-provisioning.tsv"
            real = models / ".atlas-model-provisioning-real.tsv"
            status.rename(real)
            status.symlink_to(real)

        unsafe = subprocess.run(
            ["sh", str(HEALTH_SCRIPT)],
            env=_health_env(case, manifest, models, nodes),
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        assert unsafe.returncode != 0, target


def test_compose_waits_for_model_init_and_runs_provisioning_healthcheck() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    comfyui = compose["services"]["comfyui"]

    assert comfyui["depends_on"]["comfyui-init"]["condition"] == "service_completed_successfully"
    assert comfyui["healthcheck"]["test"] == [
        "CMD",
        "/opt/ai-dock/bin/atlas-comfyui-healthcheck.sh",
    ]
    mounts = comfyui["volumes"]
    assert any("healthcheck.sh:/opt/ai-dock/bin/atlas-comfyui-healthcheck.sh:ro" in mount for mount in mounts)
