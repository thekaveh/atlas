#!/bin/sh
set -eu

LOCKED_SPECS="n8n-nodes-comfyui@0.0.9,@ksc1234/n8n-nodes-comfyui-image-to-image@1.0.2"
LEGACY_DEFAULT_SPECS="n8n-nodes-comfyui,@ksc1234/n8n-nodes-comfyui-image-to-image,n8n-nodes-mcp"
REQUESTED_SPECS=${N8N_INIT_NODES:-$LOCKED_SPECS}
N8N_USER_FOLDER=${N8N_USER_FOLDER:-/home/node/.n8n}
NODES_DIR="$N8N_USER_FOLDER/nodes"

echo "n8n-init: Preparing community packages before n8n startup..."
mkdir -p "$NODES_DIR"

validate_exact_spec() {
  spec=$1
  case "$spec" in
    @*/*@*) version=${spec##*@} ;;
    *@*) version=${spec##*@} ;;
    *)
      echo "n8n-init: ERROR - Community package '$spec' must pin an exact version (name@x.y.z)."
      return 1
      ;;
  esac
  if ! printf '%s' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+([+~-][0-9A-Za-z.-]+)?$'; then
    echo "n8n-init: ERROR - Community package '$spec' must pin an exact version (name@x.y.z)."
    return 1
  fi
}

if [ "$REQUESTED_SPECS" = "$LOCKED_SPECS" ] || [ "$REQUESTED_SPECS" = "$LEGACY_DEFAULT_SPECS" ]; then
  echo "n8n-init: Installing Atlas' lockfile-backed community package set."
  cp /config/package.json "$NODES_DIR/package.json"
  cp /config/package-lock.json "$NODES_DIR/package-lock.json"
  npm ci \
    --prefix "$NODES_DIR" \
    --omit=dev \
    --ignore-scripts \
    --no-audit \
    --no-fund
else
  old_ifs=$IFS
  IFS=','
  set -f
  # Intentional IFS splitting turns the comma-separated setting into argv.
  # shellcheck disable=SC2086
  set -- $REQUESTED_SPECS
  set +f
  IFS=$old_ifs
  if [ "$#" -eq 0 ]; then
    echo "n8n-init: ERROR - N8N_INIT_NODES did not contain any packages."
    exit 1
  fi
  for spec in "$@"; do
    validate_exact_spec "$spec"
  done

  echo "n8n-init: Installing operator-supplied exact community package set."
  rm -rf "$NODES_DIR/node_modules" "$NODES_DIR/package.json" "$NODES_DIR/package-lock.json"
  printf '%s\n' '{"name":"atlas-n8n-community-nodes","private":true}' > "$NODES_DIR/package.json"
  npm install \
    --prefix "$NODES_DIR" \
    --save-exact \
    --omit=dev \
    --ignore-scripts \
    --no-audit \
    --no-fund \
    "$@"
fi

echo "n8n-init: Community packages installed successfully."
