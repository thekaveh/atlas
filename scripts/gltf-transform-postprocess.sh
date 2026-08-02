#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage: scripts/gltf-transform-postprocess.sh INPUT.glb OUTPUT.glb

Inspect, validate, and optimize a GLB asset with @gltf-transform/cli.
Requires Docker; writes OUTPUT.glb in the current repository tree.
USAGE
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

input=$1
output=$2

if [[ ! -f "$input" ]]; then
  echo "Input GLB not found: $input" >&2
  exit 1
fi

case "$input" in
  /*) echo "Input must be relative to the current working tree: $input" >&2; exit 2 ;;
esac

case "$output" in
  /*) echo "Output must be relative to the current working tree: $output" >&2; exit 2 ;;
esac

mkdir -p "$(dirname "$output")"

docker run --rm \
  -v "$PWD:/workspace" \
  -v "$PWD/services/asset-worker/app:/tooling:ro" \
  -w /workspace \
  node:22-bookworm-slim@sha256:f32b81066cde10a75dbac96646099533316d94bac4150c55da1636e1f0ffdc46 \
  sh -eu -c '
    mkdir /tmp/gltf-transform
    cp /tooling/package.json /tooling/package-lock.json /tmp/gltf-transform/
    npm ci --omit=dev --prefix /tmp/gltf-transform
    cli=/tmp/gltf-transform/node_modules/.bin/gltf-transform
    "$cli" inspect "$1"
    "$cli" validate "$1"
    "$cli" optimize "$1" "$2" --compress meshopt --texture-compress webp
  ' sh "$input" "$output"
