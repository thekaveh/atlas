#!/bin/bash
# AI-Dock provisioning hook for Atlas-managed ComfyUI custom nodes.
#
# Mounted at /opt/ai-dock/bin/provisioning.sh. AI-Dock sources and executes
# this script before launching ComfyUI, which lets Atlas install node Python
# dependencies into the same runtime environment that ComfyUI uses.

set -u

CUSTOM_NODES_ROOT="${COMFYUI_CUSTOM_NODES_PATH:-/opt/ComfyUI/custom_nodes}"
CUSTOM_NODES_TSV="${COMFYUI_CUSTOM_NODES_TSV:-/comfyui-manifest/active-custom-nodes.tsv}"

OK_COUNT=0
SKIP_COUNT=0
FAIL_COUNT=0

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
  name="$1"; repo="$2"; ref="$3"; install_requirements="$4"

  case "$name" in
    ""|.|..|*/*|*\\*)
      echo "atlas-comfyui-provision: unsafe custom node name '$name'; skipping"
      FAIL_COUNT=$((FAIL_COUNT + 1))
      return 0
      ;;
  esac
  case "$repo" in
    https://github.com/*.git) ;;
    *)
      echo "atlas-comfyui-provision: $name has unsafe repo '$repo'; skipping"
      FAIL_COUNT=$((FAIL_COUNT + 1))
      return 0
      ;;
  esac
  if ! printf '%s' "$ref" | grep -Eq '^[0-9a-fA-F]{40}$'; then
    echo "atlas-comfyui-provision: $name has unsafe ref '$ref'; skipping"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    return 0
  fi

  mkdir -p "$CUSTOM_NODES_ROOT"
  dest="$CUSTOM_NODES_ROOT/$name"
  if [ -d "$dest/.git" ]; then
    current_ref=$(git -C "$dest" rev-parse HEAD 2>/dev/null || true)
    if [ "$current_ref" = "$ref" ]; then
      echo "atlas-comfyui-provision: $name cached at $ref"
      SKIP_COUNT=$((SKIP_COUNT + 1))
    else
      echo "atlas-comfyui-provision: updating $name to $ref"
      if git -C "$dest" fetch origin "$ref" && git -C "$dest" checkout --detach "$ref"; then
        OK_COUNT=$((OK_COUNT + 1))
      else
        echo "atlas-comfyui-provision: $name update failed"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        return 0
      fi
    fi
  else
    tmp="$dest.tmp"
    rm -rf "$tmp"
    echo "atlas-comfyui-provision: cloning $name at $ref"
    if git clone "$repo" "$tmp" && git -C "$tmp" checkout --detach "$ref"; then
      rm -rf "$dest"
      mv "$tmp" "$dest"
      OK_COUNT=$((OK_COUNT + 1))
    else
      echo "atlas-comfyui-provision: $name clone failed"
      rm -rf "$tmp"
      FAIL_COUNT=$((FAIL_COUNT + 1))
      return 0
    fi
  fi

  if [ "$install_requirements" = "true" ] && [ -f "$dest/requirements.txt" ]; then
    echo "atlas-comfyui-provision: installing requirements for $name"
    if ! pip_install -r "$dest/requirements.txt"; then
      echo "atlas-comfyui-provision: requirements install failed for $name"
      FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
  fi
}

provisioning_start() {
  # These ai-dock runtime files exist only in the published base image.
  # shellcheck source=/dev/null
  [ -f /opt/ai-dock/etc/environment.sh ] && source /opt/ai-dock/etc/environment.sh
  # shellcheck source=/dev/null
  [ -f /opt/ai-dock/bin/venv-set.sh ] && source /opt/ai-dock/bin/venv-set.sh comfyui

  echo "atlas-comfyui-provision: reading custom-node plan from $CUSTOM_NODES_TSV"
  if [ ! -f "$CUSTOM_NODES_TSV" ] || [ ! -s "$CUSTOM_NODES_TSV" ]; then
    echo "atlas-comfyui-provision: no active custom nodes"
    return 0
  fi

  while IFS="$(printf '\t')" read -r node_name repo ref install_requirements; do
    if [ -z "$node_name" ] || [ -z "$repo" ] || [ -z "$ref" ]; then
      echo "atlas-comfyui-provision: custom-node row missing name, repo, or ref; skipping"
      FAIL_COUNT=$((FAIL_COUNT + 1))
      continue
    fi
    install_custom_node "$node_name" "$repo" "$ref" "$install_requirements"
  done < "$CUSTOM_NODES_TSV"

  echo "atlas-comfyui-provision: $OK_COUNT installed/updated, $SKIP_COUNT cached, $FAIL_COUNT failed"
  return 0
}

provisioning_start
