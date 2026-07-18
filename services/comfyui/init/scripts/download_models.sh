#!/bin/sh
# services/comfyui/init/scripts/download_models.sh
#
# Downloads active ComfyUI models into the correct subdirectory of
# /opt/ComfyUI/models (or $COMFYUI_MODELS_PATH).
#
# MANIFEST SOURCE (no DB):
#   The bootstrapper writes volumes/comfyui/active-models.tsv at startup via
#   bootstrapper/utils/comfyui_manifest_generator.py (wrapping C2's
#   comfyui_resolver.active_comfyui_models).  That file is bind-mounted into
#   this container at /comfyui-manifest/active-models.tsv (see compose.yml).
#   This script reads it via $MANIFEST_TSV (default:
#   /comfyui-manifest/active-models.tsv).
#
# TSV FORMAT (set by comfyui_manifest_generator._row_tsv):
#   name TAB type TAB filename TAB download_url TAB sha256 [TAB target_dir]
#   (sha256 is empty string when null — same as the old COALESCE(sha256,''))
#   No header row.  target_dir is optional for backwards compatibility with
#   older manifests; when absent, category_to_dir is used.
#
# EMPTY / MISSING MANIFEST:
#   Missing or empty $MANIFEST_TSV → download nothing, exit 0 (mirrors the
#   old "no active comfyui_models rows" path).
#
# Failures are non-fatal: bad rows / failed wgets increment FAIL_COUNT and
# the script still exits 0 + writes the .download_complete marker so ComfyUI
# can start (workflows referencing missing models just fail at render time —
# the historical behavior).
#
# Required apk packages on alpine: wget ca-certificates
# — installed inline below (postgresql-client no longer needed).
set -e

apk add --no-cache wget ca-certificates

MODELS_ROOT="${COMFYUI_MODELS_PATH:-/models}"
MANIFEST_TSV="${COMFYUI_MANIFEST_TSV:-/comfyui-manifest/active-models.tsv}"

# 1. Materialize per-category directories (idempotent on every run).
for d in checkpoints vae loras controlnet ipadapter instantid \
         upscale_models embeddings clip animatediff_models \
         animatediff_motion_lora voice audio mesh_models \
         diffusion_models text_encoders; do
  mkdir -p "$MODELS_ROOT/$d"
done

# Map category enum → directory. Mirrors CATEGORY_TARGET_DIR in
# bootstrapper/utils/comfyui_library.py. Keep in sync with that dict
# when adding new categories.
category_to_dir() {
  case "$1" in
    checkpoint)   echo "checkpoints" ;;
    vae)          echo "vae" ;;
    lora)         echo "loras" ;;
    controlnet)   echo "controlnet" ;;
    ipadapter)    echo "ipadapter" ;;
    instantid)    echo "instantid" ;;
    upscaler)     echo "upscale_models" ;;
    embedding)    echo "embeddings" ;;
    clip)         echo "clip" ;;
    animatediff)  echo "animatediff_models" ;;
    motion_lora)  echo "animatediff_motion_lora" ;;
    video_model)  echo "checkpoints" ;;
    voice_model)  echo "voice" ;;
    audio_model)  echo "audio" ;;
    mesh_model)   echo "mesh_models" ;;
    diffusion_models)  echo "diffusion_models" ;;   # FLUX / DiT / Krea 2 / SD3
    text_encoders)     echo "text_encoders" ;;       # Large text encoders (t5xxl, Qwen3-VL)
    *)            echo "" ;;
  esac
}

# Counters for the summary line.
OK_COUNT=0
SKIP_COUNT=0
FAIL_COUNT=0

# download_one <name> <url> <dest_path> <sha256_or_empty>
# Downloads to a .part sibling and atomically renames on success, so $dest
# only ever holds a COMPLETE file. The skip-cache guard below ([ -s "$dest" ])
# therefore never mistakes an interrupted multi-GB partial for a finished
# model — which mattered because none of the curated models carry a sha256 to
# verify against, so the old direct-to-$dest form silently cached truncated
# downloads forever (ComfyUI then loaded the corrupt blob and failed
# cryptically at render time). -c resumes the .part across runs.
# Failure is non-fatal: log + increment FAIL_COUNT + return 0.
download_one() {
  name="$1"; url="$2"; dest="$3"; sha="$4"
  if [ -s "$dest" ]; then
    if [ -n "$sha" ]; then
      if echo "$sha  $dest" | sha256sum -c - >/dev/null 2>&1; then
        echo "= $name (cached, sha verified)"
        SKIP_COUNT=$((SKIP_COUNT + 1))
        return 0
      else
        echo "! $name (cached but sha mismatch — re-downloading)"
        rm -f "$dest"
      fi
    else
      echo "= $name (cached)"
      SKIP_COUNT=$((SKIP_COUNT + 1))
      return 0
    fi
  fi
  echo "+ $name → $dest"
  wget_rc=0
  # --timeout caps each TCP/DNS/read stage (default is unbounded); --tries
  # bounds retries on transient errors. ComfyUI model downloads can be
  # multi-GB; a stalled HF/civitai mirror would otherwise hang the init
  # container indefinitely with no compose-level kill switch.
  wget_out=$(wget --timeout=30 --tries=3 -c -O "$dest.part" "$url" 2>&1) || wget_rc=$?
  printf '%s\n' "$wget_out" | tail -1
  if [ $wget_rc -eq 0 ]; then
    mv "$dest.part" "$dest"
    OK_COUNT=$((OK_COUNT + 1))
  else
    echo "✗ $name failed (wget exit $wget_rc)"
    rm -f "$dest.part"  # leave no partial behind on hard failure
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

# 2. Read the active set from the bootstrapper-generated manifest TSV.
# Tab-separated, header-less, tuples-only — same shape as the former psql output.
# Use a tempfile rather than a pipe so the while-loop body runs in the
# current shell, not a subshell. A piped `cmd | while read` forks a
# subshell for the loop body on POSIX sh / Alpine ash, causing counter
# mutations (OK_COUNT etc.) to be lost when the loop exits.
echo "comfyui-init: Reading active ComfyUI models from $MANIFEST_TSV..."
if [ ! -f "$MANIFEST_TSV" ] || [ ! -s "$MANIFEST_TSV" ]; then
  echo "(no active-models.tsv or empty manifest — nothing to download)"
  echo "--- summary: $OK_COUNT downloaded, $SKIP_COUNT cached, $FAIL_COUNT failed ---"
  touch "$MODELS_ROOT/.download_complete"
  exit 0
fi

cp "$MANIFEST_TSV" /tmp/_comfy_active

if [ ! -s /tmp/_comfy_active ]; then
  echo "(no active comfyui models in manifest — nothing to download)"
else
  row_count=$(wc -l < /tmp/_comfy_active | tr -d ' ')
  echo "--- found $row_count active row(s) ---"
  # Split each tab-separated row with `cut`, which preserves empty interior
  # fields. `IFS=<tab> read` treats tab as IFS-whitespace and collapses runs of
  # it, so a no-checksum row (empty `sha` column → two adjacent tabs) would
  # shift `target_dir` into `sha` and leave `target_dir` empty — the bogus
  # `sha` then fails `sha256sum -c` on every cache hit and re-downloads the
  # (multi-GB) file on every restart.
  while IFS= read -r _row; do
    name=$(printf '%s\n' "$_row" | cut -f1)
    category=$(printf '%s\n' "$_row" | cut -f2)
    filename=$(printf '%s\n' "$_row" | cut -f3)
    url=$(printf '%s\n' "$_row" | cut -f4)
    sha=$(printf '%s\n' "$_row" | cut -f5)
    target_dir=$(printf '%s\n' "$_row" | cut -f6)
    # Skip blank lines (defensive against trailing newline-only chunks).
    if [ -z "$name" ] || [ -z "$url" ]; then
      echo "✗ row with missing name or url; skipping"
      FAIL_COUNT=$((FAIL_COUNT + 1))
      continue
    fi
    dir="$target_dir"
    if [ -z "$dir" ]; then
      dir=$(category_to_dir "$category")
    fi
    if [ -z "$dir" ]; then
      echo "✗ $name: unknown category '$category'; skipping"
      FAIL_COUNT=$((FAIL_COUNT + 1))
      continue
    fi
    case "$dir" in
      ""|.|..|/*|*../*|*/*|*\\*)
        echo "✗ $name: unsafe target_dir '$dir'; skipping"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        continue
        ;;
    esac
    # Filename column is authoritative; fall back to a URL-derived
    # basename only if the manifest row is empty.
    if [ -z "$filename" ]; then
      filename=$(basename "$url" | cut -d '?' -f 1)
    fi
    case "$filename" in
      ""|.|..|*/*|*\\*)
        echo "✗ $name: unsafe filename '$filename'; skipping"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        continue
        ;;
    esac
    download_one "$name" "$url" "$MODELS_ROOT/$dir/$filename" "$sha"
  done < /tmp/_comfy_active
fi
rm -f /tmp/_comfy_active

echo "--- summary: $OK_COUNT downloaded, $SKIP_COUNT cached, $FAIL_COUNT failed ---"
touch "$MODELS_ROOT/.download_complete"
exit 0
