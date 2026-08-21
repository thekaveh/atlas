#!/usr/bin/env sh
#
# Shared dispatcher used by start.sh, stop.sh, and
# bootstrapper/generate_supabase_keys.sh.
#
# Picks `uv` when available (fast venv resolution, locked deps), falls
# back to system `python3` otherwise. Usage:
#
#   sh bootstrapper/_run.sh <script.py> [args...]
#
# The <script.py> path is interpreted relative to this file's directory
# (i.e. bootstrapper/), so callers can pass plain `start.py`, `stop.py`,
# or `generate_supabase_keys.py`.

set -e

if [ $# -lt 1 ]; then
    echo "Usage: $0 <python-script-relative-to-bootstrapper> [args...]" >&2
    exit 64
fi

SCRIPT_REL="$1"
shift

# Resolve the bootstrapper directory (this script's parent).
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

if command -v uv >/dev/null 2>&1; then
    # Banner to stderr: stdout must stay clean so `<script> --format json`
    # (e.g. `doctor --format json`) is directly parseable without a shim (#650).
    echo "📦 Using uv for dependency management..." >&2
    exec uv run --directory "$SELF_DIR" "$SCRIPT_REL" "$@"
elif command -v python3 >/dev/null 2>&1; then
    echo "📦 Using system Python (install uv for better dependency management)..." >&2
    BOOTSTRAPPER_VENV="${ATLAS_BOOTSTRAPPER_VENV:-$SELF_DIR/.venv}"
    VENV_PYTHON="$BOOTSTRAPPER_VENV/bin/python"
    REQUIRED_IMPORTS="import click, jsonschema, PIL, requests, rich, textual, textual_image, urllib3, yaml"

    # Fingerprint the DEPENDENCY DECLARATION, not the module list. The guard
    # used to be presence-only: if those modules imported at ANY version, the
    # install was skipped forever. So the security floors in pyproject.toml —
    # click>=8.3.3 (command-injection advisory), urllib3>=2.7.0
    # (CVE-2026-44431/44432), pillow>=12.3.0 — were never applied to an
    # existing venv, and the two dispatch branches produced materially
    # different dependency states from identical inputs (the uv branch
    # re-resolves against uv.lock every run). A new dependency added to
    # pyproject.toml but not to REQUIRED_IMPORTS was likewise never installed.
    DEPS_STAMP="$BOOTSTRAPPER_VENV/.atlas-deps-stamp"
    if command -v shasum >/dev/null 2>&1; then
        WANT_STAMP="$(shasum -a 256 "$SELF_DIR/pyproject.toml" | cut -d' ' -f1)"
    elif command -v sha256sum >/dev/null 2>&1; then
        WANT_STAMP="$(sha256sum "$SELF_DIR/pyproject.toml" | cut -d' ' -f1)"
    else
        # No hasher: fall back to size+mtime. Weaker, but still detects an edit.
        WANT_STAMP="$(wc -c < "$SELF_DIR/pyproject.toml" | tr -d ' ')"
    fi
    HAVE_STAMP=""
    if [ -f "$DEPS_STAMP" ]; then
        HAVE_STAMP="$(cat "$DEPS_STAMP" 2>/dev/null || true)"
    fi

    # Does the venv already satisfy every import? Decides whether a failed
    # refresh is fatal or merely a warning.
    VENV_USABLE=0
    if [ -x "$VENV_PYTHON" ] && "$VENV_PYTHON" -c "$REQUIRED_IMPORTS" >/dev/null 2>&1; then
        VENV_USABLE=1
    fi

    if [ "$VENV_USABLE" = "0" ] || [ "$HAVE_STAMP" != "$WANT_STAMP" ]; then
        # Create the venv only when it is genuinely absent. A refresh must NOT
        # re-run the interpreter gate: the existing venv already has a
        # supported Python, and gating on the SYSTEM python3 would refuse to
        # apply a security floor on a host whose system interpreter has since
        # aged out — turning a dependency refresh into a hard launch failure.
        if [ ! -x "$VENV_PYTHON" ]; then
            echo "Creating Atlas bootstrapper environment at $BOOTSTRAPPER_VENV..." >&2
            if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
                echo "❌ Atlas requires Python 3.10 or newer." >&2
                exit 1
            fi
            if ! python3 -m venv "$BOOTSTRAPPER_VENV"; then
                echo "❌ Could not create the Atlas bootstrapper virtual environment." >&2
                echo "   Install the Python venv module or install uv, then retry." >&2
                exit 1
            fi
        else
            echo "Refreshing Atlas bootstrapper dependencies (declaration changed)..." >&2
        fi
        if "$VENV_PYTHON" -m pip install --disable-pip-version-check --upgrade "$SELF_DIR"; then
            # Stamp only AFTER a successful install, so a failed run retries.
            printf '%s\n' "$WANT_STAMP" > "$DEPS_STAMP" 2>/dev/null || true
        elif [ "$VENV_USABLE" = "1" ]; then
            # A REFRESH failed on a venv that already imports everything we
            # need. `uv venv` does not install pip, so `python -m pip` is
            # simply absent on a perfectly good environment — aborting here
            # would break a working setup in order to apply a version floor.
            # Warn instead, and leave the stamp unwritten so the next run
            # retries rather than silently accepting the old versions.
            echo "⚠ Could not refresh Atlas bootstrapper dependencies (pip unavailable?)." >&2
            echo "  Continuing with the existing environment; declared version" >&2
            echo "  floors in bootstrapper/pyproject.toml may not be applied." >&2
            echo "  Install uv, or run: $VENV_PYTHON -m ensurepip --upgrade" >&2
        else
            echo "❌ Could not install Atlas bootstrapper dependencies." >&2
            exit 1
        fi
    fi

    exec "$VENV_PYTHON" "$SELF_DIR/$SCRIPT_REL" "$@"
else
    echo "❌ Neither 'uv' nor 'python3' was found on PATH." >&2
    echo "" >&2
    echo "  Install one of:" >&2
    echo "    • uv (recommended):  https://github.com/astral-sh/uv" >&2
    echo "    • Python 3.10+:      https://www.python.org/downloads/" >&2
    echo "" >&2
    echo "  Then re-run the script that invoked this dispatcher." >&2
    exit 127
fi
