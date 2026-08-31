#!/bin/sh
# Authenticate both database components into one private, atomically published
# restore-artifact set. The host validates this set in fresh data volumes.
set -eu

[ "${1:-prepare}" = prepare ] || { echo "database restore: only prepare mode is supported" >&2; exit 64; }
: "${BACKUP_TIMESTAMP:?database restore requires an exact BACKUP_TIMESTAMP}"
: "${BACKUP_RESTORE_TOKEN:?required}"
: "${BACKUP_MANIFEST_HMAC_KEY:?required}"
: "${BACKUP_DEPLOYMENT_ID:?required}"
case "${BACKUP_RESTORE_TOKEN}" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
  *) echo "database restore: invalid ownership token" >&2; exit 64 ;;
esac

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck disable=SC1091 # runtime container path is deliberate.
. "${SCRIPT_DIR}/s3-client.sh"
# shellcheck disable=SC1091 # runtime container path is deliberate.
. "${SCRIPT_DIR}/database-snapshots.sh"

TIMEOUT_SECONDS=${BACKUP_COMMAND_TIMEOUT_SECONDS:-900}
BACKUP_MAX_DATABASE_ARCHIVE_BYTES=${BACKUP_MAX_DATABASE_ARCHIVE_BYTES:-53687091200}
BACKUP_NEO4J_SOURCE=${BACKUP_NEO4J_SOURCE:-container}
BACKUP_WEAVIATE_SOURCE=${BACKUP_WEAVIATE_SOURCE:-container}
BUCKET=${BACKUP_BUCKET:-atlas-backups}
DATABASE_RESTORE_ROOT=${DATABASE_RESTORE_ROOT:-/database-restore}
case "${TIMEOUT_SECONDS}" in ''|*[!0-9]*|0|0*) echo "database restore: invalid command timeout" >&2; exit 64;; esac
case "${BACKUP_MAX_DATABASE_ARCHIVE_BYTES}" in ''|*[!0-9]*|0|0*) echo "database restore: invalid archive limit" >&2; exit 64;; esac
[ "${TIMEOUT_SECONDS}" -le 86400 ] || { echo "database restore: command timeout must be at most 86400" >&2; exit 64; }
[ "${BACKUP_MAX_DATABASE_ARCHIVE_BYTES}" -le 1099511627776 ] || { echo "database restore: archive limit must be at most 1099511627776" >&2; exit 64; }
case "${BACKUP_NEO4J_SOURCE}:${BACKUP_WEAVIATE_SOURCE}" in
  *localhost*) echo "database restore: localhost source is unsupported" >&2; exit 64 ;;
esac
case "${BACKUP_NEO4J_SOURCE}" in container|disabled) ;; *) echo "database restore: invalid Neo4j source" >&2; exit 64;; esac
case "${BACKUP_WEAVIATE_SOURCE}" in container|disabled) ;; *) echo "database restore: invalid Weaviate source" >&2; exit 64;; esac
if [ "${DATABASE_RESTORE_ROOT}" != /database-restore ] &&
  [ "${DATABASE_RESTORE_ROOT}" != "/tmp/atlas-database-restore-test-${BACKUP_RESTORE_TOKEN}" ]; then
  echo "database restore: restore root is outside the bounded private contract" >&2; exit 64
fi
[ ! -L "${DATABASE_RESTORE_ROOT}" ] || { echo "database restore: restore root must not be a symlink" >&2; exit 64; }
run_bounded() { timeout -s TERM -k 10 "${TIMEOUT_SECONDS}" "$@"; }

valid_timestamp() {
  value=$1
  case "$value" in [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9]) ;; *) return 1;; esac
  year=$(printf '%s' "$value" | cut -c1-4); month=$(printf '%s' "$value" | cut -c5-6); day=$(printf '%s' "$value" | cut -c7-8)
  hour=$(printf '%s' "$value" | cut -c10-11); minute=$(printf '%s' "$value" | cut -c12-13); second=$(printf '%s' "$value" | cut -c14-15)
  year_num=$(printf '%s' "$year" | sed 's/^0*//'); year_num=${year_num:-0}
  month_num=$(printf '%s' "$month" | sed 's/^0*//'); month_num=${month_num:-0}
  day_num=$(printf '%s' "$day" | sed 's/^0*//'); day_num=${day_num:-0}
  hour_num=$(printf '%s' "$hour" | sed 's/^0*//'); hour_num=${hour_num:-0}
  minute_num=$(printf '%s' "$minute" | sed 's/^0*//'); minute_num=${minute_num:-0}
  second_num=$(printf '%s' "$second" | sed 's/^0*//'); second_num=${second_num:-0}
  [ "$year_num" -ge 1 ] && [ "$month_num" -ge 1 ] && [ "$month_num" -le 12 ] && \
    [ "$hour_num" -le 23 ] && [ "$minute_num" -le 59 ] && [ "$second_num" -le 59 ] || return 1
  case "$month_num" in
    1|3|5|7|8|10|12) max_day=31;;
    4|6|9|11) max_day=30;;
    2) max_day=28; if { [ $((year_num % 4)) -eq 0 ] && [ $((year_num % 100)) -ne 0 ]; } || [ $((year_num % 400)) -eq 0 ]; then max_day=29; fi;;
  esac
  [ "$day_num" -ge 1 ] && [ "$day_num" -le "$max_day" ]
}
valid_iso() {
  value=$1
  case "$value" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z) ;; *) return 1;; esac
  compact=$(printf '%s' "$value" | sed 's/-//g; s/T/_/; s/://g; s/Z$//')
  valid_timestamp "$compact"
}
valid_timestamp "${BACKUP_TIMESTAMP}" || { echo "database restore: invalid backup calendar timestamp" >&2; exit 64; }

prepare_backup_s3 "database restore"
work="/tmp/atlas-database-restore-${BACKUP_RESTORE_TOKEN}"
stage_tmp="${DATABASE_RESTORE_ROOT}/.prepare-${BACKUP_RESTORE_TOKEN}"
artifact_stage="restore-${BACKUP_RESTORE_TOKEN}"
stage_final="${DATABASE_RESTORE_ROOT}/${artifact_stage}"
umask 077
[ ! -e "${work}" ] && [ ! -e "${stage_tmp}" ] && [ ! -e "${stage_final}" ] || {
  echo "database restore: private restore stage already exists" >&2; exit 73;
}
mkdir -p "${work}" "${stage_tmp}/neo4j" "${stage_tmp}/weaviate"
published=0
DOWNLOAD_PID=
DOWNLOAD_FIFO=
stop_download() {
  if [ -n "${DOWNLOAD_PID}" ]; then
    kill "${DOWNLOAD_PID}" 2>/dev/null || true
    wait "${DOWNLOAD_PID}" 2>/dev/null || true
    DOWNLOAD_PID=
  fi
  if [ -n "${DOWNLOAD_FIFO}" ]; then
    rm -f "${DOWNLOAD_FIFO}"
    DOWNLOAD_FIFO=
  fi
}
cleanup_restore_stages() {
  rc=$?
  trap - EXIT HUP INT TERM
  set +e
  cleanup_rc=0
  stop_download || cleanup_rc=$?
  rm -rf "${work}" || cleanup_rc=$?
  if [ "${published}" -ne 1 ]; then
    rm -rf "${stage_tmp}" "${stage_final}"
    stage_cleanup_rc=$?
    if [ "${stage_cleanup_rc}" -ne 0 ] && [ "${cleanup_rc}" -eq 0 ]; then
      cleanup_rc=$stage_cleanup_rc
    fi
  fi
  if [ "${rc}" -ne 0 ]; then
    [ "${cleanup_rc}" -eq 0 ] || echo "database restore: cleanup failed with status ${cleanup_rc}; preserving primary status ${rc}" >&2
    exit "${rc}"
  fi
  exit "${cleanup_rc}"
}
trap cleanup_restore_stages EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
configure_backup_s3 "${work}/mc"

metadata_value() {
  file=$1 key=$2
  count="$(grep -c "^${key}=" "${file}" || true)"
  [ "${count}" -eq 1 ] || { echo "database restore: invalid ${key}" >&2; exit 65; }
  sed -n "s/^${key}=//p" "${file}"
}
exact_keys() {
  file=$1; shift
  expected="$(printf '%s\n' "$@" | sort)"
  actual="$(sed -n 's/=.*//p' "${file}" | sort)"
  [ "${actual}" = "${expected}" ] || { echo "database restore: unexpected signed metadata fields" >&2; exit 65; }
}
verify_signed_metadata() {
  file=$1 payload="${file}.payload"
  [ "$(tail -n 1 "${file}" | cut -d= -f1)" = hmac_sha256 ] || {
    echo "database restore: signature must be the final field" >&2; exit 65;
  }
  expected="$(metadata_value "${file}" hmac_sha256)"
  sed '$d' "${file}" >"${payload}"
  output="$(run_bounded openssl dgst -sha256 -mac HMAC -macopt "hexkey:${BACKUP_MANIFEST_HMAC_KEY}" "${payload}")"
  actual=${output##* }
  [ "${actual}" = "${expected}" ] || { echo "database restore: metadata authentication failed" >&2; exit 65; }
  rm -f "${payload}"
}
download_bounded() {
  object=$1 destination=$2 maximum=$3 expected=${4:-}
  DOWNLOAD_FIFO="${work}/download.fifo"
  rm -f "${DOWNLOAD_FIFO}"
  mkfifo "${DOWNLOAD_FIFO}"
  backup_s3_stream_command "${TIMEOUT_SECONDS}" mc cat "${object}" \
    >"${DOWNLOAD_FIFO}" &
  DOWNLOAD_PID=$!
  if ! run_bounded head -c "$((maximum + 1))" \
      <"${DOWNLOAD_FIFO}" >"${destination}"; then
    stop_download
    return 1
  fi
  actual="$(run_bounded wc -c <"${destination}" | tr -d '[:space:]')"
  if [ "${actual}" -gt "${maximum}" ]; then
    stop_download
    echo "database restore: object exceeds signed size" >&2
    exit 65
  fi
  if ! wait "${DOWNLOAD_PID}"; then
    DOWNLOAD_PID=
    rm -f "${DOWNLOAD_FIFO}"
    DOWNLOAD_FIFO=
    echo "database restore: object download failed: ${object}" >&2
    return 1
  fi
  DOWNLOAD_PID=
  rm -f "${DOWNLOAD_FIFO}"
  DOWNLOAD_FIFO=
  [ -z "${expected}" ] || [ "${actual}" = "${expected}" ] || {
    echo "database restore: object size does not match signed size" >&2; exit 65;
  }
}
safe_archive() {
  archive=$1 listing=$2
  run_bounded tar tzf "${archive}" >"${listing}"
  [ -s "${listing}" ] || { echo "database restore: empty archive" >&2; exit 65; }
  if grep -Eq '(^/|(^|/)\.\.(/|$))' "${listing}"; then
    echo "database restore: unsafe archive path" >&2; exit 65
  fi
  run_bounded tar tvzf "${archive}" >"${listing}.verbose"
  if awk 'substr($0,1,1) != "-" && substr($0,1,1) != "d" { bad=1 } END { exit bad ? 0 : 1 }' "${listing}.verbose"; then
    echo "database restore: unsafe archive link type or target" >&2; exit 65
  fi
}

download_bounded "s3/${BUCKET}/${BACKUP_TIMESTAMP}/databases.complete" "${work}/databases.complete" 2048
exact_keys "${work}/databases.complete" completion_format snapshot_state backup_timestamp backup_id manifest_sha256 manifest_bytes hmac_sha256
verify_signed_metadata "${work}/databases.complete"
[ "$(metadata_value "${work}/databases.complete" completion_format)" = 1 ] || { echo "database restore: completion format mismatch" >&2; exit 65; }
[ "$(metadata_value "${work}/databases.complete" snapshot_state)" = complete ] || { echo "database restore: incomplete publication" >&2; exit 65; }
[ "$(metadata_value "${work}/databases.complete" backup_timestamp)" = "${BACKUP_TIMESTAMP}" ] || { echo "database restore: timestamp mismatch" >&2; exit 65; }
backup_id="$(metadata_value "${work}/databases.complete" backup_id)"
case "${backup_id}" in *[!0-9a-f]*|'') echo "database restore: invalid backup id" >&2; exit 65;; esac
[ "${#backup_id}" -eq 32 ] || { echo "database restore: invalid backup id" >&2; exit 65; }
manifest_bytes="$(metadata_value "${work}/databases.complete" manifest_bytes)"
case "${manifest_bytes}" in ''|*[!0-9]*|0|0*) echo "database restore: invalid manifest size" >&2; exit 65;; esac
[ "${manifest_bytes}" -le 8192 ] || { echo "database restore: manifest too large" >&2; exit 65; }
prefix="s3/${BUCKET}/${BACKUP_TIMESTAMP}/${backup_id}"
download_bounded "${prefix}/databases.manifest" "${work}/databases.manifest" "${manifest_bytes}" "${manifest_bytes}"
[ "$(database_sha256 "${work}/databases.manifest")" = "$(metadata_value "${work}/databases.complete" manifest_sha256)" ] || {
  echo "database restore: manifest checksum mismatch" >&2; exit 65;
}
exact_keys "${work}/databases.manifest" \
  format_version snapshot_state backup_timestamp backup_id deployment_id_hex \
  neo4j_image neo4j_version neo4j_state neo4j_started_at neo4j_completed_at \
  neo4j_archive_sha256 neo4j_archive_bytes weaviate_image weaviate_version \
  weaviate_state weaviate_snapshot_id weaviate_started_at \
  weaviate_completed_at weaviate_archive_sha256 weaviate_archive_bytes hmac_sha256
verify_signed_metadata "${work}/databases.manifest"
[ "$(metadata_value "${work}/databases.manifest" format_version)" = 1 ] || { echo "database restore: manifest format mismatch" >&2; exit 65; }
[ "$(metadata_value "${work}/databases.manifest" snapshot_state)" = complete ] || { echo "database restore: incomplete snapshot" >&2; exit 65; }
[ "$(metadata_value "${work}/databases.manifest" backup_timestamp)" = "${BACKUP_TIMESTAMP}" ] || { echo "database restore: manifest timestamp mismatch" >&2; exit 65; }
[ "$(metadata_value "${work}/databases.manifest" backup_id)" = "${backup_id}" ] || { echo "database restore: backup identity mismatch" >&2; exit 65; }
deployment_hex="$(printf '%s' "${BACKUP_DEPLOYMENT_ID}" | od -An -v -tx1 | tr -d '[:space:]')"
[ "$(metadata_value "${work}/databases.manifest" deployment_id_hex)" = "${deployment_hex}" ] || { echo "database restore: deployment identity mismatch" >&2; exit 65; }
[ "$(metadata_value "${work}/databases.manifest" neo4j_image)" = "${EXPECTED_NEO4J_IMAGE}" ] && \
  [ "$(metadata_value "${work}/databases.manifest" neo4j_version)" = "${EXPECTED_NEO4J_VERSION}" ] || {
  echo "database restore: Neo4j exact-version mismatch" >&2; exit 65;
}
[ "$(metadata_value "${work}/databases.manifest" weaviate_image)" = "${EXPECTED_WEAVIATE_IMAGE}" ] && \
  [ "$(metadata_value "${work}/databases.manifest" weaviate_version)" = "${EXPECTED_WEAVIATE_VERSION}" ] || {
  echo "database restore: Weaviate exact-version mismatch" >&2; exit 65;
}

neo4j_state="$(metadata_value "${work}/databases.manifest" neo4j_state)"
weaviate_state="$(metadata_value "${work}/databases.manifest" weaviate_state)"
case "${neo4j_state}:${weaviate_state}" in
  complete:complete|complete:disabled|disabled:complete|disabled:disabled) ;;
  *) echo "database restore: invalid database state enum" >&2; exit 65;;
esac
[ "${BACKUP_NEO4J_SOURCE}" = container ] && expected_neo=complete || expected_neo=disabled
[ "${BACKUP_WEAVIATE_SOURCE}" = container ] && expected_weaviate=complete || expected_weaviate=disabled
[ "${neo4j_state}" = "${expected_neo}" ] && [ "${weaviate_state}" = "${expected_weaviate}" ] || {
  echo "database restore: signed states do not match selected sources" >&2; exit 65;
}

for database in neo4j weaviate; do
  case "${database}" in
    neo4j) state=${neo4j_state} ;;
    weaviate) state=${weaviate_state} ;;
  esac
  started="$(metadata_value "${work}/databases.manifest" "${database}_started_at")"
  completed="$(metadata_value "${work}/databases.manifest" "${database}_completed_at")"
  if [ "${state}" = complete ]; then
    if ! valid_iso "${started}" || ! valid_iso "${completed}" || \
      [ "$(printf '%s\n%s\n' "${started}" "${completed}" | sort | head -n 1)" != "${started}" ]; then
      echo "database restore: invalid ${database} timestamp ordering" >&2; exit 65;
    fi
    bytes="$(metadata_value "${work}/databases.manifest" "${database}_archive_bytes")"
    case "${bytes}" in ''|*[!0-9]*|0|0*) echo "database restore: invalid ${database} archive size" >&2; exit 65;; esac
    [ "${bytes}" -le "${BACKUP_MAX_DATABASE_ARCHIVE_BYTES}" ] || { echo "database restore: ${database} archive exceeds limit" >&2; exit 65; }
    download_bounded "${prefix}/${database}.snapshot.tar.gz" "${work}/${database}.snapshot.tar.gz" "${bytes}" "${bytes}"
    [ "$(database_sha256 "${work}/${database}.snapshot.tar.gz")" = "$(metadata_value "${work}/databases.manifest" "${database}_archive_sha256")" ] || {
      echo "database restore: ${database} archive checksum mismatch" >&2; exit 65;
    }
    safe_archive "${work}/${database}.snapshot.tar.gz" "${work}/${database}.listing"
  else
    [ "${started}" = disabled ] && [ "${completed}" = disabled ] || { echo "database restore: disabled ${database} has timestamps" >&2; exit 65; }
  fi
done

if [ "${neo4j_state}" = complete ]; then
  run_bounded tar xzf "${work}/neo4j.snapshot.tar.gz" -C "${stage_tmp}/neo4j"
  neo_metadata="${stage_tmp}/neo4j/snapshot.metadata"
  [ -f "${neo_metadata}" ] || { echo "database restore: Neo4j inner metadata missing" >&2; exit 65; }
  neo_names="$(find "${stage_tmp}/neo4j" -mindepth 1 -maxdepth 1 -type f -exec basename {} \; | sort)"
  [ "${neo_names}" = "$(printf '%s\n' neo4j.dump snapshot.metadata system.dump | sort)" ] || {
    echo "database restore: unexpected Neo4j inner names" >&2; exit 65;
  }
  for database in system neo4j; do
    inner="${stage_tmp}/neo4j/${database}.dump"
    [ -s "${inner}" ] || { echo "database restore: Neo4j inner dump missing" >&2; exit 65; }
    inner_sha="$(database_metadata_value "${neo_metadata}" "${database}_sha256")"
    inner_bytes="$(database_metadata_value "${neo_metadata}" "${database}_bytes")"
    [ "$(database_sha256 "${inner}")" = "${inner_sha}" ] && \
      [ "$(wc -c <"${inner}" | tr -d '[:space:]')" = "${inner_bytes}" ] || {
      echo "database restore: Neo4j inner artifact integrity mismatch" >&2; exit 65;
    }
  done
fi

weaviate_snapshot_id="$(metadata_value "${work}/databases.manifest" weaviate_snapshot_id)"
if [ "${weaviate_state}" = complete ]; then
  [ "${weaviate_snapshot_id}" = "atlas-${BACKUP_TIMESTAMP}-${backup_id}" ] || {
    echo "database restore: Weaviate snapshot identity mismatch" >&2; exit 65;
  }
  run_bounded tar xzf "${work}/weaviate.snapshot.tar.gz" -C "${stage_tmp}/weaviate"
  if ! [ -d "${stage_tmp}/weaviate/${weaviate_snapshot_id}" ] || \
    ! find "${stage_tmp}/weaviate/${weaviate_snapshot_id}" -type f -size +0c -print -quit | grep -q .; then
    echo "database restore: Weaviate snapshot is empty" >&2; exit 65;
  fi
else
  [ "${weaviate_snapshot_id}" = disabled ] || {
    echo "database restore: disabled Weaviate has snapshot data" >&2; exit 65;
  }
fi

cat >"${stage_tmp}/restore-set.complete" <<EOF
restore_format=1
backup_timestamp=${BACKUP_TIMESTAMP}
backup_id=${backup_id}
restore_token=${BACKUP_RESTORE_TOKEN}
neo4j_state=${neo4j_state}
weaviate_state=${weaviate_state}
weaviate_snapshot_id=${weaviate_snapshot_id}
EOF
chmod -R go-rwx "${stage_tmp}"
mv "${stage_tmp}" "${stage_final}"
published=1
printf 'ATLAS_DATABASE_RESTORE_PLAN backup_timestamp=%s restore_token=%s backup_id=%s neo4j_state=%s weaviate_state=%s artifact_stage=%s weaviate_snapshot_id=%s\n' \
  "${BACKUP_TIMESTAMP}" "${BACKUP_RESTORE_TOKEN}" "${backup_id}" "${neo4j_state}" "${weaviate_state}" "${artifact_stage}" "${weaviate_snapshot_id}"
echo "database restore: authenticated private restore set staged"
