#!/bin/sh
# Cross-service backup: Postgres dump + named-volume tarballs -> S3 (MinIO or external).
# One-shot; intended to be invoked via `docker compose run --rm backup`.
set -eu

: "${SUPABASE_DB_USER:?required}"; : "${SUPABASE_DB_PASSWORD:?required}"; : "${SUPABASE_DB_NAME:?required}"
: "${MINIO_ROOT_USER:?required}"; : "${MINIO_ROOT_PASSWORD:?required}"
BUCKET="${BACKUP_BUCKET:-atlas-backups}"
TS="${BACKUP_TIMESTAMP:-$(date +%Y%m%d_%H%M%S)}"
TIMEOUT_SECONDS="${BACKUP_COMMAND_TIMEOUT_SECONDS:-900}"
case "$TIMEOUT_SECONDS" in
  ''|*[!0-9]*|0)
    echo "backup: BACKUP_COMMAND_TIMEOUT_SECONDS must be a positive integer" >&2
    exit 64
    ;;
esac
run_bounded() {
  timeout -s TERM -k 10 "$TIMEOUT_SECONDS" "$@"
}
WORK=/tmp/backup
rm -rf "$WORK" && mkdir -p "$WORK"

echo "backup: pg_dump ${SUPABASE_DB_NAME}..."
run_bounded env PGPASSWORD="$SUPABASE_DB_PASSWORD" pg_dump -h supabase-db -U "$SUPABASE_DB_USER" -d "$SUPABASE_DB_NAME" -Fc -f "$WORK/postgres.dump"

echo "backup: snapshot mounted volumes..."
# Volumes to snapshot are bind-mounted read-only at /volumes/<name> by the fragment.
vols=0
for d in /volumes/*; do
  [ -d "$d" ] || continue
  name="$(basename "$d")"
  run_bounded tar czf "$WORK/${name}.tar.gz" -C "$d" .
  vols=$((vols + 1))
  echo "backup: archived ${name}"
done
[ "$vols" -gt 0 ] || echo "backup: WARNING — no volumes found under /volumes/* (only the Postgres dump was captured)" >&2

echo "backup: push to s3://${BUCKET}/${TS}/..."
run_bounded mc alias set s3 "${BACKUP_S3_ALIAS_URL:-http://minio:9000}" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
run_bounded mc mb --ignore-existing "s3/${BUCKET}"
run_bounded mc cp --recursive "$WORK/" "s3/${BUCKET}/${TS}/"
echo "backup: done -> s3/${BUCKET}/${TS}/"
