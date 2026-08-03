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

The default for fresh installs is **`speaches-container-cpu`** — works on
every platform with no host install. The first transcription request pulls
the Faster-Whisper model (~466 MB for distil-large-v3) and caches it under
the `speaches-cache` volume.

For Mac users who care about transcription speed, **`whisper-cpp-localhost`**
is the fastest option (Metal + Core ML / ANE), followed by
**`parakeet-localhost`** with parakeet-mlx. The Parakeet path remains the
SOTA-quality choice for English/European languages.

## 2. Directory layout

```
services/parakeet/provider/
├── mlx/                Apple Silicon MLX server for Parakeet (parakeet-localhost)
│   ├── api_server.py
│   ├── README.md
│   └── requirements.txt
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
curl -X POST http://localhost:63042/v1/audio/transcriptions \
  -F file=@sample.wav -F model=whisper-1
```

Parakeet on NVIDIA GPU:

```bash
./start.sh --stt-provider-source parakeet-container-gpu
```

The GPU API starts a deadline-bounded background load for the configured
Parakeet model. Its health endpoint returns `503` until the model is loaded, so
dependent services cannot begin against a process that is not yet inference-ready.

Parakeet on macOS MLX:

```bash
# Terminal 1 — run from repo root
pip install -r services/parakeet/provider/mlx/requirements.txt
cd services/parakeet/provider && python -m uvicorn mlx.api_server:app --host 0.0.0.0 --port 63042

# Terminal 2
./start.sh --stt-provider-source parakeet-localhost
```

The MLX health endpoint starts one shared background model load and returns
`503` with `status=starting` while that work is in progress. Concurrent health
and transcription requests share the same load; model initialization never
runs on the API event loop. Both Parakeet providers default advanced segment
timestamps to disabled unless `return_timestamps=true` is supplied.

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

## 4. Performance reference

| Backend + hardware | Realtime factor (lower is faster) |
|---|---|
| Speaches CPU (Faster-Whisper distil-large-v3) on M2 Pro | ~0.3× |
| Speaches GPU (CUDA, large-v3) on RTX 4090 | ~0.05× |
| whisper.cpp Metal+CoreML (large-v3) on M2 Pro | ~0.1× |
| Parakeet-MLX (v3) on M2 Ultra | ~0.003× (300× realtime) |
| Parakeet CUDA (v3) on A100 | ~0.0003× (3380× realtime) |

## 5. How Open WebUI is wired

The bootstrapper sets these env vars on the open-web-ui container based on
the chosen source:

- `AUDIO_STT_ENGINE=openai`
- `AUDIO_STT_OPENAI_API_BASE_URL=${STT_ENDPOINT}/v1`
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
