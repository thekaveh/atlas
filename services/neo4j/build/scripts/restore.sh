#!/bin/bash

# Legacy restore of the newest /snapshot/backup_*.dump into the neo4j database.
#
# Like backup.sh, this cannot run inside the live container (`neo4j stop`
# kills it). Run it against the stopped database volume:
#
#   docker compose stop neo4j-graph-db
#   docker compose run --rm --no-deps --entrypoint /usr/local/bin/restore.sh neo4j-graph-db
#   docker compose start neo4j-graph-db
#
# Coordinated restores use services/backup/run-database-restore.sh.

# /snapshot is the named-volume target from services/neo4j/compose.yml.
SNAPSHOT_DIR=/snapshot

if neo4j status 2>/dev/null | grep -q "Neo4j is running"; then
  echo "ERROR: Neo4j is running in this container; stopping it would kill the container." >&2
  echo "Run this script offline (see its header) or use services/backup/run-database-restore.sh." >&2
  exit 75
fi

mkdir -p "${SNAPSHOT_DIR}"

# Find the latest backup file
LATEST_BACKUP=$(find "${SNAPSHOT_DIR}" -name "backup_*.dump" -type f -printf "%T@ %p\n" 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2)

if [ -n "${LATEST_BACKUP}" ] && [ -f "${LATEST_BACKUP}" ]; then
    echo "Found backup file: ${LATEST_BACKUP}"
    echo "Restoring Neo4j database from backup..."

    # 5.x community has no `database restore` subcommand (that pairs with
    # enterprise `backup`); dumps are restored with `database load`.
    # --from-stdin sidesteps load's <database>.dump naming requirement for
    # our timestamped files.
    # Same marker as auto_restore.sh: if this load dies part-way, the next
    # container start retries it instead of booting the partial store.
    RESTORE_MARKER="${NEO4J_RESTORE_MARKER:-/data/.atlas-restore-incomplete}"
    mkdir -p "$(dirname "${RESTORE_MARKER}")"
    touch "${RESTORE_MARKER}"
    if neo4j-admin database load neo4j --from-stdin --overwrite-destination < "${LATEST_BACKUP}"; then
        rm -f "${RESTORE_MARKER}"
        echo "Database restored successfully. Start the service again."
    else
        echo "ERROR: restore from ${LATEST_BACKUP} FAILED (load exited non-zero)." >&2
        echo "ERROR: the neo4j database may be in a partially-overwritten state; inspect before trusting data." >&2
        exit 1
    fi
else
    echo "No backup file found. Skipping restore."
fi
