#!/bin/sh
# Shared entrypoint for the backup runner.
#
# Ensures the MinIO client (`mc`) is present, then execs the requested script.
# Running the bootstrap here (in the entrypoint, not in `command`) means it also
# applies when the command is overridden for a restore:
#
#   docker compose run --rm backup /scripts/restore-postgres.sh
#
# If the bootstrap lived in `command` (as it used to), overriding the command to
# run the restore script silently dropped it, so `mc` was never installed and
# restore failed at the first `mc alias set` with `mc: not found`.
#
# Both this entrypoint and the target script are invoked via `sh` so they work
# regardless of whether the bind-mounted files carry the executable bit (the
# scripts are mounted read-only and git stores them mode 0644).
#
# Alpine's `minio-client` package installs the binary as `mcli`, not `mc`; we
# symlink it so backup-all.sh / restore-postgres.sh can call `mc` unchanged.
set -e
if [ "${BACKUP_SOURCE:-disabled}" != "container" ]; then
    echo "backup: disabled; set BACKUP_SOURCE=container before running backup or restore" >&2
    exit 64
fi

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

if ! command -v mc >/dev/null 2>&1; then
    run_bounded apk add --no-cache minio-client
    ln -sf /usr/bin/mcli /usr/local/bin/mc
fi
exec sh "$@"
