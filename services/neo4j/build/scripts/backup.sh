#!/bin/bash
set -euo pipefail

# This script creates a backup of Neo4j database

# /snapshot is the named-volume target declared in services/neo4j/compose.yml.
# Earlier revisions derived the path from
# $SCRIPT_DIR, which resolved to /usr/local/snapshot (an unmounted
# directory inside the container) and silently lost every backup on
# container restart. Hardcoding the mount target is the simplest guard.
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR=/snapshot
BACKUP_FILE="${BACKUP_DIR}/backup_${TIMESTAMP}.dump"

# Ensure backup directory exists
mkdir -p "${BACKUP_DIR}"

echo "Creating Neo4j database backup to ${BACKUP_FILE}..."

# Restart Neo4j even if the dump fails — under `set -e`, a dump error
# previously aborted the script BEFORE `neo4j start`, leaving the
# database stopped.
trap 'neo4j start' EXIT

# Stop Neo4j service temporarily to ensure consistent backup
neo4j stop

# Wait for Neo4j to stop. Bounded (60s) like the other init wait loops —
# a wedged JVM/lock should fail the backup, not hang the docker exec forever.
echo "Waiting for Neo4j to stop..."
WAITED=0
until ! neo4j status | grep -q "Neo4j is running"; do
  WAITED=$((WAITED + 1))
  if [ "$WAITED" -ge 60 ]; then
    echo "ERROR: Neo4j did not stop after 60s; aborting backup." >&2
    exit 1
  fi
  sleep 1
done

# Perform the backup using neo4j-admin. 5.x `database dump` accepts only
# --to-path (emitting <database>.dump) or --to-stdout — the old
# --output-name flag does not exist and failed every backup. Dump to the
# fixed name, then rename to the timestamped file.
neo4j-admin database dump neo4j --to-path="${BACKUP_DIR}" --overwrite-destination
mv "${BACKUP_DIR}/neo4j.dump" "${BACKUP_FILE}"

echo "Backup completed and stored at: ${BACKUP_FILE}"
echo "Neo4j service restarting (EXIT trap)."
