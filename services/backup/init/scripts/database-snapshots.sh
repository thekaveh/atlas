#!/bin/sh
# Collect completed Neo4j offline dumps and a native online Weaviate snapshot.
# Sourced by backup-all.sh after run_bounded and WORK are initialized.

EXPECTED_NEO4J_IMAGE="neo4j:5.26.27"
EXPECTED_NEO4J_VERSION="5.26.27"
EXPECTED_WEAVIATE_IMAGE="cr.weaviate.io/semitechnologies/weaviate:1.38.13"
EXPECTED_WEAVIATE_VERSION="1.38.13"

prune_completed_database_snapshots() {
  [ "${BACKUP_DATABASE_SERVICES_QUIESCED:-}" = true ] || {
    echo "backup retention: database services must be quiesced" >&2; return 64;
  }
  database_retention=${BACKUP_LOCAL_SNAPSHOT_RETENTION_COUNT:-3}
  case "${database_retention}" in ''|*[!0-9]*|0|0*) echo "backup retention: invalid count" >&2; return 64;; esac
  [ "${database_retention}" -le 100 ] || { echo "backup retention: count must be at most 100" >&2; return 64; }
  for database_retention_spec in \
    "${DATABASE_NEO4J_SNAPSHOT_ROOT:-/database-snapshots/neo4j}|[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9]" \
    "${DATABASE_WEAVIATE_SNAPSHOT_ROOT:-/database-snapshots/weaviate}|atlas-*"
  do
    database_retention_root=${database_retention_spec%%|*}
    database_retention_pattern=${database_retention_spec#*|}
    case "${database_retention_root}" in /database-snapshots/neo4j|/database-snapshots/weaviate) ;; *) echo "backup retention: unsafe root" >&2; return 64;; esac
    [ -d "${database_retention_root}" ] || continue
    find "${database_retention_root}" -mindepth 1 -maxdepth 1 -type d -name "${database_retention_pattern}" -print \
      | sort -r | awk -v keep="${database_retention}" 'NR > keep' \
      | while IFS= read -r database_retention_candidate; do
          case "${database_retention_candidate}" in "${database_retention_root}"/*) rm -rf "${database_retention_candidate}";; esac
        done
  done
}

database_metadata_value() {
  database_metadata_file=$1
  database_metadata_key=$2
  database_metadata_count="$(grep -c "^${database_metadata_key}=" "${database_metadata_file}" || true)"
  [ "${database_metadata_count}" -eq 1 ] || return 1
  sed -n "s/^${database_metadata_key}=//p" "${database_metadata_file}"
}

database_archive_size() {
  database_archive=$1
  database_bytes="$(run_bounded wc -c <"${database_archive}" | tr -d '[:space:]')"
  case "${database_bytes}" in ''|*[!0-9]*) return 1 ;; esac
  [ "${database_bytes}" -le "${BACKUP_MAX_DATABASE_ARCHIVE_BYTES}" ] || return 1
  printf '%s' "${database_bytes}"
}

database_sha256() {
  database_sha_output="$(run_bounded sha256sum "$1")"
  printf '%s' "${database_sha_output%% *}"
}

weaviate_request() {
  database_method=$1
  database_url=$2
  database_output=$3
  database_body=${4:-}
  if [ "${database_method}" = POST ]; then
    run_bounded wget -q -O "${database_output}" \
      --header='Content-Type: application/json' --post-data="${database_body}" \
      "${database_url}"
  elif [ "${database_method}" = DELETE ]; then
    run_bounded wget -q -O "${database_output}" --method=DELETE "${database_url}"
  else
    run_bounded wget -q -O "${database_output}" "${database_url}"
  fi
  database_response_bytes="$(run_bounded wc -c <"${database_output}" | tr -d '[:space:]')"
  case "${database_response_bytes}" in ''|*[!0-9]*) return 1 ;; esac
  [ "${database_response_bytes}" -gt 0 ] && [ "${database_response_bytes}" -le 65536 ] || {
    echo "backup: Weaviate response exceeded the 64 KiB contract" >&2
    return 1
  }
}

weaviate_json_uint() {
  database_json_file=$1
  database_json_key=$2
  database_json_flat="$(tr -d ' \t\r\n' <"${database_json_file}")"
  database_json_needle="\"${database_json_key}\":"
  case "${database_json_flat}" in *"${database_json_needle}"*) ;; *) return 1 ;; esac
  database_json_after=${database_json_flat#*"${database_json_needle}"}
  database_json_value=${database_json_after%%,*}
  database_json_value=${database_json_value%%\}*}
  case "${database_json_value}" in ''|*[!0-9]*) return 1 ;; esac
  database_json_rest=${database_json_after#"${database_json_value}"}
  case "${database_json_rest}" in *"${database_json_needle}"*) return 1 ;; esac
  printf '%s' "${database_json_value}"
}

weaviate_json_string() {
  database_json_file=$1
  database_json_key=$2
  database_json_flat="$(tr -d ' \t\r\n' <"${database_json_file}")"
  database_json_needle="\"${database_json_key}\":\""
  case "${database_json_flat}" in *"${database_json_needle}"*) ;; *) return 1 ;; esac
  database_json_after=${database_json_flat#*"${database_json_needle}"}
  database_json_value=${database_json_after%%\"*}
  [ "${database_json_after}" != "${database_json_value}" ] || return 1
  database_json_rest=${database_json_after#*\"}
  case "${database_json_rest}" in *"${database_json_needle}"*) return 1 ;; esac
  case "${database_json_value}" in ''|*[!A-Za-z0-9._-]*) return 1 ;; esac
  printf '%s' "${database_json_value}"
}

wait_for_weaviate_status() {
  database_operation=$1
  database_status_url=$2
  database_response=$3
  database_deadline=$(( $(date +%s) + BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS ))
  while :; do
    weaviate_request GET "${database_status_url}" "${database_response}"
    database_status="$(weaviate_json_string "${database_response}" status)" || {
      echo "backup: Weaviate ${database_operation} response was malformed" >&2
      return 1
    }
    case "${database_status}" in
      SUCCESS) return 0 ;;
      FAILED|CANCELED) echo "backup: Weaviate ${database_operation} failed" >&2; return 1 ;;
      STARTED|TRANSFERRING|TRANSFERRED|FINALIZING|CANCELLING) ;;
      *) echo "backup: Weaviate ${database_operation} returned unknown status" >&2; return 1 ;;
    esac
    [ "$(date +%s)" -lt "${database_deadline}" ] || {
      echo "backup: Weaviate ${database_operation} timed out" >&2
      return 1
    }
    sleep 1
  done
}

cancel_owned_weaviate_backup() {
  database_cancel_response=$1
  run_bounded wget -q -O /dev/null --method=DELETE \
    "${WEAVIATE_URL}/v1/backups/filesystem/${database_weaviate_snapshot_id}" || return 1
  database_cancel_deadline=$(( $(date +%s) + BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS ))
  while [ "$(date +%s)" -lt "${database_cancel_deadline}" ]; do
    weaviate_request GET \
      "${WEAVIATE_URL}/v1/backups/filesystem/${database_weaviate_snapshot_id}" \
      "${database_cancel_response}" || return 1
    database_cancel_status="$(weaviate_json_string "${database_cancel_response}" status)" || return 1
    case "${database_cancel_status}" in
      CANCELED|FAILED) return 0 ;;
      CANCELLING|FINALIZING|STARTED|TRANSFERRING|TRANSFERRED) sleep 1 ;;
      SUCCESS) return 0 ;;
      *) return 1 ;;
    esac
  done
  return 1
}

capture_database_snapshots() {
  database_work=$1
  database_timestamp=$2
  database_backup_id=$3
  : "${BACKUP_MANIFEST_HMAC_KEY:?required}"
  : "${BACKUP_DEPLOYMENT_ID:?required}"
  BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS=${BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS:-120}
  BACKUP_MAX_DATABASE_ARCHIVE_BYTES=${BACKUP_MAX_DATABASE_ARCHIVE_BYTES:-53687091200}
  BACKUP_NEO4J_SOURCE=${BACKUP_NEO4J_SOURCE:-container}
  BACKUP_WEAVIATE_SOURCE=${BACKUP_WEAVIATE_SOURCE:-container}
  WEAVIATE_URL=${WEAVIATE_URL:-http://weaviate:8080}
  DATABASE_NEO4J_SNAPSHOT_ROOT=${DATABASE_NEO4J_SNAPSHOT_ROOT:-/database-snapshots/neo4j}
  DATABASE_WEAVIATE_SNAPSHOT_ROOT=${DATABASE_WEAVIATE_SNAPSHOT_ROOT:-/database-snapshots/weaviate}

  case "${BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS}" in
    ''|*[!0-9]*|0|0*) echo "backup: invalid database quiesce timeout" >&2; return 64 ;;
  esac
  [ "${BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS}" -le 3600 ] || {
    echo "backup: database quiesce timeout must be at most 3600" >&2; return 64;
  }
  case "${BACKUP_MAX_DATABASE_ARCHIVE_BYTES}" in
    ''|*[!0-9]*|0|0*) echo "backup: invalid maximum database archive size" >&2; return 64 ;;
  esac
  [ "${BACKUP_MAX_DATABASE_ARCHIVE_BYTES}" -le 1099511627776 ] || {
    echo "backup: maximum database archive size must be at most 1099511627776" >&2; return 64;
  }
  case "${BACKUP_NEO4J_SOURCE}:${BACKUP_WEAVIATE_SOURCE}" in
    *localhost*) echo "backup: localhost databases require an operator-managed external backup contract" >&2; return 64 ;;
  esac

  database_zero_sha=0000000000000000000000000000000000000000000000000000000000000000
  database_neo4j_state=disabled
  database_neo4j_sha=${database_zero_sha}
  database_neo4j_bytes=0
  database_neo4j_started=disabled
  database_neo4j_completed=disabled
  if [ "${BACKUP_NEO4J_SOURCE}" = container ]; then
    database_neo4j_dir="${DATABASE_NEO4J_SNAPSHOT_ROOT}/${database_timestamp}"
    database_neo4j_metadata="${database_neo4j_dir}/snapshot.metadata"
    [ -f "${database_neo4j_metadata}" ] || {
      echo "backup: Neo4j offline snapshot is missing; run services/backup/run-consistent-backup.sh" >&2
      return 75
    }
    [ "$(database_metadata_value "${database_neo4j_metadata}" snapshot_state)" = complete ] &&
      [ "$(database_metadata_value "${database_neo4j_metadata}" backup_timestamp)" = "${database_timestamp}" ] &&
      [ "$(database_metadata_value "${database_neo4j_metadata}" neo4j_image)" = "${EXPECTED_NEO4J_IMAGE}" ] &&
      [ "$(database_metadata_value "${database_neo4j_metadata}" neo4j_version)" = "${EXPECTED_NEO4J_VERSION}" ] || {
        echo "backup: Neo4j snapshot metadata is incomplete or incompatible" >&2
        return 65
      }
    database_neo4j_started="$(database_metadata_value "${database_neo4j_metadata}" started_at)"
    database_neo4j_completed="$(database_metadata_value "${database_neo4j_metadata}" completed_at)"
    database_neo4j_names="$(find "${database_neo4j_dir}" -mindepth 1 -maxdepth 1 -type f -exec basename {} \; | sort)"
    [ "${database_neo4j_names}" = "$(printf '%s\n' neo4j.dump snapshot.metadata system.dump | sort)" ] || {
      echo "backup: Neo4j snapshot contains unexpected inner names" >&2; return 65;
    }
    for database_inner_name in system neo4j; do
      database_inner_file="${database_neo4j_dir}/${database_inner_name}.dump"
      database_inner_sha="$(database_metadata_value "${database_neo4j_metadata}" "${database_inner_name}_sha256")"
      database_inner_bytes="$(database_metadata_value "${database_neo4j_metadata}" "${database_inner_name}_bytes")"
      [ "$(database_sha256 "${database_inner_file}")" = "${database_inner_sha}" ] &&
        [ "$(wc -c <"${database_inner_file}" | tr -d '[:space:]')" = "${database_inner_bytes}" ] || {
        echo "backup: Neo4j inner artifact changed before publication" >&2; return 65;
      }
    done
    run_bounded tar czf "${database_work}/neo4j.snapshot.tar.gz" -C "${database_neo4j_dir}" .
    database_neo4j_bytes="$(database_archive_size "${database_work}/neo4j.snapshot.tar.gz")" || {
      echo "backup: Neo4j snapshot archive exceeds the configured limit" >&2; return 1;
    }
    database_neo4j_sha="$(database_sha256 "${database_work}/neo4j.snapshot.tar.gz")"
    database_neo4j_state=complete
  else
    run_bounded tar czf "${database_work}/neo4j.snapshot.tar.gz" -T /dev/null
    database_neo4j_bytes="$(database_archive_size "${database_work}/neo4j.snapshot.tar.gz")"
    database_neo4j_sha="$(database_sha256 "${database_work}/neo4j.snapshot.tar.gz")"
  fi

  database_weaviate_state=disabled
  database_weaviate_sha=${database_zero_sha}
  database_weaviate_bytes=0
  database_weaviate_started=disabled
  database_weaviate_completed=disabled
  database_weaviate_snapshot_id=disabled
  if [ "${BACKUP_WEAVIATE_SOURCE}" = container ]; then
    database_meta_response="${database_work}/weaviate.meta.response"
    weaviate_request GET "${WEAVIATE_URL}/v1/meta" "${database_meta_response}"
    database_weaviate_version="$(weaviate_json_string "${database_meta_response}" version)"
    [ "${database_weaviate_version}" = "${EXPECTED_WEAVIATE_VERSION}" ] || {
      echo "backup: expected ${EXPECTED_WEAVIATE_IMAGE}, got Weaviate ${database_weaviate_version:-unknown}" >&2
      return 78
    }
    database_weaviate_snapshot_id="atlas-${database_timestamp}-${database_backup_id}"
    [ ! -e "${DATABASE_WEAVIATE_SNAPSHOT_ROOT}/${database_weaviate_snapshot_id}" ] || {
      echo "backup: refusing to reuse an existing Weaviate snapshot id" >&2
      return 73
    }
    database_weaviate_started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    database_response="${database_work}/weaviate.backup.response"
    weaviate_request POST "${WEAVIATE_URL}/v1/backups/filesystem" "${database_response}" \
      "{\"id\":\"${database_weaviate_snapshot_id}\"}"
    database_initial_status="$(weaviate_json_string "${database_response}" status)" || {
      echo "backup: Weaviate backup start response was malformed" >&2; return 1;
    }
    case "${database_initial_status}" in
      SUCCESS) ;;
      STARTED)
        if ! wait_for_weaviate_status backup \
          "${WEAVIATE_URL}/v1/backups/filesystem/${database_weaviate_snapshot_id}" \
          "${database_response}"; then
          cancel_owned_weaviate_backup "${database_response}" || \
            echo "backup: WARNING — owned Weaviate backup cancellation did not settle" >&2
          return 1
        fi
        ;;
      FAILED|CANCELED) echo "backup: Weaviate rejected the backup" >&2; return 1 ;;
      *) echo "backup: Weaviate backup start returned unknown status" >&2; return 1 ;;
    esac
    database_weaviate_dir="${DATABASE_WEAVIATE_SNAPSHOT_ROOT}/${database_weaviate_snapshot_id}"
    [ -d "${database_weaviate_dir}" ] || {
      echo "backup: completed Weaviate snapshot directory is missing" >&2; return 1;
    }
    database_weaviate_completed="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    run_bounded tar czf "${database_work}/weaviate.snapshot.tar.gz" \
      -C "${DATABASE_WEAVIATE_SNAPSHOT_ROOT}" "${database_weaviate_snapshot_id}"
    database_weaviate_bytes="$(database_archive_size "${database_work}/weaviate.snapshot.tar.gz")" || {
      echo "backup: Weaviate snapshot archive exceeds the configured limit" >&2; return 1;
    }
    database_weaviate_sha="$(database_sha256 "${database_work}/weaviate.snapshot.tar.gz")"
    database_weaviate_state=complete
    rm -f "${database_meta_response}" "${database_response}"
  else
    run_bounded tar czf "${database_work}/weaviate.snapshot.tar.gz" -T /dev/null
    database_weaviate_bytes="$(database_archive_size "${database_work}/weaviate.snapshot.tar.gz")"
    database_weaviate_sha="$(database_sha256 "${database_work}/weaviate.snapshot.tar.gz")"
  fi

  database_deployment_hex="$(printf '%s' "${BACKUP_DEPLOYMENT_ID}" | od -An -v -tx1 | tr -d '[:space:]')"
  cat >"${database_work}/databases.manifest.payload" <<EOF
format_version=1
snapshot_state=complete
backup_timestamp=${database_timestamp}
backup_id=${database_backup_id}
deployment_id_hex=${database_deployment_hex}
neo4j_image=${EXPECTED_NEO4J_IMAGE}
neo4j_version=${EXPECTED_NEO4J_VERSION}
neo4j_state=${database_neo4j_state}
neo4j_started_at=${database_neo4j_started}
neo4j_completed_at=${database_neo4j_completed}
neo4j_archive_sha256=${database_neo4j_sha}
neo4j_archive_bytes=${database_neo4j_bytes}
weaviate_image=${EXPECTED_WEAVIATE_IMAGE}
weaviate_version=${EXPECTED_WEAVIATE_VERSION}
weaviate_state=${database_weaviate_state}
weaviate_snapshot_id=${database_weaviate_snapshot_id}
weaviate_started_at=${database_weaviate_started}
weaviate_completed_at=${database_weaviate_completed}
weaviate_archive_sha256=${database_weaviate_sha}
weaviate_archive_bytes=${database_weaviate_bytes}
EOF
  database_manifest_hmac_output="$(run_bounded openssl dgst -sha256 -mac HMAC \
    -macopt "hexkey:${BACKUP_MANIFEST_HMAC_KEY}" "${database_work}/databases.manifest.payload")"
  database_manifest_hmac=${database_manifest_hmac_output##* }
  run_bounded cp "${database_work}/databases.manifest.payload" "${database_work}/databases.manifest"
  printf 'hmac_sha256=%s\n' "${database_manifest_hmac}" >>"${database_work}/databases.manifest"
  rm -f "${database_work}/databases.manifest.payload"

  database_manifest_bytes="$(run_bounded wc -c <"${database_work}/databases.manifest" | tr -d '[:space:]')"
  database_manifest_sha="$(database_sha256 "${database_work}/databases.manifest")"
  cat >"${database_work}/databases.complete.payload" <<EOF
completion_format=1
snapshot_state=complete
backup_timestamp=${database_timestamp}
backup_id=${database_backup_id}
manifest_sha256=${database_manifest_sha}
manifest_bytes=${database_manifest_bytes}
EOF
  database_completion_hmac_output="$(run_bounded openssl dgst -sha256 -mac HMAC \
    -macopt "hexkey:${BACKUP_MANIFEST_HMAC_KEY}" "${database_work}/databases.complete.payload")"
  database_completion_hmac=${database_completion_hmac_output##* }
  run_bounded cp "${database_work}/databases.complete.payload" "${database_work}/databases.complete"
  printf 'hmac_sha256=%s\n' "${database_completion_hmac}" >>"${database_work}/databases.complete"
  rm -f "${database_work}/databases.complete.payload"
  echo "backup: captured consistent Neo4j and Weaviate snapshots"
}

if [ "${0##*/}" = database-snapshots.sh ]; then
  [ "${1:-}" = prune ] || {
    echo "usage: database-snapshots.sh prune" >&2
    exit 64
  }
  prune_completed_database_snapshots
fi
