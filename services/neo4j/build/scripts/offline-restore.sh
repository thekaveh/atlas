#!/bin/bash
# Load an authenticated, pre-staged Atlas snapshot into offline Neo4j Community.
set -euo pipefail

EXPECTED_NEO4J_IMAGE="neo4j:5.26.27"
EXPECTED_NEO4J_VERSION="5.26.27"
SOURCE="${1:?usage: offline-restore.sh /snapshot/restore-TIMESTAMP}"
TIMEOUT_SECONDS="${BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS:-120}"
REPORT_ROOT="${NEO4J_REPORT_ROOT:-/reports}"

case "${TIMEOUT_SECONDS}" in
  ''|*[!0-9]*|0|0*) echo "neo4j restore: timeout must be a canonical positive integer" >&2; exit 64 ;;
esac
[ "${TIMEOUT_SECONDS}" -le 3600 ] || { echo "neo4j restore: timeout must be at most 3600" >&2; exit 64; }
run_bounded() { timeout -s TERM -k 10 "${TIMEOUT_SECONDS}" "$@"; }

[ -d "${SOURCE}" ] && [ -f "${SOURCE}/snapshot.metadata" ] || {
  echo "neo4j restore: verified snapshot directory is missing" >&2
  exit 66
}
if run_bounded neo4j status >/dev/null 2>&1; then
  echo "neo4j restore: database is still online; the host orchestrator must stop neo4j-graph-db first" >&2
  exit 75
fi
runtime_version="$(run_bounded neo4j-admin --version | tr -d '\r\n')"
[ "${runtime_version}" = "${EXPECTED_NEO4J_VERSION}" ] || {
  echo "neo4j restore: exact version mismatch" >&2; exit 78;
}
mkdir -p "${REPORT_ROOT}"

metadata_value() {
  key=$1
  value="$(sed -n "s/^${key}=//p" "${SOURCE}/snapshot.metadata")"
  [ -n "${value}" ] && [ "$(grep -c "^${key}=" "${SOURCE}/snapshot.metadata")" -eq 1 ] || {
    echo "neo4j restore: invalid ${key} metadata" >&2
    exit 65
  }
  printf '%s' "${value}"
}
[ "$(metadata_value snapshot_state)" = complete ] || { echo "neo4j restore: snapshot is incomplete" >&2; exit 65; }
[ "$(metadata_value neo4j_image)" = "${EXPECTED_NEO4J_IMAGE}" ] || { echo "neo4j restore: image contract mismatch" >&2; exit 65; }
[ "$(metadata_value neo4j_version)" = "${EXPECTED_NEO4J_VERSION}" ] || { echo "neo4j restore: version contract mismatch" >&2; exit 65; }

for database in system neo4j; do
  archive="${SOURCE}/${database}.dump"
  [ -s "${archive}" ] || { echo "neo4j restore: ${database}.dump is missing" >&2; exit 65; }
  expected="$(metadata_value "${database}_sha256")"
  actual="$(run_bounded sha256sum "${archive}")"; actual="${actual%% *}"
  [ "${actual}" = "${expected}" ] || { echo "neo4j restore: ${database}.dump checksum mismatch" >&2; exit 65; }
  expected_bytes="$(metadata_value "${database}_bytes")"
  case "${expected_bytes}" in ''|*[!0-9]*|0|0*) echo "neo4j restore: invalid ${database}.dump size" >&2; exit 65;; esac
  actual_bytes="$(run_bounded wc -c <"${archive}" | tr -d '[:space:]')"
  [ "${actual_bytes}" = "${expected_bytes}" ] || { echo "neo4j restore: ${database}.dump size mismatch" >&2; exit 65; }
done

# `--info` parses the complete dump envelope without changing /data. Both
# archives must pass before either live-store mutation is attempted.
run_bounded neo4j-admin database load --info system --from-path="${SOURCE}"
run_bounded neo4j-admin database load --info neo4j --from-path="${SOURCE}"

echo "neo4j restore: loading system database offline"
run_bounded neo4j-admin database load system --from-path="${SOURCE}" --overwrite-destination
if [ "${ATLAS_NEO4J_RESTORE_TEST_FAIL_AFTER_SYSTEM_LOAD:-}" = confirmed ]; then
  echo "neo4j restore: injected failure after system load" >&2
  exit 79
fi
echo "neo4j restore: loading neo4j database offline"
run_bounded neo4j-admin database load neo4j --from-path="${SOURCE}" --overwrite-destination
run_bounded neo4j-admin database check system --report-path="${REPORT_ROOT}/check-system"
run_bounded neo4j-admin database check neo4j --report-path="${REPORT_ROOT}/check-neo4j"
echo "neo4j restore: offline load complete"
