"""Managed-host ComfyUI model provisioning (#754: #718 warn → provision).

`provision_models` fetches the SAME resolved per-file set the container TSV is
built from into COMFYUI_MPS_MODELS_PATH — idempotent (sha-verified skip, state
sidecar fast path), self-healing (corrupt re-fetch, `.part` resume), atomic
(temp + rename), BF16-aware, disk-preflighted, license-announcing, and
non-fatal per file. The #718 doctor lint flips warn → pass once the host tree
satisfies the declared catalog.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace as NS

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

from services.comfyui_mps_manager import (  # noqa: E402
    ComfyUiMpsManager,
    ProvisionResult,
)

PAYLOAD = b"fake-model-bytes" * 1000
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _manager(tmp_path: Path) -> ComfyUiMpsManager:
    models = tmp_path / "models"
    models.mkdir(exist_ok=True)
    m = ComfyUiMpsManager(state_dir=tmp_path / "state", models_path=models)

    def fake_fetch(url, part, chunk_size=1 << 20):  # resume-aware local writer
        existing = part.stat().st_size if part.exists() else 0
        with open(part, "ab") as handle:
            handle.write(PAYLOAD[existing:])

    m._fetch_to_part = fake_fetch
    return m


def _row(**overrides) -> dict:
    row = {
        "name": "t", "type": "vae", "filename": "t.safetensors",
        "target_dir": "vae", "download_url": "https://example/t.safetensors",
        "sha256": SHA, "file_size_bytes": len(PAYLOAD), "precision": "bf16",
        "license_name": "Test License", "license_url": "https://example/LICENSE",
        "license_restrictions": ["restriction one"],
    }
    row.update(overrides)
    return row


def _dest(m: ComfyUiMpsManager) -> Path:
    return m.models_path / "vae" / "t.safetensors"


# ── AC matrix ────────────────────────────────────────────────────────


def test_provision_missing_then_idempotent_skip(tmp_path):
    m = _manager(tmp_path)
    r1 = m.provision_models([_row()])
    assert r1.ok and r1.provisioned == ["vae/t.safetensors"]
    assert _dest(m).read_bytes() == PAYLOAD
    r2 = m.provision_models([_row()])
    assert r2.ok and r2.skipped == ["vae/t.safetensors"] and not r2.provisioned


def test_refetch_corrupt_file(tmp_path):
    m = _manager(tmp_path)
    m.provision_models([_row()])
    _dest(m).write_bytes(b"corrupt")
    r = m.provision_models([_row()])
    assert r.ok and r.provisioned == ["vae/t.safetensors"]
    assert _dest(m).read_bytes() == PAYLOAD


def test_interrupted_part_resumes(tmp_path):
    m = _manager(tmp_path)
    part = m.models_path / "vae" / "t.safetensors.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(PAYLOAD[:128])  # interrupted prior pull
    r = m.provision_models([_row()])
    assert r.ok and r.provisioned == ["vae/t.safetensors"]
    assert _dest(m).read_bytes() == PAYLOAD
    assert not part.exists()  # atomically renamed


def test_no_sha_presence_is_a_hit(tmp_path):
    m = _manager(tmp_path)
    dest = _dest(m)
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"whatever the operator staged")
    r = m.provision_models([_row(sha256="")])
    assert r.ok and r.skipped == ["vae/t.safetensors"]
    assert dest.read_bytes() == b"whatever the operator staged"  # untouched


def test_state_sidecar_fast_path_and_verify_flag(tmp_path):
    m = _manager(tmp_path)
    m.provision_models([_row()])
    hashes = {"n": 0}
    original = ComfyUiMpsManager._sha256_file

    def counting(path, chunk_size=1 << 20):
        hashes["n"] += 1
        return original(path, chunk_size=chunk_size)

    m._sha256_file = counting
    r = m.provision_models([_row()])  # unchanged stat → no re-hash
    assert r.skipped and hashes["n"] == 0
    r = m.provision_models([_row()], verify=True)  # forced full re-hash
    assert r.skipped and hashes["n"] == 1


def test_mps_unsafe_precision_skipped_with_warning(tmp_path):
    m = _manager(tmp_path)
    r = m.provision_models([_row(precision="fp8-scaled")])
    assert r.ok and not r.provisioned
    assert any("fp8-scaled" in w and "BF16" in w for w in r.warnings)
    assert not _dest(m).exists()


def test_disk_preflight_fails_early(tmp_path, monkeypatch):
    import shutil as _shutil

    m = _manager(tmp_path)
    monkeypatch.setattr(
        _shutil, "disk_usage", lambda p: NS(free=10, total=100, used=90)
    )
    r = m.provision_models([_row()])
    assert not r.ok
    assert "insufficient disk space" in r.failed[0]
    assert not _dest(m).exists() and not (_dest(m).parent / "t.safetensors.part").exists()


def test_license_announced_before_download_only_for_missing(tmp_path):
    m = _manager(tmp_path)
    logs: list[str] = []
    m.provision_models([_row()], log=logs.append)
    assert any("Test License" in line for line in logs)
    assert any("restriction one" in line for line in logs)
    logs.clear()
    m.provision_models([_row()], log=logs.append)  # present → no re-announce
    assert not any("Test License" in line for line in logs)


def test_sha_mismatch_after_download_drops_partial(tmp_path):
    m = _manager(tmp_path)

    def bad_fetch(url, part, chunk_size=1 << 20):
        part.write_bytes(b"not the declared bytes")

    m._fetch_to_part = bad_fetch
    r = m.provision_models([_row()])
    assert not r.ok and "sha256 mismatch" in r.failed[0]
    assert not _dest(m).exists()
    assert not (m.models_path / "vae" / "t.safetensors.part").exists()


def test_per_file_failures_are_isolated(tmp_path):
    m = _manager(tmp_path)
    bad = _row(filename="bad.safetensors", download_url="", sha256="")
    r = m.provision_models([bad, _row()])
    assert not r.ok
    assert r.failed and "bad.safetensors" in r.failed[0]
    assert r.provisioned == ["vae/t.safetensors"]  # good row still lands


def test_bundle_dedupe_downloads_shared_file_once(tmp_path):
    m = _manager(tmp_path)
    calls = {"n": 0}
    real = m._fetch_to_part

    def counting_fetch(url, part, chunk_size=1 << 20):
        calls["n"] += 1
        real(url, part)

    m._fetch_to_part = counting_fetch
    r = m.provision_models([_row(name="bundle-a"), _row(name="bundle-b")])
    assert r.ok and calls["n"] == 1 and len(r.provisioned) == 1


def test_models_satisfied_reports_missing_and_ignores_unsafe(tmp_path):
    m = _manager(tmp_path)
    ok, missing = m.models_satisfied([_row(), _row(precision="fp8", filename="x.st")])
    assert not ok and missing == ["vae/t.safetensors"]  # unsafe row excluded
    m.provision_models([_row()])
    ok, missing = m.models_satisfied([_row()])
    assert ok and missing == []


# ── container parity ─────────────────────────────────────────────────


def test_provision_consumes_the_container_tsv_row_shape():
    """The provisioner reads exactly the fields the container TSV carries —
    resolved from the SAME `comfyui_resolver` source of truth (parity AC)."""
    from utils.comfyui_resolver import active_comfyui_models, manifest_dict

    rows = manifest_dict(
        active_comfyui_models({"COMFYUI_USER_MODELS": "krea2-turbo-bf16"})
    )["models"]
    assert rows, "krea2-turbo-bf16 must resolve to per-file rows"
    for row in rows:
        for key in ("filename", "target_dir", "download_url", "sha256",
                    "file_size_bytes", "precision"):
            assert key in row, f"row missing {key} — provisioner contract broken"
    # The catalog set the container would download == the provisioner's plan.
    assert {(r["target_dir"], r["filename"]) for r in rows} == {
        ("diffusion_models", "krea2_turbo_bf16.safetensors"),
        ("text_encoders", "qwen3vl_4b_bf16.safetensors"),
        ("vae", "qwen_image_vae.safetensors"),
    }


# ── doctor + CLI wiring ──────────────────────────────────────────────


def test_doctor_unpullable_flips_to_pass_when_tree_satisfied(tmp_path, monkeypatch):
    import start

    models = tmp_path / "models"
    (models / "diffusion_models").mkdir(parents=True)
    (models / "text_encoders").mkdir()
    (models / "vae").mkdir()
    env = {
        "COMFYUI_USER_MODELS": "krea2-turbo-bf16",
        "COMFYUI_SOURCE": "managed-localhost-mps",
        "COMFYUI_MPS_MODELS_PATH": str(models),
    }
    s = start.AtlasStarter.__new__(start.AtlasStarter)
    s.config_parser = NS(parse_env_file=lambda: dict(env))

    result = start._doctor_check_unpullable_models(s)
    assert result["status"] == "warn"
    assert "comfyui-mps provision" in result["message"]

    for sub, name in (
        ("diffusion_models", "krea2_turbo_bf16.safetensors"),
        ("text_encoders", "qwen3vl_4b_bf16.safetensors"),
        ("vae", "qwen_image_vae.safetensors"),
    ):
        (models / sub / name).write_bytes(b"x")
    result = start._doctor_check_unpullable_models(s)
    assert result["status"] == "pass"


def test_doctor_unmanaged_localhost_still_warns():
    import start

    env = {
        "COMFYUI_USER_MODELS": "krea2-turbo-bf16",
        "COMFYUI_SOURCE": "localhost",
    }
    s = start.AtlasStarter.__new__(start.AtlasStarter)
    s.config_parser = NS(parse_env_file=lambda: dict(env))
    result = start._doctor_check_unpullable_models(s)
    assert result["status"] == "warn"
    assert "not for an unmanaged localhost install" in result["message"]


def test_provision_cli_command_registered():
    import start

    assert "provision" in start.comfyui_mps_group.commands
    params = [p.name for p in start.comfyui_mps_group.commands["provision"].params]
    assert "verify" in params


def test_provision_result_ok_semantics():
    r = ProvisionResult()
    assert r.ok
    r.failed.append("x")
    assert not r.ok and r.to_dict()["ok"] is False
