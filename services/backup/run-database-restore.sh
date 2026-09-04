#!/bin/sh
# Destructive restore is refused until the operator confirms maintenance mode.
set -eu
if [ "${BACKUP_RESTORE_MAINTENANCE_MODE:-}" != confirmed ]; then
  echo "database restore orchestrator: set BACKUP_RESTORE_MAINTENANCE_MODE=confirmed after quiescing all database writers" >&2
  exit 64
fi
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec python3 "${SCRIPT_DIR}/database_orchestrator.py" restore
