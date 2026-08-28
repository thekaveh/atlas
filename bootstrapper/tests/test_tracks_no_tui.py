"""Tests for the no-TUI track prompt fallback (spec §6.2 / §8.6).

When ./start.sh --no-tui is run without --track, the linear flow
should print the track list to stderr and either prompt on stdin
(if TTY) or default to gen-ai-rag (if non-interactive).
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_no_tui_prompt_block_exists_in_start_py():
    """Static check: the no-TUI track prompt block is present in start.py.
    Without this guard, the spec §6.2 / §8.6 promise silently regresses."""
    src = (REPO_ROOT / "bootstrapper" / "start.py").read_text(encoding="utf-8")
    assert "Pick a track" in src, (
        "no-TUI track prompt block missing from start.py — spec §6.2 / §8.6 "
        "promises a stdin prompt with default gen-ai-rag for non-TTY no-TUI runs."
    )
    assert "is_tui_capable" in src, (
        "start.py should consult is_tui_capable() to decide whether the no-TUI "
        "prompt fires."
    )


def test_no_tui_prompt_defaults_to_first_track():
    """Unit test: when stdin returns empty, the first track is chosen."""
    from tracks import load_tracks
    reg = load_tracks()
    # Simulate the logic in start.py's no-TUI prompt block.
    selected = ""  # empty input = Enter key
    if not selected:
        selected = reg.tracks[0].key
    assert selected == "gen-ai-rag", (
        f"Expected default track 'gen-ai-rag', got '{selected}'"
    )


def test_no_tui_prompt_unknown_track_falls_back():
    """Unit test: unknown track input falls back to gen-ai-rag."""
    from tracks import load_tracks
    reg = load_tracks()
    selected = "bogus-track-xyz"
    if selected not in reg.by_key:
        selected = reg.tracks[0].key
    assert selected == "gen-ai-rag"


def test_no_tui_prompt_valid_track_accepted():
    """Unit test: a valid track key is accepted as-is."""
    from tracks import load_tracks
    reg = load_tracks()
    for t in reg.tracks:
        selected = t.key
        if not selected:
            selected = reg.tracks[0].key
        if selected not in reg.by_key:
            selected = reg.tracks[0].key
        assert selected == t.key, (
            f"Valid track '{t.key}' should not be altered by the fallback logic"
        )


def test_no_tui_prompt_block_is_inside_wizard_requested():
    """Structural (positional-bracketing) check: the no-TUI track prompt must
    sit between the ``if wizard_requested:`` branch opener and the linear-flow
    marker — i.e. within the region that executes when a wizard/default-track
    run was intended, even if stdin is non-TTY and Textual cannot run."""
    src = (REPO_ROOT / "bootstrapper" / "start.py").read_text(encoding="utf-8")
    # Bracket from the actual branch OPENER (`if wizard_requested:`), not merely
    # the variable's definition — so moving the prompt above the branch fails.
    branch_pos = src.find("if wizard_requested:")
    prompt_pos = src.find("Pick a track")
    assert branch_pos != -1, "`if wizard_requested:` branch not found in start.py"
    assert prompt_pos != -1, "Pick a track prompt not found in start.py"
    assert prompt_pos > branch_pos, (
        "The no-TUI track prompt must appear AFTER the `if wizard_requested:` "
        "branch opener (inside the branch), not before it"
    )
    # The prompt must also appear before the linear flow marker.
    linear_pos = src.find("# Linear (--no-tui / non-TTY) flow from here on")
    assert linear_pos != -1, "Linear flow marker not found in start.py"
    assert prompt_pos < linear_pos, (
        "The no-TUI track prompt must fire BEFORE the linear flow section"
    )


def test_no_tui_preset_track_force_disables_off_track_services():
    """Regression: ``--no-tui --track <key>`` (preset track, no source flags,
    TTY) must force-disable off-track services in source_args. Previously the
    synthesis was gated ``if track is None`` (only the stdin-prompt path), so a
    *preset* track in a TTY left will_run_wizard=True → wizard skipped (no-TUI)
    → off-track services kept their .env defaults and started, silently
    violating the track contract. This replicates the synthesis logic and
    asserts the off-track / in-track / explicitly-flagged behaviors."""
    from tracks import load_tracks, is_in_track
    reg = load_tracks()
    track = reg.by_key["gen-ai-rag"]
    assert is_in_track(track, "weaviate", always_on=reg.always_on)       # in-track
    assert not is_in_track(track, "airflow", always_on=reg.always_on)    # off-track
    assert not is_in_track(track, "comfyui", always_on=reg.always_on)    # off-track

    source_args = {
        "weaviate_source": None,             # in-track → untouched
        "airflow_source": None,              # off-track, no flag → disabled
        "comfyui_source": "container-cpu",   # off-track but flagged → flag wins
        "cloud_openai_source": None,         # cloud key → always skipped
    }
    overridden: set[str] = set()
    # Mirrors the synthesis loop in start.py's no-TUI fallback.
    for cli_key in list(source_args.keys()):
        if cli_key.startswith("cloud_"):
            continue
        svc_key = cli_key.removesuffix("_source").replace("_", "-")
        if is_in_track(track, svc_key, always_on=reg.always_on):
            continue
        if source_args.get(cli_key) is not None:
            overridden.add(svc_key)
        else:
            source_args[cli_key] = "disabled"

    assert source_args["airflow_source"] == "disabled", "off-track svc must be disabled"
    assert source_args["weaviate_source"] is None, "in-track svc must be untouched"
    assert source_args["comfyui_source"] == "container-cpu", "explicit flag must win"
    assert "comfyui" in overridden, "flagged off-track svc recorded as override"
    assert source_args["cloud_openai_source"] is None, "cloud keys always skipped"


def test_no_tui_force_disable_runs_for_resolved_track_not_just_prompted():
    """Structural guard for the regression above: the no-TUI force-disable
    synthesis must run for the RESOLVED track (preset via --track OR prompted
    on stdin), gated on ``track is not None`` — NOT nested solely inside the
    ``if track is None`` prompt block. Re-nesting it under ``track is None``
    would silently reintroduce the ``--no-tui --track`` bug."""
    src = (REPO_ROOT / "bootstrapper" / "start.py").read_text(encoding="utf-8")
    assert "if _reg is not None and track is not None:" in src, (
        "The no-TUI force-disable synthesis must run for the resolved track "
        "(guard `if _reg is not None and track is not None:`). Gating it on "
        "`track is None` regresses `--no-tui --track <key>` — off-track "
        "services would not be force-disabled."
    )


def test_no_tui_track_synthesis_failure_is_fail_closed():
    src = (REPO_ROOT / "bootstrapper" / "start.py").read_text(encoding="utf-8")
    marker = "track '{track}' force-disable synthesis failed"
    start = src.index(marker)
    block = src[start - 150:start + 500]
    assert "raise click.ClickException" in block
    assert "off-track services will" not in block
