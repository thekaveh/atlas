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
    REQUIRED_IMPORTS="import click, jsonschema, PIL, requests, rich, textual, textual_image, yaml"

    if [ ! -x "$VENV_PYTHON" ] || ! "$VENV_PYTHON" -c "$REQUIRED_IMPORTS" >/dev/null 2>&1; then
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
        if ! "$VENV_PYTHON" -m pip install --disable-pip-version-check "$SELF_DIR"; then
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
