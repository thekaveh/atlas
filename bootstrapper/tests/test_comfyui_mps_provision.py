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
    ComfyUiMpsError,
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


def test_provision_nodes_cli_command_registered():
    import start

    assert "provision-nodes" in start.comfyui_mps_group.commands


def test_provision_custom_nodes_accepts_dataclass(tmp_path):
    """The production MPS path delivers ComfyUICustomNode instances (not just
    dicts) — _node_fields must handle the dataclass branch (the krea2edit case)."""
    from utils.comfyui_custom_nodes import ComfyUICustomNode

    m = _node_manager(tmp_path)
    m._run = lambda cmd: _simulate_clone(cmd)  # type: ignore[assignment]
    node = ComfyUICustomNode(
        name="comfyui-krea2edit",
        repo="https://github.com/atlas/comfyui-krea2edit.git",
        ref="a" * 40,
        install_requirements=False,
        from_consumer=True,
    )

    r = m.provision_custom_nodes([node])

    assert r.ok
    assert "comfyui-krea2edit" in r.provisioned


def test_provision_result_ok_semantics():
    r = ProvisionResult()
    assert r.ok
    r.failed.append("x")
    assert not r.ok and r.to_dict()["ok"] is False


# ── custom-node provisioning (#905: parity with #754 for nodes) ──────
#
# ``provision_custom_nodes`` mirrors ``provision_models`` but is ref-based
# (no sha256 state sidecar): git clone/fetch/checkout and pip install go
# through ``self._run`` (returns None); git rev-parse HEAD and pip freeze go
# through ``self._run_capture`` (returns stdout str). Nodes land under
# ``repo_dir/custom_nodes/<name>``. ``_node_fields`` accepts ``ComfyUICustomNode``
# OR plain dicts — the tests below pass plain dicts.


def _node_manager(tmp_path: Path) -> ComfyUiMpsManager:
    """Manager with ``repo_dir`` + ``venv_python`` pre-created (nodes live under
    repo_dir/custom_nodes; the venv-guard in _pip_install_node_requirements
    checks venv_python.exists()).

    Node provisioning never touches ``models_path``; all host effects are mocked
    per-test on the instance (``_run`` / ``_run_capture`` / ``_pip_freeze``),
    matching the existing ``_manager`` + ``m._fetch_to_part = ...`` style."""
    m = ComfyUiMpsManager(state_dir=tmp_path / "state")
    m.repo_dir.mkdir(parents=True, exist_ok=True)
    venv_bin = m.venv_dir / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "python").touch()
    return m


def _node_dict(name="my-node", *, ref=None, **overrides) -> dict:
    """A valid node dict (name/repo/ref/install_requirements/mps_unsafe)."""
    node = {
        "name": name,
        "repo": f"https://github.com/atlas/{name}.git",
        "ref": ref or "a" * 40,
        "install_requirements": False,
        "mps_unsafe": False,
    }
    node.update(overrides)
    return node


def _simulate_clone(cmd: list[str]) -> None:
    """Fake ``git clone <repo> <tmp>``: materialize the tmp target + .git marker
    so the production ``tmp.rename(dest)`` succeeds without a real subprocess."""
    if len(cmd) >= 4 and cmd[:2] == ["git", "clone"]:
        target = Path(cmd[3])
        target.mkdir(parents=True, exist_ok=True)
        (target / ".git").mkdir(exist_ok=True)


def test_provision_custom_node_new_clone(tmp_path):
    """Absent dest -> clone into .tmp sibling, checkout --detach ref, atomic mv."""
    m = _node_manager(tmp_path)
    ref = "a" * 40
    runs: list[list[str]] = []

    def fake_run(cmd):
        runs.append(list(cmd))
        _simulate_clone(cmd)

    m._run = fake_run
    r = m.provision_custom_nodes([_node_dict("my-node", ref=ref)])

    assert r.ok
    assert r.provisioned == ["my-node"]
    # _run received: ['git','clone',repo,tmp] then ['git','-C',tmp,'checkout','--detach',ref]
    assert runs[0][:3] == ["git", "clone", "https://github.com/atlas/my-node.git"]
    clone_target = runs[0][3]
    assert runs[1] == ["git", "-C", clone_target, "checkout", "--detach", ref]
    assert clone_target.endswith("my-node.tmp")  # staged in the .tmp sibling
    dest = m.repo_dir / "custom_nodes" / "my-node"
    assert (dest / ".git").exists()  # tmp renamed atomically -> dest


def test_provision_custom_node_idempotent_skip(tmp_path):
    """dest/.git present + rev-parse HEAD == ref -> no clone, result.skipped."""
    m = _node_manager(tmp_path)
    ref = "b" * 40
    dest = m.repo_dir / "custom_nodes" / "my-node"
    dest.mkdir(parents=True)
    (dest / ".git").mkdir()

    runs: list[list[str]] = []
    captures: list[list[str]] = []
    m._run = lambda cmd: runs.append(list(cmd))
    m._run_capture = lambda cmd: (captures.append(list(cmd)) or ref)

    r = m.provision_custom_nodes([_node_dict("my-node", ref=ref)])

    assert r.ok
    assert r.skipped == ["my-node"]
    assert r.provisioned == []
    assert runs == []  # no clone/fetch/checkout — pure skip
    # rev-parse HEAD was the only captured command
    assert captures == [["git", "-C", str(dest), "rev-parse", "HEAD"]]


def test_provision_custom_node_head_drift_updates(tmp_path):
    """dest/.git present but rev-parse HEAD != ref -> fetch + checkout --detach."""
    m = _node_manager(tmp_path)
    ref = "c" * 40
    dest = m.repo_dir / "custom_nodes" / "my-node"
    dest.mkdir(parents=True)
    (dest / ".git").mkdir()

    runs: list[list[str]] = []
    m._run = lambda cmd: runs.append(list(cmd))
    m._run_capture = lambda cmd: "0" * 40  # different sha -> HEAD drift

    r = m.provision_custom_nodes([_node_dict("my-node", ref=ref)])

    assert r.ok
    assert r.provisioned == ["my-node"]  # "updated" outcome lands in provisioned
    assert runs[0] == ["git", "-C", str(dest), "fetch", "origin", ref]
    assert runs[1] == ["git", "-C", str(dest), "checkout", "--detach", ref]


def test_provision_custom_node_pip_install_no_torch_drift(tmp_path):
    """install_requirements=True + requirements.txt present -> pip install -r issued;
    equal torch triple before/after -> NO drift warning."""
    m = _node_manager(tmp_path)
    ref = "d" * 40
    dest = m.repo_dir / "custom_nodes" / "my-node"
    dest.mkdir(parents=True)
    (dest / ".git").mkdir()
    (dest / "requirements.txt").write_text("numpy\n")

    runs: list[list[str]] = []
    torch_triple = {"torch": "2.11.0", "torchvision": "0.26.0", "torchaudio": "2.11.0"}
    freeze_calls: list[int] = []

    def fake_freeze():
        freeze_calls.append(1)
        return torch_triple  # equal before + after

    m._run = lambda cmd: runs.append(list(cmd))
    m._run_capture = lambda cmd: "0" * 40  # drift -> update path reaches req install
    m._pip_freeze = fake_freeze

    logs: list[str] = []
    r = m.provision_custom_nodes(
        [_node_dict("my-node", ref=ref, install_requirements=True)], log=logs.append,
    )

    assert r.ok
    # a 'pip install -r <requirements>' command was issued through _run
    assert any("pip" in c and "install" in c and "-r" in c for c in runs)
    assert len(freeze_calls) == 2  # before + after
    assert not any("install --update" in line for line in logs)  # no torch drift
    assert r.warnings == []


def test_provision_custom_node_pip_install_torch_drift_warns(tmp_path):
    """install_requirements=True + torch versions DIFFER between the two pip-freeze
    calls -> warning names the node + 'install --update' repair pointer.

    NOTE: per the real ``_pip_install_node_requirements``, the drift surface is the
    ``log`` (emit) callback — NOT ``result.warnings`` (only mps_unsafe appends to
    result.warnings). This test asserts the implemented behavior."""
    m = _node_manager(tmp_path)
    ref = "e" * 40
    dest = m.repo_dir / "custom_nodes" / "my-node"
    dest.mkdir(parents=True)
    (dest / ".git").mkdir()
    (dest / "requirements.txt").write_text("torch==2.9.0\n")

    runs: list[list[str]] = []
    freeze_state = {"n": 0}

    def fake_freeze():
        freeze_state["n"] += 1
        if freeze_state["n"] == 1:  # before install: pinned torch stack
            return {"torch": "2.11.0", "torchvision": "0.26.0", "torchaudio": "2.11.0"}
        return {"torch": "2.9.0", "torchvision": "0.26.0", "torchaudio": "2.11.0"}  # after: downgraded

    m._run = lambda cmd: runs.append(list(cmd))
    m._run_capture = lambda cmd: "0" * 40  # drift -> update path
    m._pip_freeze = fake_freeze

    logs: list[str] = []
    r = m.provision_custom_nodes(
        [_node_dict("my-node", ref=ref, install_requirements=True)], log=logs.append,
    )

    assert r.ok  # torch drift is a warning, never a failure
    assert any("my-node" in line and "install --update" in line for line in logs)
    assert any("pip" in c and "install" in c and "-r" in c for c in runs)


def test_provision_custom_node_deps_free_issues_no_pip(tmp_path):
    """A node with no requirements.txt issues NO pip install command
    (the comfyui-krea2edit case — deps-free node)."""
    m = _node_manager(tmp_path)
    ref = "1" * 40
    dest = m.repo_dir / "custom_nodes" / "comfyui-krea2edit"
    dest.mkdir(parents=True)
    (dest / ".git").mkdir()
    # deliberately NO requirements.txt

    runs: list[list[str]] = []
    m._run = lambda cmd: runs.append(list(cmd))
    m._run_capture = lambda cmd: "0" * 40  # drift -> update path, exercises req check

    r = m.provision_custom_nodes(
        [_node_dict("comfyui-krea2edit", ref=ref, install_requirements=True)],
    )

    assert r.ok
    # no pip command of any kind was issued (only git fetch/checkout ran)
    assert not any("pip" in c for c in runs)


def test_provision_custom_node_mps_unsafe_skipped_no_git(tmp_path):
    """mps_unsafe=True -> result.warnings names the node and git is never invoked."""
    m = _node_manager(tmp_path)
    runs: list[list[str]] = []
    captures: list[list[str]] = []
    m._run = lambda cmd: runs.append(list(cmd))
    m._run_capture = lambda cmd: (captures.append(list(cmd)) or "")

    r = m.provision_custom_nodes(
        [_node_dict("cuda-only-node", ref="2" * 40, mps_unsafe=True)],
    )

    assert r.ok
    assert any("cuda-only-node" in w and "mps_unsafe" in w for w in r.warnings)
    assert "cuda-only-node" not in r.provisioned
    assert "cuda-only-node" not in r.skipped  # mps_unsafe warns + continues
    assert runs == [] and captures == []  # never touched git


def test_provision_custom_node_per_node_failure_isolated(tmp_path):
    """A node whose _run raises ComfyUiMpsError -> result.failed has the name,
    and the run continues to the next node (per-node non-fatal)."""
    m = _node_manager(tmp_path)

    def fake_run(cmd):
        _simulate_clone(cmd)  # good node's clone materializes its tmp
        if len(cmd) >= 4 and cmd[:2] == ["git", "clone"]:
            if "bad-node" in Path(cmd[3]).name:
                raise ComfyUiMpsError("clone failed: network down")

    m._run = fake_run
    nodes = [
        _node_dict("bad-node", ref="3" * 40),
        _node_dict("good-node", ref="4" * 40),
    ]
    r = m.provision_custom_nodes(nodes)

    assert not r.ok  # bad-node failed
    assert any("bad-node" in f and "network down" in f for f in r.failed)
    assert "good-node" in r.provisioned  # good node still landed (isolation)


def test_nodes_satisfied_present_missing_unsafe(tmp_path):
    """nodes_satisfied: present at ref -> (True, []); missing -> (False, [name]);
    mps_unsafe excluded (must not keep the lint red)."""
    m = _node_manager(tmp_path)
    ref = "5" * 40
    present = m.repo_dir / "custom_nodes" / "present-node"
    present.mkdir(parents=True)
    (present / ".git").mkdir()
    m._run_capture = lambda cmd: ref  # rev-parse HEAD == declared ref

    nodes = [
        _node_dict("present-node", ref=ref),
        _node_dict("missing-node", ref=ref),
        _node_dict("unsafe-node", ref=ref, mps_unsafe=True),
    ]
    ok, missing = m.nodes_satisfied(nodes)
    assert not ok
    assert missing == ["missing-node"]  # present-at-ref + unsafe excluded
    assert not any("unsafe" in n for n in missing)

    # materialize the missing node at ref -> satisfied
    miss = m.repo_dir / "custom_nodes" / "missing-node"
    miss.mkdir(parents=True)
    (miss / ".git").mkdir()
    ok, missing = m.nodes_satisfied(nodes)
    assert ok and missing == []


def test_nodes_satisfied_wrong_ref_counts_as_missing(tmp_path):
    """A node present but checked out at the wrong ref is reported missing
    (with the 'wrong ref' suffix)."""
    m = _node_manager(tmp_path)
    declared = "6" * 40
    dest = m.repo_dir / "custom_nodes" / "drifted-node"
    dest.mkdir(parents=True)
    (dest / ".git").mkdir()
    m._run_capture = lambda cmd: "7" * 40  # HEAD != declared ref

    ok, missing = m.nodes_satisfied([_node_dict("drifted-node", ref=declared)])
    assert not ok
    assert missing and "drifted-node" in missing[0]
    assert "wrong ref" in missing[0]
