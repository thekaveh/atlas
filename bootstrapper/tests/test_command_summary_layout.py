"""The wizard command summary must not grow one line per selected source.

Atlas ships enough source-configurable services that the previous
line-per-flag summary outgrew its slot in ``#lower-pane`` (shared with the
prompt) by the end of the wizard and overflowed. The panel now renders the
command as one flowing, soft-wrapping line and caps its height with
``max-height`` + ``overflow-y: auto``.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

from ui.textual.widgets.command_summary import (  # noqa: E402
    CommandSummary,
    _build_text,
)


def _flags(n: int) -> list[tuple[str, str]]:
    return [(f"--service-{i}-source", "container") for i in range(n)]


def test_no_newline_per_flag_even_for_the_full_service_set():
    """The regression: N selections must NOT produce N+1 hard lines."""
    text = _build_text("./start.sh", _flags(40))

    assert "\n" not in text.plain, (
        "command summary re-introduced hard line breaks per flag — this is what "
        "overflowed the pane once enough services were selected"
    )


def test_every_flag_and_value_is_still_present():
    """Flat wrapping must not drop content — the command stays copy-pasteable."""
    flags = [
        ("--llm-provider-source", "ollama-localhost"),
        ("--comfyui-source", "container-gpu"),
        ("--base-port", "63000"),
    ]

    plain = _build_text("./start.sh", flags).plain

    assert plain.startswith("./start.sh")
    for flag, value in flags:
        assert flag in plain
        assert value in plain
    # Flags are space-separated on one line, not backslash-continued.
    assert "\\" not in plain


def test_no_flags_renders_the_defaults_hint():
    plain = _build_text("./start.sh", []).plain

    assert plain.startswith("./start.sh")
    assert "(using .env defaults)" in plain


def test_panel_height_is_bounded_and_scrollable():
    """max-height + overflow-y are what stop a long command from starving the
    prompt/service pane; auto-height alone let it grow without bound."""
    css = CommandSummary.DEFAULT_CSS

    assert "max-height:" in css, "summary must cap its height"
    assert "overflow-y: auto" in css, "capped panel must scroll, not clip"
    assert CommandSummary.MAX_BODY_ROWS > 0


def test_model_flag_values_are_pasteable_literals():
    """``--X-models`` takes a CSV, so the summary must emit the CSV itself.

    It previously rendered ``--ollama-models 3 selected (a,b,c)``, which Click
    parses as a flag value plus stray positional args — the panel advertises a
    copy-pasteable command, so a human-readable description is a defect.
    """
    from ui.textual.screens.wizard_screen import _quote_csv

    csv = "nomic-embed-text,qwen3-embedding:0.6b,qwen3.8:latest"
    quoted = _quote_csv(csv)

    assert "selected" not in quoted
    # A single bare token needs no quoting.
    assert _quote_csv("llama3.2") == "llama3.2"


def test_quote_csv_round_trips_through_a_real_shell():
    """Regression: a prior hand-rolled ``_quote_csv`` only escaped ``\\``
    and ``"`` inside its own double quotes, leaving other shell-significant
    characters untouched. Reproduced live in bash:

        _quote_csv('my$model')      -> "my$model"      -> bash yields  my
        _quote_csv('a`echo PWNED`') -> "a`echo PWNED`"  -> bash yields  aPWNED
                                                            (substitution RAN)

    ``--ollama-custom-models`` is wizard free-text, and the
    ``--openai/anthropic/openrouter-models`` values come from a live remote
    ``/v1/models`` response — both reachable by an untrusted/unexpected
    value. The fix delegates to the stdlib ``shlex.quote``, so the contract
    to assert is round-tripping through a real shell tokenizer
    (``shlex.split``), not pinning any particular implementation's quoting
    style.
    """
    import shlex

    from ui.textual.screens.wizard_screen import _quote_csv

    values = [
        "llama3.2",
        "nomic-embed-text,qwen3-embedding:0.6b,qwen3.8:latest",
        'a"b',
        "my$model",
        "a`echo PWNED`",
        "it's a test",
        "has space",
        "has,comma",
        "",
    ]
    for value in values:
        quoted = _quote_csv(value)
        assert shlex.split(quoted) == [value], (
            f"_quote_csv({value!r}) -> {quoted!r} did not round-trip through "
            "shlex.split back to the original value"
        )


def test_set_flags_updates_state_and_rebuilds_flat_text(monkeypatch):
    """``set_flags`` stores the new flags and re-renders them flat.

    ``Static.update()`` needs a running Textual app, so the body update is
    stubbed — the contract under test is the state + the rebuilt text.
    """
    panel = CommandSummary()
    rendered: list[str] = []
    monkeypatch.setattr(
        panel._body, "update", lambda text: rendered.append(text.plain)
    )

    panel.set_flags([("--comfyui-source", "container-gpu"), ("--n8n-source", "container")])

    assert panel.flags == [
        ("--comfyui-source", "container-gpu"),
        ("--n8n-source", "container"),
    ]
    assert rendered, "set_flags must push the new text into the body"
    assert "--comfyui-source container-gpu" in rendered[0]
    assert "\n" not in rendered[0]
