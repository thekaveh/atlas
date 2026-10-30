"""`bootstrapper/_run.sh` dispatcher banner stream (#650).

The shared `uv` / system-Python dispatcher printed its `📦 Using …` banner to
**stdout** before exec'ing the Python entrypoint, so `<script> --format json`
(e.g. `doctor --format json`) emitted banner+JSON on one stream and was not
directly parseable — every consumer needed a JSON-extraction shim. The banner
must go to stderr instead, leaving stdout clean.
"""
from __future__ import annotations

import json
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_RUN_SH = Path(__file__).resolve().parents[1] / "_run.sh"


def _dependency_stamp(self_dir: Path) -> str:
    inputs = (self_dir / "pyproject.toml", self_dir / "requirements-locked.txt")
    rows = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path}\n" for path in inputs
    )
    return hashlib.sha256(rows.encode("utf-8")).hexdigest()


def _bootstrapper_python_wrapper(tmp_path: Path, *, self_dir: Path) -> Path:
    venv = tmp_path / "bootstrapper-venv"
    python = venv / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    (venv / ".atlas-deps-stamp").write_text(
        _dependency_stamp(self_dir) + "\n", encoding="utf-8"
    )
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
    shutil.copy(_RUN_SH.parent / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copy(
        _RUN_SH.parent / "requirements-locked.txt",
        tmp_path / "requirements-locked.txt",
    )
    (tmp_path / "probe.py").write_text('print(\'{"ok": true}\')\n', encoding="utf-8")

    env = {
        **os.environ,
        "PATH": _path_without_uv(tmp_path, sh, dirname),
        "ATLAS_BOOTSTRAPPER_VENV": str(
            _bootstrapper_python_wrapper(tmp_path, self_dir=tmp_path)
        ),
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
        "ATLAS_BOOTSTRAPPER_VENV": str(
            _bootstrapper_python_wrapper(tmp_path, self_dir=_RUN_SH.parent)
        ),
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


@pytest.mark.parametrize(
    "scenario",
    [(10, 10, False, False, False), (11, 10, True, False, False),
     (10, 11, True, True, False), (10, 11, False, True, True)],
)
def test_fallback_checks_existing_interpreter_before_reuse(
    tmp_path: Path, scenario: tuple[int, int, bool, bool, bool],
) -> None:
    """A cached dependency stamp must not keep an unsupported interpreter alive."""
    venv_minor, system_minor, accepted, repaired, kept_old = scenario
    run_sh = tmp_path / "_run.sh"
    shutil.copy(_RUN_SH, run_sh)
    for name in ("pyproject.toml", "requirements-locked.txt"):
        shutil.copy(_RUN_SH.parent / name, tmp_path / name)
    venv = _bootstrapper_python_wrapper(tmp_path, self_dir=tmp_path)
    fake_python = '''#!{interpreter}
import os, pathlib, sys
minor = int(os.environ["TEST_SYSTEM_MINOR"] if pathlib.Path(sys.argv[0]).name == "python3"
            else pathlib.Path(os.environ["TEST_MINOR_FILE"]).read_text())
args = sys.argv[1:]
if args[0] == "-c":
    if "sys.version_info" in args[1]:
        exec(args[1].replace("sys.version_info", repr((3, minor))))
    raise SystemExit(0)
if args[:2] == ["-m", "venv"]:
    if os.environ["TEST_KEEP_OLD"] != "1":
        pathlib.Path(os.environ["TEST_MINOR_FILE"]).write_text(str(minor))
    pathlib.Path(os.environ["TEST_REPAIR_FILE"]).touch()
elif args[:2] == ["-m", "pip"]:
    pathlib.Path(os.environ["TEST_INSTALL_FILE"]).touch()
else:
    print("entrypoint-ran")
'''.format(interpreter=sys.executable)
    (venv / "bin" / "python").write_text(fake_python, encoding="utf-8")
    sh, dirname = shutil.which("sh"), shutil.which("dirname")
    assert sh and dirname
    path = _path_without_uv(tmp_path, sh, dirname)
    (tmp_path / "system-bin" / "python3").write_text(fake_python, encoding="utf-8")
    minor_file, repair_file, install_file = (
        tmp_path / name for name in ("minor", "repair", "install")
    )
    minor_file.write_text(str(venv_minor), encoding="utf-8")
    result = subprocess.run(
        [sh, str(run_sh), "probe.py"], capture_output=True, text=True, timeout=20,
        env={**os.environ, "PATH": path, "ATLAS_BOOTSTRAPPER_VENV": str(venv),
             "TEST_SYSTEM_MINOR": str(system_minor), "TEST_MINOR_FILE": str(minor_file),
             "TEST_REPAIR_FILE": str(repair_file), "TEST_INSTALL_FILE": str(install_file),
             "TEST_KEEP_OLD": str(int(kept_old))},
    )
    assert result.returncode == (0 if accepted else 1), result.stderr
    assert ("entrypoint-ran" in result.stdout) is accepted
    assert repair_file.exists() is repaired
    assert install_file.exists() is (repaired and accepted)
    # A refusal names the floor. `python3 -m venv` keeps an old interpreter
    # link, so that refusal also names the one-step fix: delete it and rerun.
    assert (
        "Atlas requires Python 3.11 or newer" in result.stderr,
        f"Delete {venv} and rerun" in result.stderr,
    ) == (not accepted, kept_old), result.stderr
