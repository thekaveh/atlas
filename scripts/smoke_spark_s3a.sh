#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <spark-image>" >&2
  exit 2
fi

spark_image="$1"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
minio_image="minio/minio:RELEASE.2025-09-07T16-13-09Z"
mc_image="minio/mc:RELEASE.2025-08-13T08-35-41Z"
access_key="atlasS3aAccess"
secret_key="atlasS3aSecret123"
overall_timeout="${ATLAS_S3A_SMOKE_TIMEOUT_SECONDS:-600}"
command_timeout="${ATLAS_S3A_COMMAND_TIMEOUT_SECONDS:-30}"
pull_timeout="${ATLAS_S3A_PULL_TIMEOUT_SECONDS:-300}"
for timeout_value in "$overall_timeout" "$command_timeout" "$pull_timeout"; do
  if [[ ! "$timeout_value" =~ ^[1-9][0-9]*$ ]]; then
    echo "S3A smoke timeouts must be positive integer seconds" >&2
    exit 2
  fi
done
deadline=$((SECONDS + overall_timeout))
owner_token="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
if [[ ! "$owner_token" =~ ^[0-9a-f]{32}$ ]]; then
  echo "S3A smoke could not generate a 128-bit ownership token" >&2
  exit 2
fi
export ATLAS_S3A_OWNER_TOKEN="$owner_token"
owner_label="com.atlas.s3a-smoke-token"
suffix="$owner_token"
network="atlas-s3a-smoke-${suffix}"
server="atlas-s3a-minio-${suffix}"
client="atlas-s3a-mc-${suffix}"
probe="atlas-s3a-spark-${suffix}"
ready_dir=""
ready_counter=0
active_bounded_pid=""
bounded_phase="idle"
interruption_generation=0
interruption_signal=""
interruption_status=0
network_uncertain=false
server_uncertain=false
client_uncertain=false
probe_uncertain=false
network_registered=false
server_registered=false
client_registered=false
probe_registered=false
network_reconcile_timeout="$command_timeout"
server_reconcile_timeout="$command_timeout"
client_reconcile_timeout="$command_timeout"
probe_reconcile_timeout="$pull_timeout"

handle_interrupt() {
  local signal_name="$1"
  local exit_status="$2"
  if ((interruption_status == 0)); then
    interruption_status="$exit_status"
  fi
  interruption_signal="$signal_name"
  interruption_generation=$((interruption_generation + 1))
  case "$bounded_phase" in
    *-owned)
      if [[ -n "$active_bounded_pid" ]]; then
        kill -s "$signal_name" "$active_bounded_pid" 2>/dev/null || true
      fi
      ;;
    idle)
      exit "$interruption_status"
      ;;
  esac
}

maybe_inject_test_signal() {
  local phase="$1"
  local label="${2:-}"
  if [[ "${ATLAS_S3A_TEST_SIGNAL_PHASE:-}" == "$phase" ]] && \
    { [[ -z "${ATLAS_S3A_TEST_SIGNAL_LABEL:-}" ]] || \
      [[ "${ATLAS_S3A_TEST_SIGNAL_LABEL}" == "$label" ]]; }; then
    ATLAS_S3A_TEST_SIGNAL_PHASE=""
    kill -s "${ATLAS_S3A_TEST_SIGNAL_NAME:-TERM}" "$$"
  fi
}

child_is_running() {
  kill -0 "$1" 2>/dev/null
}

mark_successful_resource_command() {
  case "$1" in
    "create S3A smoke network") network_uncertain=false ;;
    "start S3A smoke MinIO") server_uncertain=false ;;
    "probe S3A smoke MinIO readiness") client_uncertain=false ;;
    "run Spark S3A round trip") probe_uncertain=false ;;
  esac
}

run_owned_bounded() {
  local ownership="$1"
  local label="$2"
  local requested_timeout="$3"
  local launch_generation="$interruption_generation"
  local bounded_status=0
  local child_pid
  local handshake_tick
  local handshake_ticks
  local handshake_failed=false
  local handshake_timeout=2
  local ready_file
  shift 3
  if [[ "$ownership" == cleanup ]]; then
    handshake_timeout=1
  fi
  ready_counter=$((ready_counter + 1))
  ready_file="${ready_dir}/${ready_counter}"
  bounded_phase="${ownership}-launching"
  (
    trap - HUP INT TERM
    exec python3 "$repo_root/scripts/bounded_subprocess.py" \
      --label "$label" \
      --timeout-seconds "$requested_timeout" \
      --ready-file "$ready_file" \
      --forward-stderr \
      -- "$@"
  ) &
  child_pid=$!
  maybe_inject_test_signal "${ownership}-launch-window" "$label"
  active_bounded_pid="$child_pid"
  bounded_phase="${ownership}-starting"
  if ((requested_timeout < handshake_timeout)); then
    handshake_timeout="$requested_timeout"
  fi
  handshake_ticks=$((handshake_timeout * 10))
  for ((handshake_tick = 0; handshake_tick < handshake_ticks; handshake_tick++)); do
    if [[ -s "$ready_file" ]]; then
      break
    fi
    if ! child_is_running "$active_bounded_pid"; then
      break
    fi
    sleep 0.1 || true
  done
  if [[ -s "$ready_file" ]]; then
    bounded_phase="${ownership}-owned"
    if ((interruption_generation != launch_generation)); then
      kill -s "$interruption_signal" "$active_bounded_pid" 2>/dev/null || true
    fi
  else
    # Before readiness, either Python has not installed its handlers yet (and
    # therefore cannot have launched Docker), or the guarded wrapper failed to
    # publish its pre-launch handshake. TERM is safe and bounds both cases.
    handshake_failed=true
    kill -s TERM "$active_bounded_pid" 2>/dev/null || true
    sleep 0.05 || true
    if child_is_running "$active_bounded_pid"; then
      kill -s KILL "$active_bounded_pid" 2>/dev/null || true
    fi
  fi

  # A trap interrupts wait before the child is necessarily gone. Keep waiting
  # until it is reaped, even if cancellation is repeated during that window.
  while true; do
    if wait "$active_bounded_pid"; then
      bounded_status=0
      break
    else
      bounded_status=$?
    fi
    if ! kill -0 "$active_bounded_pid" 2>/dev/null; then
      break
    fi
  done
  rm -f "$ready_file"
  if [[ "$ownership" == command ]] && ((bounded_status == 0)); then
    mark_successful_resource_command "$label"
  fi
  maybe_inject_test_signal "${ownership}-exit-window" "$label"
  active_bounded_pid=""
  if [[ "$ownership" == command ]]; then
    bounded_phase="idle"
    if ((interruption_status != 0)); then
      exit "$interruption_status"
    fi
  else
    bounded_phase="cleanup-idle"
  fi
  if [[ "$handshake_failed" == true ]]; then
    return 126
  fi
  return "$bounded_status"
}

run_bounded() {
  local label="$1"
  local requested_timeout="$2"
  shift 2
  if ((interruption_status != 0)); then
    exit "$interruption_status"
  fi
  local remaining=$((deadline - SECONDS))
  if ((remaining < 1)); then
    echo "S3A smoke exceeded its ${overall_timeout}s overall deadline" >&2
    return 124
  fi
  if ((requested_timeout > remaining)); then
    requested_timeout="$remaining"
  fi
  run_owned_bounded command "$label" "$requested_timeout" "$@"
}

cleanup_bounded() {
  local cleanup_deadline=$((SECONDS + 10))
  local attempt_generation
  local last_status=1
  local remaining
  while ((SECONDS < cleanup_deadline)); do
    attempt_generation="$interruption_generation"
    remaining=$((cleanup_deadline - SECONDS))
    if run_owned_bounded cleanup "S3A smoke cleanup" "$remaining" \
      docker "$@"; then
      return 0
    else
      last_status=$?
    fi
    # Retry only a newly interrupted attempt; ordinary Docker failures are
    # reported to the EXIT trap instead of being silently converted to success.
    if ((interruption_generation == attempt_generation)); then
      return "$last_status"
    fi
  done
  return "$last_status"
}

inspect_resource_owner() {
  local kind="$1"
  local name="$2"
  local output
  local status
  local actual
  local observed_token
  if [[ "$kind" == container ]]; then
    output="$(cleanup_bounded container inspect --format \
      '{{.Name}} {{index .Config.Labels "com.atlas.s3a-smoke-token"}}' \
      "$name" 2>/dev/null)"
  else
    output="$(cleanup_bounded network inspect --format \
      '{{.Name}} {{index .Labels "com.atlas.s3a-smoke-token"}}' \
      "$name" 2>/dev/null)"
  fi
  status=$?
  if ((status == 0)); then
    actual="${output%% *}"
    observed_token="${output#* }"
    actual="${actual#/}"
    if [[ "$actual" != "$name" || "$observed_token" == "$output" ]]; then
      return 3
    fi
    if [[ "$observed_token" == "$owner_token" ]]; then
      return 0
    fi
    return 2
  fi
  if [[ "$kind" == container ]]; then
    output="$(cleanup_bounded ps -a --filter "name=^/${name}$" \
      --format '{{.Names}}' 2>/dev/null)" || return 3
  else
    output="$(cleanup_bounded network ls --filter "name=^${name}$" \
      --format '{{.Name}}' 2>/dev/null)" || return 3
  fi
  if grep -Fxq "$name" <<<"$output"; then
    return 3
  fi
  return 1
}

cleanup_resource() {
  local kind="$1"
  local name="$2"
  local uncertain="$3"
  local reconcile_timeout="$4"
  local reconcile_deadline="$SECONDS"
  local ownership_status
  if [[ "$uncertain" == true ]]; then
    reconcile_deadline=$((SECONDS + reconcile_timeout))
  fi
  while true; do
    inspect_resource_owner "$kind" "$name"
    ownership_status=$?
    case "$ownership_status" in
      0)
        if [[ "$kind" == container ]]; then
          cleanup_bounded rm -f "$name" >/dev/null 2>&1 || return 1
        else
          cleanup_bounded network rm "$name" >/dev/null 2>&1 || return 1
        fi
        uncertain=false
        ;;
      1)
        if [[ "$uncertain" == false ]] || ((SECONDS >= reconcile_deadline)); then
          return 0
        fi
        ;;
      2)
        return 0
        ;;
      *)
        if ((SECONDS >= reconcile_deadline)); then
          return 1
        fi
        ;;
    esac
    sleep 0.1 || true
  done
}

remaining_reconcile_timeout() {
  local requested_timeout="$1"
  local remaining=$((deadline - SECONDS))
  if ((remaining < 1)); then
    remaining=1
  fi
  if ((requested_timeout > remaining)); then
    requested_timeout="$remaining"
  fi
  printf '%s\n' "$requested_timeout"
}

cleanup() {
  local original_status="$1"
  local cleanup_failed=0
  local final_status="$original_status"
  local ready_index
  # EXIT traps inherit errexit.  Cleanup is intentionally best-effort, and a
  # failed container removal must not prevent the network and handshake files
  # from being removed.
  set +e
  maybe_inject_test_signal "cleanup-entry"
  if [[ "$probe_registered" == true ]]; then
    cleanup_resource container "$probe" "$probe_uncertain" "$probe_reconcile_timeout" || cleanup_failed=1
  fi
  if [[ "$client_registered" == true ]]; then
    cleanup_resource container "$client" "$client_uncertain" "$client_reconcile_timeout" || cleanup_failed=1
  fi
  if [[ "$server_registered" == true ]]; then
    cleanup_resource container "$server" "$server_uncertain" "$server_reconcile_timeout" || cleanup_failed=1
  fi
  if [[ "$network_registered" == true ]]; then
    cleanup_resource network "$network" "$network_uncertain" "$network_reconcile_timeout" || cleanup_failed=1
  fi
  if [[ -n "$ready_dir" ]]; then
    for ((ready_index = 1; ready_index <= ready_counter; ready_index++)); do
      rm -f "${ready_dir}/${ready_index}"
    done
    rm -f "${ready_dir}"/.*.pending
    rmdir "$ready_dir" 2>/dev/null || true
  fi
  trap - EXIT
  bounded_phase="idle"
  if ((cleanup_failed != 0)); then
    echo "S3A smoke cleanup could not be proven" >&2
    if ((final_status == 0)); then
      final_status=1
    fi
  fi
  if ((interruption_status != 0)); then
    exit "$interruption_status"
  fi
  exit "$final_status"
}
trap 'bounded_phase=cleanup-idle cleanup "$?"' EXIT
trap 'handle_interrupt HUP 129' HUP
trap 'handle_interrupt INT 130' INT
trap 'handle_interrupt TERM 143' TERM

ready_dir="$(mktemp -d "${TMPDIR:-/tmp}/atlas-s3a-ready.XXXXXX")"

run_bounded "pull MinIO server image" "$pull_timeout" docker pull "$minio_image" \
  >/dev/null
run_bounded "pull MinIO client image" "$pull_timeout" docker pull "$mc_image" \
  >/dev/null
network_reconcile_timeout="$(remaining_reconcile_timeout "$command_timeout")"
network_registered=true
network_uncertain=true
run_bounded "create S3A smoke network" "$command_timeout" \
  docker network create --label "${owner_label}=${owner_token}" "$network" >/dev/null
network_uncertain=false
server_reconcile_timeout="$(remaining_reconcile_timeout "$command_timeout")"
server_registered=true
server_uncertain=true
run_bounded "start S3A smoke MinIO" "$command_timeout" docker run \
  --pull=never \
  --detach --rm \
  --name "$server" \
  --label "${owner_label}=${owner_token}" \
  --network "$network" \
  --network-alias minio \
  --env "MINIO_ROOT_USER=${access_key}" \
  --env "MINIO_ROOT_PASSWORD=${secret_key}" \
  --tmpfs /data:rw,size=128m \
  "$minio_image" server /data --address :9000 >/dev/null
server_uncertain=false

ready=false
for _attempt in $(seq 1 60); do
  client_reconcile_timeout="$(remaining_reconcile_timeout "$command_timeout")"
  client_registered=true
  client_uncertain=true
  if run_bounded "probe S3A smoke MinIO readiness" "$command_timeout" docker run \
    --pull=never \
    --name "$client" \
    --label "${owner_label}=${owner_token}" \
    --rm \
    --network "$network" \
    --env "MC_HOST_atlas=http://${access_key}:${secret_key}@minio:9000" \
    "$mc_image" mb --ignore-existing atlas/spark-history >/dev/null 2>&1; then
    client_uncertain=false
    ready=true
    break
  fi
  if ! cleanup_resource container "$client" "$client_uncertain" "$client_reconcile_timeout"; then
    echo "S3A smoke could not prove readiness-client cleanup" >&2
    exit 1
  fi
  client_uncertain=false
  if ((SECONDS >= deadline)); then
    break
  fi
  sleep 1 || true
  if ((interruption_status != 0)); then
    exit "$interruption_status"
  fi
done
if [[ "$ready" != true ]]; then
  echo "MinIO did not become ready for the S3A smoke" >&2
  exit 1
fi

probe_reconcile_timeout="$(remaining_reconcile_timeout "$pull_timeout")"
probe_registered=true
probe_uncertain=true
run_bounded "run Spark S3A round trip" "$pull_timeout" docker run \
  --pull=never \
  --name "$probe" \
  --label "${owner_label}=${owner_token}" \
  --rm \
  --network "$network" \
  --mount "type=bind,src=${repo_root}/scripts/spark_s3a_roundtrip.py,dst=/tmp/spark_s3a_roundtrip.py,readonly" \
  "$spark_image" \
  /opt/spark/bin/spark-submit \
  --master 'local[1]' \
  --conf spark.ui.enabled=false \
  --conf spark.hadoop.fs.s3a.endpoint=http://minio:9000 \
  --conf spark.hadoop.fs.s3a.endpoint.region=us-east-1 \
  --conf spark.hadoop.fs.s3a.path.style.access=true \
  --conf spark.hadoop.fs.s3a.connection.ssl.enabled=false \
  --conf spark.hadoop.fs.s3a.aws.credentials.provider=org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider \
  --conf "spark.hadoop.fs.s3a.access.key=${access_key}" \
  --conf "spark.hadoop.fs.s3a.secret.key=${secret_key}" \
  /tmp/spark_s3a_roundtrip.py \
  s3a://spark-history/atlas-ci-roundtrip
probe_uncertain=false

if [[ -n "${ATLAS_S3A_TEST_EVENT_LOG:-}" ]]; then
  printf '%s\n' "final-command-returned" >> "$ATLAS_S3A_TEST_EVENT_LOG"
fi
