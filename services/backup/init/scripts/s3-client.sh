#!/bin/sh
# Shared S3 client configuration for backup-all.sh and restore-postgres.sh.

backup_s3_error() {
  printf '%s: %s\n' "${BACKUP_S3_LOG_PREFIX:-backup}" "$1" >&2
  return 64
}

backup_s3_validate_port() {
  case "$1" in
    ''|*[!0-9]*|0|0*) return 1 ;;
  esac
  [ "$1" -le 65535 ] 2>/dev/null
}

backup_s3_validate_ipv4() {
  case "$1" in ''|.*|*.|*..*|*[!0-9.]*) return 1 ;; esac
  backup_s3_saved_ifs=$IFS
  IFS=.
  # shellcheck disable=SC2086 # intentional IFS split after strict character validation
  set -- $1
  IFS=$backup_s3_saved_ifs
  [ "$#" -eq 4 ] || return 1
  for backup_s3_octet do
    case "$backup_s3_octet" in ''|*[!0-9]*|0[0-9]*) return 1 ;; esac
    [ "$backup_s3_octet" -le 255 ] 2>/dev/null || return 1
  done
}

backup_s3_validate_dns() {
  [ "${#1}" -le 253 ] || return 1
  case "$1" in ''|.*|*.|*..*|*[!A-Za-z0-9.-]*) return 1 ;; esac
  backup_s3_saved_ifs=$IFS
  IFS=.
  # shellcheck disable=SC2086 # intentional IFS split after strict character validation
  set -- $1
  IFS=$backup_s3_saved_ifs
  for backup_s3_label do
    [ "${#backup_s3_label}" -le 63 ] || return 1
    case "$backup_s3_label" in ''|-*|*-) return 1 ;; esac
  done
}

backup_s3_validate_ipv6_groups() {
  backup_s3_group_count=0
  [ -n "$1" ] || return 0
  backup_s3_saved_ifs=$IFS
  IFS=:
  # shellcheck disable=SC2086 # intentional IFS split after strict character validation
  set -- $1
  IFS=$backup_s3_saved_ifs
  for backup_s3_group do
    case "$backup_s3_group" in ''|*[!0-9A-Fa-f]*) return 1 ;; esac
    [ "${#backup_s3_group}" -le 4 ] || return 1
    backup_s3_group_count=$((backup_s3_group_count + 1))
  done
}

backup_s3_validate_ipv6() {
  case "$1" in ''|*.*|*[!0-9A-Fa-f:]*) return 1 ;; esac
  case "$1" in *:::*) return 1 ;; esac
  case "$1" in
    *::* )
      backup_s3_ipv6_right=${1#*::}
      case "$backup_s3_ipv6_right" in *::*) return 1 ;; esac
      backup_s3_ipv6_left=${1%%::*}
      backup_s3_validate_ipv6_groups "$backup_s3_ipv6_left" || return 1
      backup_s3_left_groups=$backup_s3_group_count
      backup_s3_validate_ipv6_groups "$backup_s3_ipv6_right" || return 1
      backup_s3_right_groups=$backup_s3_group_count
      [ $((backup_s3_left_groups + backup_s3_right_groups)) -le 7 ] || return 1
      ;;
    *)
      case "$1" in :*|*:) return 1 ;; esac
      backup_s3_validate_ipv6_groups "$1" || return 1
      [ "$backup_s3_group_count" -eq 8 ] || return 1
      ;;
  esac
}

backup_s3_validate_endpoint() {
  case "$1" in
    http://*) backup_s3_endpoint_scheme=http; endpoint_authority=${1#http://} ;;
    https://*) backup_s3_endpoint_scheme=https; endpoint_authority=${1#https://} ;;
    *) return 1 ;;
  esac
  case "$endpoint_authority" in
    ''|*'/'*|*'?'*|*'#'*|*'@'*) return 1 ;;
  esac

  case "$endpoint_authority" in
    \[*\])
      endpoint_host=${endpoint_authority#\[}
      endpoint_host=${endpoint_host%\]}
      backup_s3_validate_ipv6 "$endpoint_host" || return 1
      ;;
    \[*\]:*)
      endpoint_host=${endpoint_authority#\[}
      endpoint_port=${endpoint_host#*\]:}
      endpoint_host=${endpoint_host%%\]*}
      backup_s3_validate_port "$endpoint_port" || return 1
      backup_s3_validate_ipv6 "$endpoint_host" || return 1
      ;;
    \[*|*\]*) return 1 ;;
    *:*:*) return 1 ;;
    *:*)
      endpoint_host=${endpoint_authority%:*}
      endpoint_port=${endpoint_authority##*:}
      backup_s3_validate_port "$endpoint_port" || return 1
      ;;
    *) endpoint_host=$endpoint_authority ;;
  esac

  case "$endpoint_authority" in
    \[*\]* ) ;;
    *)
      case "$endpoint_host" in
        *[!0-9.]*) backup_s3_validate_dns "$endpoint_host" || return 1 ;;
        *) backup_s3_validate_ipv4 "$endpoint_host" || return 1 ;;
      esac
      ;;
  esac
  return 0
}

backup_s3_validate_credential() {
  backup_s3_utf8_need=0
  backup_s3_utf8_min=128
  backup_s3_utf8_max=191
  for backup_s3_utf8_byte in $(printf '%s' "$1" | od -An -v -tu1); do
    if [ "$backup_s3_utf8_need" -gt 0 ]; then
      [ "$backup_s3_utf8_byte" -ge "$backup_s3_utf8_min" ] &&
        [ "$backup_s3_utf8_byte" -le "$backup_s3_utf8_max" ] || return 1
      backup_s3_utf8_need=$((backup_s3_utf8_need - 1))
      backup_s3_utf8_min=128
      backup_s3_utf8_max=191
      continue
    fi
    if [ "$backup_s3_utf8_byte" -le 127 ]; then
      [ "$backup_s3_utf8_byte" -ge 32 ] && [ "$backup_s3_utf8_byte" -ne 127 ] || return 1
    elif [ "$backup_s3_utf8_byte" -ge 194 ] && [ "$backup_s3_utf8_byte" -le 223 ]; then
      backup_s3_utf8_need=1
    elif [ "$backup_s3_utf8_byte" -eq 224 ]; then
      backup_s3_utf8_need=2; backup_s3_utf8_min=160
    elif [ "$backup_s3_utf8_byte" -ge 225 ] && [ "$backup_s3_utf8_byte" -le 236 ]; then
      backup_s3_utf8_need=2
    elif [ "$backup_s3_utf8_byte" -eq 237 ]; then
      backup_s3_utf8_need=2; backup_s3_utf8_max=159
    elif [ "$backup_s3_utf8_byte" -ge 238 ] && [ "$backup_s3_utf8_byte" -le 239 ]; then
      backup_s3_utf8_need=2
    elif [ "$backup_s3_utf8_byte" -eq 240 ]; then
      backup_s3_utf8_need=3; backup_s3_utf8_min=144
    elif [ "$backup_s3_utf8_byte" -ge 241 ] && [ "$backup_s3_utf8_byte" -le 243 ]; then
      backup_s3_utf8_need=3
    elif [ "$backup_s3_utf8_byte" -eq 244 ]; then
      backup_s3_utf8_need=3; backup_s3_utf8_max=143
    else
      return 1
    fi
  done
  [ "$backup_s3_utf8_need" -eq 0 ]
}

backup_s3_json_escape() {
  for backup_s3_json_byte in $(od -An -v -tu1); do
    case "$backup_s3_json_byte" in
      34) printf '%s' '\"' ;;
      92) printf '%s' "\\\\" ;;
      *)
        backup_s3_json_octal=$(printf '%03o' "$backup_s3_json_byte")
        # POSIX printf %b specifies octal escapes as \0ddd.  macOS sh also
        # accepts \ddd, but dash (used by Ubuntu CI) preserves it literally
        # and corrupts the generated JSON.
        printf '%b' "\\0${backup_s3_json_octal}"
        ;;
    esac
  done
}

prepare_backup_s3() {
  BACKUP_S3_LOG_PREFIX=${1:-backup}
  BACKUP_S3_MODE=${BACKUP_S3_MODE:-local}
  BACKUP_S3_ENDPOINT=${BACKUP_S3_ENDPOINT:-http://minio:9000}
  BACKUP_S3_REGION=${BACKUP_S3_REGION:-us-east-1}
  BACKUP_S3_TLS_VERIFY=${BACKUP_S3_TLS_VERIFY:-true}
  BACKUP_S3_SESSION_TOKEN=${BACKUP_S3_SESSION_TOKEN:-}

  case "$BACKUP_S3_MODE" in
    local|external) ;;
    *) backup_s3_error "BACKUP_S3_MODE must be local or external" || return $? ;;
  esac

  if [ -n "${BACKUP_S3_ALIAS_URL:-}" ]; then
    if [ "$BACKUP_S3_ENDPOINT" = "http://minio:9000" ]; then
      BACKUP_S3_ENDPOINT=$BACKUP_S3_ALIAS_URL
      printf '%s: WARNING — BACKUP_S3_ALIAS_URL is deprecated; migrate to BACKUP_S3_ENDPOINT\n' "$BACKUP_S3_LOG_PREFIX" >&2
    elif [ "$BACKUP_S3_ALIAS_URL" != "$BACKUP_S3_ENDPOINT" ]; then
      backup_s3_error "BACKUP_S3_ALIAS_URL conflicts with BACKUP_S3_ENDPOINT" || return $?
    fi
  fi

  backup_s3_validate_endpoint "$BACKUP_S3_ENDPOINT" || {
    backup_s3_error "BACKUP_S3_ENDPOINT must be an http(s) origin without userinfo, path, query, or fragment" || return $?
  }
  case "$BACKUP_S3_TLS_VERIFY" in
    true|false) ;;
    *) backup_s3_error "BACKUP_S3_TLS_VERIFY must be true or false" || return $? ;;
  esac
  if [ "$BACKUP_S3_TLS_VERIFY" = false ] && [ "$backup_s3_endpoint_scheme" != https ]; then
    backup_s3_error "BACKUP_S3_TLS_VERIFY=false is valid only for https endpoints" || return $?
  fi
  case "$BACKUP_S3_REGION" in
    ''|[-._]*|*[-._]|*[!A-Za-z0-9._-]*) backup_s3_error "BACKUP_S3_REGION has invalid syntax" || return $? ;;
  esac
  [ "${#BACKUP_S3_REGION}" -le 64 ] || {
    backup_s3_error "BACKUP_S3_REGION must be at most 64 characters" || return $?
  }

  case "$BACKUP_S3_MODE" in
    local)
      [ "$BACKUP_S3_ENDPOINT" = "http://minio:9000" ] || {
        backup_s3_error "BACKUP_S3_MODE=local requires BACKUP_S3_ENDPOINT=http://minio:9000" || return $?
      }
      if [ -n "${BACKUP_S3_ACCESS_KEY:-}" ] || [ -n "${BACKUP_S3_SECRET_KEY:-}" ]; then
        [ -n "${BACKUP_S3_ACCESS_KEY:-}" ] && [ -n "${BACKUP_S3_SECRET_KEY:-}" ] || {
          backup_s3_error "BACKUP_S3_ACCESS_KEY and BACKUP_S3_SECRET_KEY must be set together" || return $?
        }
      else
        BACKUP_S3_ACCESS_KEY=${MINIO_ROOT_USER:-}
        BACKUP_S3_SECRET_KEY=${MINIO_ROOT_PASSWORD:-}
        [ -n "$BACKUP_S3_ACCESS_KEY" ] && [ -n "$BACKUP_S3_SECRET_KEY" ] || {
          backup_s3_error "local mode requires dedicated S3 credentials or both MINIO_ROOT_USER and MINIO_ROOT_PASSWORD" || return $?
        }
        [ -z "$BACKUP_S3_SESSION_TOKEN" ] || {
          backup_s3_error "BACKUP_S3_SESSION_TOKEN requires dedicated S3 credentials in local mode" || return $?
        }
      fi
      ;;
    external)
      [ -n "${BACKUP_S3_ACCESS_KEY:-}" ] && [ -n "${BACKUP_S3_SECRET_KEY:-}" ] || {
        backup_s3_error "BACKUP_S3_ACCESS_KEY and BACKUP_S3_SECRET_KEY are required for external endpoints" || return $?
      }
      ;;
  esac

  if ! backup_s3_validate_credential "$BACKUP_S3_ACCESS_KEY" ||
     ! backup_s3_validate_credential "$BACKUP_S3_SECRET_KEY" ||
     ! backup_s3_validate_credential "$BACKUP_S3_SESSION_TOKEN"; then
    backup_s3_error "S3 credentials must be valid UTF-8 without control bytes" || return $?
  fi

  unset backup_s3_access_key backup_s3_secret_key backup_s3_session_token
  backup_s3_access_key=$BACKUP_S3_ACCESS_KEY
  backup_s3_secret_key=$BACKUP_S3_SECRET_KEY
  backup_s3_session_token=$BACKUP_S3_SESSION_TOKEN
  unset BACKUP_S3_ACCESS_KEY BACKUP_S3_SECRET_KEY BACKUP_S3_SESSION_TOKEN
  unset MINIO_ROOT_USER MINIO_ROOT_PASSWORD
  unset MC_HOST_s3 MC_CONFIG_ENV_FILE
  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN

  MC_REGION=$BACKUP_S3_REGION
  if [ "$BACKUP_S3_TLS_VERIFY" = false ]; then
    MC_INSECURE=1
    export MC_INSECURE
  else
    unset MC_INSECURE
  fi
  export MC_REGION
}

configure_backup_s3() {
  backup_s3_config_dir=$1
  backup_s3_credentials_file=${backup_s3_config_dir}/credentials.json
  umask 077
  mkdir -p "$backup_s3_config_dir"
  chmod 700 "$backup_s3_config_dir"

  backup_s3_access_json=$(printf '%s' "$backup_s3_access_key" | backup_s3_json_escape)
  backup_s3_secret_json=$(printf '%s' "$backup_s3_secret_key" | backup_s3_json_escape)
  backup_s3_token_json=$(printf '%s' "$backup_s3_session_token" | backup_s3_json_escape)
  printf '{"url":"%s","accessKey":"%s","secretKey":"%s","sessionToken":"%s","api":"S3v4","path":"auto"}\n' \
    "$BACKUP_S3_ENDPOINT" "$backup_s3_access_json" "$backup_s3_secret_json" "$backup_s3_token_json" \
    >"$backup_s3_credentials_file"
  chmod 600 "$backup_s3_credentials_file"

  MC_CONFIG_DIR=$backup_s3_config_dir
  export MC_CONFIG_DIR
  if ! run_bounded mc alias import s3 "$backup_s3_credentials_file" >/dev/null; then
    rm -f "$backup_s3_credentials_file"
    unset backup_s3_access_key backup_s3_secret_key backup_s3_session_token
    unset backup_s3_access_json backup_s3_secret_json backup_s3_token_json
    backup_s3_error "could not configure the S3 client" || return $?
  fi
  rm -f "$backup_s3_credentials_file"
  unset backup_s3_access_key backup_s3_secret_key backup_s3_session_token
  unset backup_s3_access_json backup_s3_secret_json backup_s3_token_json
}

# Stream an S3 command through a positively owned supervisor.  Callers may
# stop the supervisor with TERM; it gives the producer one second to exit and
# then uses KILL, so FIFO cleanup cannot block on a TERM-resistant client.
# The watchdog preserves the normal command deadline with a ten-second grace.
backup_s3_stream_command() {
  backup_s3_stream_timeout=$1
  shift
  if ! command -v setsid >/dev/null 2>&1; then
    printf '%s\n' \
      'backup: setsid is required for bounded S3 stream process ownership' >&2
    return 69
  fi
  backup_s3_stream_child=
  backup_s3_stream_group=
  backup_s3_stream_producer_ready=
  backup_s3_stream_watchdog=
  backup_s3_stream_watchdog_group=
  backup_s3_stream_watchdog_ready=
  backup_s3_stream_pending_status=0
  backup_s3_stream_deadline_status=0
  backup_s3_stream_status=

  # shellcheck disable=SC2329 # invoked by the supervisor's signal traps.
  backup_s3_note_stream_signal() {
    if [ "$backup_s3_stream_pending_status" -eq 0 ]; then
      backup_s3_stream_pending_status=$1
    fi
    # Once ownership is published, interrupt the producer group so `wait`
    # returns and the main path can perform the bounded TERM -> KILL sweep.
    if [ -n "$backup_s3_stream_group" ] || [ -n "$backup_s3_stream_child" ]; then
      backup_s3_signal_producer TERM
    fi
  }

  # shellcheck disable=SC2329 # invoked by the watchdog deadline trap.
  backup_s3_note_stream_deadline() { backup_s3_stream_deadline_status=137; }

  backup_s3_signal_producer() {
    backup_s3_stream_signal=$1
    if [ -n "$backup_s3_stream_group" ]; then
      kill "-${backup_s3_stream_signal}" "-${backup_s3_stream_group}" 2>/dev/null || true
    fi
    # setsid(1) has a short launch window before PID == PGID is established.
    # Keep signalling the positively owned direct PID until it is reaped.
    if [ -n "$backup_s3_stream_child" ]; then
      kill "-${backup_s3_stream_signal}" "$backup_s3_stream_child" 2>/dev/null || true
    fi
  }

  backup_s3_producer_alive() {
    if [ -n "$backup_s3_stream_group" ] \
      && kill -0 "-${backup_s3_stream_group}" 2>/dev/null; then
      return 0
    fi
    [ -n "$backup_s3_stream_child" ] \
      && kill -0 "$backup_s3_stream_child" 2>/dev/null
  }

  backup_s3_bounded_reap() {
    backup_s3_reap_pid=$1
    if ! kill -0 "$backup_s3_reap_pid" 2>/dev/null; then
      wait "$backup_s3_reap_pid" 2>/dev/null || true
      return 0
    fi
    backup_s3_reaper_pid=$(sh -c 'printf "%s" "$PPID"')
    # shellcheck disable=SC2329 # invoked by the bounded wait's timer signal.
    backup_s3_note_reap_timeout() { backup_s3_reap_timed_out=1; }
    backup_s3_reap_attempt=0
    while kill -0 "$backup_s3_reap_pid" 2>/dev/null \
      && [ "$backup_s3_reap_attempt" -lt 3 ]; do
      backup_s3_reap_attempt=$((backup_s3_reap_attempt + 1))
      backup_s3_reap_timed_out=0
      trap 'backup_s3_note_reap_timeout' USR1
      (
        sleep 0.25
        kill -USR1 "$backup_s3_reaper_pid" 2>/dev/null || true
      ) </dev/null >/dev/null 2>&1 &
      backup_s3_reap_timer=$!
      while [ "$backup_s3_reap_timed_out" -eq 0 ] \
        && kill -0 "$backup_s3_reap_pid" 2>/dev/null; do
        wait "$backup_s3_reap_pid" 2>/dev/null || true
      done
      kill "$backup_s3_reap_timer" 2>/dev/null || true
      wait "$backup_s3_reap_timer" 2>/dev/null || true
      trap - USR1
    done
    if kill -0 "$backup_s3_reap_pid" 2>/dev/null; then
      return 1
    fi
    wait "$backup_s3_reap_pid" 2>/dev/null || true
    return 0
  }

  backup_s3_sweep_producer() {
    if backup_s3_producer_alive; then
      backup_s3_signal_producer TERM
      sleep 1
      backup_s3_signal_producer KILL
    fi
    if [ -n "$backup_s3_stream_child" ]; then
      if ! backup_s3_bounded_reap "$backup_s3_stream_child"; then
        printf '%s\n' \
          'backup: owned S3 stream child survived forced termination' >&2
        return 1
      fi
      backup_s3_stream_child=
    fi

    # Do not discard the process-group identity until absence is proven.  A
    # direct-child wait cannot reap an adopted descendant which kept the FIFO
    # open, and even KILL can fail (for example, for an uninterruptible task).
    backup_s3_stream_probe_attempt=0
    while backup_s3_producer_alive; do
      backup_s3_stream_probe_attempt=$((backup_s3_stream_probe_attempt + 1))
      if [ "$backup_s3_stream_probe_attempt" -ge 20 ]; then
        printf '%s\n' \
          'backup: owned S3 stream process group survived forced termination' >&2
        return 1
      fi
      sleep 0.05
    done
    backup_s3_stream_group=
    return 0
  }

  backup_s3_watchdog_alive() {
    if [ -n "$backup_s3_stream_watchdog_group" ] \
      && kill -0 "-${backup_s3_stream_watchdog_group}" 2>/dev/null; then
      return 0
    fi
    [ -n "$backup_s3_stream_watchdog" ] \
      && kill -0 "$backup_s3_stream_watchdog" 2>/dev/null
  }

  backup_s3_signal_watchdog() {
    backup_s3_watchdog_signal=$1
    if [ -n "$backup_s3_stream_watchdog_group" ]; then
      kill "-${backup_s3_watchdog_signal}" \
        "-${backup_s3_stream_watchdog_group}" 2>/dev/null || true
    fi
    if [ -n "$backup_s3_stream_watchdog" ]; then
      kill "-${backup_s3_watchdog_signal}" \
        "$backup_s3_stream_watchdog" 2>/dev/null || true
    fi
  }

  backup_s3_reap_watchdog() {
    [ -n "$backup_s3_stream_watchdog" ] || return 0
    backup_s3_signal_watchdog TERM
    backup_s3_watchdog_probe_attempt=0
    while backup_s3_watchdog_alive \
      && [ "$backup_s3_watchdog_probe_attempt" -lt 5 ]; do
      backup_s3_watchdog_probe_attempt=$((backup_s3_watchdog_probe_attempt + 1))
      sleep 0.05
    done
    if backup_s3_watchdog_alive; then
      backup_s3_signal_watchdog KILL
    fi
    if ! backup_s3_bounded_reap "$backup_s3_stream_watchdog"; then
      printf '%s\n' \
        'backup: owned S3 stream watchdog survived forced termination' >&2
      return 1
    fi
    backup_s3_stream_watchdog=
    backup_s3_watchdog_probe_attempt=0
    while backup_s3_watchdog_alive; do
      backup_s3_watchdog_probe_attempt=$((backup_s3_watchdog_probe_attempt + 1))
      if [ "$backup_s3_watchdog_probe_attempt" -ge 20 ]; then
        printf '%s\n' \
          'backup: owned S3 stream watchdog group survived forced termination' >&2
        return 1
      fi
      sleep 0.05
    done
    backup_s3_stream_watchdog_group=
    if [ -n "$backup_s3_stream_watchdog_ready" ]; then
      rm -f "$backup_s3_stream_watchdog_ready"
      backup_s3_stream_watchdog_ready=
    fi
    return 0
  }
  trap 'backup_s3_note_stream_signal 129' HUP
  trap 'backup_s3_note_stream_signal 130' INT
  trap 'backup_s3_note_stream_signal 143' TERM
  trap 'backup_s3_note_stream_deadline' USR2

  backup_s3_stream_producer_ready=$(mktemp \
    "${TMPDIR:-/tmp}/atlas-backup-producer.XXXXXX") || {
      printf '%s\n' 'backup: could not allocate S3 producer readiness file' >&2
      backup_s3_stream_status=69
      trap '' HUP INT TERM USR2
      if [ "$backup_s3_stream_pending_status" -ne 0 ]; then
        backup_s3_stream_status=$backup_s3_stream_pending_status
      fi
      return "$backup_s3_stream_status"
    }
  # shellcheck disable=SC2016 # expanded by the dedicated `sh -c`, not here.
  backup_s3_producer_script='
    producer_ready=$1
    shift
    printf "%s\n" ready >"$producer_ready" || exit 70
    exec "$@"
  '
  setsid sh -c "$backup_s3_producer_script" atlas-s3-producer \
    "$backup_s3_stream_producer_ready" "$@" &
  backup_s3_stream_child=$!
  backup_s3_stream_group=$backup_s3_stream_child

  backup_s3_producer_start_attempt=0
  while [ ! -s "$backup_s3_stream_producer_ready" ] \
    && [ "$backup_s3_stream_pending_status" -eq 0 ] \
    && [ "$backup_s3_producer_start_attempt" -lt 500 ]; do
    backup_s3_producer_start_attempt=$((backup_s3_producer_start_attempt + 1))
    sleep 0.01
  done
  if [ ! -s "$backup_s3_stream_producer_ready" ]; then
    printf '%s\n' 'backup: S3 stream producer failed to establish its session' >&2
    backup_s3_stream_status=69
  fi
  rm -f "$backup_s3_stream_producer_ready"
  backup_s3_stream_producer_ready=

  # Run the watchdog and both of its sleeps in a second, positively owned
  # process group.  Group ownership closes the `sleep &` / `$!` publication
  # race and lets the supervisor bound cleanup even if the watchdog is STOPped.
  # shellcheck disable=SC2016 # expanded by the dedicated `sh -c`, not here.
  backup_s3_watchdog_script='
    producer_target=$1
    stream_timeout=$2
    producer_target_kind=$3
    watchdog_ready=$4
    supervisor_pid=$5
    watchdog_sleep=
    watchdog_pending=0
    note_watchdog_signal() {
      watchdog_pending=1
      [ -z "$watchdog_sleep" ] || kill "$watchdog_sleep" 2>/dev/null || true
    }
    stop_watchdog() {
      trap "" HUP INT TERM
      if [ -n "$watchdog_sleep" ]; then
        kill "$watchdog_sleep" 2>/dev/null || true
        kill -KILL "$watchdog_sleep" 2>/dev/null || true
        wait "$watchdog_sleep" 2>/dev/null || true
      fi
      exit 0
    }
    delay_watchdog() {
      watchdog_sleep=
      sleep "$1" &
      watchdog_sleep=$!
      [ "$watchdog_pending" -eq 0 ] || stop_watchdog
      watchdog_wait_status=0
      wait "$watchdog_sleep" || watchdog_wait_status=$?
      [ "$watchdog_pending" -eq 0 ] || stop_watchdog
      watchdog_sleep=
      [ "${watchdog_wait_status:-0}" -eq 0 ]
    }
    signal_producer() {
      if [ "$producer_target_kind" = group ]; then
        kill "-$1" "-$producer_target" 2>/dev/null || true
      else
        kill "-$1" "$producer_target" 2>/dev/null || true
      fi
    }
    trap note_watchdog_signal HUP INT TERM
    printf "%s\n" ready >"$watchdog_ready" || exit 70
    delay_watchdog "$stream_timeout" || { signal_producer KILL; exit 70; }
    signal_producer TERM
    delay_watchdog 10 || { signal_producer KILL; exit 70; }
    signal_producer KILL
    kill -USR2 "$supervisor_pid" 2>/dev/null || true
  '
  if [ -z "$backup_s3_stream_status" ]; then
    if backup_s3_stream_watchdog_ready=$(mktemp \
      "${TMPDIR:-/tmp}/atlas-backup-watchdog.XXXXXX"); then
      backup_s3_stream_supervisor_pid=$(sh -c 'printf "%s" "$PPID"')
      setsid sh -c "$backup_s3_watchdog_script" atlas-s3-watchdog \
        "$backup_s3_stream_group" "$backup_s3_stream_timeout" group \
        "$backup_s3_stream_watchdog_ready" \
        "$backup_s3_stream_supervisor_pid" \
        </dev/null >/dev/null 2>&1 &
      backup_s3_stream_watchdog=$!
      backup_s3_stream_watchdog_group=$backup_s3_stream_watchdog

      backup_s3_watchdog_start_attempt=0
      while [ ! -s "$backup_s3_stream_watchdog_ready" ] \
        && [ "$backup_s3_watchdog_start_attempt" -lt 200 ]; do
        backup_s3_watchdog_start_attempt=$((backup_s3_watchdog_start_attempt + 1))
        sleep 0.01
      done
      if [ ! -s "$backup_s3_stream_watchdog_ready" ]; then
        printf '%s\n' 'backup: S3 stream watchdog failed to become ready' >&2
        backup_s3_stream_status=69
      fi
      rm -f "$backup_s3_stream_watchdog_ready"
      backup_s3_stream_watchdog_ready=
    else
      printf '%s\n' 'backup: could not allocate S3 watchdog readiness file' >&2
      backup_s3_stream_watchdog_ready=
      backup_s3_stream_status=69
    fi
  fi

  if [ -n "$backup_s3_stream_status" ]; then
    :
  elif [ "$backup_s3_stream_pending_status" -ne 0 ]; then
    backup_s3_stream_status=$backup_s3_stream_pending_status
  elif wait "$backup_s3_stream_child"; then
    backup_s3_stream_status=0
  else
    backup_s3_stream_status=$?
  fi
  if [ "$backup_s3_stream_deadline_status" -ne 0 ]; then
    backup_s3_stream_status=$backup_s3_stream_deadline_status
  fi

  backup_s3_stream_cleanup_status=0
  backup_s3_reap_watchdog || backup_s3_stream_cleanup_status=$?
  backup_s3_sweep_producer || backup_s3_stream_cleanup_status=$?

  # Freeze signal delivery only after all owned processes have been reaped,
  # then take the final pending status.  Every caller runs this supervisor as
  # an asynchronous job, so the ignored traps disappear with that job's shell.
  trap '' HUP INT TERM USR2
  if [ "$backup_s3_stream_pending_status" -ne 0 ]; then
    backup_s3_stream_status=$backup_s3_stream_pending_status
  elif [ "$backup_s3_stream_status" -eq 0 ] \
    && [ "$backup_s3_stream_cleanup_status" -ne 0 ]; then
    backup_s3_stream_status=$backup_s3_stream_cleanup_status
  fi
  return "$backup_s3_stream_status"
}
