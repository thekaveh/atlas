#!/bin/sh
# Shared entrypoint for the backup runner.
#
# Ensures the pinned MinIO client (`mc`) and OpenSSL are present, then execs the requested script.
# Running the bootstrap here (in the entrypoint, not in `command`) means it also
# applies when the command is overridden for a restore:
#
#   docker compose run --rm backup /scripts/restore-postgres.sh
#
# If the bootstrap lived in `command` (as it used to), overriding the command to
# run the restore script silently dropped it, so `mc` was never installed and
# restore failed at the first `mc` command with `mc: not found`.
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

MC_RELEASE=RELEASE.2025-08-13T08-35-41Z
# Official MinIO GitHub release asset SHA-256 values for the two architectures
# supported by the postgres Alpine backup image.
MC_SHA256_AMD64=01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891
MC_SHA256_ARM64=14c8c9616cfce4636add161304353244e8de383b2e2752c0e9dad01d4c27c12c
MC_INSTALL_DIR=${ATLAS_BACKUP_MC_INSTALL_DIR:-/usr/local/bin}

select_mc_architecture() {
    case "$(uname -m)" in
        x86_64|amd64) mc_arch=amd64; mc_sha256=$MC_SHA256_AMD64 ;;
        aarch64|arm64) mc_arch=arm64; mc_sha256=$MC_SHA256_ARM64 ;;
        *) echo "backup: unsupported architecture for pinned mc" >&2; return 64 ;;
    esac
}

mc_is_expected_release() {
    command -v mc >/dev/null 2>&1 || return 1
    select_mc_architecture || return $?
    mc_existing_path=$(command -v mc)
    verify_mc_candidate "$mc_existing_path"
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

install_pinned_mc() {
    case "$MC_INSTALL_DIR" in
        /*) ;;
        *) echo "backup: mc install directory must be absolute" >&2; return 64 ;;
    esac
    select_mc_architecture || return $?

    mc_tmp_dir=$(mktemp -d /tmp/atlas-mc-install.XXXXXX) || {
        echo "backup: could not create private mc download directory" >&2
        return 70
    }
    case "$mc_tmp_dir" in
        /tmp/atlas-mc-install.??????) ;;
        *) echo "backup: unsafe mc download directory" >&2; return 70 ;;
    esac
    mc_download=$mc_tmp_dir/mc
    mc_install_tmp=$MC_INSTALL_DIR/.atlas-mc.$$.tmp
    cleanup_mc_install() {
        case "${mc_download:-}" in /tmp/atlas-mc-install.??????/mc) rm -f "$mc_download" ;; esac
        case "${mc_tmp_dir:-}" in /tmp/atlas-mc-install.??????) rmdir "$mc_tmp_dir" 2>/dev/null || true ;; esac
        case "${mc_install_tmp:-}" in "$MC_INSTALL_DIR"/.atlas-mc.*.tmp) rm -f "$mc_install_tmp" ;; esac
    }
    trap 'cleanup_mc_install' 0
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM

    mc_asset=mc.linux-${mc_arch}.${MC_RELEASE}
    # The override is intentionally test-only and is not exposed by the
    # manifest or Compose. Production always uses the exact official release
    # origin; the integration fixture serves the checksum-identical artifact
    # on an internal Docker network so it never depends on public internet.
    mc_artifact_base_url=${ATLAS_BACKUP_TEST_MC_ARTIFACT_BASE_URL:-https://github.com/minio/mc/releases/download/${MC_RELEASE}}
    mc_url=${mc_artifact_base_url}/${mc_asset}
    if ! run_bounded wget -q -O "$mc_download" "$mc_url"; then
        echo "backup: pinned mc download failed" >&2
        return 69
    fi
    chmod 0755 "$mc_download"
    if ! verify_mc_candidate "$mc_download"; then
        echo "backup: pinned mc ${mc_verify_error}" >&2
        return 65
    fi
    mkdir -p "$MC_INSTALL_DIR"
    run_bounded cp "$mc_download" "$mc_install_tmp"
    run_bounded chmod 0755 "$mc_install_tmp"
    run_bounded mv -f "$mc_install_tmp" "$MC_INSTALL_DIR/mc"
    if ! verify_mc_candidate "$MC_INSTALL_DIR/mc"; then
        rm -f "$MC_INSTALL_DIR/mc"
        echo "backup: installed mc ${mc_verify_error}" >&2
        return 65
    fi
    cleanup_mc_install
    trap - 0 HUP INT TERM
}

if ! command -v openssl >/dev/null 2>&1; then
    echo "backup: backup image is missing required OpenSSL" >&2
    exit 69
fi
if [ -d /proc/self/ns ] && ! command -v setsid >/dev/null 2>&1; then
    echo "backup: backup image is missing required setsid" >&2
    exit 69
fi
if ! mc_is_expected_release; then
    install_pinned_mc
fi
exec sh "$@"
