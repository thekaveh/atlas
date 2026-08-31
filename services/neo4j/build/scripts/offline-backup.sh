#!/bin/bash
# Exact Neo4j Community 5.26.27 offline dump contract.
set -euo pipefail

EXPECTED_NEO4J_IMAGE="neo4j:5.26.27"
EXPECTED_NEO4J_VERSION="5.26.27"
SNAPSHOT_ROOT="${NEO4J_SNAPSHOT_ROOT:-/snapshot}"
TIMESTAMP="${BACKUP_TIMESTAMP:?BACKUP_TIMESTAMP is required}"
TIMEOUT_SECONDS="${BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS:-120}"

case "${TIMESTAMP}" in
  [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9]) ;;
  *) echo "neo4j backup: BACKUP_TIMESTAMP must be YYYYMMDD_HHMMSS" >&2; exit 64 ;;
esac
case "${TIMEOUT_SECONDS}" in
  ''|*[!0-9]*|0|0*) echo "neo4j backup: timeout must be a canonical positive integer" >&2; exit 64 ;;
esac
[ "${TIMEOUT_SECONDS}" -le 3600 ] || { echo "neo4j backup: timeout must be at most 3600" >&2; exit 64; }

run_bounded() {
  timeout -s TERM -k 10 "${TIMEOUT_SECONDS}" "$@"
}

if run_bounded neo4j status >/dev/null 2>&1; then
  echo "neo4j backup: database is still online; the host orchestrator must stop neo4j-graph-db first" >&2
  exit 75
fi

runtime_version="$(run_bounded neo4j-admin --version | tr -d '\r\n')"
[ "${runtime_version}" = "${EXPECTED_NEO4J_VERSION}" ] || {
  echo "neo4j backup: exact version mismatch" >&2; exit 78;
}

destination="${SNAPSHOT_ROOT}/${TIMESTAMP}"
stage="${SNAPSHOT_ROOT}/.atlas-${TIMESTAMP}-$$"
[ ! -e "${destination}" ] || { echo "neo4j backup: snapshot ${TIMESTAMP} already exists" >&2; exit 73; }
umask 077
mkdir -p "${stage}"
cleanup() {
  rc=$?
  trap - EXIT HUP INT TERM
  [ "${rc}" -eq 0 ] || rm -rf "${stage}"
  exit "${rc}"
}
trap cleanup EXIT HUP INT TERM

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "neo4j backup: dumping offline system database with ${EXPECTED_NEO4J_IMAGE}"
run_bounded neo4j-admin database dump system --to-path="${stage}" --overwrite-destination
echo "neo4j backup: dumping offline neo4j database with ${EXPECTED_NEO4J_IMAGE}"
run_bounded neo4j-admin database dump neo4j --to-path="${stage}" --overwrite-destination

for database in system neo4j; do
  archive="${stage}/${database}.dump"
  [ -s "${archive}" ] || { echo "neo4j backup: ${database}.dump is empty" >&2; exit 1; }
  sha="$(run_bounded sha256sum "${archive}")"; sha="${sha%% *}"
  bytes="$(run_bounded wc -c <"${archive}" | tr -d '[:space:]')"
  printf '%s_sha256=%s\n%s_bytes=%s\n' "${database}" "${sha}" "${database}" "${bytes}" \
    >>"${stage}/snapshot.metadata.artifacts"
done
completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat >"${stage}/snapshot.metadata" <<EOF
snapshot_format=1
snapshot_state=complete
backup_timestamp=${TIMESTAMP}
neo4j_image=${EXPECTED_NEO4J_IMAGE}
neo4j_version=${EXPECTED_NEO4J_VERSION}
started_at=${started_at}
completed_at=${completed_at}
EOF
cat "${stage}/snapshot.metadata.artifacts" >>"${stage}/snapshot.metadata"
rm -f "${stage}/snapshot.metadata.artifacts"
chmod 0600 "${stage}"/*
mv "${stage}" "${destination}"
echo "neo4j backup: complete -> ${destination}"
