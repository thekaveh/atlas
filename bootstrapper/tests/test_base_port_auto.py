"""The wizard's base-port step accepts the literal ``auto``.

``auto`` is already a first-class base-port value everywhere else:
``./start.sh --base-port auto`` resolves a fresh free block, and a
consumer manifest may commit ``BASE_PORT: auto`` for a durable one
(``start.py::_resolve_auto_base_port_override``). The interactive wizard
was the only surface that could not express it, because its step is
``kind="number"`` with ``number_min=1024`` — the literal was unenterable,
and ``PromptPanel.selected_option`` coerced anything non-numeric back to
the default, so even a hand-typed ``auto`` vanished silently.

Resolution happens at selection time rather than being deferred: the
concrete port is what every downstream consumer needs (three separate
``int(...)`` call sites), and resolving up front means the stack overview
previews the ports the run will really use. The user's *intent* is kept
separately so the command summary still advertises ``--base-port auto``,
which is what reproduces the behaviour on a later run.
"""
from __future__ import annotations

from ui.textual.widgets.prompt_panel import PromptOption, PromptStep


def _number_step(**kw) -> PromptStep:
    base = dict(
        title="Base port  ·  range",
        step_index=1,
        step_total=1,
        heading="Which base port range do you want?",
        subtitle="",
        options=[],
        default_value="63000",
        service_name="",
        kind="number",
        number_min=1024,
        number_max=65000,
    )
    base.update(kw)
    return PromptStep(**base)


def test_the_step_advertises_auto_when_it_accepts_it() -> None:
    step = _number_step(accepts_auto=True)
    assert step.accepts_auto is True


def test_number_steps_reject_auto_by_default() -> None:
    """Only the base-port step opts in; other number steps stay strict."""
    step = _number_step()
    assert step.accepts_auto is False


def test_auto_is_preserved_not_clamped_to_the_default() -> None:
    """The bug: a non-numeric entry fell back to default_value."""
    from ui.textual.widgets.prompt_panel import normalize_number_entry

    assert normalize_number_entry("auto", _number_step(accepts_auto=True)) == "auto"
    assert normalize_number_entry("AUTO", _number_step(accepts_auto=True)) == "auto"
    assert normalize_number_entry("  auto  ", _number_step(accepts_auto=True)) == "auto"


def test_auto_is_still_rejected_when_the_step_does_not_opt_in() -> None:
    from ui.textual.widgets.prompt_panel import normalize_number_entry

    assert normalize_number_entry("auto", _number_step()) == "63000"


def test_numbers_still_clamp_into_range() -> None:
    from ui.textual.widgets.prompt_panel import normalize_number_entry

    step = _number_step(accepts_auto=True)
    assert normalize_number_entry("70000", step) == "65000"
    assert normalize_number_entry("10", step) == "1024"
    assert normalize_number_entry("63500", step) == "63500"


def test_option_value_round_trips_through_prompt_option() -> None:
    """selected_option builds a synthetic PromptOption; auto must survive."""
    opt = PromptOption(value="auto", label="auto")
    assert opt.value == "auto"


def test_the_real_base_port_step_opts_in() -> None:
    """Guard the wiring, not just the flag: the shipped step must set it."""
    from core.config_parser import ConfigParser
    from ui.textual import integration as I

    class _HM:
        def __getattr__(self, _n):
            return lambda *a, **k: False

    steps, *_ = I._build_steps_and_rows(ConfigParser(), _HM())
    step = next(s for s in steps if s.title.startswith("Base port"))
    assert step.accepts_auto is True
    assert "auto" in (step.subtitle or "").lower()


def test_auto_resolves_to_a_concrete_int_for_downstream_consumers() -> None:
    """Three separate call sites int() this value; none may see 'auto'."""
    from ui.textual.integration import _resolve_auto_base_port

    resolved = _resolve_auto_base_port(63000)
    assert isinstance(resolved, int)
    assert 1024 <= resolved <= 65535


def test_auto_resolution_falls_back_rather_than_raising() -> None:
    """A failed probe must not abort a launch over a cosmetic value."""
    import ui.textual.integration as I

    class _Boom:
        def __init__(self, *a, **k):
            raise OSError("no sockets today")

    import core.port_manager as pm
    original = pm.PortManager
    pm.PortManager = _Boom
    try:
        assert I._resolve_auto_base_port(63000) == 63000
    finally:
        pm.PortManager = original


# ────────────────────────────────────────────────────────────────────────────
# The step's DEFAULT should be "auto", not a number (bug: the step let
# the user TYPE "auto" via accepts_auto, but always pre-filled a
# concrete port — the maintainer expects a bare Enter to resolve a
# free block unless they deliberately pinned one).
# ────────────────────────────────────────────────────────────────────────────


def _step_from_fake_root(tmp_path, env_body: str | None):
    """Build the real base-port step against a throwaway root_dir so
    the test doesn't depend on (or mutate) the repo's own .env. Only
    ``services/`` and ``.env.example`` are needed for manifest
    discovery; both are symlinked in from the real tree."""
    import os
    from pathlib import Path
    from core.config_parser import ConfigParser
    from ui.textual import integration as I

    repo_root = Path(__file__).resolve().parents[2]
    for name in ("services", ".env.example"):
        os.symlink(repo_root / name, tmp_path / name)
    if env_body is not None:
        (tmp_path / ".env").write_text(env_body)

    class _HM:
        def __getattr__(self, _n):
            return lambda *a, **k: False

    steps, *_ = I._build_steps_and_rows(ConfigParser(root_dir=str(tmp_path)), _HM())
    return next(s for s in steps if s.title.startswith("Base port"))


def test_default_is_auto_when_base_port_is_unset(tmp_path) -> None:
    step = _step_from_fake_root(tmp_path, env_body=None)
    assert step.default_value == "auto"


def test_default_is_auto_when_base_port_is_already_auto(tmp_path) -> None:
    step = _step_from_fake_root(tmp_path, env_body="BASE_PORT=auto\n")
    assert step.default_value == "auto"


def test_default_stays_pinned_number_when_explicitly_set(tmp_path) -> None:
    """A deliberately-pinned BASE_PORT must NOT be silently swapped for
    "auto" — that would move an existing, working stack to a different
    port block on a bare Enter."""
    step = _step_from_fake_root(tmp_path, env_body="BASE_PORT=64000\n")
    assert step.default_value == "64000"
