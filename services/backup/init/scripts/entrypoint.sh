#!/bin/sh
# Shared entrypoint for the backup runner.
#
# Verifies the baked, pinned MinIO client (`mc`) and OpenSSL, then execs the requested script.
# Running the check here (in the entrypoint, not in `command`) means it also
# applies when the command is overridden for a restore:
#
#   docker compose run --rm backup /scripts/restore-postgres.sh
#
# If the bootstrap lived in `command` (as it used to), overriding the command to
# run the restore script silently dropped it, so `mc` was never installed and
# restore failed at the first `mc` command with `mc: not found`. The image now
# bakes `mc` (#1288); the check stays here so every command path verifies it.
#
# Both this entrypoint and the target script are invoked via `sh` so they work
# regardless of whether the bind-mounted files carry the executable bit (the
# scripts are mounted read-only and git stores them mode 0644).
#
set -e
if [ "${BACKUP_SOURCE:-disabled}" != "container" ]; then
    echo "backup: disabled; set BACKUP_SOURCE=container before running backup or restore" >&2
    exit 64
fi

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

MC_RELEASE=RELEASE.2026-09-16T00-00-00Z
# SHA-256 of /usr/bin/mc in the digest-pinned pgsty/mc image that
# init/Dockerfile copies to /usr/local/bin/mc (#1288), for the two
# architectures supported by the postgres Alpine backup image.
MC_SHA256_AMD64=1e745aaf4684ccda198288466d122ebc5a63609247d27497a196a68511b886b8
MC_SHA256_ARM64=d89a6daf059921eb5a1728663c0fa6bd545293631528aee9e810443da99548fa

select_mc_architecture() {
    case "$(uname -m)" in
        x86_64|amd64) mc_sha256=$MC_SHA256_AMD64 ;;
        aarch64|arm64) mc_sha256=$MC_SHA256_ARM64 ;;
        *) echo "backup: unsupported architecture for pinned mc" >&2; return 64 ;;
    esac
}

verify_mc_candidate() {
    mc_candidate=$1
    mc_verify_error="checksum verification failed"
    mc_candidate_sha=$(run_bounded sha256sum "$mc_candidate") || return 1
    case "$mc_candidate_sha" in
        "$mc_sha256  "*) ;;
        *) return 1 ;;
    esac
    mc_verify_error="version verification failed"
    mc_candidate_version=$(run_bounded "$mc_candidate" --version 2>&1) || return 1
    case "$mc_candidate_version" in
        *"mc version $MC_RELEASE"*) return 0 ;;
        *) return 1 ;;
    esac
}

# The image bakes mc at build time, so backup and restore never download a
# client at run time. Fail closed rather than run a missing or different one.
require_pinned_mc() {
    select_mc_architecture || return $?
    if ! mc_path=$(command -v mc); then
        echo "backup: backup image is missing the pinned mc; rebuild it with 'docker compose build backup'" >&2
        return 69
    fi
    if ! verify_mc_candidate "$mc_path"; then
        echo "backup: pinned mc ${mc_verify_error}; rebuild the image with 'docker compose build backup'" >&2
        return 65
    fi
}

if ! command -v openssl >/dev/null 2>&1; then
    echo "backup: backup image is missing required OpenSSL" >&2
    exit 69
fi
if [ -d /proc/self/ns ] && ! command -v setsid >/dev/null 2>&1; then
    echo "backup: backup image is missing required setsid" >&2
    exit 69
fi
require_pinned_mc || exit $?
exec sh "$@"
