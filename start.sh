#!/usr/bin/env sh
#
# Atlas - Start Script Wrapper
#
# This is a thin wrapper that calls the Python implementation for full cross-platform compatibility.
#

# Refuse to run as root. The wizard runs entirely as the invoking user;
# only --setup-hosts touches /etc/hosts, and that step shells out to
# sudo internally for the single privileged write. Running the whole
# script under sudo creates root-owned files (.venv, volumes/api/
# kong-dynamic.yml, __pycache__) the next non-sudo run can't overwrite
# — a silent footgun. Fail fast instead.
# See docs/TROUBLESHOOTING.md for recovery if you've already hit this.
if [ "$(id -u)" -eq 0 ]; then
    echo "start.sh: refusing to run as root." >&2
    echo "" >&2
    echo "  The wizard runs as your user; only /etc/hosts editing needs root," >&2
    echo "  and the script handles that internally." >&2
    echo "" >&2
    echo "  For host entries up front:  ./start.sh --setup-hosts" >&2
    echo "  Otherwise:                  ./start.sh" >&2
    echo "" >&2
    echo "  See docs/TROUBLESHOOTING.md if a previous sudo run left" >&2
    echo "  root-owned files behind." >&2
    exit 2
fi

# Capture the caller's directory before entering the Atlas checkout. Python
# uses this to resolve relative ATLAS_ENV_USER_FILE values for parent-owned
# consumer overlays.
if [ -z "${ATLAS_INVOKER_CWD:-}" ]; then
    ATLAS_INVOKER_CWD="${PWD}"
    export ATLAS_INVOKER_CWD
fi

# Change to the script directory
cd "$(dirname "$0")" || { echo "start.sh: failed to enter script directory" >&2; exit 1; }

exec sh bootstrapper/_run.sh start.py "$@"
