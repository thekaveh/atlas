"""OPTIONAL Blender integration test — proves the AC bake contract on a real
headless Blender. Skipped on generic CI (no Blender); the deterministic worker
behaviour is covered by the fully-mocked tests in test_api.py / test_runner.py.

Run with Blender on PATH (or ASSET_BAKER_BLENDER_BIN set):

    pytest services/asset-baker/app/tests/test_bake_integration.py -q
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE_BUILDER = Path(__file__).resolve().parent / "fixtures" / "build_metallic_cube.py"


def _blender_bin() -> str | None:
    candidate = os.getenv("ASSET_BAKER_BLENDER_BIN") or shutil.which("blender")
    if candidate and Path(candidate).exists():
        return candidate
    return shutil.which(candidate) if candidate else None


pytestmark = pytest.mark.skipif(_blender_bin() is None, reason="Blender not available")


def _build_metallic_fixture(tmp_path: Path) -> Path:
    out = tmp_path / "metallic_cube.glb"
    result = subprocess.run(
        [_blender_bin(), "-b", "-noaudio", "-P", str(FIXTURE_BUILDER), "--", str(out)],
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert out.exists() and out.stat().st_size > 0, result.stderr or result.stdout
    return out


def test_metallic_source_bakes_non_black(tmp_path, monkeypatch):
    """AC: a metallic=1 source bakes non-black (metallic neutralization works)."""
    from asset_baker.models import ResolvedParams
    from asset_baker.runner import run_bake

    monkeypatch.setenv("ASSET_BAKER_TIMEOUT_SECONDS", "1800")
    fixture = _build_metallic_fixture(tmp_path)
    params = ResolvedParams(target_tris=5000, tex_size=512, canonical_size=4.0,
                            brightness_min=0.05, mode="bake")
    artifacts = run_bake(fixture, tmp_path / "out", params)
    assert artifacts.glb_path.exists()
    assert artifacts.basecolor_path is not None and artifacts.normal_path is not None
    # non-black: the gate would have raised BakeError otherwise, but assert explicitly
    assert artifacts.summary["color_mean"] >= 0.05


def test_foliage_flag_bypasses_remesh(tmp_path, monkeypatch):
    """AC: a foliage-flagged input bypasses (skip mode emits no textures)."""
    from asset_baker.models import ResolvedParams
    from asset_baker.runner import run_bake

    monkeypatch.setenv("ASSET_BAKER_TIMEOUT_SECONDS", "1800")
    fixture = _build_metallic_fixture(tmp_path)
    params = ResolvedParams(target_tris=5000, tex_size=512, canonical_size=4.0,
                            brightness_min=0.05, mode="skip")
    artifacts = run_bake(fixture, tmp_path / "out", params)
    assert artifacts.glb_path.exists()
    assert artifacts.basecolor_path is None and artifacts.normal_path is None
    assert artifacts.summary["mode"] == "skip"
