"""Contracts in shell entrypoints that no Python test otherwise covers.

Static assertions: these files are dispatched by `./start.sh` long before any
Python runs, and exercising them for real means building virtualenvs and
starting containers. Both defects below were reproduced end-to-end by hand
(a stale venv refusing to upgrade `click`, and a container exiting 0 with an
empty `model.default`); these keep the fix from silently regressing.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_SH = REPO_ROOT / "bootstrapper" / "_run.sh"
HERMES_INIT = REPO_ROOT / "services" / "hermes" / "init" / "scripts" / "init-hermes.sh"


def test_the_system_python_fallback_detects_a_stale_venv():
    """The reuse guard was PRESENCE-only.

    If the required modules imported at ANY version the install was skipped
    forever, so the security floors in pyproject.toml — click>=8.3.3
    (command-injection advisory), urllib3>=2.7.0 (CVE-2026-44431/44432),
    pillow>=12.3.0 — were never applied to an existing venv. The `uv` branch
    re-resolves against uv.lock every run, so the two dispatch branches
    produced materially different dependency states from identical inputs.
    """
    source = RUN_SH.read_text(encoding="utf-8")

    # staleness is decided against the dependency DECLARATION, not a module list
    assert "pyproject.toml" in source
    assert "DEPS_STAMP" in source

    # ...and the stamp comparison is actually IN the reinstall condition.
    # Asserting only that the strings appear somewhere in the file let a
    # mutation that gutted the condition pass.
    guard = source[source.index('if [ "$VENV_USABLE" = "0" ]'):]
    condition = guard[: guard.index("then")]
    assert '"$HAVE_STAMP" != "$WANT_STAMP"' in condition, (
        f"the reinstall condition does not consult the stamp:\n{condition}"
    )
    # the stamp must be derived from the dependency declaration
    want = source[source.index("WANT_STAMP=") : source.index("HAVE_STAMP=")]
    assert "pyproject.toml" in want
    # the stamp is only written after a successful install, so a failure retries
    install_at = source.index("pip install")
    stamp_at = source.index("> \"$DEPS_STAMP\"")
    assert install_at < stamp_at, "the stamp is written before the install succeeds"
    # ...and the install must be able to upgrade an already-present package
    assert "--upgrade" in source


def test_the_fallback_does_not_regate_python_version_on_a_refresh():
    """A refresh must not re-run the interpreter gate.

    The existing venv already has a supported Python; gating a dependency
    refresh on the SYSTEM python3 would refuse to apply a security floor on a
    host whose system interpreter has since aged out, turning the refresh into
    a hard launch failure.
    """
    source = RUN_SH.read_text(encoding="utf-8")
    gate = source.index("Atlas requires Python 3.10 or newer")
    guard = source.rindex('if [ ! -x "$VENV_PYTHON" ]; then', 0, gate)
    assert guard < gate, "the version gate is not inside the venv-absent branch"


def test_urllib3_is_import_checked():
    """It carries a CVE floor but was absent from the import probe."""
    assert re.search(r"REQUIRED_IMPORTS=.*urllib3", RUN_SH.read_text(encoding="utf-8"))


def test_hermes_init_fails_when_no_default_model_resolves():
    """It warned and exited 0, contradicting its own header contract.

    `model.default` empty means every Hermes request 500s — while hermes's
    healthcheck hits `/v1/models` and passes regardless, so the container
    reports HEALTHY with nothing working. The header states the script "Exits
    non-zero on any unexpected condition so docker compose surfaces the failure
    instead of letting hermes start with a broken config."
    """
    source = HERMES_INIT.read_text(encoding="utf-8")
    marker = "could not resolve a default model"
    assert marker in source, "the failure branch was renamed — re-check this test"

    tail = source[source.index(marker):]
    # `exit 1` must come before the config is rendered
    exit_at = tail.index("exit 1")
    render_at = tail.find("wrote ")
    assert render_at == -1 or exit_at < render_at
    assert "log \"⚠ could not auto-select a default model" not in source, (
        "the warn-and-continue path is back"
    )


def test_a_failed_refresh_degrades_instead_of_breaking_a_working_venv():
    """`uv venv` installs no pip, so `python -m pip` is absent on a good env.

    Aborting there would break a working setup in order to apply a version
    floor — and the existing dispatcher test encodes exactly that shape: a venv
    that imports everything but has no pip. A refresh failure therefore warns
    and continues, leaving the stamp unwritten so the next run retries. A venv
    that does NOT import cleanly is still a hard failure.
    """
    source = RUN_SH.read_text(encoding="utf-8")
    assert "VENV_USABLE" in source
    tail = source[source.index("pip install --disable-pip-version-check"):]
    warn_at = tail.index("Could not refresh Atlas bootstrapper dependencies")
    fatal_at = tail.index("Could not install Atlas bootstrapper dependencies")
    assert warn_at < fatal_at, "the usable-venv branch must come first"
    # the warning branch must not exit
    assert "exit 1" not in tail[warn_at:fatal_at]
