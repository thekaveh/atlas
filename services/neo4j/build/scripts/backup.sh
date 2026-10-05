#!/bin/bash
set -euo pipefail

# Legacy single-database dump of the neo4j database into /snapshot.
#
# Neo4j Community has no online dump, and inside the Atlas container the
# server IS the container's main process: `neo4j stop` from `docker exec`
# kills the container before any dump runs. So this script only runs against
# a stopped database volume, in a one-off container:
#
#   docker compose stop neo4j-graph-db
#   docker compose run --rm --no-deps --entrypoint /usr/local/bin/backup.sh neo4j-graph-db
#   docker compose start neo4j-graph-db
#
# Coordinated backups (system + neo4j, signed, offsite) use
# services/backup/run-consistent-backup.sh instead.

# /snapshot is the named-volume target declared in services/neo4j/compose.yml.
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR=/snapshot
BACKUP_FILE="${BACKUP_DIR}/backup_${TIMESTAMP}.dump"

if neo4j status 2>/dev/null | grep -q "Neo4j is running"; then
  echo "ERROR: Neo4j is running in this container; stopping it would kill the container." >&2
  echo "Run this script offline (see its header) or use services/backup/run-consistent-backup.sh." >&2
  exit 75
fi

mkdir -p "${BACKUP_DIR}"
echo "Creating Neo4j database backup to ${BACKUP_FILE}..."

# 5.x `database dump` accepts only --to-path (emitting <database>.dump) or
# --to-stdout; dump to the fixed name, then rename to the timestamped file.
neo4j-admin database dump neo4j --to-path="${BACKUP_DIR}" --overwrite-destination
mv "${BACKUP_DIR}/neo4j.dump" "${BACKUP_FILE}"

echo "Backup completed and stored at: ${BACKUP_FILE}"
echo "Note: auto_restore.sh loads the newest backup_*.dump on every container start."
