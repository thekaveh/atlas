from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import ResolvedParams

BAKE_SCRIPT = Path(__file__).resolve().parent / "bake.py"


class BakeError(RuntimeError):
    """A bake failed (import error, black-bake gate, timeout, or crash)."""

    def __init__(self, message: str, *, kind: str = "failed") -> None:
        super().__init__(message)
        self.kind = kind  # failed | timeout | black_bake


@dataclass
class BakeArtifacts:
    glb_path: Path
    basecolor_path: Path | None
    normal_path: Path | None
    summary: dict


def _blender_bin() -> str:
    return os.getenv("ASSET_BAKER_BLENDER_BIN", "blender")


def build_command(
    input_path: Path,
    output_dir: Path,
    summary_path: Path,
    params: ResolvedParams,
) -> list[str]:
    """Assemble the headless Blender invocation. Kept pure + importable so the
    command contract is unit-tested without Blender present."""
    cmd = [
        _blender_bin(), "-b", "-noaudio",
        "-P", str(BAKE_SCRIPT),
        "--",
        str(input_path),
        "--target", str(params.target_tris),
        "--tex", str(params.tex_size),
        "--canonical", str(params.canonical_size),
        "--brightness-min", str(params.brightness_min),
        "--outdir", str(output_dir),
        "--summary", str(summary_path),
    ]
    if params.mode == "skip":
        cmd += ["--mode", "skip"]
    return cmd


def run_bake(input_path: Path, output_dir: Path, params: ResolvedParams) -> BakeArtifacts:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    cmd = build_command(input_path, output_dir, summary_path, params)
    timeout = float(os.getenv("ASSET_BAKER_TIMEOUT_SECONDS", "600"))

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise BakeError(f"bake timed out after {timeout:.0f}s", kind="timeout") from exc

    record = _load_record(summary_path)
    if proc.returncode != 0 or record is None or not record.get("ok"):
        detail = (record or {}).get("error") if record else None
        detail = detail or (proc.stderr or proc.stdout or "").strip()[-500:]
        # A black bake is the specific gate failure the AC calls out.
        kind = "black_bake" if record and record.get("black_bake") else "failed"
        raise BakeError(f"bake failed: {detail}", kind=kind)

    # Defense-in-depth: re-enforce the brightness gate at the worker boundary so
    # the QA invariant holds even if a future script regression forgets it.
    if params.mode == "bake":
        mean = record.get("color_mean")
        if mean is None or mean < params.brightness_min:
            raise BakeError(
                f"baked color is black (mean {mean}) < {params.brightness_min} — refusing to emit",
                kind="black_bake",
            )

    glb = record.get("glb")
    if not glb or not Path(glb).exists():
        raise BakeError("bake reported success but produced no GLB")

    basecolor = record.get("basecolor")
    normal = record.get("normal")
    return BakeArtifacts(
        glb_path=Path(glb),
        basecolor_path=Path(basecolor) if basecolor and Path(basecolor).exists() else None,
        normal_path=Path(normal) if normal and Path(normal).exists() else None,
        summary=record,
    )


def _load_record(summary_path: Path) -> dict | None:
    """The bake script writes a JSON list of per-source records; the worker bakes
    one input per request, so return the first record (or None if unwritten)."""
    if not summary_path.exists():
        return None
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data
    return None
