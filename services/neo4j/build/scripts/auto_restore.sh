#!/bin/bash

# This script automatically checks for backup files in the snapshot directory
# and restores the latest one if it exists.

SNAPSHOT_DIR="${NEO4J_SNAPSHOT_DIR:-/snapshot}"
DATABASE_DIR="${NEO4J_DATABASE_DIR:-/data/databases/neo4j}"
# Present while a load is in flight; a failed load leaves it behind so the
# next start retries instead of booting the partially loaded store.
RESTORE_MARKER="${NEO4J_RESTORE_MARKER:-/data/.atlas-restore-incomplete}"

# Ensure snapshot directory exists
mkdir -p "${SNAPSHOT_DIR}"

# Restore only into an empty database (first boot of a fresh data volume).
# Loading on every boot silently rolled the live graph back to the last
# backup, discarding every write made since. An explicit restore of a
# populated database is restore.sh (README §4.2).
if [ ! -e "${RESTORE_MARKER}" ] && [ -d "${DATABASE_DIR}" ] \
  && [ -n "$(ls -A "${DATABASE_DIR}" 2>/dev/null)" ]; then
  echo "Neo4j database already present at ${DATABASE_DIR}; skipping automatic restore."
  exit 0
fi

echo "Checking for Neo4j backups to restore..."

# Look for the latest backup in the snapshot directory
LATEST_BACKUP=$(find "${SNAPSHOT_DIR}" -name "backup_*.dump" -type f -printf "%T@ %p\n" 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2)

# If a backup file exists, restore it
if [ -n "${LATEST_BACKUP}" ] && [ -f "${LATEST_BACKUP}" ]; then
  echo "Found backup file: ${LATEST_BACKUP}"
  echo "Automatically restoring Neo4j database from backup..."

  # Note: this script runs from docker-entrypoint-wrapper.sh BEFORE the
  # neo4j server is started, so no `neo4j stop` dance is needed here.
  # (Earlier revisions guarded with `if neo4j status | grep -q running`,
  # but the check was unreachable in this execution path.)

  # Restore the database (5.x: `database load`; community ships no
  # `database restore` — see restore.sh).
  mkdir -p "$(dirname "${RESTORE_MARKER}")"
  touch "${RESTORE_MARKER}"
  if neo4j-admin database load neo4j --from-stdin --overwrite-destination < "${LATEST_BACKUP}"; then
    rm -f "${RESTORE_MARKER}"
    echo "Database automatically restored successfully."
  else
    echo "ERROR: automatic restore from ${LATEST_BACKUP} FAILED (load exited non-zero)." >&2
    echo "ERROR: the neo4j database may be in a partially-overwritten state; inspect before trusting data." >&2
    exit 1
  fi
else
  echo "No backup file found. Skipping automatic restore."
fi