#!/bin/sh
# ComfyUI is ready only when both provisioners completed the exact active plan
# currently mounted into the container and the HTTP API is reachable.
set -eu

MANIFEST_ROOT="${COMFYUI_MANIFEST_ROOT:-/comfyui-manifest}"
MODELS_ROOT="${COMFYUI_MODELS_PATH:-/opt/ComfyUI/models}"
CUSTOM_NODES_ROOT="${COMFYUI_CUSTOM_NODES_PATH:-/opt/ComfyUI/custom_nodes}"
HEALTH_URL="${COMFYUI_HEALTH_URL:-http://localhost:18188/system_stats}"

for root in "$MANIFEST_ROOT" "$MODELS_ROOT" "$CUSTOM_NODES_ROOT"; do
  if [ -L "$root" ] || [ ! -d "$root" ]; then
    echo "atlas-comfyui-health: provisioning root is missing or unsafe"
    exit 1
  fi
done

verify_status() {
  label="$1"
  plan="$2"
  status="$3"

  if [ -L "$plan" ] || [ -L "$status" ] || [ ! -f "$plan" ] || [ ! -f "$status" ]; then
    echo "atlas-comfyui-health: $label provisioning plan/status missing"
    return 1
  fi
  if ! awk -F '\t' '
      NR != 1 { bad = 1 }
      NF != 5 { bad = 1 }
      $1 != "v1" { bad = 1 }
      $2 !~ /^[0-9a-f]{64}$/ { bad = 1 }
      $3 !~ /^(provisioning|ready|failed)$/ { bad = 1 }
      $4 !~ /^[0-9]+$/ || $5 !~ /^[0-9]+$/ { bad = 1 }
      $3 == "ready" && $4 != "0" { bad = 1 }
      END { if (NR != 1) bad = 1; exit bad }
    ' "$status"; then
    echo "atlas-comfyui-health: $label provisioning status malformed"
    return 1
  fi

  expected=$(sha256sum "$plan" | cut -d ' ' -f1)
  actual=$(cut -f2 "$status")
  state=$(cut -f3 "$status")
  if [ "$actual" != "$expected" ]; then
    echo "atlas-comfyui-health: $label provisioning status is stale"
    return 1
  fi
  if [ "$state" != "ready" ]; then
    echo "atlas-comfyui-health: $label provisioning is $state"
    return 1
  fi
}

verify_status models \
  "$MANIFEST_ROOT/active-models.tsv" \
  "$MODELS_ROOT/.atlas-model-provisioning.tsv"
verify_status custom-nodes \
  "$MANIFEST_ROOT/active-custom-nodes.tsv" \
  "$CUSTOM_NODES_ROOT/.atlas-node-provisioning.tsv"
curl -fsS "$HEALTH_URL" >/dev/null
