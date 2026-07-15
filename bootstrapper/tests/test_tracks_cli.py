"""CLI tests for --track and --list-tracks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
START_PY = REPO_ROOT / "bootstrapper" / "start.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    """Invoke start.py with isolated env so we don't accidentally touch
    docker. We rely on --list-tracks / --track foo exiting BEFORE any
    side effect."""
    return subprocess.run(
        [sys.executable, str(START_PY), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT / "bootstrapper")},
        timeout=30,
    )


def test_list_tracks_exits_zero():
    r = _run("--list-tracks")
    assert r.returncode == 0, f"--list-tracks should exit 0; stderr={r.stderr!r}"


def test_list_tracks_lists_every_track():
    r = _run("--list-tracks")
    for key in ("gen-ai-rag", "gen-ai-eng", "gen-ai-creative",
                "ml-eng", "data-eng", "trading", "all"):
        assert key in r.stdout, f"--list-tracks must mention {key}; stdout={r.stdout!r}"


def test_list_tracks_distinguishes_prompted_from_always_running_services():
    r = _run("--list-tracks")

    assert "prompted in every track" in r.stdout.lower()
    assert "always-on tier (asked" not in r.stdout.lower()


def test_track_unknown_exits_two():
    r = _run("--track", "nonexistent-track")
    assert r.returncode == 2
    assert "unknown track" in r.stderr.lower()
    # Lists available tracks in the error message so the user can self-correct.
    assert "gen-ai-rag" in r.stderr


def test_track_help_lists_trading_profile():
    r = _run("--help")
    assert r.returncode == 0
    assert "trading" in r.stdout


def test_off_track_flag_emits_warning():
    """--track gen-ai-rag --comfyui-source container-gpu must emit
    a stderr warning since comfyui is excluded from gen-ai-rag.
    Combined with --list-tracks so the wizard never launches."""
    r = _run(
        "--track", "gen-ai-rag",
        "--comfyui-source", "container-gpu",
        "--list-tracks",
    )
    # The warning fires when --track is set AND any off-track --*-source
    # flag is passed. The warning check runs BEFORE --list-tracks exits.
    # The warning must use the display name ("ComfyUI") not the folder key.
    assert "ComfyUI" in r.stderr, (
        f"warning text must use display name 'ComfyUI'; stderr={r.stderr!r}"
    )
    assert "gen-ai-rag" in r.stderr


def test_all_track_suppresses_warning():
    """--track all + any --*-source flag → no warning (all includes
    everything)."""
    r = _run(
        "--track", "all",
        "--comfyui-source", "container-gpu",
        "--list-tracks",
    )
    assert "overrides the all track" not in r.stderr.lower()


def test_no_track_suppresses_warning():
    """Bare --comfyui-source with no --track → no warning."""
    r = _run(
        "--comfyui-source", "container-gpu",
        "--list-tracks",
    )
    assert "overrides the" not in r.stderr.lower()
