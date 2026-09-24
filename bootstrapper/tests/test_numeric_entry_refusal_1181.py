"""Invalid numeric input is refused, not reinterpreted (#1181).

``normalize_number_entry`` used to clamp an out-of-range number into
``[number_min, number_max]`` and swap an unparseable one for the step's
``default_value``; ``secondary_values`` did the same for the inline
per-row inputs. Confirmation consumed the coerced value, so a typo became
a different base port or worker count and the user was never told.

These pin the replacement contract: the entry is refused, the reason
names the accepted range, the user stays on the step, and nothing
downstream is written until an accepted value exists. Empty and ``auto``
keep their documented meanings.
"""

from __future__ import annotations

import pytest

from ui.textual.widgets.prompt_panel import (
    AUTO_PORT,
    InvalidNumberEntry,
    PromptOption,
    PromptStep,
    SecondaryNumberInput,
    normalize_number_entry,
    number_entry_error,
    secondary_entry_error,
)

BASE_PORT_RANGE = "choose 1024–65000, or enter auto"


def _port_step(**kw) -> PromptStep:
    """The base-port step's shape: wide range, a default, accepts auto."""
    defaults = dict(
        title="Base port", step_index=1, step_total=1, heading="Base port?",
        kind="number", number_min=1024, number_max=65000,
        default_value="63000", accepts_auto=True,
    )
    defaults.update(kw)
    return PromptStep(**defaults)


def _strict_step(**kw) -> PromptStep:
    """A number step that does not accept ``auto`` (e.g. worker counts)."""
    return _port_step(number_min=1, number_max=16, default_value="4",
                      accepts_auto=False, **kw)


# ─── AC1: out of range keeps the user on the step, showing the range ──


def test_seventy_thousand_is_refused_with_the_supported_range():
    """AC1, verbatim from the ticket's expected behaviour."""
    assert number_entry_error("70000", _port_step()) == f"70000 — {BASE_PORT_RANGE}"


def test_a_refused_entry_commits_nothing():
    with pytest.raises(InvalidNumberEntry) as excinfo:
        normalize_number_entry("70000", _port_step())
    assert str(excinfo.value) == f"70000 — {BASE_PORT_RANGE}"


@pytest.mark.parametrize("raw", ["70000", "65001", "1023", "10", "0", "-1"])
def test_nothing_outside_the_range_is_clamped_into_it(raw):
    step = _port_step()
    assert number_entry_error(raw, step) is not None
    with pytest.raises(InvalidNumberEntry):
        normalize_number_entry(raw, step)


@pytest.mark.parametrize("raw", ["1024", "63000", "63500", "65000"])
def test_every_value_inside_the_range_commits_unchanged(raw):
    step = _port_step()
    assert number_entry_error(raw, step) is None
    assert normalize_number_entry(raw, step) == raw


def test_the_message_names_the_step_s_own_bounds_not_a_fixed_pair():
    assert number_entry_error("99", _strict_step()) == "99 — choose 1–16"


# ─── AC2: a typo cannot become the old value ─────────────────────────


@pytest.mark.parametrize("raw", ["six", "6k", "63,000", "63 000", "1e4", "--5"])
def test_unparseable_input_cannot_silently_become_the_old_value(raw):
    """AC2. The pre-fix path returned default_value for every one of these."""
    step = _port_step()
    message = number_entry_error(raw, step)
    assert message is not None
    assert message.startswith(f"'{raw.strip()}' is not a number")
    assert BASE_PORT_RANGE in message
    with pytest.raises(InvalidNumberEntry):
        normalize_number_entry(raw, step)


def test_input_python_parses_but_a_reader_would_not_is_still_refused():
    """``int()`` accepts fullwidth digits and a leading ``+``. Those parse,
    so they are judged on range — and the refusal echoes what was typed,
    not what ``int()`` made of it."""
    step = _port_step()
    assert number_entry_error("\uff10", step) == f"\uff10 — {BASE_PORT_RANGE}"
    assert number_entry_error("+70000", step) == f"+70000 — {BASE_PORT_RANGE}"
    assert number_entry_error("+63500", step) is None
    assert normalize_number_entry("+63500", step) == "63500"


def test_a_refusal_never_names_a_value_the_user_did_not_type():
    """The old default must not appear in the refusal — that is the value
    the fix exists to stop substituting."""
    step = _port_step()
    for raw in ("six", "70000"):
        assert "63000" not in number_entry_error(raw, step)


# ─── AC3: empty and auto keep their documented meanings ──────────────


def test_empty_input_still_keeps_the_displayed_default():
    step = _port_step()
    assert number_entry_error("", step) is None
    assert number_entry_error("   ", step) is None
    assert normalize_number_entry("", step) == "63000"


def test_empty_input_returns_the_default_verbatim_without_clamping_it():
    """A default outside the step's own bounds is a bug elsewhere; Enter
    means "keep what is displayed", so it is not quietly rewritten."""
    step = _port_step(default_value="70000")
    assert normalize_number_entry("", step) == "70000"


@pytest.mark.parametrize("raw", ["auto", "AUTO", "Auto", "  auto  "])
def test_auto_still_passes_through_on_a_step_that_accepts_it(raw):
    step = _port_step()
    assert number_entry_error(raw, step) is None
    assert normalize_number_entry(raw, step) == AUTO_PORT


def test_auto_is_refused_rather_than_defaulted_where_it_is_not_accepted():
    step = _strict_step()
    assert number_entry_error("auto", step) == "'auto' is not accepted here — choose 1–16"
    with pytest.raises(InvalidNumberEntry):
        normalize_number_entry("auto", step)


def test_only_an_accepting_step_advertises_auto_in_its_refusal():
    assert "auto" in number_entry_error("70000", _port_step())
    assert "auto" not in number_entry_error("99", _strict_step())


def test_a_pathological_paste_cannot_fill_the_hint_line():
    """The hint is one line; ``int()`` parses a thousand-digit paste and
    raises past its own digit limit, so both branches cap the echo."""
    step = _port_step()
    long_digits = number_entry_error("9" * 400, step)
    long_text = number_entry_error("x" * 400, step)
    assert "\u2026" in long_digits and "\u2026" in long_text
    assert len(long_digits) < 90 and len(long_text) < 90
    assert BASE_PORT_RANGE in long_digits
    assert BASE_PORT_RANGE in long_text


# ─── Secondary inline inputs get the same rule ───────────────────────


def _cfg(**kw) -> SecondaryNumberInput:
    defaults = dict(env_var="PARAKEET_PORT", default_value=63022,
                    number_min=1024, number_max=65535)
    defaults.update(kw)
    return SecondaryNumberInput(**defaults)


def test_a_secondary_entry_out_of_range_is_refused():
    assert secondary_entry_error("99999", _cfg()) == "99999 — choose 1024–65535"


def test_a_secondary_entry_that_is_not_a_number_is_refused():
    assert secondary_entry_error("eight", _cfg()) == (
        "'eight' is not a number — choose 1024–65535"
    )


@pytest.mark.parametrize("raw", ["1024", "63022", "65535"])
def test_a_secondary_entry_inside_its_range_is_accepted(raw):
    assert secondary_entry_error(raw, _cfg()) is None


def test_an_empty_secondary_entry_still_means_keep_the_default():
    assert secondary_entry_error("", _cfg()) is None
    assert secondary_entry_error("  ", _cfg()) is None


def test_a_secondary_refusal_names_its_own_unit_when_it_has_one():
    cfg = _cfg(env_var="SPARK_WORKERS", default_value=2, number_min=1,
               number_max=8, unit_suffix="workers")
    assert secondary_entry_error("99", cfg) == "workers 99 — choose 1–8"


def test_no_secondary_refusal_names_the_default_it_used_to_substitute():
    cfg = _cfg(default_value=63022)
    for raw in ("eight", "99999"):
        assert "63022" not in secondary_entry_error(raw, cfg)


# ─── The panel refuses through its public surface ────────────────────


class _Input:
    def __init__(self, value: str):
        self.value = value


class _Panel:
    """The slice of PromptPanel the numeric guards read."""

    def __init__(self, step: PromptStep, raw: str = "", secondary=()):
        from ui.textual.widgets.prompt_panel import PromptPanel

        self._panel_cls = PromptPanel
        self._step = step
        self._number_input = _Input(raw)
        self._secondary_inputs = list(secondary)
        self._selected_index = 0

    def current_number_error(self):
        return self._panel_cls.current_number_error(self)

    def current_secondary_error(self):
        return self._panel_cls.current_secondary_error(self)

    def secondary_values(self):
        return self._panel_cls.secondary_values(self)

    @property
    def selected_option(self):
        return self._panel_cls.selected_option.fget(self)


def test_the_panel_reports_the_refusal_for_the_entry_on_screen():
    assert _Panel(_port_step(), "70000").current_number_error() == (
        f"70000 — {BASE_PORT_RANGE}"
    )
    assert _Panel(_port_step(), "63500").current_number_error() is None
    assert _Panel(_port_step(), "").current_number_error() is None


def test_the_panel_yields_no_option_for_a_refused_entry():
    """``action_confirm`` returns early on None, which is what keeps the
    user on the step."""
    assert _Panel(_port_step(), "70000").selected_option is None
    assert _Panel(_port_step(), "six").selected_option is None
    assert _Panel(_port_step(), "63500").selected_option.value == "63500"
    assert _Panel(_port_step(), "auto").selected_option.value == AUTO_PORT


def test_a_non_number_step_reports_no_numeric_refusal():
    text_step = _port_step(kind="text")
    assert _Panel(text_step, "70000").current_number_error() is None


def _options_step(cfg: SecondaryNumberInput) -> PromptStep:
    return PromptStep(
        title="STT provider", step_index=1, step_total=1, heading="Which?",
        kind="options",
        options=[PromptOption(value="parakeet", label="parakeet",
                             secondary_number=cfg)],
    )


def test_the_panel_reports_a_refused_secondary_entry_and_writes_nothing():
    cfg = _cfg()
    panel = _Panel(_options_step(cfg), secondary=[_Input("99999")])
    assert panel.current_secondary_error() == "99999 — choose 1024–65535"
    assert panel.secondary_values() == []


def test_an_accepted_secondary_entry_reports_no_error_and_is_written():
    cfg = _cfg()
    panel = _Panel(_options_step(cfg), secondary=[_Input("63030")])
    assert panel.current_secondary_error() is None
    assert panel.secondary_values() == [("PARAKEET_PORT", "63030")]


def test_an_empty_secondary_entry_writes_the_default():
    cfg = _cfg()
    panel = _Panel(_options_step(cfg), secondary=[_Input("")])
    assert panel.current_secondary_error() is None
    assert panel.secondary_values() == [("PARAKEET_PORT", "63022")]


def test_a_row_without_an_inline_input_reports_no_refusal():
    step = PromptStep(
        title="s", step_index=1, step_total=1, heading="h", kind="options",
        options=[PromptOption(value="a", label="a")],
    )
    panel = _Panel(step)
    assert panel.current_secondary_error() is None
    assert panel.secondary_values() == []
