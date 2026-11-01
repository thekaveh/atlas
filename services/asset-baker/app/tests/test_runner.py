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


def _compiled_post_bake_script():
    import ast
    from asset_baker import runner

    tree = ast.parse(runner.BAKE_SCRIPT.read_text(encoding="utf-8"))
    definitions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    process = definitions["process"]
    start = next(index for index, node in enumerate(process.body) if isinstance(node, ast.Assign)
                 and getattr(node.targets[0], "id", None) == "mb")
    process.body = process.body[start:]
    names = ("log", "err", "BlackBake", "parse_args", "main", "_write_summary", "process")
    nodes = [definitions[name] for name in names]
    return compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(runner.BAKE_SCRIPT), "exec")


def _synthetic_post_bake_namespace(measured):
    import glob
    import os
    import sys
    import time
    import traceback

    mesh = types.SimpleNamespace(hide_set=lambda value: None, hide_render=False)
    image = types.SimpleNamespace(pack=lambda: None, save_render=lambda path: Path(path).write_bytes(b"synthetic-png"))
    bpy = types.SimpleNamespace(
        context=types.SimpleNamespace(scene=object()),
        ops=types.SimpleNamespace(wm=types.SimpleNamespace(read_factory_settings=lambda **kwargs: None)),
        data=types.SimpleNamespace(objects=types.SimpleNamespace(remove=lambda *args, **kwargs: None)),
    )
    return dict(
        os=os, sys=sys, time=time, traceback=traceback, json=json, glob=glob, bpy=bpy,
        TARGET_TRIS=39000, TEX_COLOR=2048, TEX_NORMAL=2048, CANONICAL=4.0,
        BRIGHTNESS_MIN=0.05, MODE="bake", TRY_GPU=False, OUTDIR=None, SUMMARY=None,
        import_glb=lambda path: mesh, enable_cycles=lambda scene: None,
        mean_brightness=lambda baked_image: measured, col=image, nrm=image,
        _export_glb=lambda obj, path: Path(path).write_bytes(b"synthetic-glb"),
        lp=mesh, base="synthetic", shr=False, sh=False, t0=time.time(),
        faces_in=12, tris_out=6, shells_kept=1,
    )


def _install_synthetic_post_bake_command(monkeypatch, measured):
    import contextlib
    import io
    import sys
    from asset_baker import runner

    code = _compiled_post_bake_script()
    observed = {}

    def fake_run(command, **kwargs):
        stdout, stderr = io.StringIO(), io.StringIO()
        namespace = _synthetic_post_bake_namespace(measured)
        exec(code, namespace)
        with monkeypatch.context() as context, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            context.setattr(sys, "argv", command)
            with pytest.raises(SystemExit) as exitinfo:
                namespace["main"]()
        observed.update(stdout=stdout.getvalue(), stderr=stderr.getvalue(), returncode=exitinfo.value.code)
        return types.SimpleNamespace(**observed)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    return observed


def _assert_precise_post_bake_artifacts(artifacts, measured):
    assert artifacts.summary["color_mean"] == measured
    assert artifacts.glb_path.exists()
    assert artifacts.basecolor_path.exists() and artifacts.normal_path.exists()


@pytest.mark.parametrize("case", [
    pytest.param((0.05004, 0.050045, True), id="custom-threshold-above"),
    pytest.param((0.05, 0.05001, True), id="ordinary-threshold-above"),
    pytest.param((0.05, 0.05, True), id="ordinary-threshold-equal"),
    pytest.param((0.05004, 0.05004, True), id="custom-threshold-equal"),
    pytest.param((0.05, 0.04999, False), id="ordinary-threshold-below"),
    pytest.param((0.05004, 0.05003, False), id="custom-threshold-below"),
])
def test_post_bake_machine_summary_preserves_brightness_precision(tmp_path, monkeypatch, case):
    from asset_baker import runner

    threshold, measured, accepted = case

    input_path = tmp_path / "input.glb"
    input_path.write_bytes(b"synthetic-input")
    output = tmp_path / "out"
    observed = _install_synthetic_post_bake_command(monkeypatch, measured)
    params = _params(brightness_min=threshold)
    if accepted:
        artifacts = runner.run_bake(input_path, output, params)
        _assert_precise_post_bake_artifacts(artifacts, measured)
    else:
        with pytest.raises(runner.BakeError) as error:
            runner.run_bake(input_path, output, params)
        assert error.value.kind == "black_bake"
        assert not list(output.glob("*.glb")) and not list(output.glob("*.png"))
    record = json.loads((output / "summary.json").read_text(encoding="utf-8"))[0]
    assert record["ok"] is accepted
    assert record.get("black_bake", False) is (not accepted)
    assert observed["returncode"] == (0 if accepted else 1)
    assert f"color-bake mean brightness {measured:.3f}" in observed["stdout"]
