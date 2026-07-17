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
    exec python3 "$SELF_DIR/$SCRIPT_REL" "$@"
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
