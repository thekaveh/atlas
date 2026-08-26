# 5.3.2. Parakeet Provider Overview

Pluggable speech-to-text layer. All backends expose an OpenAI-compatible
`/v1/audio/transcriptions` endpoint so Open WebUI, n8n, and the backend API
can use them interchangeably.

## 1. Available backends

| `STT_PROVIDER_SOURCE` | Engine | License | Runs on |
|---|---|---|---|
| `speaches-container-cpu` | Speaches (Faster-Whisper inside) | MIT | Linux + macOS Docker, CPU |
| `speaches-container-gpu` | Speaches CUDA build | MIT | NVIDIA |
| `parakeet-container-gpu` | NVIDIA Parakeet-TDT (NeMo) | CC-BY-4.0 | NVIDIA |
| `parakeet-localhost` | Parakeet-MLX or native Parakeet | NVIDIA Open Model | macOS MLX (best) / Linux |
| `whisper-cpp-localhost` | whisper.cpp | MIT | macOS Metal+Core ML (best) / Linux |
| `disabled` | none | — | — |

The default for fresh installs is **`speaches-container-cpu`** — it starts on
every platform with no host install. The pinned Speaches release does not
download a missing model on the first transcription request, and Atlas keeps
`PRELOAD_MODELS` empty until the source-aware preload work in #799 is complete.
See the STT provider guide before expecting transcription from this default.

For Mac users, both **`whisper-cpp-localhost`** (Metal + Core ML / ANE) and
**`parakeet-localhost`** (MLX) provide native acceleration. Benchmark the
chosen model and representative audio on the target host; Atlas does not make
a hardware-independent speed or quality ranking.

## 2. Directory layout

```
services/parakeet/provider/
├── mlx/                Apple Silicon MLX server for Parakeet (parakeet-localhost)
│   ├── api_server.py
│   ├── README.md
│   ├── requirements.txt
│   └── requirements-locked.txt
├── gpu/                NVIDIA CUDA container build for Parakeet (parakeet-container-gpu)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── transcribe.py
├── whisper-cpp/        whisper.cpp host install notes (whisper-cpp-localhost)
│   └── README.md
└── shared/             Common server scaffolding
    ├── api_server.py
    └── utils.py
```

The Speaches path doesn't have a directory here because it's an
off-the-shelf container — see
[services/speaches/compose.yml](../../speaches/compose.yml) for the
runtime config.

## 3. Quick start

Speaches (default — already enabled in `.env.example`):

```bash
./start.sh
curl http://localhost:63060/health
```

This proves the default Speaches container is up. Transcription remains
unavailable until the `whisper-1` alias target is explicitly installed or
preloaded; see the warning in the full STT provider guide and tracked issue
#799. The service does not lazily pull it on the first request.

Parakeet on NVIDIA GPU:

```bash
./start.sh --stt-provider-source parakeet-container-gpu
```

The GPU API starts a deadline-bounded background load for the configured
Parakeet model. Its health endpoint and transcription routes return `503` until
the model is loaded, allowing health-aware callers and orchestration to wait for
inference readiness even though consumers may start independently.

Parakeet on macOS MLX:

```bash
# Terminal 1 — run from repo root with the lock's certified interpreter
python3.12 -m venv .venv-parakeet-mlx
. .venv-parakeet-mlx/bin/activate
python -m pip install -r services/parakeet/provider/mlx/requirements-locked.txt
export PARAKEET_API_TOKEN='<same random value configured in repo-root .env>'
export PARAKEET_LOCALHOST_BIND_HOST=0.0.0.0
export PARAKEET_LOCALHOST_PORT=63042
cd services/parakeet/provider && python -m mlx.api_server

# Terminal 2
./start.sh --stt-provider-source parakeet-localhost
```

The MLX health endpoint starts one shared background model load and returns
`503` with `status=loading` while that work is in progress. Concurrent health
and transcription requests share the same load; model initialization never
runs on the API event loop. Both Parakeet providers default advanced segment
timestamps to disabled unless `return_timestamps=true` is supplied.
`PARAKEET_MAX_UPLOAD_BYTES` is parsed as a positive integer during provider
startup; malformed, zero, and negative values fail fast before the API serves.
The complete request body must also arrive within the positive total
`PARAKEET_UPLOAD_TIMEOUT_SECONDS` deadline (1-3600 seconds; 120 by default), or the
provider returns `408` and releases its admission slot.

whisper.cpp on macOS (Metal + Core ML):

```bash
# Terminal 1
brew install whisper-cpp
bash $(brew --prefix)/share/whisper-cpp/models/download-ggml-model.sh large-v3
whisper-server --host 0.0.0.0 --port 63042 \
  --model "$(brew --prefix)/share/whisper-cpp/models/ggml-large-v3.bin" \
  --inference-path /v1/audio/transcriptions

# Terminal 2
./start.sh --stt-provider-source whisper-cpp-localhost
```

See [whisper-cpp/README.md](whisper-cpp/README.md) for the full whisper.cpp
walk-through and Linux build instructions.

Disable STT entirely:

```bash
./start.sh --stt-provider-source disabled
```

## 4. Performance evaluation

Throughput and word-error rate depend on the exact checkpoint, audio corpus,
codec, duration, timestamps, hardware, and thermal state. Benchmark
representative inputs on the deployment host and record real-time factor,
word-error rate, and peak resident/GPU memory before capacity planning.

## 5. How Open WebUI is wired

The bootstrapper sets these env vars on the open-web-ui container based on
the chosen source:

- `AUDIO_STT_ENGINE=openai`
- `AUDIO_STT_OPENAI_API_BASE_URL=${STT_ENDPOINT}/v1`
- `AUDIO_STT_OPENAI_API_KEY=${OPEN_WEB_UI_STT_API_KEY}` (source-aware bearer token)
- `AUDIO_STT_MODEL=whisper-1` (the OpenAI-compatible model name all engines accept)

You can change the model name in the Open WebUI admin panel — Audio settings.

## 6. Full configuration reference

See [services/stt-provider/README.md](../../../services/stt-provider/README.md).

## 7. References

- [Speaches](https://github.com/speaches-ai/speaches)
- [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper)
- [NVIDIA Parakeet-TDT v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
- [parakeet-mlx](https://github.com/senstella/parakeet-mlx)
- [whisper.cpp upstream](https://github.com/ggml-org/whisper.cpp)
- [OpenAI Whisper API spec](https://platform.openai.com/docs/guides/speech-to-text)
