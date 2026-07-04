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
  -w /workspace \
  node:22-alpine \
  sh -eu -c '
    npx --yes --package @gltf-transform/cli@4.4.1 -- gltf-transform inspect "$1"
    npx --yes --package @gltf-transform/cli@4.4.1 -- gltf-transform validate "$1"
    npx --yes --package @gltf-transform/cli@4.4.1 -- gltf-transform optimize "$1" "$2" --compress meshopt --texture-compress webp
  ' sh "$input" "$output"
