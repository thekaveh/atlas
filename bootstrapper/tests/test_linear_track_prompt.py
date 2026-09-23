"""Linear-fallback track selection rejects typos instead of guessing (#1184).

The resolved track drives force-disable synthesis, so a typo that silently
resolved to the first track disabled every service of the workload the user
actually wanted. These pin the re-prompt contract, and that the explicit
``--track`` CLI rejection is unchanged.
"""

from __future__ import annotations

import io
import sys

import pytest

import start
from tracks import load_tracks


class _FakeStdin(io.StringIO):
    """stdin whose tty-ness is controllable."""

    def __init__(self, text: str = "", *, tty: bool = True):
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def registry():
    return load_tracks()


def _run_prompt(monkeypatch, typed: str, *, tty: bool = True, **kwargs):
    lines = iter(typed.split("\n"))
    monkeypatch.setattr(sys, "stdin", _FakeStdin(typed, tty=tty))
    if tty:
        monkeypatch.setattr("builtins.input", lambda: next(lines))
    return start._prompt_for_track(load_tracks(), **kwargs)


def test_typo_never_resolves_to_another_workload(monkeypatch, capsys):
    """AC1: typing 'data-en' must not silently become the default track."""
    resolved = _run_prompt(monkeypatch, "data-en\ndata-eng")
    assert resolved == "data-eng"
    err = capsys.readouterr().err
    assert "Unknown track 'data-en'" in err
    assert "data-eng" in err  # the suggestion


def test_empty_input_still_takes_the_displayed_default(monkeypatch, registry):
    """AC3: pressing Enter keeps the documented default behaviour."""
    assert _run_prompt(monkeypatch, "") == registry.tracks[0].key


def test_non_interactive_stdin_takes_the_default_without_blocking(monkeypatch, registry):
    resolved = _run_prompt(monkeypatch, "", tty=False)
    assert resolved == registry.tracks[0].key


def test_repeated_invalid_input_exits_nonzero_rather_than_guessing(monkeypatch, capsys):
    with pytest.raises(SystemExit) as excinfo:
        _run_prompt(monkeypatch, "\n".join(["nope"] * 4), max_attempts=3)
    assert excinfo.value.code == 2
    assert "no valid track selected" in capsys.readouterr().err


def test_eof_falls_back_to_the_default(monkeypatch, registry):
    def _raise_eof():
        raise EOFError

    monkeypatch.setattr(sys, "stdin", _FakeStdin("", tty=True))
    monkeypatch.setattr("builtins.input", _raise_eof)
    assert start._prompt_for_track(load_tracks()) == registry.tracks[0].key


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("data-en", "data-eng"),
        ("gen-ai", "gen-ai-rag"),
        ("ml", "ml-eng"),
    ],
)
def test_suggestions_prefer_prefix_matches(registry, typed, expected):
    assert start._track_suggestions(typed, registry)[0] == expected


def test_suggestions_are_empty_for_empty_input(registry):
    assert start._track_suggestions("   ", registry) == []


def test_every_suggestion_is_a_real_track(registry):
    keys = {track.key for track in registry.tracks}
    for typed in ("data", "gen", "xyz", "eng"):
        assert set(start._track_suggestions(typed, registry)) <= keys


def test_explicit_cli_track_rejection_is_unchanged():
    """AC2: an invalid explicit --track still exits non-zero before any
    configuration mutation. Pinned structurally so the re-prompt work cannot
    weaken it."""
    from pathlib import Path

    source = Path(start.__file__).read_text(encoding="utf-8")
    rejection = source.index("Error: unknown track")
    assert "sys.exit(2)" in source[rejection:rejection + 400]
    # The guard must run before AtlasStarter() — i.e. before anything reads
    # or writes .env.
    assert rejection < source.index("starter = AtlasStarter()")
