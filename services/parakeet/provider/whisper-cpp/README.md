# 5.3.4. Parakeet whisper.cpp Provider

Run [whisper.cpp](https://github.com/ggml-org/whisper.cpp) natively on your
host and have the stack reach it via `host.docker.internal`. This is the
**recommended STT path for Apple Silicon** — whisper.cpp ships first-class
Metal + Core ML / Apple Neural Engine support that easily beats any
container-side STT on a Mac.

It also works fine on Linux (CPU, CUDA, or Vulkan) and is the lightest STT
option overall — no Python deps, no model server framework, just a single
binary.

## 1. Why localhost instead of container

- **Mac users**: Metal + Core ML / ANE acceleration only works on the host,
  not through Docker Desktop. The Speaches CPU container will work too, but
  whisper.cpp natively is ~5–10× faster on Apple Silicon.
- **Lightweight**: a single static binary, ggml-format models, no PyTorch.
- **Quantized models**: pull a `q5_0` or `q4_0` quant and run faster-than-realtime
  on modest CPUs.

If you want a container-only setup with no host install, use
`STT_PROVIDER_SOURCE=speaches-container-cpu` (Speaches container) or
`parakeet-container-gpu` (NVIDIA-only). See
[the parakeet provider README](../README.md) for the full STT-source matrix.

## 2. Install (macOS)

```bash
brew install whisper-cpp
```

This installs the `whisper-cli` and `whisper-server` binaries with Metal +
Core ML support pre-built.

## 3. Install (Linux)

```bash
git clone https://github.com/ggml-org/whisper.cpp
cd whisper.cpp
make -j server         # CPU only
# or:
GGML_CUDA=1 make -j server   # NVIDIA CUDA
GGML_VULKAN=1 make -j server # AMD / Intel via Vulkan
```

## 4. Download a model

```bash
# 142 MB, good balance for English-only
bash ./models/download-ggml-model.sh base.en

# 1.5 GB, multilingual SOTA
bash ./models/download-ggml-model.sh large-v3

# 466 MB, multilingual + distilled (fast)
bash ./models/download-ggml-model.sh distil-large-v3
```

On macOS the Homebrew install puts models at
`~/Library/Application Support/whisper-cpp/models/` by default; check
`whisper-cli --help` for the path on your version.

## 5. Run the server (OpenAI-compatible)

```bash
# Default port matches WHISPER_CPP_LOCALHOST_PORT in .env (63042).
whisper-server \
  --host 0.0.0.0 \
  --port 63042 \
  --model ~/path/to/ggml-large-v3.bin \
  --inference-path /v1/audio/transcriptions
```

The `/v1/audio/transcriptions` path makes the server drop-in compatible with
the OpenAI Whisper API surface (which is what Open WebUI / Speaches /
Parakeet also expose).

## 6. Wire the stack

```bash
./start.sh --stt-provider-source whisper-cpp-localhost
```

If you used a port other than 63042, update `.env` (URL is derived inline
as `http://host.docker.internal:${WHISPER_CPP_LOCALHOST_PORT:-63042}`):

```bash
WHISPER_CPP_LOCALHOST_PORT=63099
```

## 7. Verify

```bash
# Record or grab a sample WAV/MP3/M4A
curl -X POST http://localhost:63042/v1/audio/transcriptions \
  -H "Content-Type: multipart/form-data" \
  -F file=@sample.wav \
  -F model=whisper-1
# expect JSON: {"text":"..."}
```

## 8. Performance reference (English, 10s audio)

| Hardware + model | Wall time |
|---|---|
| M2 Pro, `base.en`, Metal+ANE | ~0.6 s |
| M2 Pro, `large-v3-distil`, Metal+ANE | ~1.0 s |
| Intel i7-12700, `base.en`, CPU only | ~3.0 s |
| RTX 4090, `large-v3`, CUDA | ~0.3 s |

## 9. Troubleshooting

**`Address already in use`** — pick another port (then update `.env`).

**Slow on Mac** — make sure you used Homebrew (Metal-enabled by default).
If you built from source, pass `-DGGML_METAL=ON` and `-DWHISPER_COREML=ON`
to CMake.

**Model load OOMs** — pick a smaller quant: `ggml-large-v3-q5_0.bin` is
~1 GB vs ~3 GB unquantized, with negligible WER difference.

## 10. References

- [whisper.cpp upstream](https://github.com/ggml-org/whisper.cpp)
- [Apple Core ML / ANE setup notes](https://github.com/ggml-org/whisper.cpp#core-ml-support)
- [ggml model registry](https://huggingface.co/ggerganov/whisper.cpp)
