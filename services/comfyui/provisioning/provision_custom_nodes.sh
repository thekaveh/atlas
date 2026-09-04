#!/bin/bash
# AI-Dock provisioning hook for Atlas-managed ComfyUI custom nodes.
#
# Mounted at /opt/ai-dock/bin/provisioning.sh. AI-Dock sources and executes
# this script before launching ComfyUI, which lets Atlas install node Python
# dependencies into the same runtime environment that ComfyUI uses.

set -u

CUSTOM_NODES_ROOT="${COMFYUI_CUSTOM_NODES_PATH:-/opt/ComfyUI/custom_nodes}"
CUSTOM_NODES_TSV="${COMFYUI_CUSTOM_NODES_TSV:-/comfyui-manifest/active-custom-nodes.tsv}"
COMFYUI_MANIFEST_ROOT="${COMFYUI_MANIFEST_ROOT:-/comfyui-manifest}"

OK_COUNT=0
SKIP_COUNT=0
FAIL_COUNT=0
REQUIRED_FAIL_COUNT=0
OPTIONAL_FAIL_COUNT=0
SECURITY_FAIL_COUNT=0
PLAN_SHA=
CUSTOM_NODES_ROOT_RESOLVED=
PLAN_COPY=
SUCCESSFUL_NODES=
LOCK_WORKSPACE=
STATUS_TMP=
PRIVATE_TMP_ROOT=

cleanup_status_tmp() {
  if [ -n "$STATUS_TMP" ]; then
    case "$STATUS_TMP" in
      ./.atlas-node-provisioning.tsv.*) rm -f -- "$STATUS_TMP" ;;
    esac
    STATUS_TMP=
  fi
}

write_provisioning_status() {
  status_state="$1"
  required_failed="$2"
  optional_failed="$3"
  status_path="./.atlas-node-provisioning.tsv"
  safe_node_root || return 1
  STATUS_TMP=$(mktemp "./.atlas-node-provisioning.tsv.XXXXXX") || return 1
  if ! chmod 600 "$STATUS_TMP" \
      || ! printf 'v1\t%s\t%s\t%s\t%s\n' \
        "$PLAN_SHA" "$status_state" "$required_failed" "$optional_failed" \
        > "$STATUS_TMP"; then
    cleanup_status_tmp
    return 1
  fi
  if ! safe_node_root \
      || [ -L "$STATUS_TMP" ] \
      || [ "$(realpath "$STATUS_TMP" 2>/dev/null)" != \
        "$(realpath .)/$(basename "$STATUS_TMP")" ]; then
    cleanup_status_tmp
    return 1
  fi
  if ! mv -f "$STATUS_TMP" "$status_path"; then
    cleanup_status_tmp
    return 1
  fi
  STATUS_TMP=
  safe_node_root \
    && [ -f "$status_path" ] \
    && [ ! -L "$status_path" ] \
    && [ "$(realpath "$status_path" 2>/dev/null)" = \
      "$(realpath .)/.atlas-node-provisioning.tsv" ]
}

safe_node_root() {
  [ -n "$CUSTOM_NODES_ROOT_RESOLVED" ] \
    && [ -d . ]
}

safe_node_destination() {
  checked_name="$1"
  checked_dest="./$checked_name"
  safe_node_root || return 1
  [ ! -L "$checked_dest" ] || return 1
  [ ! -L "$checked_dest/.git" ] || return 1
  if [ -e "$checked_dest" ]; then
    [ -d "$checked_dest" ] || return 1
    [ "$(realpath "$checked_dest" 2>/dev/null)" = \
      "$(realpath .)/$checked_name" ] || return 1
  fi
}

safe_existing_node() {
  checked_name="$1"
  safe_node_destination "$checked_name" \
    && [ -d "./$checked_name/.git" ] \
    && [ ! -L "./$checked_name/.git" ]
}

safe_dependency_lock() {
  checked_lock="$1"
  checked_sha="$2"
  checked_expected="${COMFYUI_MANIFEST_ROOT}/custom-node-locks/${checked_sha}.txt"
  printf '%s' "$checked_sha" | grep -Eq '^[0-9a-f]{64}$' \
    && [ "$checked_lock" = "$checked_expected" ] \
    && [ -f "$checked_lock" ] \
    && [ ! -L "$checked_lock" ] \
    && printf '%s  %s\n' "$checked_sha" "$checked_lock" \
      | sha256sum -c - >/dev/null 2>&1
}

cleanup_private_state() {
  cleanup_status_tmp
  [ -n "$PLAN_COPY" ] && rm -f -- "$PLAN_COPY"
  [ -n "$SUCCESSFUL_NODES" ] && rm -f -- "$SUCCESSFUL_NODES"
  if [ -n "$LOCK_WORKSPACE" ] \
      && [ -d "$LOCK_WORKSPACE" ] \
      && [ ! -L "$LOCK_WORKSPACE" ]; then
    rm -rf -- "$LOCK_WORKSPACE"
  fi
}

safe_private_lock() {
  private_lock="$1"
  private_sha="$2"
  [ -n "$LOCK_WORKSPACE" ] \
    && [ -d "$LOCK_WORKSPACE" ] \
    && [ ! -L "$LOCK_WORKSPACE" ] \
    && [ -f "$private_lock" ] \
    && [ ! -L "$private_lock" ] \
    && [ "$(realpath "$private_lock" 2>/dev/null)" = \
      "$(realpath "$LOCK_WORKSPACE")/$private_sha.txt" ] \
    && printf '%s  %s\n' "$private_sha" "$private_lock" \
      | sha256sum -c - >/dev/null 2>&1
}

snapshot_dependency_lock() {
  original_lock="$1"
  original_sha="$2"
  private_lock="$LOCK_WORKSPACE/$original_sha.txt"
  private_tmp="$LOCK_WORKSPACE/.$original_sha.tmp.$$"

  if safe_private_lock "$private_lock" "$original_sha"; then
    printf '%s\n' "$private_lock"
    return 0
  fi
  rm -f -- "$private_tmp"
  safe_dependency_lock "$original_lock" "$original_sha" || return 1
  cp -- "$original_lock" "$private_tmp" || return 1
  chmod 600 "$private_tmp" || return 1
  if [ -L "$private_tmp" ] \
      || ! printf '%s  %s\n' "$original_sha" "$private_tmp" \
        | sha256sum -c - >/dev/null 2>&1; then
    rm -f -- "$private_tmp"
    return 1
  fi
  mv -f "$private_tmp" "$private_lock" || return 1
  safe_private_lock "$private_lock" "$original_sha" || return 1
  printf '%s\n' "$private_lock"
}

mark_security_failure() {
  SECURITY_FAIL_COUNT=$((SECURITY_FAIL_COUNT + 1))
  # Unsafe filesystem state is not an advisory asset failure. Even an optional
  # node must not make ComfyUI healthy while it resolves outside the managed
  # custom-node tree.
  [ "$REQUIRED_FAIL_COUNT" -gt 0 ] || REQUIRED_FAIL_COUNT=1
}

preflight_plan() {
  checked_plan="$1"

  # POSIX command substitution strips trailing newlines. Appending a sentinel
  # makes a final newline observable without parsing the plan a second way.
  if [ -s "$checked_plan" ]; then
    final_byte_with_sentinel=$(tail -c 1 "$checked_plan"; printf x)
    newline_with_sentinel=$(printf '\nx')
    [ "$final_byte_with_sentinel" = "$newline_with_sentinel" ] || return 1
  fi

  LC_ALL=C awk -F '\t' -v lock_root="$COMFYUI_MANIFEST_ROOT/custom-node-locks/" '
    function exact_ref(v) { return v ~ /^[0-9a-fA-F]{40}$/ }
    function exact_sha(v) { return v ~ /^[0-9a-f]{64}$/ }
    function safe_name(v) {
      return v != "" && v != "." && v != ".." && length(v) <= 255 && v !~ /[\\\/]/
    }
    function safe_repo(v) {
      return v ~ /^https:\/\/github[.]com\/[^\/[:space:]]+\/[^\/[:space:]]+[.]git$/
    }
    {
      printable = $0
      gsub(/\t/, "", printable)
      if (printable ~ /[[:cntrl:]]/) bad = 1
      if (NF != 6 && NF != 7) bad = 1
      if (!safe_name($1)) {
        print "atlas-comfyui-provision: unsafe custom node name; refusing plan"
        bad = 1
      }
      if (!safe_repo($2)) {
        print "atlas-comfyui-provision: custom node has unsafe repo; refusing plan"
        bad = 1
      }
      if (!exact_ref($3)) {
        print "atlas-comfyui-provision: custom node has unsafe ref; refusing plan"
        bad = 1
      }
      if ($4 != "true" && $4 != "false") {
        print "atlas-comfyui-provision: invalid install_requirements flag; refusing plan"
        bad = 1
      }
      if ($4 == "true" && (!exact_sha($6) || $5 != lock_root $6 ".txt")) {
        print "atlas-comfyui-provision: dependency lock is missing or unpinned; refusing plan"
        bad = 1
      }
      if ($4 == "false" && ($5 != "" || $6 != "")) {
        print "atlas-comfyui-provision: unexpected dependency lock; refusing plan"
        bad = 1
      }
      if (NF == 7 && $7 !~ /^(required|optional)$/) {
        print "atlas-comfyui-provision: invalid provisioning policy; refusing plan"
        bad = 1
      }
      if ($1 in prior && prior[$1] != $0) {
        print "atlas-comfyui-provision: conflicting custom node metadata; refusing plan"
        bad = 1
      }
      prior[$1] = $0
    }
    END { exit bad }
  ' "$checked_plan" || return 1

  # Verify every declared dependency lock before the first Git, pip, or node
  # tree effect. Structural validation above guarantees this loop sees every
  # row and that fields cannot shift.
  while IFS= read -r checked_row; do
    checked_install=$(printf '%s\n' "$checked_row" | cut -f4)
    [ "$checked_install" = "true" ] || continue
    checked_lock=$(printf '%s\n' "$checked_row" | cut -f5)
    checked_sha=$(printf '%s\n' "$checked_row" | cut -f6)
    safe_dependency_lock "$checked_lock" "$checked_sha" || return 1
  done < "$checked_plan"
}

pip_install() {
  if [ -n "${COMFYUI_VENV_PIP:-}" ] && [ -x "${COMFYUI_VENV_PIP}" ]; then
    "${COMFYUI_VENV_PIP}" install --no-cache-dir "$@"
  elif command -v micromamba >/dev/null 2>&1; then
    micromamba run -n comfyui pip install --no-cache-dir "$@"
  elif command -v pip >/dev/null 2>&1; then
    pip install --no-cache-dir "$@"
  else
    echo "atlas-comfyui-provision: no pip installer found for $*"
    return 1
  fi
}

install_custom_node() {
  name="$1"; repo="$2"; ref="$3"

  if ! safe_node_destination "$name"; then
    echo "atlas-comfyui-provision: $name has an unsafe destination; skipping"
    mark_security_failure
    FAIL_COUNT=$((FAIL_COUNT + 1))
    return 0
  fi
  dest="./$name"
  if [ -d "$dest/.git" ]; then
    if ! safe_existing_node "$name"; then
      echo "atlas-comfyui-provision: $name cached destination is unsafe"
      mark_security_failure
      FAIL_COUNT=$((FAIL_COUNT + 1))
      return 0
    fi
    current_ref=$(git -C "$dest" rev-parse HEAD 2>/dev/null || true)
    if [ "$current_ref" = "$ref" ]; then
      if safe_existing_node "$name"; then
        echo "atlas-comfyui-provision: $name cached at $ref"
        SKIP_COUNT=$((SKIP_COUNT + 1))
      else
        echo "atlas-comfyui-provision: $name destination changed during cache check"
        mark_security_failure
        FAIL_COUNT=$((FAIL_COUNT + 1))
        return 0
      fi
    else
      echo "atlas-comfyui-provision: updating $name to $ref"
      if safe_existing_node "$name" \
          && git -C "$dest" fetch origin "$ref" \
          && safe_existing_node "$name" \
          && git -C "$dest" checkout --detach "$ref" \
          && safe_existing_node "$name"; then
        OK_COUNT=$((OK_COUNT + 1))
      else
        echo "atlas-comfyui-provision: $name update failed"
        safe_existing_node "$name" || mark_security_failure
        FAIL_COUNT=$((FAIL_COUNT + 1))
        return 0
      fi
    fi
  else
    tmp="$dest.tmp"
    if ! safe_node_root; then
      echo "atlas-comfyui-provision: custom-node root changed before clone"
      mark_security_failure
      FAIL_COUNT=$((FAIL_COUNT + 1))
      return 0
    fi
    if [ -L "$tmp" ]; then
      echo "atlas-comfyui-provision: $name temporary destination is unsafe"
      mark_security_failure
      FAIL_COUNT=$((FAIL_COUNT + 1))
      return 0
    fi
    rm -rf "$tmp"
    echo "atlas-comfyui-provision: cloning $name at $ref"
    if safe_node_root \
        && git clone "$repo" "$tmp" \
        && safe_node_root \
        && [ ! -L "$tmp" ] \
        && [ "$(realpath "$tmp" 2>/dev/null)" = "$(realpath .)/$name.tmp" ] \
        && git -C "$tmp" checkout --detach "$ref" \
        && safe_node_root \
        && [ ! -L "$tmp" ] \
        && [ "$(realpath "$tmp" 2>/dev/null)" = "$(realpath .)/$name.tmp" ] \
        && safe_node_destination "$name" \
        && [ ! -e "$dest" ] \
        && mv "$tmp" "$dest" \
        && safe_existing_node "$name"; then
      OK_COUNT=$((OK_COUNT + 1))
    else
      echo "atlas-comfyui-provision: $name clone failed"
      if ! safe_node_root \
          || [ -L "$tmp" ] \
          || { [ -e "$tmp" ] \
            && [ "$(realpath "$tmp" 2>/dev/null)" != \
              "$(realpath .)/$name.tmp" ]; }; then
        mark_security_failure
      fi
      if safe_node_root && [ ! -L "$tmp" ]; then
        rm -rf "$tmp"
      fi
      FAIL_COUNT=$((FAIL_COUNT + 1))
      return 0
    fi
  fi

}

install_node_dependencies() {
  name="$1"; requirements_lock="$2"; requirements_lock_sha256="$3"

  echo "atlas-comfyui-provision: installing hash-locked requirements for $name"
  if ! safe_existing_node "$name"; then
    mark_security_failure
    echo "atlas-comfyui-provision: requirements install failed for $name"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    return 0
  fi
  expected_lock="${COMFYUI_MANIFEST_ROOT}/custom-node-locks/${requirements_lock_sha256}.txt"
  if [ "$requirements_lock" != "$expected_lock" ]; then
    echo "atlas-comfyui-provision: dependency lock verification failed for $name"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    return 0
  fi
  private_lock=$(snapshot_dependency_lock \
    "$requirements_lock" "$requirements_lock_sha256") || {
    echo "atlas-comfyui-provision: dependency lock verification failed for $name"
    echo "atlas-comfyui-provision: requirements install failed for $name"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    return 0
  }
  if ! safe_private_lock "$private_lock" "$requirements_lock_sha256"; then
    mark_security_failure
    echo "atlas-comfyui-provision: requirements install failed for $name"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  elif ! pip_install --no-deps --require-hashes -r "$private_lock"; then
    echo "atlas-comfyui-provision: requirements install failed for $name"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  elif ! safe_existing_node "$name"; then
    mark_security_failure
    echo "atlas-comfyui-provision: requirements install changed the node destination for $name"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

provisioning_start() {
  # These ai-dock runtime files exist only in the published base image.
  # shellcheck source=/dev/null
  [ -f /opt/ai-dock/etc/environment.sh ] && source /opt/ai-dock/etc/environment.sh
  # shellcheck source=/dev/null
  [ -f /opt/ai-dock/bin/venv-set.sh ] && source /opt/ai-dock/bin/venv-set.sh comfyui

  echo "atlas-comfyui-provision: reading custom-node plan from $CUSTOM_NODES_TSV"
  if [ -L "$CUSTOM_NODES_ROOT" ] \
      || { [ -e "$CUSTOM_NODES_ROOT" ] && [ ! -d "$CUSTOM_NODES_ROOT" ]; }; then
    echo "atlas-comfyui-provision: unsafe custom-node root; refusing provisioning"
    return 0
  fi
  mkdir -p -- "$CUSTOM_NODES_ROOT" || return 0
  if [ -L "$CUSTOM_NODES_ROOT" ] || [ ! -d "$CUSTOM_NODES_ROOT" ]; then
    echo "atlas-comfyui-provision: unsafe custom-node root after creation"
    return 0
  fi
  CUSTOM_NODES_ROOT_RESOLVED=$(realpath -- "$CUSTOM_NODES_ROOT") || return 0
  (
    umask 077
    cd -- "$CUSTOM_NODES_ROOT" || exit 0
    if [ "$(realpath .)" != "$CUSTOM_NODES_ROOT_RESOLVED" ]; then
      echo "atlas-comfyui-provision: custom-node root changed before pinning"
      exit 0
    fi
    provisioning_start_pinned
  )
  return 0
}

provisioning_start_pinned() {
  trap cleanup_private_state EXIT
  # Fail closed even if the private plan snapshot cannot be created: an old
  # exact-plan ready row must never survive the beginning of a retry.
  rm -f "./.atlas-node-provisioning.tsv"
  PRIVATE_TMP_ROOT="${TMPDIR:-/tmp}"
  case "$PRIVATE_TMP_ROOT" in
    /*) ;;
    *) echo "atlas-comfyui-provision: unsafe temporary root"; return 0 ;;
  esac
  if [ -L "$PRIVATE_TMP_ROOT" ] || [ ! -d "$PRIVATE_TMP_ROOT" ]; then
    echo "atlas-comfyui-provision: unsafe temporary root"
    return 0
  fi
  PLAN_COPY=$(mktemp "$PRIVATE_TMP_ROOT/comfy-nodes.XXXXXX") || return 0
  chmod 600 "$PLAN_COPY" || return 0
  if [ -f "$CUSTOM_NODES_TSV" ]; then
    cp -- "$CUSTOM_NODES_TSV" "$PLAN_COPY"
    chmod 600 "$PLAN_COPY" || return 0
  fi
  PLAN_SHA=$(sha256sum "$PLAN_COPY" | cut -d ' ' -f1)
  # A previous exact-plan success must not remain visible during a retry.
  write_provisioning_status provisioning 0 0 || {
    return 0
  }

  if [ ! -s "$PLAN_COPY" ]; then
    echo "atlas-comfyui-provision: no active custom nodes"
    write_provisioning_status ready 0 0
    return 0
  fi

  if ! preflight_plan "$PLAN_COPY"; then
    echo "atlas-comfyui-provision: invalid custom-node plan"
    REQUIRED_FAIL_COUNT=$((REQUIRED_FAIL_COUNT + 1))
    FAIL_COUNT=$((FAIL_COUNT + 1))
  else
    SUCCESSFUL_NODES=$(mktemp "$PRIVATE_TMP_ROOT/comfy-node-success.XXXXXX") || {
      REQUIRED_FAIL_COUNT=$((REQUIRED_FAIL_COUNT + 1))
      FAIL_COUNT=$((FAIL_COUNT + 1))
      write_provisioning_status failed "$REQUIRED_FAIL_COUNT" "$OPTIONAL_FAIL_COUNT"
      return 0
    }
    chmod 600 "$SUCCESSFUL_NODES" || {
      REQUIRED_FAIL_COUNT=$((REQUIRED_FAIL_COUNT + 1))
      FAIL_COUNT=$((FAIL_COUNT + 1))
      write_provisioning_status failed "$REQUIRED_FAIL_COUNT" "$OPTIONAL_FAIL_COUNT"
      return 0
    }
    while IFS= read -r plan_row; do
      node_name=$(printf '%s\n' "$plan_row" | cut -f1)
      repo=$(printf '%s\n' "$plan_row" | cut -f2)
      ref=$(printf '%s\n' "$plan_row" | cut -f3)
      install_requirements=$(printf '%s\n' "$plan_row" | cut -f4)
      requirements_lock=$(printf '%s\n' "$plan_row" | cut -f5)
      requirements_lock_sha256=$(printf '%s\n' "$plan_row" | cut -f6)
      provisioning=$(printf '%s\n' "$plan_row" | cut -f7)
      [ -n "$provisioning" ] || provisioning=required
      failures_before=$FAIL_COUNT
      security_failures_before=$SECURITY_FAIL_COUNT
      if [ -z "$node_name" ] || [ -z "$repo" ] || [ -z "$ref" ]; then
        echo "atlas-comfyui-provision: custom-node row missing name, repo, or ref; skipping"
        FAIL_COUNT=$((FAIL_COUNT + 1))
      else
        install_custom_node "$node_name" "$repo" "$ref"
      fi
      if [ "$FAIL_COUNT" -gt "$failures_before" ]; then
        if [ "$SECURITY_FAIL_COUNT" -gt "$security_failures_before" ]; then
          echo "atlas-comfyui-provision: node $node_name failed the managed-path security boundary"
        elif [ "$provisioning" = "optional" ]; then
          OPTIONAL_FAIL_COUNT=$((OPTIONAL_FAIL_COUNT + 1))
          echo "atlas-comfyui-provision: optional node $node_name failed; readiness is not blocked"
        else
          REQUIRED_FAIL_COUNT=$((REQUIRED_FAIL_COUNT + 1))
        fi
      else
        printf '%s\n' "$node_name" >> "$SUCCESSFUL_NODES"
      fi
    done < "$PLAN_COPY"

    # All repository operations finish before dependency installation begins.
    # Lock contents are then copied into a private, hash-verified workspace so
    # pip never consumes the original manifest pathname.
    LOCK_WORKSPACE=$(mktemp -d "$PRIVATE_TMP_ROOT/comfy-node-locks.XXXXXX") || {
      REQUIRED_FAIL_COUNT=$((REQUIRED_FAIL_COUNT + 1))
      FAIL_COUNT=$((FAIL_COUNT + 1))
      write_provisioning_status failed "$REQUIRED_FAIL_COUNT" "$OPTIONAL_FAIL_COUNT"
      return 0
    }
    if ! chmod 700 "$LOCK_WORKSPACE" \
        || [ -L "$LOCK_WORKSPACE" ] \
        || [ ! -d "$LOCK_WORKSPACE" ]; then
      REQUIRED_FAIL_COUNT=$((REQUIRED_FAIL_COUNT + 1))
      FAIL_COUNT=$((FAIL_COUNT + 1))
    else
      while IFS= read -r dependency_row; do
        node_name=$(printf '%s\n' "$dependency_row" | cut -f1)
        install_requirements=$(printf '%s\n' "$dependency_row" | cut -f4)
        [ "$install_requirements" = "true" ] || continue
        grep -Fqx -- "$node_name" "$SUCCESSFUL_NODES" || continue
        requirements_lock=$(printf '%s\n' "$dependency_row" | cut -f5)
        requirements_lock_sha256=$(printf '%s\n' "$dependency_row" | cut -f6)
        provisioning=$(printf '%s\n' "$dependency_row" | cut -f7)
        [ -n "$provisioning" ] || provisioning=required
        failures_before=$FAIL_COUNT
        security_failures_before=$SECURITY_FAIL_COUNT
        install_node_dependencies \
          "$node_name" "$requirements_lock" "$requirements_lock_sha256"
        if [ "$FAIL_COUNT" -gt "$failures_before" ]; then
          if [ "$SECURITY_FAIL_COUNT" -gt "$security_failures_before" ]; then
            echo "atlas-comfyui-provision: node $node_name failed the managed-path security boundary"
          elif [ "$provisioning" = "optional" ]; then
            OPTIONAL_FAIL_COUNT=$((OPTIONAL_FAIL_COUNT + 1))
            echo "atlas-comfyui-provision: optional node $node_name failed; readiness is not blocked"
          else
            REQUIRED_FAIL_COUNT=$((REQUIRED_FAIL_COUNT + 1))
          fi
        fi
      done < "$PLAN_COPY"
    fi
  fi

  echo "atlas-comfyui-provision: $OK_COUNT installed/updated, $SKIP_COUNT cached, $FAIL_COUNT failed"
  if ! safe_node_root; then
    echo "atlas-comfyui-provision: custom-node root changed before final status"
    # Do not follow a replaced root to publish status outside the named volume.
    # The original volume retains its non-ready `provisioning` row and health
    # rejects the replacement root itself.
    return 0
  else
    while IFS= read -r final_row; do
      final_name=$(printf '%s\n' "$final_row" | cut -f1)
      final_policy=$(printf '%s\n' "$final_row" | cut -f7)
      [ -n "$final_policy" ] || final_policy=required
      final_dest="./$final_name"
      if { [ -e "$final_dest" ] || [ -L "$final_dest" ]; } \
          && ! safe_existing_node "$final_name"; then
        echo "atlas-comfyui-provision: $final_name destination is unsafe at final status"
        mark_security_failure
        FAIL_COUNT=$((FAIL_COUNT + 1))
        if [ "$final_policy" = "optional" ]; then
          [ "$OPTIONAL_FAIL_COUNT" -gt 0 ] || OPTIONAL_FAIL_COUNT=1
        else
          [ "$REQUIRED_FAIL_COUNT" -gt 0 ] || REQUIRED_FAIL_COUNT=1
        fi
      fi
    done < "$PLAN_COPY"
  fi
  if [ "$REQUIRED_FAIL_COUNT" -gt 0 ]; then
    write_provisioning_status failed "$REQUIRED_FAIL_COUNT" "$OPTIONAL_FAIL_COUNT"
  else
    write_provisioning_status ready 0 "$OPTIONAL_FAIL_COUNT"
  fi
  return 0
}

provisioning_start
