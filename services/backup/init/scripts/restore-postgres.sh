#!/bin/sh
# Failure-atomic Postgres restore from a given (or latest) S3 backup timestamp.
set -eu
: "${SUPABASE_DB_USER:?required}"; : "${SUPABASE_DB_PASSWORD:?required}"; : "${SUPABASE_DB_NAME:?required}"
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
S3_CLIENT_SCRIPT=${BACKUP_S3_CLIENT_SCRIPT:-${SCRIPT_DIR}/s3-client.sh}
if [ ! -r "$S3_CLIENT_SCRIPT" ] && [ -r "${PWD}/services/backup/init/scripts/s3-client.sh" ]; then
  S3_CLIENT_SCRIPT=${PWD}/services/backup/init/scripts/s3-client.sh
fi
[ -r "$S3_CLIENT_SCRIPT" ] || { echo "restore: S3 client configuration is unavailable" >&2; exit 64; }
# shellcheck disable=SC1090 # runtime override is intentional and readability-checked above.
. "$S3_CLIENT_SCRIPT"
backup_s3_config_dir_is_safe() {
  backup_s3_checked_dir=${MC_CONFIG_DIR:-}
  case "$backup_s3_checked_dir" in
    /tmp/atlas-restore-s3-????????????????????????????????)
      backup_s3_checked_suffix=${backup_s3_checked_dir#/tmp/atlas-restore-s3-}
      case "$backup_s3_checked_suffix" in *[!0-9a-f]*) return 1 ;; esac
      return 0
      ;;
  esac
  return 1
}
cleanup_backup_s3_config() {
  if backup_s3_config_dir_is_safe; then
    rm -rf "$MC_CONFIG_DIR"
  fi
}
if [ "${ATLAS_BACKUP_S3_PREPARED:-0}" = "1" ]; then
  backup_s3_config_dir_is_safe && [ -d "$MC_CONFIG_DIR" ] || {
    echo "restore: prepared S3 client configuration is invalid" >&2
    exit 64
  }
  trap cleanup_backup_s3_config 0
  trap 'exit 130' 1 2 15
fi
: "${BACKUP_MANIFEST_HMAC_KEY:=}"
: "${BACKUP_DEPLOYMENT_ID:=}"
case "$BACKUP_MANIFEST_HMAC_KEY" in
  *[!0-9a-f]*|'') echo "restore: BACKUP_MANIFEST_HMAC_KEY must be exactly 64 lowercase hex characters" >&2; exit 64 ;;
esac
[ "${#BACKUP_MANIFEST_HMAC_KEY}" -eq 64 ] || { echo "restore: BACKUP_MANIFEST_HMAC_KEY must be exactly 64 lowercase hex characters" >&2; exit 64; }
case "$BACKUP_DEPLOYMENT_ID" in
  *[!A-Za-z0-9._-]*|'') echo "restore: BACKUP_DEPLOYMENT_ID must use only letters, digits, dot, underscore, and hyphen" >&2; exit 64 ;;
esac
[ "${#BACKUP_DEPLOYMENT_ID}" -le 128 ] || { echo "restore: BACKUP_DEPLOYMENT_ID must be at most 128 characters" >&2; exit 64; }
BUCKET="${BACKUP_BUCKET:-atlas-backups}"
TIMEOUT_SECONDS="${BACKUP_COMMAND_TIMEOUT_SECONDS:-900}"
case "$TIMEOUT_SECONDS" in
  ''|*[!0-9]*|0|0*)
    echo "restore: BACKUP_COMMAND_TIMEOUT_SECONDS must be a canonical positive integer" >&2
    exit 64
    ;;
esac
if ! [ "$TIMEOUT_SECONDS" -le 86400 ] 2>/dev/null; then
  echo "restore: BACKUP_COMMAND_TIMEOUT_SECONDS must be at most 86400" >&2
  exit 64
fi
GLOBAL_TIMEOUT_SECONDS="${BACKUP_RESTORE_GLOBAL_TIMEOUT_SECONDS:-28800}"
case "$GLOBAL_TIMEOUT_SECONDS" in
  ''|*[!0-9]*|0|0*)
    echo "restore: BACKUP_RESTORE_GLOBAL_TIMEOUT_SECONDS must be a canonical positive integer" >&2
    exit 64
    ;;
esac
if ! [ "$GLOBAL_TIMEOUT_SECONDS" -le 604800 ] 2>/dev/null; then
  echo "restore: BACKUP_RESTORE_GLOBAL_TIMEOUT_SECONDS must be at most 604800" >&2
  exit 64
fi
[ "$GLOBAL_TIMEOUT_SECONDS" -gt "$TIMEOUT_SECONDS" ] || {
  echo "restore: BACKUP_RESTORE_GLOBAL_TIMEOUT_SECONDS must exceed BACKUP_COMMAND_TIMEOUT_SECONDS" >&2
  exit 64
}
GLOBAL_CLEANUP_GRACE_SECONDS=$((TIMEOUT_SECONDS * 3 + 60))
run_bounded() {
  timeout -s TERM -k 10 "$TIMEOUT_SECONDS" "$@"
}
MAX_DUMP_BYTES="${BACKUP_MAX_POSTGRES_DUMP_BYTES:-10737418240}"
case "$MAX_DUMP_BYTES" in
  ''|*[!0-9]*|0|0*) echo "restore: BACKUP_MAX_POSTGRES_DUMP_BYTES must be a canonical positive integer" >&2; exit 64 ;;
esac
[ "$MAX_DUMP_BYTES" -le 1099511627776 ] 2>/dev/null || { echo "restore: BACKUP_MAX_POSTGRES_DUMP_BYTES must be at most 1099511627776" >&2; exit 64; }
MAX_CANDIDATES="${BACKUP_RESTORE_MAX_CANDIDATES:-100}"
case "$MAX_CANDIDATES" in
  ''|*[!0-9]*|0|0*) echo "restore: BACKUP_RESTORE_MAX_CANDIDATES must be a canonical positive integer" >&2; exit 64 ;;
esac
[ "$MAX_CANDIDATES" -le 1000 ] 2>/dev/null || { echo "restore: BACKUP_RESTORE_MAX_CANDIDATES must be at most 1000" >&2; exit 64; }

if [ "${BACKUP_RESTORE_MAINTENANCE_MODE:-unconfirmed}" != "confirmed" ]; then
  echo "restore: quiesce all database writers, then set BACKUP_RESTORE_MAINTENANCE_MODE=confirmed" >&2
  exit 64
fi

if [ "${ATLAS_BACKUP_S3_PREPARED:-0}" != "1" ]; then
  prepare_backup_s3 restore
  s3_config_suffix="$(od -An -N16 -v -tx1 /dev/urandom | tr -d '[:space:]')"
  case "$s3_config_suffix" in
    [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
    *) echo "restore: could not generate a safe S3 client configuration path" >&2; exit 1 ;;
  esac
  MC_CONFIG_DIR="/tmp/atlas-restore-s3-${s3_config_suffix}"
  export MC_CONFIG_DIR
  trap cleanup_backup_s3_config 0
  trap 'exit 130' 1 2 15
  configure_backup_s3 "$MC_CONFIG_DIR"
  ATLAS_BACKUP_S3_PREPARED=1
  export ATLAS_BACKUP_S3_PREPARED
fi

if [ "${ATLAS_RESTORE_GLOBAL_DEADLINE_ACTIVE:-0}" != "1" ]; then
  exec env ATLAS_RESTORE_GLOBAL_DEADLINE_ACTIVE=1 \
    timeout -s TERM -k "$GLOBAL_CLEANUP_GRACE_SECONDS" "$GLOBAL_TIMEOUT_SECONDS" sh "$0" "$@"
fi
suffix="$(od -An -N8 -tx1 /dev/urandom | tr -d '[:space:]')"
case "$suffix" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
  *) echo "restore: could not generate a safe temporary database name" >&2; exit 1 ;;
esac

TEMP_DB="atlas_restore_${suffix}"
ROLLBACK_DB="atlas_rollback_${suffix}"
LOCK_APP="atlas-restore-lock-${suffix}"
LOCK_HOLD_SECONDS=$((GLOBAL_TIMEOUT_SECONDS + GLOBAL_CLEANUP_GRACE_SECONDS))
WORK="/tmp/atlas-restore-${suffix}"
DUMP="${WORK}/postgres.dump"
COMPLETE="${WORK}/postgres.complete"
COMPLETE_PAYLOAD="${WORK}/postgres.complete.payload"
MANIFEST="${WORK}/postgres.manifest"
MANIFEST_PAYLOAD="${WORK}/postgres.manifest.payload"
TABLES="${WORK}/postgres.tables"
OBJECTS="${WORK}/postgres.objects"
CANDIDATES="${WORK}/candidates"
TEMP_CREATED=0
LOCK_PID=""
DOWNLOAD_PID=""
DOWNLOAD_FIFO=""
CUTOVER_STARTED=0
CUTOVER_COMPLETE=0

stop_download() {
  stop_rc=0
  if [ -n "$DOWNLOAD_PID" ]; then
    kill "$DOWNLOAD_PID" 2>/dev/null || true
    wait "$DOWNLOAD_PID" 2>/dev/null || true
    DOWNLOAD_PID=""
  fi
  if [ -n "$DOWNLOAD_FIFO" ]; then
    rm -f "$DOWNLOAD_FIFO" || stop_rc=$?
    DOWNLOAD_FIFO=""
  fi
  return "$stop_rc"
}

recover_cutover() {
  echo "restore: cutover recovery target=${SUPABASE_DB_NAME} staging=${TEMP_DB} rollback=${ROLLBACK_DB}" >&2
  run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
    -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
    -v ON_ERROR_STOP=1 -v target_db="$SUPABASE_DB_NAME" \
    -v temp_db="$TEMP_DB" -v rollback_db="$ROLLBACK_DB" <<'SQL'
SELECT format('ALTER DATABASE %I RENAME TO %I', :'rollback_db', :'target_db')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'target_db')
  AND EXISTS (SELECT 1 FROM pg_database WHERE datname = :'rollback_db') \gexec
SELECT 'restore: recovery state target=' ||
       EXISTS (SELECT 1 FROM pg_database WHERE datname = :'target_db') ||
       ' staging=' || EXISTS (SELECT 1 FROM pg_database WHERE datname = :'temp_db') ||
       ' rollback=' || EXISTS (SELECT 1 FROM pg_database WHERE datname = :'rollback_db');
SQL
  recovery_rc=$?
  if [ "$recovery_rc" -ne 0 ]; then
    echo "restore: CRITICAL — automatic cutover-state inspection failed; preserve all reported databases" >&2
  fi
  return "$recovery_rc"
}

cleanup() {
  rc=$?
  trap - 0 1 2 15
  set +e
  cleanup_rc=0
  record_cleanup_failure() {
    candidate=$1
    if [ "$candidate" -ne 0 ] && [ "$cleanup_rc" -eq 0 ]; then
      cleanup_rc=$candidate
    fi
  }
  stop_download; record_cleanup_failure "$?"
  if [ "$CUTOVER_STARTED" -eq 1 ] && [ "$CUTOVER_COMPLETE" -eq 0 ]; then
    recover_cutover; record_cleanup_failure "$?"
  elif [ "$TEMP_CREATED" -eq 1 ] && [ "$CUTOVER_STARTED" -eq 0 ]; then
    echo "restore: removing temporary database ${TEMP_DB}" >&2
    run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
      -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
      -v ON_ERROR_STOP=1 -v temp_db="$TEMP_DB" <<'SQL'
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = :'temp_db' AND pid <> pg_backend_pid();
SELECT format('DROP DATABASE %I', :'temp_db')
WHERE EXISTS (SELECT 1 FROM pg_database WHERE datname = :'temp_db') \gexec
SQL
    temp_cleanup_rc=$?
    if [ "$temp_cleanup_rc" -ne 0 ]; then
      echo "restore: WARNING — temporary database cleanup failed: ${TEMP_DB}" >&2
      record_cleanup_failure "$temp_cleanup_rc"
    fi
  fi
  if [ -n "$LOCK_PID" ]; then
    run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
      -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
      -v ON_ERROR_STOP=1 -v lock_app="$LOCK_APP" <<'SQL' >/dev/null
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE application_name = :'lock_app' AND pid <> pg_backend_pid();
SQL
    lock_cleanup_rc=$?
    if [ "$lock_cleanup_rc" -ne 0 ]; then
      echo "restore: WARNING — restore lock session cleanup failed" >&2
      record_cleanup_failure "$lock_cleanup_rc"
    fi
    kill "$LOCK_PID" 2>/dev/null || true
    wait "$LOCK_PID" 2>/dev/null || true
  fi
  cleanup_backup_s3_config; record_cleanup_failure "$?"
  rm -rf "$WORK"; record_cleanup_failure "$?"
  if [ "$rc" -ne 0 ]; then
    [ "$cleanup_rc" -eq 0 ] || echo "restore: cleanup failed with status ${cleanup_rc}; preserving primary status ${rc}" >&2
    exit "$rc"
  fi
  exit "$cleanup_rc"
}
trap cleanup 0
trap 'exit 130' 1 2 15

mkdir -p "$WORK"

valid_timestamp() {
  value=$1
  case "$value" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9]) ;;
    *) return 1 ;;
  esac
  year="$(printf '%s' "$value" | cut -c1-4)"
  month="$(printf '%s' "$value" | cut -c5-6)"
  day="$(printf '%s' "$value" | cut -c7-8)"
  hour="$(printf '%s' "$value" | cut -c10-11)"
  minute="$(printf '%s' "$value" | cut -c12-13)"
  second="$(printf '%s' "$value" | cut -c14-15)"
  month_num="$(printf '%s' "$month" | sed 's/^0*//')"; month_num="${month_num:-0}"
  day_num="$(printf '%s' "$day" | sed 's/^0*//')"; day_num="${day_num:-0}"
  hour_num="$(printf '%s' "$hour" | sed 's/^0*//')"; hour_num="${hour_num:-0}"
  minute_num="$(printf '%s' "$minute" | sed 's/^0*//')"; minute_num="${minute_num:-0}"
  second_num="$(printf '%s' "$second" | sed 's/^0*//')"; second_num="${second_num:-0}"
  year_num="$(printf '%s' "$year" | sed 's/^0*//')"; year_num="${year_num:-0}"
  [ "$year_num" -ge 1 ] || return 1
  [ "$month_num" -ge 1 ] && [ "$month_num" -le 12 ] || return 1
  [ "$hour_num" -le 23 ] && [ "$minute_num" -le 59 ] && [ "$second_num" -le 59 ] || return 1
  case "$month_num" in
    1|3|5|7|8|10|12) max_day=31 ;;
    4|6|9|11) max_day=30 ;;
    2)
      max_day=28
      if { [ $((year_num % 4)) -eq 0 ] && [ $((year_num % 100)) -ne 0 ]; } || [ $((year_num % 400)) -eq 0 ]; then
        max_day=29
      fi
      ;;
  esac
  [ "$day_num" -ge 1 ] && [ "$day_num" -le "$max_day" ]
}

echo "restore: phase preflight"

download_bounded() {
  object=$1; destination=$2; limit=$3
  DOWNLOAD_FIFO="${WORK}/download.fifo"
  rm -f "$DOWNLOAD_FIFO"; mkfifo "$DOWNLOAD_FIFO"
  backup_s3_stream_command "$TIMEOUT_SECONDS" mc cat "$object" >"$DOWNLOAD_FIFO" & DOWNLOAD_PID=$!
  run_bounded head -c $((limit + 1)) <"$DOWNLOAD_FIFO" >"$destination" || { stop_download; return 1; }
  bytes="$(run_bounded wc -c <"$destination" | tr -d '[:space:]')"
  if [ "$bytes" -gt "$limit" ]; then stop_download; echo "restore: object exceeds authenticated download limit: ${object}" >&2; return 1; fi
  if ! wait "$DOWNLOAD_PID"; then DOWNLOAD_PID=""; rm -f "$DOWNLOAD_FIFO"; DOWNLOAD_FIFO=""; echo "restore: object download failed: ${object}" >&2; return 1; fi
  DOWNLOAD_PID=""
  rm -f "$DOWNLOAD_FIFO"
  DOWNLOAD_FIFO=""
}
discover_candidates() {
  DOWNLOAD_FIFO="${WORK}/listing.fifo"
  rm -f "$DOWNLOAD_FIFO"; mkfifo "$DOWNLOAD_FIFO"
  backup_s3_stream_command "$TIMEOUT_SECONDS" mc ls --recursive "s3/${BUCKET}/" >"$DOWNLOAD_FIFO" & DOWNLOAD_PID=$!
  # shellcheck disable=SC2016 # awk program, not shell interpolation
  if ! run_bounded awk -v max="$MAX_CANDIDATES" '
    function add(value, i, j, position, upper) {
      for (i = 1; i <= count; i++) if (newest[i] == value) return
      position = count + 1
      for (i = 1; i <= count; i++) if (value > newest[i]) { position = i; break }
      if (position > max) return
      upper = count < max ? count + 1 : max
      for (j = upper; j > position; j--) newest[j] = newest[j - 1]
      newest[position] = value
      if (count < max) count++
    }
    $NF ~ /^[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9]\/postgres[.]complete$/ {
      value = $NF
      sub(/\/postgres[.]complete$/, "", value)
      add(value)
    }
    END { for (i = 1; i <= count; i++) print newest[i] }
  ' "$DOWNLOAD_FIFO" >"$CANDIDATES"; then
    stop_download
    return 1
  fi
  if ! wait "$DOWNLOAD_PID"; then
    DOWNLOAD_PID=""; rm -f "$DOWNLOAD_FIFO"; DOWNLOAD_FIFO=""
    return 1
  fi
  DOWNLOAD_PID=""
  rm -f "$DOWNLOAD_FIFO"
  DOWNLOAD_FIFO=""
}
metadata_value() {
  path=$1; wanted=$2
  # shellcheck disable=SC2016 # awk fields, not shell variables
  run_bounded awk -F= -v wanted="$wanted" '
    $1 == wanted { count++; value=substr($0, index($0, "=") + 1) }
    END { if (count == 1) print value; else exit 1 }
  ' "$path"
}
authenticate_candidate() {
  candidate=$1
  download_bounded "s3/${BUCKET}/${candidate}/postgres.complete" "$COMPLETE" 2048 || return 1
  [ "$(run_bounded wc -l <"$COMPLETE" | tr -d '[:space:]')" = 9 ] || return 1
  completion_format="$(metadata_value "$COMPLETE" completion_format)" || return 1
  signed_timestamp="$(metadata_value "$COMPLETE" backup_timestamp)" || return 1
  backup_id="$(metadata_value "$COMPLETE" backup_id)" || return 1
  expected_manifest_sha="$(metadata_value "$COMPLETE" manifest_sha256)" || return 1
  expected_manifest_bytes="$(metadata_value "$COMPLETE" manifest_bytes)" || return 1
  completion_dump_bytes="$(metadata_value "$COMPLETE" dump_bytes)" || return 1
  completion_tables_bytes="$(metadata_value "$COMPLETE" tables_bytes)" || return 1
  completion_objects_bytes="$(metadata_value "$COMPLETE" objects_bytes)" || return 1
  expected_completion_hmac="$(metadata_value "$COMPLETE" hmac_sha256)" || return 1
  [ "$completion_format" = 1 ] && [ "$signed_timestamp" = "$candidate" ] && valid_timestamp "$candidate" || return 1
  case "$backup_id" in *[!0-9a-f]*|'') return 1;; esac
  [ "${#backup_id}" -eq 32 ] || return 1
  case "$expected_manifest_sha:$expected_completion_hmac" in *[!0-9a-f:]*) return 1;; esac
  case "$expected_manifest_bytes:$completion_dump_bytes:$completion_tables_bytes:$completion_objects_bytes" in *[!0-9:]*) return 1;; esac
  [ "${#expected_manifest_sha}" -eq 64 ] && [ "${#expected_completion_hmac}" -eq 64 ] || return 1
  [ "$expected_manifest_bytes" -le 4096 ] && [ "$completion_dump_bytes" -le "$MAX_DUMP_BYTES" ] && [ "$completion_tables_bytes" -le 8388608 ] && [ "$completion_objects_bytes" -le 16777216 ] || return 1
  sed '$d' "$COMPLETE" >"$COMPLETE_PAYLOAD"
  completion_hmac_output="$(run_bounded openssl dgst -sha256 -mac HMAC -macopt "hexkey:${BACKUP_MANIFEST_HMAC_KEY}" "$COMPLETE_PAYLOAD")" || return 1
  [ "${completion_hmac_output##* }" = "$expected_completion_hmac" ] || return 1
  download_bounded "s3/${BUCKET}/${candidate}/${backup_id}/postgres.manifest" "$MANIFEST" "$expected_manifest_bytes" || return 1
  [ "$(run_bounded wc -c <"$MANIFEST" | tr -d '[:space:]')" = "$expected_manifest_bytes" ] || return 1
  manifest_sha_output="$(run_bounded sha256sum "$MANIFEST")"; [ "${manifest_sha_output%% *}" = "$expected_manifest_sha" ] || return 1
  [ "$(run_bounded wc -l <"$MANIFEST" | tr -d '[:space:]')" = 16 ] || return 1
  return 0
}
requested_timestamp="${BACKUP_TIMESTAMP:-}"
if [ -n "$requested_timestamp" ]; then
  valid_timestamp "$requested_timestamp" || { echo "restore: invalid backup timestamp: ${requested_timestamp}" >&2; exit 64; }
  authenticate_candidate "$requested_timestamp" || { echo "restore: selected backup is incomplete or unauthenticated: ${requested_timestamp}" >&2; exit 1; }
  TS=$requested_timestamp
else
  discover_candidates || { echo "restore: cannot list completed backups in s3/${BUCKET}/" >&2; exit 1; }
  TS=""
  while IFS= read -r candidate; do
    if authenticate_candidate "$candidate"; then TS=$candidate; break; fi
  done <"$CANDIDATES"
  [ -n "$TS" ] || { echo "restore: no authenticated completed backups found in s3/${BUCKET}/" >&2; exit 1; }
fi
echo "restore: using completed backup ${TS}"

format_version="$(metadata_value "$MANIFEST" format_version)" || { echo "restore: invalid postgres.manifest format" >&2; exit 1; }
manifest_timestamp="$(metadata_value "$MANIFEST" backup_timestamp)" || { echo "restore: invalid backup timestamp metadata" >&2; exit 1; }
manifest_backup_id="$(metadata_value "$MANIFEST" backup_id)" || { echo "restore: invalid backup identity metadata" >&2; exit 1; }
deployment_id_hex="$(metadata_value "$MANIFEST" deployment_id_hex)" || { echo "restore: invalid deployment identity metadata" >&2; exit 1; }
database_name_hex="$(metadata_value "$MANIFEST" database_name_hex)" || { echo "restore: invalid database identity metadata" >&2; exit 1; }
expected_dump_sha="$(metadata_value "$MANIFEST" dump_sha256)"; expected_dump_bytes="$(metadata_value "$MANIFEST" dump_bytes)"
expected_tables_sha="$(metadata_value "$MANIFEST" tables_sha256)"; expected_tables_bytes="$(metadata_value "$MANIFEST" tables_bytes)"
expected_table_count="$(metadata_value "$MANIFEST" table_count)"
expected_objects_sha="$(metadata_value "$MANIFEST" objects_sha256)"; expected_objects_bytes="$(metadata_value "$MANIFEST" objects_bytes)"
expected_object_count="$(metadata_value "$MANIFEST" object_count)"; expected_completion_bytes="$(metadata_value "$MANIFEST" completion_bytes)"
backup_server_version="$(metadata_value "$MANIFEST" server_version_num)"; expected_manifest_hmac="$(metadata_value "$MANIFEST" hmac_sha256)"
[ "$format_version" = 3 ] && [ "$manifest_timestamp" = "$TS" ] && [ "$manifest_backup_id" = "$backup_id" ] || { echo "restore: manifest publication identity mismatch" >&2; exit 1; }
[ "$expected_dump_bytes" = "$completion_dump_bytes" ] && [ "$expected_tables_bytes" = "$completion_tables_bytes" ] && [ "$expected_objects_bytes" = "$completion_objects_bytes" ] || { echo "restore: publication size metadata mismatch" >&2; exit 1; }
[ "$expected_completion_bytes" = "$(run_bounded wc -c <"$COMPLETE" | tr -d '[:space:]')" ] || { echo "restore: completion size mismatch" >&2; exit 1; }
case "$deployment_id_hex" in ''|*[!0-9a-f]*) echo "restore: invalid deployment identity metadata" >&2; exit 1;; esac
case "$database_name_hex" in ''|*[!0-9a-f]*) echo "restore: invalid database identity metadata" >&2; exit 1;; esac
case "$expected_dump_sha:$expected_tables_sha:$expected_objects_sha:$expected_manifest_hmac" in
  *[!0-9a-f:]*|*:*:*:*:*:*|:*) echo "restore: invalid checksum or HMAC metadata" >&2; exit 1 ;;
esac
[ "${#expected_dump_sha}" -eq 64 ] && [ "${#expected_tables_sha}" -eq 64 ] && \
  [ "${#expected_objects_sha}" -eq 64 ] && [ "${#expected_manifest_hmac}" -eq 64 ] || \
  { echo "restore: invalid checksum or HMAC metadata" >&2; exit 1; }
case "$expected_table_count" in ''|*[!0-9]*|0) echo "restore: refusing backup with empty table inventory" >&2; exit 1;; esac
case "$expected_object_count" in ''|*[!0-9]*|0) echo "restore: refusing backup with empty object inventory" >&2; exit 1;; esac
case "$backup_server_version" in ''|*[!0-9]*) echo "restore: invalid server version metadata" >&2; exit 1;; esac
target_deployment_hex="$(printf '%s' "$BACKUP_DEPLOYMENT_ID" | od -An -v -tx1 | tr -d '[:space:]')"
[ "$deployment_id_hex" = "$target_deployment_hex" ] || { echo "restore: backup deployment identity does not match target" >&2; exit 1; }
target_name_hex="$(printf '%s' "$SUPABASE_DB_NAME" | od -An -v -tx1 | tr -d '[:space:]')"
[ "$database_name_hex" = "$target_name_hex" ] || { echo "restore: backup database identity does not match target" >&2; exit 1; }
sed '$d' "$MANIFEST" >"$MANIFEST_PAYLOAD"
manifest_hmac_output="$(run_bounded openssl dgst -sha256 -mac HMAC -macopt "hexkey:${BACKUP_MANIFEST_HMAC_KEY}" "$MANIFEST_PAYLOAD")"
actual_manifest_hmac="${manifest_hmac_output##* }"
[ "$actual_manifest_hmac" = "$expected_manifest_hmac" ] || { echo "restore: trusted manifest HMAC mismatch" >&2; exit 1; }
download_bounded "s3/${BUCKET}/${TS}/${backup_id}/postgres.dump" "$DUMP" "$expected_dump_bytes" || exit 1
download_bounded "s3/${BUCKET}/${TS}/${backup_id}/postgres.tables" "$TABLES" "$expected_tables_bytes" || exit 1
download_bounded "s3/${BUCKET}/${TS}/${backup_id}/postgres.objects" "$OBJECTS" "$expected_objects_bytes" || exit 1
[ "$(run_bounded wc -c <"$DUMP" | tr -d '[:space:]')" = "$expected_dump_bytes" ] && [ "$(run_bounded wc -c <"$TABLES" | tr -d '[:space:]')" = "$expected_tables_bytes" ] && [ "$(run_bounded wc -c <"$OBJECTS" | tr -d '[:space:]')" = "$expected_objects_bytes" ] || { echo "restore: downloaded artifact size mismatch" >&2; exit 1; }
actual_dump_sha_output="$(run_bounded sha256sum "$DUMP")"; actual_dump_sha="${actual_dump_sha_output%% *}"
actual_tables_sha_output="$(run_bounded sha256sum "$TABLES")"; actual_tables_sha="${actual_tables_sha_output%% *}"
actual_objects_sha_output="$(run_bounded sha256sum "$OBJECTS")"; actual_objects_sha="${actual_objects_sha_output%% *}"
[ "$actual_dump_sha" = "$expected_dump_sha" ] || { echo "restore: dump checksum mismatch" >&2; exit 1; }
[ "$actual_tables_sha" = "$expected_tables_sha" ] || { echo "restore: table inventory checksum mismatch" >&2; exit 1; }
[ "$actual_objects_sha" = "$expected_objects_sha" ] || { echo "restore: object inventory checksum mismatch" >&2; exit 1; }
actual_table_count="$(run_bounded wc -l <"$TABLES" | tr -d '[:space:]')"
[ "$actual_table_count" = "$expected_table_count" ] || { echo "restore: table inventory count mismatch" >&2; exit 1; }
actual_object_count="$(run_bounded wc -l <"$OBJECTS" | tr -d '[:space:]')"
[ "$actual_object_count" = "$expected_object_count" ] || { echo "restore: object inventory count mismatch" >&2; exit 1; }
run_bounded pg_restore --list "$DUMP" >"$WORK/postgres.objects.raw"
# shellcheck disable=SC2016 # awk fields, not shell variables
run_bounded awk '
  NF && $1 !~ /^;/ {
    sub(/^[[:space:]]+/, "")
    sub(/[[:space:]]+$/, "")
    print
  }
' "$WORK/postgres.objects.raw" >"$WORK/postgres.objects.actual"
archive_objects_sha_output="$(run_bounded sha256sum "$WORK/postgres.objects.actual")"; archive_objects_sha="${archive_objects_sha_output%% *}"
archive_object_count="$(run_bounded wc -l <"$WORK/postgres.objects.actual" | tr -d '[:space:]')"
[ "$archive_objects_sha" = "$expected_objects_sha" ] && [ "$archive_object_count" = "$expected_object_count" ] || {
  echo "restore: archive object inventory does not match trusted manifest" >&2
  exit 1
}

# Hold a cluster-wide advisory lock for the complete staged restore.  The
# background session is itself deadline-bounded and cleanup terminates only
# this script's process.
timeout -s TERM -k 10 "$LOCK_HOLD_SECONDS" \
  env PGPASSWORD="$SUPABASE_DB_PASSWORD" PGAPPNAME="$LOCK_APP" \
  psql -X -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
  -v ON_ERROR_STOP=1 \
  -c "SELECT pg_advisory_lock(hashtextextended('atlas-backup-restore', 0))" \
  -c "SELECT pg_sleep(${LOCK_HOLD_SECONDS})" >/dev/null 2>&1 &
LOCK_PID=$!

lock_attempt=0
while :; do
  lock_status="$(run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
    -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
    -v ON_ERROR_STOP=1 -v lock_app="$LOCK_APP" -At <<'SQL'
SELECT CASE
  WHEN EXISTS (
    SELECT 1 FROM pg_locks AS l
    JOIN pg_stat_activity AS a ON a.pid = l.pid
    WHERE l.locktype = 'advisory' AND l.granted
      AND a.application_name = :'lock_app'
  ) THEN 'locked'
  WHEN EXISTS (
    SELECT 1 FROM pg_stat_activity AS a
    JOIN pg_locks AS l ON l.pid = a.pid
    WHERE a.application_name = :'lock_app'
      AND l.locktype = 'advisory' AND NOT l.granted
  ) THEN 'busy'
  ELSE 'starting'
END;
SQL
)"
  case "$lock_status" in
    locked) break ;;
    busy) echo "restore: another restore is already in progress" >&2; exit 75 ;;
    starting) ;;
    *) echo "restore: could not determine restore lock state" >&2; exit 1 ;;
  esac
  lock_attempt=$((lock_attempt + 1))
  [ "$lock_attempt" -lt 50 ] || { echo "restore: timed out acquiring restore lock" >&2; exit 1; }
  sleep 0.1
done

target_exists="$(run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
  -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
  -v ON_ERROR_STOP=1 -v target_db="$SUPABASE_DB_NAME" -At <<'SQL'
SELECT count(*) FROM pg_database WHERE datname = :'target_db';
SQL
)"
[ "$target_exists" = "1" ] || { echo "restore: target database does not exist: ${SUPABASE_DB_NAME}" >&2; exit 1; }
current_server_version="$(run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
  -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
  -v ON_ERROR_STOP=1 -Atqc "SHOW server_version_num")"
case "$current_server_version" in ''|*[!0-9]*) echo "restore: could not determine target server version" >&2; exit 1;; esac
[ $((backup_server_version / 10000)) -le $((current_server_version / 10000)) ] || {
  echo "restore: backup PostgreSQL major version is newer than target server" >&2
  exit 1
}

unsupported_state="$(run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
  -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
  -v ON_ERROR_STOP=1 -v target_db="$SUPABASE_DB_NAME" -At <<'SQL'
SELECT count(*) FROM (
  SELECT 1 FROM pg_replication_slots WHERE database = :'target_db'
  UNION ALL
  SELECT 1 FROM pg_prepared_xacts WHERE database = :'target_db'
  UNION ALL
  SELECT 1 FROM pg_subscription AS s
  JOIN pg_database AS d ON d.oid = s.subdbid
  WHERE d.datname = :'target_db'
) AS unsupported;
SQL
)"
[ "$unsupported_state" = "0" ] || { echo "restore: target has unsupported database-bound replication/subscription/prepared state" >&2; exit 1; }

locale_provider="$(run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
  -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
  -v ON_ERROR_STOP=1 -v target_db="$SUPABASE_DB_NAME" -At <<'SQL'
SELECT datlocprovider FROM pg_database WHERE datname = :'target_db';
SQL
)"
case "$locale_provider" in
  c|i|b) ;;
  *) echo "restore: unsupported target locale provider: ${locale_provider}" >&2; exit 1 ;;
esac

echo "restore: phase restore into ${TEMP_DB}"
run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
  -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
  -v ON_ERROR_STOP=1 -v temp_db="$TEMP_DB" -v target_db="$SUPABASE_DB_NAME" <<'SQL'
SELECT CASE datlocprovider
  WHEN 'c' THEN format(
    'CREATE DATABASE %I WITH TEMPLATE template0 OWNER %I ENCODING %L LC_COLLATE %L LC_CTYPE %L LOCALE_PROVIDER libc TABLESPACE %I CONNECTION LIMIT %s',
    :'temp_db', pg_get_userbyid(datdba), pg_encoding_to_char(encoding),
    datcollate, datctype, spcname, datconnlimit
  )
  WHEN 'i' THEN format(
    'CREATE DATABASE %I WITH TEMPLATE template0 OWNER %I ENCODING %L LOCALE_PROVIDER icu ICU_LOCALE %L ICU_RULES %L TABLESPACE %I CONNECTION LIMIT %s',
    :'temp_db', pg_get_userbyid(datdba), pg_encoding_to_char(encoding),
    datlocale, COALESCE(daticurules, ''), spcname, datconnlimit
  )
  WHEN 'b' THEN format(
    'CREATE DATABASE %I WITH TEMPLATE template0 OWNER %I ENCODING %L LOCALE_PROVIDER builtin BUILTIN_LOCALE %L TABLESPACE %I CONNECTION LIMIT %s',
    :'temp_db', pg_get_userbyid(datdba), pg_encoding_to_char(encoding),
    datlocale, spcname, datconnlimit
  )
END
FROM pg_database AS d
JOIN pg_tablespace AS t ON t.oid = d.dattablespace
WHERE datname = :'target_db' \gexec
SQL
TEMP_CREATED=1

run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
  -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
  -v ON_ERROR_STOP=1 -v temp_db="$TEMP_DB" -v target_db="$SUPABASE_DB_NAME" <<'SQL'
SELECT format(
  'ALTER DATABASE %I IS_TEMPLATE %s', :'temp_db',
  CASE WHEN datistemplate THEN 'true' ELSE 'false' END
)
FROM pg_database WHERE datname = :'target_db' \gexec

SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'temp_db')
FROM pg_database WHERE datname = :'target_db' AND datacl IS NOT NULL \gexec
SELECT DISTINCT format(
  'REVOKE ALL ON DATABASE %I FROM %s', :'temp_db',
  CASE acl.grantee WHEN 0 THEN 'PUBLIC' ELSE format('%I', grantee_role.rolname) END
)
FROM pg_database AS d
CROSS JOIN LATERAL aclexplode(d.datacl) AS acl
LEFT JOIN pg_roles AS grantee_role ON grantee_role.oid = acl.grantee
WHERE d.datname = :'target_db' \gexec
SELECT format(
  'GRANT %s ON DATABASE %I TO %s%s', acl.privilege_type, :'temp_db',
  CASE acl.grantee WHEN 0 THEN 'PUBLIC' ELSE format('%I', grantee_role.rolname) END,
  CASE WHEN acl.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END
)
FROM pg_database AS d
CROSS JOIN LATERAL aclexplode(d.datacl) AS acl
LEFT JOIN pg_roles AS grantee_role ON grantee_role.oid = acl.grantee
WHERE d.datname = :'target_db' \gexec

SELECT format(
  CASE WHEN s.setrole = 0
    THEN 'ALTER DATABASE %I SET %I TO %L'
    ELSE 'ALTER ROLE %I IN DATABASE %I SET %I TO %L'
  END,
  CASE WHEN s.setrole = 0 THEN :'temp_db' ELSE r.rolname END,
  CASE WHEN s.setrole = 0 THEN split_part(cfg, '=', 1) ELSE :'temp_db' END,
  CASE WHEN s.setrole = 0 THEN substring(cfg FROM position('=' IN cfg) + 1) ELSE split_part(cfg, '=', 1) END,
  CASE WHEN s.setrole = 0 THEN NULL ELSE substring(cfg FROM position('=' IN cfg) + 1) END
)
FROM pg_database AS d
JOIN pg_db_role_setting AS s ON s.setdatabase = d.oid
LEFT JOIN pg_roles AS r ON r.oid = s.setrole
CROSS JOIN LATERAL unnest(s.setconfig) AS cfg
WHERE d.datname = :'target_db' \gexec
SQL
run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" pg_restore \
  -h supabase-db -U "$SUPABASE_DB_USER" -d "$TEMP_DB" \
  --exit-on-error "$DUMP"

echo "restore: phase validate"
validation_result="$(run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
  -h supabase-db -U "$SUPABASE_DB_USER" -d "$TEMP_DB" \
  -v ON_ERROR_STOP=1 -At <<'SQL'
SELECT CASE WHEN
  NOT EXISTS (SELECT 1 FROM pg_index WHERE NOT indisvalid)
  AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE NOT convalidated)
THEN 1 ELSE 0 END;
SQL
)"
[ "$validation_result" = "1" ] || { echo "restore: validation failed in ${TEMP_DB}" >&2; exit 1; }
run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
  -h supabase-db -U "$SUPABASE_DB_USER" -d "$TEMP_DB" \
  -v ON_ERROR_STOP=1 -At <<'SQL' >"$WORK/staged.tables"
SELECT encode(convert_to(n.nspname, 'UTF8'), 'hex') || E'\t' ||
       encode(convert_to(c.relname, 'UTF8'), 'hex')
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname !~ '^pg_toast'
ORDER BY n.nspname, c.relname;
SQL
staged_tables_sha_output="$(run_bounded sha256sum "$WORK/staged.tables")"; staged_tables_sha="${staged_tables_sha_output%% *}"
staged_table_count="$(run_bounded wc -l <"$WORK/staged.tables" | tr -d '[:space:]')"
[ "$staged_tables_sha" = "$expected_tables_sha" ] && [ "$staged_table_count" = "$expected_table_count" ] || {
  echo "restore: staged table inventory does not match authenticated backup inventory" >&2
  exit 1
}

lock_still_held="$(run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
  -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
  -v ON_ERROR_STOP=1 -v lock_app="$LOCK_APP" -At <<'SQL'
SELECT count(*)
FROM pg_locks AS l
JOIN pg_stat_activity AS a ON a.pid = l.pid
WHERE l.locktype = 'advisory' AND l.granted
  AND a.application_name = :'lock_app';
SQL
)"
[ "$lock_still_held" = "1" ] || {
  echo "restore: lost advisory lock before cutover; original database remains active" >&2
  exit 1
}

echo "restore: phase cutover"
CUTOVER_STARTED=1
if ! run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
  -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
  -v target_db="$SUPABASE_DB_NAME" -v temp_db="$TEMP_DB" \
  -v rollback_db="$ROLLBACK_DB" <<'SQL'
\set ON_ERROR_STOP on
SELECT (
  EXISTS (SELECT 1 FROM pg_database WHERE datname = :'target_db')
  AND EXISTS (SELECT 1 FROM pg_database WHERE datname = :'temp_db')
  AND NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'rollback_db')
) AS cutover_ready \gset
\if :cutover_ready
\else
  \echo 'restore: cutover preconditions changed'
  SELECT 1/0;
\endif

SELECT format(
  'ALTER DATABASE %I ALLOW_CONNECTIONS %s', :'temp_db',
  CASE WHEN datallowconn THEN 'true' ELSE 'false' END
)
FROM pg_database WHERE datname = :'target_db' \gexec

SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname IN (:'target_db', :'temp_db') AND pid <> pg_backend_pid();

\set ON_ERROR_STOP off
SELECT format('ALTER DATABASE %I RENAME TO %I', :'target_db', :'rollback_db') \gexec
\set ON_ERROR_STOP on
SELECT (
  NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'target_db')
  AND EXISTS (SELECT 1 FROM pg_database WHERE datname = :'rollback_db')
  AND EXISTS (SELECT 1 FROM pg_database WHERE datname = :'temp_db')
) AS first_rename_ok \gset
\if :first_rename_ok
\else
  \echo 'restore: original database rename failed'
  SELECT 1/0;
\endif

\set ON_ERROR_STOP off
SELECT format('ALTER DATABASE %I RENAME TO %I', :'temp_db', :'target_db') \gexec
\set ON_ERROR_STOP on
SELECT (
  EXISTS (SELECT 1 FROM pg_database WHERE datname = :'target_db')
  AND EXISTS (SELECT 1 FROM pg_database WHERE datname = :'rollback_db')
  AND NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'temp_db')
) AS second_rename_ok \gset
\if :second_rename_ok
  \quit
\else
  \echo 'restore: replacement rename failed; restoring original name'
\endif

\set ON_ERROR_STOP off
SELECT format('ALTER DATABASE %I RENAME TO %I', :'rollback_db', :'target_db')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'target_db') \gexec
\set ON_ERROR_STOP on
SELECT (
  EXISTS (SELECT 1 FROM pg_database WHERE datname = :'target_db')
  AND NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'rollback_db')
) AS compensated \gset
\if :compensated
  \echo 'restore: original database name restored after failed cutover'
  SELECT 1/0;
\else
  \echo 'restore: CRITICAL — manual recovery required from rollback database'
  SELECT 1/0;
\endif
SQL
then
  echo "restore: cutover failed; original retained as ${ROLLBACK_DB} if automatic compensation was impossible" >&2
  exit 1
fi

TEMP_CREATED=0
CUTOVER_COMPLETE=1
echo "restore: done; rollback database retained as ${ROLLBACK_DB}"
