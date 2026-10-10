# 5.3.4. Parakeet whisper.cpp Provider

Run [whisper.cpp](https://github.com/ggml-org/whisper.cpp) `whisper-server` on
the host, and Atlas containers reach it through `host.docker.internal`. Select
it with `STT_PROVIDER_SOURCE=whisper-cpp-localhost`.

## 1. When to use it

- **macOS:** Docker Desktop containers cannot use Metal or Core ML. A host
  build of whisper.cpp can.
- **Small footprint:** one native binary and a ggml model file, with no
  Python or PyTorch.
- **Quantized models:** `q5_0` and similar quantizations reduce memory use.

Atlas publishes no speed ranking against the other STT engines. Benchmark
representative audio on the deployment host.

For a container-only setup, use `speaches-container-cpu` or
`parakeet-container-gpu` (NVIDIA only). The
[STT Provider README](../../../stt-provider/README.md) lists all sources.

## 2. Build the server

The Homebrew `whisper-cpp` formula does not install `whisper-server` (it
builds with `WHISPER_BUILD_SERVER=OFF`). Build from source on macOS and on
Linux:

```bash
git clone https://github.com/ggml-org/whisper.cpp
cd whisper.cpp
cmake -B build
cmake --build build -j --config Release
```

The binary is `./build/bin/whisper-server`. On Apple Silicon, the default
build runs inference on the GPU through Metal. For other accelerators, add
one flag to the first `cmake` command:

| Flag | Accelerator |
|---|---|
| `-DGGML_CUDA=1` | NVIDIA CUDA |
| `-DGGML_VULKAN=1` | AMD or Intel through Vulkan |
| `-DWHISPER_COREML=1` | Apple Neural Engine. Also generate the Core ML encoder with `./models/generate-coreml-model.sh <model>`; see the upstream Core ML section. |

## 3. Download a model

Run these commands in the `whisper.cpp` checkout:

```bash
# English only, 142 MiB
sh ./models/download-ggml-model.sh base.en

# Multilingual, 2.9 GiB
sh ./models/download-ggml-model.sh large-v3

# Multilingual, quantized: smaller and less memory
sh ./models/download-ggml-model.sh large-v3-q5_0
```

The script writes `models/ggml-<model>.bin`. Run the script without
arguments to list the other models, for example `large-v3-turbo`.

## 4. Run the server

```bash
# The port matches WHISPER_CPP_LOCALHOST_PORT in .env (default 63042).
./build/bin/whisper-server \
  --host 0.0.0.0 \
  --port 63042 \
  --model models/ggml-large-v3.bin \
  --inference-path /v1/audio/transcriptions \
  --convert
```

`--inference-path` serves the OpenAI-compatible
`/v1/audio/transcriptions` route that Atlas calls. `--convert` converts
formats other than WAV, MP3 and FLAC, for example OGG/Opus voice notes. It
needs `ffmpeg` on the `PATH`.

`whisper-server` has no authentication. `--host 0.0.0.0` makes it reachable
from the local network as well as from Docker. Use the host firewall when the
network is not trusted.

## 5. Connect Atlas

```bash
./start.sh --stt-provider-source whisper-cpp-localhost
```

If the server uses a port other than 63042, set it in `.env`. Atlas derives
the URL `http://host.docker.internal:${WHISPER_CPP_LOCALHOST_PORT:-63042}`:

```bash
WHISPER_CPP_LOCALHOST_PORT=18143
```

## 6. Verify

```bash
curl -X POST http://localhost:63042/v1/audio/transcriptions \
  -F file=@sample.wav \
  -F model=whisper-1
# expect JSON: {"text":"..."}
```

whisper.cpp ignores `model` and uses the loaded file.

## 7. Troubleshooting

**`Address already in use`:** start the server on another port, then set
`WHISPER_CPP_LOCALHOST_PORT` in `.env`.

**Slow on a Mac:** check that the server log reports the Metal backend. A
build without Metal runs on the CPU.

**Model load runs out of memory:** use a quantized or smaller model, for
example `large-v3-q5_0` or `base.en`.

## 8. References

- [whisper.cpp upstream](https://github.com/ggml-org/whisper.cpp)
- [whisper-server options](https://github.com/ggml-org/whisper.cpp/tree/master/examples/server)
- [Core ML support](https://github.com/ggml-org/whisper.cpp#core-ml-support)
- [ggml model files](https://huggingface.co/ggerganov/whisper.cpp)
