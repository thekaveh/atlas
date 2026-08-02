"""`bootstrapper/_run.sh` dispatcher banner stream (#650).

The shared `uv` / system-Python dispatcher printed its `📦 Using …` banner to
**stdout** before exec'ing the Python entrypoint, so `<script> --format json`
(e.g. `doctor --format json`) emitted banner+JSON on one stream and was not
directly parseable — every consumer needed a JSON-extraction shim. The banner
must go to stderr instead, leaving stdout clean.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_RUN_SH = Path(__file__).resolve().parents[1] / "_run.sh"


def _bootstrapper_python_wrapper(tmp_path: Path) -> Path:
    venv = tmp_path / "bootstrapper-venv"
    python = venv / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    return venv


def _path_without_uv(tmp_path: Path, sh: str, dirname: str) -> str:
    tool_bin = tmp_path / "system-bin"
    tool_bin.mkdir()
    python3 = tool_bin / "python3"
    python3.write_text(
        "#!/bin/sh\necho 'clean system Python has no Atlas dependencies' >&2\nexit 42\n",
        encoding="utf-8",
    )
    python3.chmod(0o755)
    return os.pathsep.join(
        (str(tool_bin), os.path.dirname(sh), os.path.dirname(dirname))
    )


def test_dispatcher_banners_are_redirected_to_stderr() -> None:
    """AC#3: BOTH dispatcher branches (uv + system Python) redirect their
    banner to stderr. Static guard — deterministic, no subprocess needed."""
    lines = _RUN_SH.read_text(encoding="utf-8").splitlines()
    banner_lines = [ln for ln in lines if "📦" in ln]
    # AC#2: the human-facing banner is still emitted (just on stderr) — one per
    # dispatcher branch.
    assert len(banner_lines) == 2, f"expected both dispatcher banners; got {banner_lines!r}"
    for ln in banner_lines:
        assert ln.rstrip().endswith(">&2"), (
            f"dispatcher banner must be redirected to stderr (>&2), not stdout: {ln!r}"
        )


def test_stdout_is_clean_json_through_system_python(tmp_path: Path) -> None:
    """AC#1: `<script> --format json` stdout is pure JSON — the banner does not
    leak onto stdout. Exercises the real `_run.sh` via its system-Python branch.

    Skips (rather than flakes) in environments where the non-uv branch can't be
    forced deterministically."""
    sh = shutil.which("sh")
    dirname = shutil.which("dirname")
    if not (sh and dirname):
        pytest.skip("requires sh and dirname on PATH")

    # Copy the real dispatcher into the sandbox so SELF_DIR resolves here and
    # the probe script is found next to it.
    run_sh = tmp_path / "_run.sh"
    shutil.copy(_RUN_SH, run_sh)
    (tmp_path / "probe.py").write_text('print(\'{"ok": true}\')\n', encoding="utf-8")

    env = {
        **os.environ,
        "PATH": _path_without_uv(tmp_path, sh, dirname),
        "ATLAS_BOOTSTRAPPER_VENV": str(_bootstrapper_python_wrapper(tmp_path)),
    }
    result = subprocess.run(
        [sh, str(run_sh), "probe.py"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    # stdout is directly parseable — no banner, no shim (AC#1).
    assert json.loads(result.stdout) == {"ok": True}
    assert "📦" not in result.stdout
    # The banner still appears, on stderr (AC#2).
    assert "📦" in result.stderr


def test_system_python_fallback_runs_real_start_help(tmp_path: Path) -> None:
    sh = shutil.which("sh")
    dirname = shutil.which("dirname")
    if not (sh and dirname):
        pytest.skip("requires sh and dirname on PATH")

    env = {
        **os.environ,
        "PATH": _path_without_uv(tmp_path, sh, dirname),
        "ATLAS_BOOTSTRAPPER_VENV": str(_bootstrapper_python_wrapper(tmp_path)),
    }
    result = subprocess.run(
        [sh, str(_RUN_SH), "start.py", "--help"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr
