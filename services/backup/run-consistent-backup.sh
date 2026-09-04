#!/bin/sh
# Host-only consistency boundary; implementation uses Python stdlib so child
# process groups, PID identity, and exact Docker resource ownership are robust.
set -eu
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec python3 "${SCRIPT_DIR}/database_orchestrator.py" backup
