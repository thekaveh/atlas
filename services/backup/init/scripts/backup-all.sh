#!/bin/sh
# Cross-service backup: PostgreSQL dump + completed snapshots -> S3.
# One-shot; normally invoked by the host quiesce wrapper. Direct container
# execution is supported only with BACKUP_DATABASES=false.
set -eu

: "${SUPABASE_DB_USER:?required}"; : "${SUPABASE_DB_PASSWORD:?required}"; : "${SUPABASE_DB_NAME:?required}"
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
S3_CLIENT_SCRIPT=${BACKUP_S3_CLIENT_SCRIPT:-${SCRIPT_DIR}/s3-client.sh}
if [ ! -r "$S3_CLIENT_SCRIPT" ] && [ -r "${PWD}/services/backup/init/scripts/s3-client.sh" ]; then
  S3_CLIENT_SCRIPT=${PWD}/services/backup/init/scripts/s3-client.sh
fi
[ -r "$S3_CLIENT_SCRIPT" ] || { echo "backup: S3 client configuration is unavailable" >&2; exit 64; }
# shellcheck disable=SC1090 # runtime override is intentional and readability-checked above.
. "$S3_CLIENT_SCRIPT"
prepare_backup_s3 backup
BACKUP_DATABASES=${BACKUP_DATABASES:-true}
case "$BACKUP_DATABASES" in true|false) ;; *) echo "backup: BACKUP_DATABASES must be true or false" >&2; exit 64;; esac
if [ "$BACKUP_DATABASES" = true ]; then
  DATABASE_SNAPSHOT_SCRIPT=${BACKUP_DATABASE_SNAPSHOT_SCRIPT:-${SCRIPT_DIR}/database-snapshots.sh}
  [ -r "$DATABASE_SNAPSHOT_SCRIPT" ] || { echo "backup: database snapshot contract is unavailable" >&2; exit 64; }
  # shellcheck disable=SC1090 # runtime override is intentional and readability-checked above.
  . "$DATABASE_SNAPSHOT_SCRIPT"
fi
: "${BACKUP_MANIFEST_HMAC_KEY:=}"
: "${BACKUP_DEPLOYMENT_ID:=}"
case "$BACKUP_MANIFEST_HMAC_KEY" in
  *[!0-9a-f]*|'') echo "backup: BACKUP_MANIFEST_HMAC_KEY must be exactly 64 lowercase hex characters" >&2; exit 64 ;;
esac
[ "${#BACKUP_MANIFEST_HMAC_KEY}" -eq 64 ] || { echo "backup: BACKUP_MANIFEST_HMAC_KEY must be exactly 64 lowercase hex characters" >&2; exit 64; }
case "$BACKUP_DEPLOYMENT_ID" in
  *[!A-Za-z0-9._-]*|'') echo "backup: BACKUP_DEPLOYMENT_ID must use only letters, digits, dot, underscore, and hyphen" >&2; exit 64 ;;
esac
[ "${#BACKUP_DEPLOYMENT_ID}" -le 128 ] || { echo "backup: BACKUP_DEPLOYMENT_ID must be at most 128 characters" >&2; exit 64; }
BUCKET="${BACKUP_BUCKET:-atlas-backups}"
TS="${BACKUP_TIMESTAMP:-$(date +%Y%m%d_%H%M%S)}"
TIMEOUT_SECONDS="${BACKUP_COMMAND_TIMEOUT_SECONDS:-900}"
case "$TIMEOUT_SECONDS" in
  ''|*[!0-9]*|0|0*)
    echo "backup: BACKUP_COMMAND_TIMEOUT_SECONDS must be a canonical positive integer" >&2
    exit 64
    ;;
esac
if ! [ "$TIMEOUT_SECONDS" -le 86400 ] 2>/dev/null; then
  echo "backup: BACKUP_COMMAND_TIMEOUT_SECONDS must be at most 86400" >&2
  exit 64
fi
run_bounded() {
  timeout -s TERM -k 10 "$TIMEOUT_SECONDS" "$@"
}
MAX_DUMP_BYTES="${BACKUP_MAX_POSTGRES_DUMP_BYTES:-10737418240}"
case "$MAX_DUMP_BYTES" in
  ''|*[!0-9]*|0|0*) echo "backup: BACKUP_MAX_POSTGRES_DUMP_BYTES must be a canonical positive integer" >&2; exit 64 ;;
esac
[ "$MAX_DUMP_BYTES" -le 1099511627776 ] 2>/dev/null || { echo "backup: BACKUP_MAX_POSTGRES_DUMP_BYTES must be at most 1099511627776" >&2; exit 64; }
valid_timestamp() {
  value=$1
  case "$value" in [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9]) ;; *) return 1;; esac
  year="$(printf '%s' "$value" | cut -c1-4)"; month="$(printf '%s' "$value" | cut -c5-6)"; day="$(printf '%s' "$value" | cut -c7-8)"
  hour="$(printf '%s' "$value" | cut -c10-11)"; minute="$(printf '%s' "$value" | cut -c12-13)"; second="$(printf '%s' "$value" | cut -c14-15)"
  year_num="$(printf '%s' "$year" | sed 's/^0*//')"; year_num="${year_num:-0}"
  month_num="$(printf '%s' "$month" | sed 's/^0*//')"; month_num="${month_num:-0}"
  day_num="$(printf '%s' "$day" | sed 's/^0*//')"; day_num="${day_num:-0}"
  hour_num="$(printf '%s' "$hour" | sed 's/^0*//')"; hour_num="${hour_num:-0}"
  minute_num="$(printf '%s' "$minute" | sed 's/^0*//')"; minute_num="${minute_num:-0}"
  second_num="$(printf '%s' "$second" | sed 's/^0*//')"; second_num="${second_num:-0}"
  [ "$year_num" -ge 1 ] && [ "$month_num" -ge 1 ] && [ "$month_num" -le 12 ] && [ "$hour_num" -le 23 ] && [ "$minute_num" -le 59 ] && [ "$second_num" -le 59 ] || return 1
  case "$month_num" in 1|3|5|7|8|10|12) max_day=31;; 4|6|9|11) max_day=30;; 2) max_day=28; if { [ $((year_num % 4)) -eq 0 ] && [ $((year_num % 100)) -ne 0 ]; } || [ $((year_num % 400)) -eq 0 ]; then max_day=29; fi;; esac
  [ "$day_num" -ge 1 ] && [ "$day_num" -le "$max_day" ]
}
valid_timestamp "$TS" || { echo "backup: invalid backup timestamp: ${TS}" >&2; exit 64; }
backup_id="$(od -An -N16 -v -tx1 /dev/urandom | tr -d '[:space:]')"
case "$backup_id" in *[!0-9a-f]*|'') echo "backup: could not generate backup identity" >&2; exit 1;; esac
[ "${#backup_id}" -eq 32 ] || { echo "backup: could not generate backup identity" >&2; exit 1; }
WORK="/tmp/atlas-backup-${backup_id}"
rm -rf "$WORK" && mkdir -p "$WORK"
COMPLETE="/tmp/atlas-backup-complete-${backup_id}"
SNAPSHOT_PID=""
SNAPSHOT_HOLD_SECONDS=$((TIMEOUT_SECONDS * 3))
BACKUP_LOCK_APP="atlas-backup-publication-${backup_id}"
BACKUP_LOCK_PID=""
BACKUP_LOCK_STATUS="${WORK}/publication-lock.status"
BACKUP_LOCK_HOLD_SECONDS=$((TIMEOUT_SECONDS * 64 + 60))

close_snapshot() {
  if [ -n "$SNAPSHOT_PID" ]; then
    kill "$SNAPSHOT_PID" 2>/dev/null || true
    wait "$SNAPSHOT_PID" 2>/dev/null || true
    SNAPSHOT_PID=""
  fi
}
release_backup_lock() {
  if [ -n "$BACKUP_LOCK_PID" ]; then
    run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
      -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
      -v ON_ERROR_STOP=1 -v lock_app="$BACKUP_LOCK_APP" <<'SQL' >/dev/null 2>&1 || true
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE application_name = :'lock_app' AND pid <> pg_backend_pid();
SQL
    kill "$BACKUP_LOCK_PID" 2>/dev/null || true
    wait "$BACKUP_LOCK_PID" 2>/dev/null || true
    BACKUP_LOCK_PID=""
  fi
}
verify_backup_lock() {
  owned="$(run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
    -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
    -v ON_ERROR_STOP=1 -v lock_app="$BACKUP_LOCK_APP" -At <<'SQL'
SELECT count(*)
FROM pg_locks AS l
JOIN pg_stat_activity AS a ON a.pid = l.pid
WHERE l.locktype = 'advisory' AND l.granted
  AND a.application_name = :'lock_app';
SQL
)" || return 1
  [ "$owned" = 1 ]
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
  close_snapshot; record_cleanup_failure "$?"
  release_backup_lock; record_cleanup_failure "$?"
  rm -f "$COMPLETE"; record_cleanup_failure "$?"
  rm -rf "$WORK/mc"; record_cleanup_failure "$?"
  if [ "$rc" -ne 0 ]; then
    [ "$cleanup_rc" -eq 0 ] || echo "backup: cleanup failed with status ${cleanup_rc}; preserving primary status ${rc}" >&2
    exit "$rc"
  fi
  exit "$cleanup_rc"
}
trap cleanup 0
trap 'exit 130' 1 2 15

configure_backup_s3 "$WORK/mc"
run_bounded mc mb --region "$BACKUP_S3_REGION" --ignore-existing "s3/${BUCKET}"
timeout -s TERM -k 10 "$BACKUP_LOCK_HOLD_SECONDS" \
  env PGPASSWORD="$SUPABASE_DB_PASSWORD" PGAPPNAME="$BACKUP_LOCK_APP" \
  psql -X -h supabase-db -U "$SUPABASE_DB_USER" -d template1 \
  -v ON_ERROR_STOP=1 -qAt >"$BACKUP_LOCK_STATUS" 2>/dev/null <<SQL &
SELECT pg_try_advisory_lock(hashtextextended('atlas-backup-publication', 0)) AS locked \gset
\if :locked
\echo locked
SELECT pg_sleep(${BACKUP_LOCK_HOLD_SECONDS});
\else
\echo busy
\quit 75
\endif
SQL
BACKUP_LOCK_PID=$!
lock_attempt=0
while :; do
  lock_status="$(run_bounded sed -n '1p' "$BACKUP_LOCK_STATUS")"
  case "$lock_status" in
    locked) break ;;
    busy)
      wait "$BACKUP_LOCK_PID" 2>/dev/null || true
      BACKUP_LOCK_PID=""
      echo "backup: another backup publication is already in progress" >&2
      exit 75
      ;;
    '') ;;
    *) echo "backup: could not determine backup publication lock state" >&2; exit 1 ;;
  esac
  if ! kill -0 "$BACKUP_LOCK_PID" 2>/dev/null; then
    # A contended psql session writes `busy` and exits immediately.  It can
    # finish between the read above and this liveness probe, so reap it and
    # perform one final status read before classifying the exit as a crash.
    wait "$BACKUP_LOCK_PID" 2>/dev/null || true
    BACKUP_LOCK_PID=""
    lock_status="$(run_bounded sed -n '1p' "$BACKUP_LOCK_STATUS")"
    if [ "$lock_status" = busy ]; then
      echo "backup: another backup publication is already in progress" >&2
      exit 75
    fi
    echo "backup: backup publication lock session exited during acquisition" >&2
    exit 1
  fi
  lock_attempt=$((lock_attempt + 1))
  [ "$lock_attempt" -lt 100 ] || { echo "backup: timed out acquiring backup publication lock" >&2; exit 1; }
  sleep 0.1
done
verify_backup_lock || { echo "backup: lost backup publication lock before prefix inspection" >&2; exit 1; }
prefix_listing="$(run_bounded mc ls --recursive "s3/${BUCKET}/${TS}/")" || { echo "backup: cannot inspect destination prefix" >&2; exit 1; }
[ -z "$prefix_listing" ] || { echo "backup: refusing to reuse existing destination prefix s3/${BUCKET}/${TS}/" >&2; exit 1; }

echo "backup: export one repeatable-read snapshot..."
timeout -s TERM -k 10 "$SNAPSHOT_HOLD_SECONDS" \
  env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
  -h supabase-db -U "$SUPABASE_DB_USER" -d "$SUPABASE_DB_NAME" \
  -v ON_ERROR_STOP=1 -qAt >"$WORK/postgres.snapshot" <<SQL &
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT pg_export_snapshot();
SELECT pg_sleep(${SNAPSHOT_HOLD_SECONDS});
ROLLBACK;
SQL
SNAPSHOT_PID=$!
snapshot_attempt=0
snapshot_id=""
while :; do
  snapshot_id="$(run_bounded sed -n '1p' "$WORK/postgres.snapshot")"
  case "$snapshot_id" in
    '') ;;
    *[!0-9A-Fa-f-]*) echo "backup: exported snapshot identifier was invalid" >&2; exit 1 ;;
    *) break ;;
  esac
  kill -0 "$SNAPSHOT_PID" 2>/dev/null || { echo "backup: snapshot holder exited before export" >&2; exit 1; }
  snapshot_attempt=$((snapshot_attempt + 1))
  [ "$snapshot_attempt" -lt 100 ] || { echo "backup: timed out exporting database snapshot" >&2; exit 1; }
  sleep 0.1
done

echo "backup: pg_dump ${SUPABASE_DB_NAME}..."
run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" pg_dump -h supabase-db -U "$SUPABASE_DB_USER" -d "$SUPABASE_DB_NAME" --snapshot="$snapshot_id" -Fc -f "$WORK/postgres.dump"
dump_bytes="$(run_bounded wc -c <"$WORK/postgres.dump" | tr -d '[:space:]')"
case "$dump_bytes" in ''|*[!0-9]*) echo "backup: invalid PostgreSQL dump size" >&2; exit 1;; esac
[ "$dump_bytes" -le "$MAX_DUMP_BYTES" ] || { echo "backup: PostgreSQL dump exceeds BACKUP_MAX_POSTGRES_DUMP_BYTES" >&2; exit 1; }

echo "backup: create PostgreSQL snapshot and archive inventories..."
run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
  -h supabase-db -U "$SUPABASE_DB_USER" -d "$SUPABASE_DB_NAME" \
  -v ON_ERROR_STOP=1 -v snapshot_id="$snapshot_id" -qAt <<'SQL' >"$WORK/postgres.tables"
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET TRANSACTION SNAPSHOT :'snapshot_id';
SELECT encode(convert_to(n.nspname, 'UTF8'), 'hex') || E'\t' ||
       encode(convert_to(c.relname, 'UTF8'), 'hex')
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname !~ '^pg_toast'
ORDER BY n.nspname, c.relname;
COMMIT;
SQL
close_snapshot
table_count="$(run_bounded wc -l <"$WORK/postgres.tables" | tr -d '[:space:]')"
case "$table_count" in
  ''|*[!0-9]*|0)
    echo "backup: refusing PostgreSQL backup with an empty user-table inventory" >&2
    exit 1
    ;;
esac
run_bounded pg_restore --list "$WORK/postgres.dump" >"$WORK/postgres.objects.raw"
# shellcheck disable=SC2016 # awk fields, not shell variables
run_bounded awk '
  NF && $1 !~ /^;/ {
    sub(/^[[:space:]]+/, "")
    sub(/[[:space:]]+$/, "")
    print
  }
' "$WORK/postgres.objects.raw" >"$WORK/postgres.objects"
object_count="$(run_bounded wc -l <"$WORK/postgres.objects" | tr -d '[:space:]')"
case "$object_count" in
  ''|*[!0-9]*|0)
    echo "backup: refusing PostgreSQL backup with an empty archive object inventory" >&2
    exit 1
    ;;
esac
database_name_hex="$(run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
  -h supabase-db -U "$SUPABASE_DB_USER" -d "$SUPABASE_DB_NAME" \
  -v ON_ERROR_STOP=1 -Atqc "SELECT encode(convert_to(current_database(), 'UTF8'), 'hex')")"
server_version_num="$(run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" psql -X \
  -h supabase-db -U "$SUPABASE_DB_USER" -d "$SUPABASE_DB_NAME" \
  -v ON_ERROR_STOP=1 -Atqc "SHOW server_version_num")"
dump_sha_output="$(run_bounded sha256sum "$WORK/postgres.dump")"; dump_sha256="${dump_sha_output%% *}"
tables_sha_output="$(run_bounded sha256sum "$WORK/postgres.tables")"; tables_sha256="${tables_sha_output%% *}"
objects_sha_output="$(run_bounded sha256sum "$WORK/postgres.objects")"; objects_sha256="${objects_sha_output%% *}"
tables_bytes="$(run_bounded wc -c <"$WORK/postgres.tables" | tr -d '[:space:]')"
objects_bytes="$(run_bounded wc -c <"$WORK/postgres.objects" | tr -d '[:space:]')"
[ "$tables_bytes" -le 8388608 ] || { echo "backup: table inventory exceeds restore limit" >&2; exit 1; }
[ "$objects_bytes" -le 16777216 ] || { echo "backup: object inventory exceeds restore limit" >&2; exit 1; }
deployment_id_hex="$(printf '%s' "$BACKUP_DEPLOYMENT_ID" | od -An -v -tx1 | tr -d '[:space:]')"
manifest_bytes=0
completion_bytes=0
iteration=0
while [ "$iteration" -lt 10 ]; do
  cat >"$WORK/postgres.manifest.payload" <<EOF
format_version=3
backup_timestamp=${TS}
backup_id=${backup_id}
deployment_id_hex=${deployment_id_hex}
database_name_hex=${database_name_hex}
dump_sha256=${dump_sha256}
dump_bytes=${dump_bytes}
tables_sha256=${tables_sha256}
tables_bytes=${tables_bytes}
table_count=${table_count}
objects_sha256=${objects_sha256}
objects_bytes=${objects_bytes}
object_count=${object_count}
completion_bytes=${completion_bytes}
server_version_num=${server_version_num}
EOF
  manifest_hmac_output="$(run_bounded openssl dgst -sha256 -mac HMAC -macopt "hexkey:${BACKUP_MANIFEST_HMAC_KEY}" "$WORK/postgres.manifest.payload")"
  manifest_hmac="${manifest_hmac_output##* }"
  case "$manifest_hmac" in *[!0-9a-f]*|'') echo "backup: could not create trusted manifest HMAC" >&2; exit 1;; esac
  [ "${#manifest_hmac}" -eq 64 ] || { echo "backup: could not create trusted manifest HMAC" >&2; exit 1; }
  run_bounded cp "$WORK/postgres.manifest.payload" "$WORK/postgres.manifest"
  printf 'hmac_sha256=%s\n' "$manifest_hmac" >>"$WORK/postgres.manifest"
  new_manifest_bytes="$(run_bounded wc -c <"$WORK/postgres.manifest" | tr -d '[:space:]')"
  manifest_sha_output="$(run_bounded sha256sum "$WORK/postgres.manifest")"; manifest_sha256="${manifest_sha_output%% *}"
  cat >"$WORK/postgres.complete.payload" <<EOF
completion_format=1
backup_timestamp=${TS}
backup_id=${backup_id}
manifest_sha256=${manifest_sha256}
manifest_bytes=${new_manifest_bytes}
dump_bytes=${dump_bytes}
tables_bytes=${tables_bytes}
objects_bytes=${objects_bytes}
EOF
  completion_hmac_output="$(run_bounded openssl dgst -sha256 -mac HMAC -macopt "hexkey:${BACKUP_MANIFEST_HMAC_KEY}" "$WORK/postgres.complete.payload")"
  completion_hmac="${completion_hmac_output##* }"
  case "$completion_hmac" in *[!0-9a-f]*|'') echo "backup: could not create completion HMAC" >&2; exit 1;; esac
  run_bounded cp "$WORK/postgres.complete.payload" "$COMPLETE"
  printf 'hmac_sha256=%s\n' "$completion_hmac" >>"$COMPLETE"
  new_completion_bytes="$(run_bounded wc -c <"$COMPLETE" | tr -d '[:space:]')"
  if [ "$manifest_bytes" = "$new_manifest_bytes" ] && [ "$completion_bytes" = "$new_completion_bytes" ]; then break; fi
  manifest_bytes=$new_manifest_bytes
  completion_bytes=$new_completion_bytes
  iteration=$((iteration + 1))
done
[ "$iteration" -lt 10 ] || { echo "backup: could not stabilize signed publication metadata" >&2; exit 1; }
[ "$new_manifest_bytes" -le 4096 ] && [ "$new_completion_bytes" -le 2048 ] || { echo "backup: signed publication metadata exceeds restore limits" >&2; exit 1; }
rm -f "$WORK/postgres.manifest.payload" "$WORK/postgres.complete.payload" "$WORK/postgres.objects.raw" "$WORK/postgres.snapshot"

case "$BACKUP_DATABASES" in
  true) capture_database_snapshots "$WORK" "$TS" "$backup_id" ;;
  false) echo "backup: database snapshots explicitly disabled" >&2 ;;
  *) echo "backup: BACKUP_DATABASES must be true or false" >&2; exit 64 ;;
esac

echo "backup: snapshot Supabase Storage volume..."
if [ -d /volumes/supabase-storage ]; then
  run_bounded tar czf "$WORK/supabase-storage.tar.gz" -C /volumes/supabase-storage .
  echo "backup: archived supabase-storage"
else
  echo "backup: WARNING — Supabase Storage volume is unavailable" >&2
fi

echo "backup: push to s3://${BUCKET}/${TS}/..."
verify_backup_lock || { echo "backup: lost backup publication lock before artifact upload" >&2; exit 1; }
run_bounded mc cp --recursive "$WORK/" "s3/${BUCKET}/${TS}/${backup_id}/"
verify_backup_lock || { echo "backup: lost backup publication lock before completion publication" >&2; exit 1; }
if [ "$BACKUP_DATABASES" = true ]; then
  run_bounded mc cp "$WORK/databases.complete" "s3/${BUCKET}/${TS}/databases.complete"
fi
run_bounded mc cp "$COMPLETE" "s3/${BUCKET}/${TS}/postgres.complete"
echo "backup: done -> s3/${BUCKET}/${TS}/"
