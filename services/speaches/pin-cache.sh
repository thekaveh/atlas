#!/usr/bin/env bash
# Stage Speaches' preload models into a Docker volume at their pinned Hugging Face
# commits (#2111).
#
# Usage: services/speaches/pin-cache.sh <docker-volume>
#
# Each file is downloaded at its commit and checked against its sha256 before the
# volume is touched. The files are laid out in the Hugging Face cache format that
# Speaches reads. The compose command in compose.yml checks the same hashes at boot,
# then runs offline. Keep the pins below in step with compose.yml.
set -euo pipefail

volume=${1:?usage: pin-cache.sh <docker-volume>}
image=${PIN_CACHE_IMAGE:-alpine:3.24.4}

# repo commit file sha256
pins='
speaches-ai/Kokoro-82M-v1.0-ONNX dc196c76d64fed9203906231372bcb98135815df model.onnx 7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5
speaches-ai/Kokoro-82M-v1.0-ONNX dc196c76d64fed9203906231372bcb98135815df voices.bin bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d
speaches-ai/Kokoro-82M-v1.0-ONNX dc196c76d64fed9203906231372bcb98135815df README.md 6d8def2b0d1b2beb96c907f33f58c6a8c10371d5c7527252b36c255f564c1ab5
Systran/faster-whisper-large-v3 edaa852ec7e145841d8ffdb056a99866b5f0a478 model.bin 69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1
Systran/faster-whisper-large-v3 edaa852ec7e145841d8ffdb056a99866b5f0a478 config.json a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9
Systran/faster-whisper-large-v3 edaa852ec7e145841d8ffdb056a99866b5f0a478 tokenizer.json 6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca
Systran/faster-whisper-large-v3 edaa852ec7e145841d8ffdb056a99866b5f0a478 vocabulary.json c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1
Systran/faster-whisper-large-v3 edaa852ec7e145841d8ffdb056a99866b5f0a478 preprocessor_config.json 7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711
Systran/faster-whisper-large-v3 edaa852ec7e145841d8ffdb056a99866b5f0a478 README.md 39e96252229f5a3d0141dc81afb65a36fd205461ac21e5b70f2cd1248ef0082c
'

sha() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

docker volume inspect "$volume" >/dev/null 2>&1 || {
  echo "pin-cache: volume $volume does not exist; run 'docker compose ... create speaches' first" >&2
  exit 1
}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
hub="$work/hub"

while read -r repo commit file want; do
  [ -n "$repo" ] || continue
  dir="$hub/models--${repo//\//--}"
  mkdir -p "$dir/blobs" "$dir/snapshots/$commit" "$dir/refs"
  curl -sSLf --retry 3 -o "$dir/blobs/$want" "https://huggingface.co/$repo/resolve/$commit/$file"
  got=$(sha "$dir/blobs/$want")
  if [ "$got" != "$want" ]; then
    echo "pin-cache: $repo/$file has sha256 $got, expected $want; volume untouched" >&2
    exit 1
  fi
  ln -sfn "../../blobs/$want" "$dir/snapshots/$commit/$file"
  printf '%s' "$commit" > "$dir/refs/main"
done <<<"$pins"

docker run --rm -v "$volume:/cache" -v "$hub:/stage:ro" "$image" \
  sh -c 'mkdir -p /cache/hub && cp -a /stage/. /cache/hub/ && chown -R 1000:1000 /cache/hub'
echo "pin-cache: staged $(grep -c . <<<"$pins") pinned files into volume $volume"
