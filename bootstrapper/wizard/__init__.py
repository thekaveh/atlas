"""Interactive Setup Wizard for Atlas."""

# Sink for wizard-time diagnostic warnings (cloud /v1/models fetch failures,
# a tracks.yml that fails to load, ...). The WizardScreen registers a thin
# adapter around its ``_safe_log`` once it exists; the step builders, which
# run BEFORE the screen, warn through ``wizard_warn``. What they raise before
# a sink exists is held (bounded) and flushed once the screen mounts its log
# pane (#1390).
_WIZARD_WARN_SINK = None
_PENDING_WIZARD_WARNINGS: list[str] = []


def begin_wizard_warnings() -> None:
    """A new wizard is building its steps: drop anything a previous screen's
    late workers left behind after its teardown."""
    _PENDING_WIZARD_WARNINGS.clear()


def set_wizard_warn_sink(fn) -> None:
    """Register a ``(msg: str) -> None`` logger; None on screen teardown,
    which also drops anything still held."""
    global _WIZARD_WARN_SINK
    _WIZARD_WARN_SINK = fn
    if fn is None:
        _PENDING_WIZARD_WARNINGS.clear()


def flush_pending_wizard_warnings() -> None:
    """Deliver the warnings held back while no sink was registered."""
    pending = list(_PENDING_WIZARD_WARNINGS)
    _PENDING_WIZARD_WARNINGS.clear()
    for msg in pending:
        wizard_warn(msg)


def wizard_warn(msg: str) -> None:
    fn = _WIZARD_WARN_SINK
    if fn is None:
        if len(_PENDING_WIZARD_WARNINGS) < 50:
            _PENDING_WIZARD_WARNINGS.append(msg)
        return
    try:
        fn(msg)
    except Exception:
        pass
