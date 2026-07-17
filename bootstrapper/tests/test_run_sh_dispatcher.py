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
import shutil
import subprocess
from pathlib import Path

import pytest

_RUN_SH = Path(__file__).resolve().parents[1] / "_run.sh"


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
    python3 = shutil.which("python3")
    if not (sh and dirname and python3):
        pytest.skip("requires sh, dirname, and python3 on PATH")

    # A PATH holding only the dirs for the tools the dispatcher needs, so
    # `command -v uv` fails and the system-Python branch runs.
    needed_dirs = {
        os.path.dirname(sh),
        os.path.dirname(dirname),
        os.path.dirname(python3),
    }
    uv = shutil.which("uv")
    if uv and os.path.dirname(uv) in needed_dirs:
        pytest.skip("uv shares a directory with a required tool; cannot force system-Python branch")

    # Copy the real dispatcher into the sandbox so SELF_DIR resolves here and
    # the probe script is found next to it.
    run_sh = tmp_path / "_run.sh"
    shutil.copy(_RUN_SH, run_sh)
    (tmp_path / "probe.py").write_text('print(\'{"ok": true}\')\n', encoding="utf-8")

    env = {**os.environ, "PATH": os.pathsep.join(sorted(needed_dirs))}
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
