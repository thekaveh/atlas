#!/bin/sh
# services/comfyui/init/scripts/download_models.sh
#
# Downloads the bootstrapper-generated active ComfyUI model plan. Curated
# rows are trusted only when they carry an immutable Hugging Face revision and
# exact SHA-256. Custom/live/fallback rows retain their historical optional-
# checksum behavior, but are labelled explicitly as unverified when no digest
# was supplied.
#
# TSV columns:
#   name TAB type TAB filename TAB download_url TAB sha256 TAB target_dir TAB source
#   TAB required|optional
#
# Missing/empty manifests remain a successful, digest-bound no-op. Required
# download failures publish a failed status and exit nonzero; explicitly
# optional failures publish a warning without blocking readiness.
set -eu
umask 077

MODELS_ROOT="${COMFYUI_MODELS_PATH:-/models}"
MANIFEST_TSV="${COMFYUI_MANIFEST_TSV:-/comfyui-manifest/active-models.tsv}"
CONNECT_TIMEOUT="${COMFYUI_DOWNLOAD_CONNECT_TIMEOUT_SECONDS:-30}"
TOTAL_TIMEOUT="${COMFYUI_DOWNLOAD_TOTAL_TIMEOUT_SECONDS:-7200}"
LOCK_TIMEOUT="${COMFYUI_DOWNLOAD_LOCK_TIMEOUT_SECONDS:-120}"
DOWNLOAD_RETRIES="${COMFYUI_DOWNLOAD_RETRIES:-3}"

OK_COUNT=0
SKIP_COUNT=0
FAIL_COUNT=0
REQUIRED_FAIL_COUNT=0
OPTIONAL_FAIL_COUNT=0
PARTIAL_PATH=
LOCK_PATH=
LOCK_PROOF_PATH=
DOWNLOAD_LOG=
TRANSFER_PID=
HEARTBEAT_PID=
MODELS_ROOT_RESOLVED=
STATUS_TMP=
PLAN_SHA=
FOREIGN_LOCK_STALE_SECONDS=10

# Fail closed before even creating the private snapshot. This deliberately
# removes (rather than rewrites) a prior result because the new plan digest is
# not known yet; a snapshot-creation failure must not leave stale readiness.
if [ -d "$MODELS_ROOT" ] && [ ! -L "$MODELS_ROOT" ]; then
  rm -f "$MODELS_ROOT/.atlas-model-provisioning.tsv"
fi

# The manifest is snapshotted before package, target, cache, lock, or network
# effects. This is the only plan that validation and execution may inspect.
ACTIVE_COPY=$(mktemp "${TMPDIR:-/tmp}/comfy-active.XXXXXX")
chmod 600 "$ACTIVE_COPY"
if [ -f "$MANIFEST_TSV" ]; then
  cp "$MANIFEST_TSV" "$ACTIVE_COPY"
  chmod 600 "$ACTIVE_COPY"
fi

release_lock() {
  if [ -n "$LOCK_PATH" ] && [ -n "$LOCK_PROOF_PATH" ] \
      && [ -f "$LOCK_PATH" ] && [ ! -L "$LOCK_PATH" ] \
      && [ "$LOCK_PATH" -ef "$LOCK_PROOF_PATH" ]; then
    rm -f "$LOCK_PATH"
  fi
  if [ -n "$LOCK_PROOF_PATH" ]; then
    rm -f "$LOCK_PROOF_PATH"
  fi
  LOCK_PATH=
  LOCK_PROOF_PATH=
}

cleanup_transfer() {
  if [ -n "$TRANSFER_PID" ]; then
    kill "$TRANSFER_PID" 2>/dev/null || true
    wait "$TRANSFER_PID" 2>/dev/null || true
    TRANSFER_PID=
  fi
  if [ -n "$PARTIAL_PATH" ]; then
    rm -f "$PARTIAL_PATH"
    PARTIAL_PATH=
  fi
  if [ -n "$DOWNLOAD_LOG" ]; then
    rm -f "$DOWNLOAD_LOG"
    DOWNLOAD_LOG=
  fi
  if [ -n "$HEARTBEAT_PID" ]; then
    kill "$HEARTBEAT_PID" 2>/dev/null || true
    wait "$HEARTBEAT_PID" 2>/dev/null || true
    HEARTBEAT_PID=
  fi
  release_lock
}

# shellcheck disable=SC2329  # invoked by traps
cleanup_all() {
  cleanup_transfer
  if [ -n "$STATUS_TMP" ]; then
    rm -f "$STATUS_TMP"
  fi
  rm -f "$ACTIVE_COPY"
}

# shellcheck disable=SC2329  # invoked by traps
on_signal() {
  signal_number="$1"
  cleanup_all
  trap - 0 HUP INT TERM
  exit $((128 + signal_number))
}

trap cleanup_all 0
trap 'on_signal 1' HUP
trap 'on_signal 2' INT
trap 'on_signal 15' TERM

# Compute the identity of the private plan snapshot before validation. If a
# writable model root already exists, atomically hide any prior ready result
# before configuration, plan validation, cache, lock, or network effects.
if ! PLAN_SHA=$(sha256sum "$ACTIVE_COPY" | cut -d ' ' -f1); then
  if [ -d "$MODELS_ROOT" ] && [ ! -L "$MODELS_ROOT" ]; then
    rm -f "$MODELS_ROOT/.atlas-model-provisioning.tsv"
  fi
  exit 1
fi
if [ -d "$MODELS_ROOT" ] && [ ! -L "$MODELS_ROOT" ]; then
  STATUS_TMP=$(mktemp "$MODELS_ROOT/.atlas-model-provisioning.tsv.XXXXXX") || {
    rm -f "$MODELS_ROOT/.atlas-model-provisioning.tsv"
    exit 1
  }
  chmod 600 "$STATUS_TMP"
  printf 'v1\t%s\tprovisioning\t0\t0\n' "$PLAN_SHA" > "$STATUS_TMP"
  mv -f "$STATUS_TMP" "$MODELS_ROOT/.atlas-model-provisioning.tsv"
  STATUS_TMP=
fi

invalid_configuration=0
validate_number() {
  knob_name="$1"
  knob_value="$2"
  knob_min="$3"
  knob_max="$4"
  knob_invalid=0
  case "$knob_value" in
    ""|0|0[0-9]*|*[!0-9]*) knob_invalid=1 ;;
    *)
      # Bound the digit string before shell integer operators. Several /bin/sh
      # implementations overflow or emit a diagnostic for longer operands and
      # then incorrectly let the value through.
      if [ "${#knob_value}" -gt "${#knob_max}" ]; then
        knob_invalid=1
      elif [ "$knob_value" -lt "$knob_min" ] || [ "$knob_value" -gt "$knob_max" ]; then
        knob_invalid=1
      fi
      ;;
  esac
  if [ "$knob_invalid" -ne 0 ]; then
    invalid_configuration=1
    echo "✗ invalid downloader configuration for $knob_name"
  fi
}

validate_number connect-timeout "$CONNECT_TIMEOUT" 1 300
validate_number total-timeout "$TOTAL_TIMEOUT" 1 604800
validate_number lock-timeout "$LOCK_TIMEOUT" 1 3600
validate_number retries "$DOWNLOAD_RETRIES" 1 10
if [ "$invalid_configuration" -ne 0 ]; then
  echo "--- summary: 0 downloaded, 0 cached, 1 failed ---"
  exit 1
fi

valid_manifest_bytes() {
  [ ! -s "$ACTIVE_COPY" ] && return 0
  last_byte=$(tail -c 1 "$ACTIVE_COPY" | od -An -t u1 | tr -d ' ')
  [ "$last_byte" = "10" ] || return 1
  od -An -v -t u1 "$ACTIVE_COPY" | awk '
    { for (i = 1; i <= NF; i++) if (($i < 32 && $i != 9 && $i != 10) || $i == 127) exit 1 }
  '
}

validate_manifest_rows() {
  awk -F '\t' '
    function known_category(v) {
      return v ~ /^(checkpoint|vae|lora|controlnet|ipadapter|instantid|upscaler|embedding|clip|animatediff|motion_lora|video_model|voice_model|audio_model|mesh_model|diffusion_models|text_encoders)$/
    }
    function known_target(v) {
      return v ~ /^(checkpoints|vae|loras|controlnet|ipadapter|instantid|upscale_models|embeddings|clip|animatediff_models|animatediff_motion_lora|voice|audio|mesh_models|diffusion_models|text_encoders)$/
    }
    function exact_sha(v) { return v ~ /^[0-9a-f]{64}$/ }
    BEGIN { bad = 0 }
    NF != 7 && NF != 8 { print "✗ invalid download plan: every row must have 7 or 8 TSV columns"; bad = 1; next }
    $1 == "" || $1 == "." || $1 == ".." || length($1) > 256 || $1 ~ /[\\\/]/ { print "✗ invalid download plan: unsafe model name"; bad = 1 }
    !known_category($2) { print "✗ invalid download plan: unknown category"; bad = 1 }
    $3 == "" || $3 == "." || $3 == ".." || length($3) > 255 || $3 ~ /[\\\/]/ { print "✗ invalid download plan: unsafe filename"; bad = 1 }
    $4 !~ /^https?:\/\/[^\/[:space:]]+\/.+/ { print "✗ invalid download plan: URL must use http(s)"; bad = 1 }
    $5 != "" && !exact_sha($5) { print "✗ invalid download plan: invalid lowercase SHA-256"; bad = 1 }
    !known_target($6) { print "✗ invalid download plan: unsafe target_dir or unknown target"; bad = 1 }
    $7 !~ /^(curated|custom|huggingface|civitai|fallback|legacy)$/ { print "✗ invalid download plan: unknown source"; bad = 1 }
    NF == 8 && $8 !~ /^(required|optional)$/ { print "✗ invalid download plan: unknown provisioning policy"; bad = 1 }
    $7 == "curated" && !exact_sha($5) { print "✗ invalid download plan: curated row requires SHA-256"; bad = 1 }
    $7 == "curated" && $4 !~ /^https:\/\/huggingface\.co\/[^\/]+\/[^\/]+\/resolve\/[0-9a-f]{40}\/.+/ {
      print "✗ invalid download plan: curated URL requires immutable Hugging Face revision"; bad = 1
    }
    {
      key = $6 "/" $3
      metadata = $2 "\t" $4 "\t" $5 "\t" $7
      if (key in seen && seen[key] != metadata) {
        print "✗ invalid download plan: conflicting duplicate target"; bad = 1
      } else seen[key] = metadata
    }
    END { exit bad }
  ' "$ACTIVE_COPY"
}

if ! valid_manifest_bytes || ! validate_manifest_rows; then
  echo "✗ invalid download plan; refusing all effects"
  echo "--- summary: 0 downloaded, 0 cached, 1 failed ---"
  exit 1
fi

# Runtime tools are baked into the digest-pinned init image. Probe the transfer
# client only after the complete plan passes its trust checks, and before any
# target/cache/lock/network effect. Besides catching a malformed image early,
# this preserves a clear boundary at which paths are rechecked for races.
if ! wget --help >/dev/null 2>&1; then
  echo "✗ baked wget runtime tool is unavailable"
  echo "--- summary: 0 downloaded, 0 cached, 1 failed ---"
  exit 1
fi

unsafe_existing_paths=0
if [ -L "$MODELS_ROOT" ] || { [ -e "$MODELS_ROOT" ] && [ ! -d "$MODELS_ROOT" ]; }; then
  unsafe_existing_paths=1
fi
if [ "$unsafe_existing_paths" -eq 0 ]; then
  while IFS= read -r plan_row; do
    filename=$(printf '%s\n' "$plan_row" | cut -f3)
    target_dir=$(printf '%s\n' "$plan_row" | cut -f6)
    target_parent="$MODELS_ROOT/$target_dir"
    destination="$target_parent/$filename"
    if [ -L "$target_parent" ] || { [ -e "$target_parent" ] && [ ! -d "$target_parent" ]; } \
        || [ -L "$destination" ] || { [ -e "$destination" ] && [ ! -f "$destination" ]; }; then
      unsafe_existing_paths=1
      break
    fi
  done < "$ACTIVE_COPY"
fi
if [ "$unsafe_existing_paths" -ne 0 ]; then
  echo "✗ unsafe model path; refusing all effects"
  echo "--- summary: 0 downloaded, 0 cached, 1 failed ---"
  exit 1
fi

if [ -L "$MODELS_ROOT" ] || { [ -e "$MODELS_ROOT" ] && [ ! -d "$MODELS_ROOT" ]; }; then
  echo "✗ unsafe model path after runtime-tool preflight"
  exit 1
fi
if [ ! -d "$MODELS_ROOT" ]; then
  mkdir -p "$MODELS_ROOT" 2>/dev/null || true
fi
if [ -L "$MODELS_ROOT" ] || [ ! -d "$MODELS_ROOT" ]; then
  echo "✗ unsafe model path after root creation"
  exit 1
fi
for d in checkpoints vae loras controlnet ipadapter instantid \
         upscale_models embeddings clip animatediff_models \
         animatediff_motion_lora voice audio mesh_models \
         diffusion_models text_encoders; do
  model_subdir="$MODELS_ROOT/$d"
  if [ -L "$model_subdir" ] || { [ -e "$model_subdir" ] && [ ! -d "$model_subdir" ]; }; then
    echo "✗ unsafe model path after runtime-tool preflight"
    exit 1
  fi
  if [ ! -d "$model_subdir" ]; then
    mkdir "$model_subdir" 2>/dev/null || true
  fi
  if [ -L "$model_subdir" ] || [ ! -d "$model_subdir" ]; then
    echo "✗ unsafe model path after directory creation"
    exit 1
  fi
done
if [ -L "$MODELS_ROOT" ] || [ ! -d "$MODELS_ROOT" ]; then
  echo "✗ unsafe model path after directory creation"
  exit 1
fi
MODELS_ROOT_RESOLVED=$(realpath "$MODELS_ROOT")

write_provisioning_status() {
  status_state="$1"
  required_failed="$2"
  optional_failed="$3"
  status_path="$MODELS_ROOT/.atlas-model-provisioning.tsv"
  STATUS_TMP=$(mktemp "$MODELS_ROOT/.atlas-model-provisioning.tsv.XXXXXX")
  chmod 600 "$STATUS_TMP"
  printf 'v1\t%s\t%s\t%s\t%s\n' \
    "$PLAN_SHA" "$status_state" "$required_failed" "$optional_failed" > "$STATUS_TMP"
  mv -f "$STATUS_TMP" "$status_path"
  STATUS_TMP=
}

# Invalidate a prior ready result for this or another plan before cache,
# lock, or network effects. Readiness accepts only an exact-plan ready row.
write_provisioning_status provisioning 0 0

assert_safe_destination() {
  checked_dest="$1"
  checked_dir="$2"
  checked_parent="$MODELS_ROOT/$checked_dir"
  [ ! -L "$MODELS_ROOT" ] && [ -d "$MODELS_ROOT" ] \
    && [ "$(realpath "$MODELS_ROOT")" = "$MODELS_ROOT_RESOLVED" ] \
    && [ ! -L "$checked_parent" ] && [ -d "$checked_parent" ] \
    && [ "$(realpath "$checked_parent")" = "$MODELS_ROOT_RESOLVED/$checked_dir" ] \
    && [ ! -L "$checked_dest" ] \
    && { [ ! -e "$checked_dest" ] || [ -f "$checked_dest" ]; }
}

verify_file() {
  expected_sha="$1"
  candidate="$2"
  actual_sha=$(sha256sum "$candidate" | cut -d ' ' -f1)
  [ "$actual_sha" = "$expected_sha" ]
}

process_start_fingerprint() {
  process_pid="$1"
  if [ -r "/proc/$process_pid/stat" ]; then
    IFS= read -r process_stat < "/proc/$process_pid/stat" || return 1
    process_rest=${process_stat##*) }
    start_ticks=$(printf '%s\n' "$process_rest" | awk '{ print $20 }')
    [ -n "$start_ticks" ] || return 1
    boot_id=unknown-boot
    if [ -r /proc/sys/kernel/random/boot_id ]; then
      IFS= read -r boot_id < /proc/sys/kernel/random/boot_id || boot_id=unknown-boot
    fi
    printf '%s:%s\n' "$boot_id" "$start_ticks"
    return 0
  fi
  ps -o lstart= -p "$process_pid" 2>/dev/null | sed -n '1p'
}

process_namespace() {
  namespace_pid="$1"
  namespace_value=$(readlink "/proc/$namespace_pid/ns/pid" 2>/dev/null || true)
  if [ -n "$namespace_value" ]; then
    printf '%s\n' "$namespace_value"
  else
    # Never collapse an unreadable namespace to a shared "host" identity:
    # separate containers commonly reuse PID 1. A per-init opaque identity
    # makes other owners foreign/uninspectable and therefore lease-protected.
    printf 'opaque:%s\n' "$(basename "$ACTIVE_COPY")"
  fi
}

INIT_START=$(process_start_fingerprint "$$")
if [ -z "$INIT_START" ]; then
  echo "✗ cannot establish downloader process identity"
  exit 1
fi
INIT_NAMESPACE=$(process_namespace "$$")
INIT_TOKEN="$(basename "$ACTIVE_COPY").$$"

lock_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null
}

lock_uid() {
  stat -c '%u' "$1" 2>/dev/null || stat -f '%u' "$1" 2>/dev/null
}

lock_mtime() {
  stat -c '%Y' "$1" 2>/dev/null || stat -f '%m' "$1" 2>/dev/null
}

start_lock_heartbeat() {
  (
    heartbeat_sleep_pid=
    trap '
      if [ -n "$heartbeat_sleep_pid" ]; then
        kill "$heartbeat_sleep_pid" 2>/dev/null || true
        wait "$heartbeat_sleep_pid" 2>/dev/null || true
      fi
      exit 0
    ' HUP INT TERM
    while :; do
      sleep 2 &
      heartbeat_sleep_pid=$!
      wait "$heartbeat_sleep_pid" 2>/dev/null || exit 0
      heartbeat_sleep_pid=
      if [ ! -f "$LOCK_PATH" ] || [ -L "$LOCK_PATH" ] \
          || [ ! "$LOCK_PATH" -ef "$LOCK_PROOF_PATH" ]; then
        exit 0
      fi
      touch "$LOCK_PROOF_PATH" || exit 0
    done
  ) </dev/null >/dev/null 2>&1 &
  HEARTBEAT_PID=$!
}

acquire_target_lock() {
  lock_target="$1"
  lock_target_dir="$2"
  LOCK_PATH="${lock_target}.download.lock"
  waited=0
  while :; do
    if ! assert_safe_destination "$lock_target" "$lock_target_dir"; then
      echo "✗ $name: unsafe model path before lock"
      LOCK_PATH=
      return 1
    fi
    LOCK_PROOF_PATH=$(mktemp "${lock_target}.lock-owner.XXXXXX")
    chmod 600 "$LOCK_PROOF_PATH"
    printf 'v2\t%s\t%s\t%s\t%s\n' \
      "$$" "$INIT_NAMESPACE" "$INIT_START" "$INIT_TOKEN" > "$LOCK_PROOF_PATH"
    if [ ! -e "$LOCK_PATH" ] && [ ! -L "$LOCK_PATH" ] \
        && ln "$LOCK_PROOF_PATH" "$LOCK_PATH" 2>/dev/null \
        && [ "$LOCK_PATH" -ef "$LOCK_PROOF_PATH" ]; then
      for abandoned in "${lock_target}.part."* "${lock_target}.lock-owner."*; do
        if [ -f "$abandoned" ] && [ "$abandoned" != "$LOCK_PROOF_PATH" ]; then
          rm -f "$abandoned"
        fi
      done
      start_lock_heartbeat
      return 0
    fi
    rm -f "$LOCK_PROOF_PATH"
    LOCK_PROOF_PATH=

    stale=0
    inspectable=1
    if [ -L "$LOCK_PATH" ] || [ ! -f "$LOCK_PATH" ] \
        || [ "$(lock_mode "$LOCK_PATH" || true)" != "600" ] \
        || [ "$(lock_uid "$LOCK_PATH" || true)" != "$(id -u)" ]; then
      inspectable=0
    fi
    if [ "$inspectable" -eq 1 ]; then
      owner_fields=$(awk -F '\t' 'NF == 5 && $1 == "v2" && $2 ~ /^[0-9]+$/ && ($3 ~ /^pid:\[[0-9]+\]$/ || $3 ~ /^opaque:[A-Za-z0-9._-]+$/) && $5 ~ /^[A-Za-z0-9._-]+$/ { print NF }' "$LOCK_PATH")
      if [ "$owner_fields" = "5" ]; then
        tab=$(printf '\t')
        IFS="$tab" read -r owner_version owner_pid owner_namespace owner_start owner_token < "$LOCK_PATH" || inspectable=0
        if [ "$owner_version" != "v2" ] || [ -z "$owner_token" ]; then
          inspectable=0
        fi
      else
        inspectable=0
      fi
    fi
    if [ "$inspectable" -eq 1 ] && [ "$owner_namespace" = "$INIT_NAMESPACE" ]; then
      current_start=
      if kill -0 "$owner_pid" 2>/dev/null; then
        current_start=$(process_start_fingerprint "$owner_pid" || true)
      fi
      if [ -z "$current_start" ] || [ "$current_start" != "$owner_start" ]; then
        stale=1
      fi
    elif [ "$inspectable" -eq 1 ]; then
      # PIDs and /proc start times are namespace-local. A foreign namespace's
      # PID (commonly 1) may resolve to our own live process, so never inspect
      # it locally. The atomically published owner file is a lease: its owner
      # refreshes the inode mtime, while a crashed/restarted container becomes
      # recoverable after a conservative bounded grace period.
      owner_mtime=$(lock_mtime "$LOCK_PATH" || true)
      now_epoch=$(date +%s)
      case "$owner_mtime:$now_epoch" in
        *[!0-9:]*|:*) ;;
        *)
          owner_age=$((now_epoch - owner_mtime))
          if [ "$owner_age" -ge "$FOREIGN_LOCK_STALE_SECONDS" ]; then
            stale=1
          fi
          ;;
      esac
    fi
    if [ "$stale" -eq 1 ]; then
      stale_claim="${LOCK_PATH}.stale.${INIT_TOKEN}.${waited}"
      if ln "$LOCK_PATH" "$stale_claim" 2>/dev/null \
          && [ "$LOCK_PATH" -ef "$stale_claim" ] \
          && cmp -s "$LOCK_PATH" "$stale_claim"; then
        rm -f "$LOCK_PATH"
        rm -f "$stale_claim"
        echo "! $name: recovered stale download lock"
        continue
      fi
      rm -f "$stale_claim"
    fi
    if [ "$waited" -ge "$LOCK_TIMEOUT" ]; then
      echo "✗ $name: timed out waiting for another downloader; inspect the target lock"
      LOCK_PATH=
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
}

download_one() {
  name="$1"
  url="$2"
  dest="$3"
  sha="$4"
  source="$5"
  target_dir="$6"

  if ! assert_safe_destination "$dest" "$target_dir"; then
    echo "✗ $name: unsafe model path"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    return 0
  fi
  if ! acquire_target_lock "$dest" "$target_dir"; then
    FAIL_COUNT=$((FAIL_COUNT + 1))
    return 0
  fi
  if ! assert_safe_destination "$dest" "$target_dir"; then
    echo "✗ $name: unsafe model path"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    cleanup_transfer
    return 0
  fi

  if [ -s "$dest" ]; then
    if [ -n "$sha" ]; then
      if verify_file "$sha" "$dest"; then
        echo "= $name (cached, sha verified)"
        SKIP_COUNT=$((SKIP_COUNT + 1))
        cleanup_transfer
        return 0
      fi
      echo "! $name (cached but sha mismatch — downloading verified replacement)"
    else
      echo "= $name (cached, $source/unverified)"
      SKIP_COUNT=$((SKIP_COUNT + 1))
      cleanup_transfer
      return 0
    fi
  fi

  if ! assert_safe_destination "$dest" "$target_dir"; then
    echo "✗ $name: unsafe model path"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    cleanup_transfer
    return 0
  fi
  echo "+ $name → $dest"
  PARTIAL_PATH=$(mktemp "${dest}.part.XXXXXX")
  chmod 600 "$PARTIAL_PATH"
  DOWNLOAD_LOG=$(mktemp "${TMPDIR:-/tmp}/comfyui-download.XXXXXX")

  transfer_rc=0
  timeout -k 10 "$TOTAL_TIMEOUT" wget \
    --timeout="$CONNECT_TIMEOUT" --tries="$DOWNLOAD_RETRIES" \
    -O "$PARTIAL_PATH" "$url" >"$DOWNLOAD_LOG" 2>&1 &
  TRANSFER_PID=$!
  wait "$TRANSFER_PID" || transfer_rc=$?
  TRANSFER_PID=
  if [ "$transfer_rc" -ne 0 ]; then
    echo "✗ $name failed (bounded downloader exit $transfer_rc)"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    cleanup_transfer
    return 0
  fi
  if [ ! -s "$PARTIAL_PATH" ]; then
    echo "✗ $name failed (empty response)"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    cleanup_transfer
    return 0
  fi
  if [ -n "$sha" ] && ! verify_file "$sha" "$PARTIAL_PATH"; then
    echo "✗ $name failed (checksum mismatch)"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    cleanup_transfer
    return 0
  fi
  if ! assert_safe_destination "$dest" "$target_dir"; then
    echo "✗ $name: unsafe model path before publish"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    cleanup_transfer
    return 0
  fi
  if [ -z "$sha" ]; then
    echo "! $name accepted as $source/unverified (no SHA-256 supplied)"
  fi
  mv -f "$PARTIAL_PATH" "$dest"
  PARTIAL_PATH=
  OK_COUNT=$((OK_COUNT + 1))
  cleanup_transfer
}

echo "comfyui-init: Reading active ComfyUI models from a private snapshot..."
if [ ! -s "$ACTIVE_COPY" ]; then
  echo "(no active-models.tsv or empty manifest — nothing to download)"
else
  row_count=$(wc -l < "$ACTIVE_COPY" | tr -d ' ')
  echo "--- found $row_count active row(s) ---"
  while IFS= read -r plan_row; do
    name=$(printf '%s\n' "$plan_row" | cut -f1)
    filename=$(printf '%s\n' "$plan_row" | cut -f3)
    url=$(printf '%s\n' "$plan_row" | cut -f4)
    sha=$(printf '%s\n' "$plan_row" | cut -f5)
    target_dir=$(printf '%s\n' "$plan_row" | cut -f6)
    source=$(printf '%s\n' "$plan_row" | cut -f7)
    provisioning=$(printf '%s\n' "$plan_row" | cut -f8)
    [ -n "$provisioning" ] || provisioning=required
    dir="$target_dir"
    failures_before=$FAIL_COUNT
    download_one "$name" "$url" "$MODELS_ROOT/$dir/$filename" "$sha" "$source" "$dir"
    if [ "$FAIL_COUNT" -gt "$failures_before" ]; then
      if [ "$provisioning" = "optional" ]; then
        OPTIONAL_FAIL_COUNT=$((OPTIONAL_FAIL_COUNT + 1))
        echo "! $name: optional provisioning failed; readiness is not blocked"
      else
        REQUIRED_FAIL_COUNT=$((REQUIRED_FAIL_COUNT + 1))
      fi
    fi
  done < "$ACTIVE_COPY"
fi

echo "--- summary: $OK_COUNT downloaded, $SKIP_COUNT cached, $FAIL_COUNT failed ---"
if [ "$REQUIRED_FAIL_COUNT" -gt 0 ]; then
  write_provisioning_status failed "$REQUIRED_FAIL_COUNT" "$OPTIONAL_FAIL_COUNT"
  exit 1
fi
write_provisioning_status ready 0 "$OPTIONAL_FAIL_COUNT"
exit 0
