#!/usr/bin/env sh
#
# Atlas - Stop Script Wrapper
#
# This is a thin wrapper that calls the Python implementation for full cross-platform compatibility.
#

# Refuse to run as root. Same rationale as start.sh's guard: `_run.sh`
# touches `bootstrapper/.venv` (uv writes the cache + __pycache__),
# and running under sudo flips the ownership root:root, blocking the
# next non-sudo `start.sh` / `stop.sh` from updating the same venv.
# See docs/TROUBLESHOOTING.md for recovery if you've already hit this.
if [ "$(id -u)" -eq 0 ]; then
    echo "stop.sh: refusing to run as root." >&2
    echo "" >&2
    echo "  Stopping the stack and removing project volumes need only your user." >&2
    echo "  --clean-hosts requests elevation internally for the one hosts-file" >&2
    echo "  mutation that actually needs it." >&2
    echo "" >&2
    echo "  Standard stop:     ./stop.sh" >&2
    echo "  Stop + wipe data:  ./stop.sh --cold" >&2
    echo "  Stop + hosts:      ./stop.sh --clean-hosts" >&2
    echo "" >&2
    echo "  See docs/TROUBLESHOOTING.md if a previous sudo run left" >&2
    echo "  root-owned files behind." >&2
    exit 2
fi

# Capture the caller's directory before entering the Atlas checkout, as
# start.sh does, so a relative --consumer / ATLAS_CONSUMER_MANIFEST path
# resolves to the same manifest at teardown as at launch.
if [ -z "${ATLAS_INVOKER_CWD:-}" ]; then
    ATLAS_INVOKER_CWD="${PWD}"
    export ATLAS_INVOKER_CWD
fi

# Change to the script directory
CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null || { echo "stop.sh: failed to enter script directory" >&2; exit 1; }

exec sh bootstrapper/_run.sh stop.py "$@"
