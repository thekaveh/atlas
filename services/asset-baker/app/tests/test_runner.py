from __future__ import annotations

import json
import subprocess
import types
from pathlib import Path

import pytest


def _params(**overrides):
    from asset_baker.models import ResolvedParams

    base = dict(target_tris=15000, tex_size=2048, canonical_size=4.0, brightness_min=0.05, mode="bake")
    base.update(overrides)
    return ResolvedParams(**base)


def test_build_command_includes_all_params(tmp_path):
    from asset_baker.runner import build_command

    cmd = build_command(Path("in.glb"), tmp_path, tmp_path / "s.json", _params())
    assert "-b" in cmd and "-P" in cmd
    assert cmd[cmd.index("--target") + 1] == "15000"
    assert cmd[cmd.index("--tex") + 1] == "2048"
    assert cmd[cmd.index("--canonical") + 1] == "4.0"
    assert cmd[cmd.index("--brightness-min") + 1] == "0.05"
    assert cmd[cmd.index("--outdir") + 1] == str(tmp_path)
    assert "--mode" not in cmd  # bake mode omits the flag
    assert cmd[-1].endswith("s.json")  # summary path present


def test_build_command_skip_mode_adds_flag(tmp_path):
    from asset_baker.runner import build_command

    cmd = build_command(Path("in.glb"), tmp_path, tmp_path / "s.json", _params(mode="skip"))
    assert cmd[cmd.index("--mode") + 1] == "skip"


def _fake_run_writing(record, *, returncode=0):
    def fake_run(cmd, *a, **k):
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        summary = Path(cmd[cmd.index("--summary") + 1])
        outdir.mkdir(parents=True, exist_ok=True)
        for key in ("glb", "basecolor", "normal"):
            if record.get(key):
                Path(record[key]).write_bytes(b"x")
        summary.write_text(json.dumps([record]), encoding="utf-8")
        return types.SimpleNamespace(returncode=returncode, stdout="", stderr="")

    return fake_run


def test_run_bake_reads_summary_and_returns_artifacts(tmp_path, monkeypatch):
    from asset_baker import runner

    out = tmp_path / "out"
    record = {
        "ok": True, "mode": "bake",
        "glb": str(out / "input_LP.glb"),
        "basecolor": str(out / "input_BaseColor.png"),
        "normal": str(out / "input_Normal.png"),
        "color_mean": 0.42, "faces_in": 1000, "tris_out": 500, "shells_kept": 1,
    }
    monkeypatch.setattr(runner.subprocess, "run", _fake_run_writing(record))
    art = runner.run_bake(tmp_path / "in.glb", out, _params())
    assert art.glb_path.exists()
    assert art.basecolor_path is not None and art.normal_path is not None
    assert art.summary["color_mean"] == 0.42


def test_run_bake_black_bake_gate_trips_even_on_zero_exit(tmp_path, monkeypatch):
    from asset_baker import runner

    out = tmp_path / "out"
    record = {"ok": True, "mode": "bake", "glb": str(out / "input_LP.glb"), "color_mean": 0.02}
    monkeypatch.setattr(runner.subprocess, "run", _fake_run_writing(record, returncode=0))
    with pytest.raises(runner.BakeError) as excinfo:
        runner.run_bake(tmp_path / "in.glb", out, _params())
    assert excinfo.value.kind == "black_bake"


def test_run_bake_nonzero_exit_with_black_flag(tmp_path, monkeypatch):
    from asset_baker import runner

    out = tmp_path / "out"
    record = {"ok": False, "mode": "bake", "black_bake": True, "error": "color bake is black"}
    monkeypatch.setattr(runner.subprocess, "run", _fake_run_writing(record, returncode=1))
    with pytest.raises(runner.BakeError) as excinfo:
        runner.run_bake(tmp_path / "in.glb", out, _params())
    assert excinfo.value.kind == "black_bake"


def test_run_bake_skip_mode_allows_no_textures(tmp_path, monkeypatch):
    from asset_baker import runner

    out = tmp_path / "out"
    record = {"ok": True, "mode": "skip", "glb": str(out / "leaf_LP.glb"), "color_mean": None}
    monkeypatch.setattr(runner.subprocess, "run", _fake_run_writing(record))
    art = runner.run_bake(tmp_path / "in.glb", out, _params(mode="skip"))
    assert art.glb_path.exists()
    assert art.basecolor_path is None and art.normal_path is None


def test_run_bake_timeout_raises_timeout_kind(tmp_path, monkeypatch):
    from asset_baker import runner

    def fake_run(cmd, *a, **k):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    with pytest.raises(runner.BakeError) as excinfo:
        runner.run_bake(tmp_path / "in.glb", tmp_path / "out", _params())
    assert excinfo.value.kind == "timeout"
