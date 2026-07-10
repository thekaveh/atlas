# TTS Provider Service

Pluggable text-to-speech layer. All backends expose an OpenAI-compatible
`/v1/audio/speech` endpoint so Open WebUI, n8n, and the backend API can use
them interchangeably.

## Available backends

| `TTS_PROVIDER_SOURCE` | Engine | License | Runs on |
|---|---|---|---|
| `speaches-container-cpu` | Speaches (Kokoro + Piper) | MIT | Linux + macOS Docker, CPU |
| `speaches-container-gpu` | Speaches (CUDA build) | MIT | NVIDIA |
| `chatterbox-container-gpu` | Resemble AI Chatterbox | MIT | NVIDIA (≥8 GB) |
| `chatterbox-localhost` | Chatterbox natively | MIT | macOS MPS / Linux (any) |
| `disabled` | none | — | — |

The default for fresh installs is **`speaches-container-cpu`** — works on
every platform with no localhost setup. ⚠ Speaches ships with no preloaded
models and does NOT auto-download them (verified against speaches v0.9.0-rc.3:
`/v1/audio/*` does a cache-only lookup and 404s on a missing model), so you must
preload Kokoro before the first synthesis (see the quick start below).

For voice cloning (5-second zero-shot), pick a Chatterbox variant. For pure
container-only setups on NVIDIA, `chatterbox-container-gpu`. For Apple
Silicon where MPS gives ~10× speedup over the Docker CPU path, install
Chatterbox natively and use `chatterbox-localhost` — see
[localhost/README.md](localhost/README.md).

## Quick start

Speaches (default — already enabled in `.env.example`):

```bash
./start.sh
# Speaches is healthy as soon as Uvicorn is up, but has no model yet —
# download the Kokoro ONNX build first (one-time; persists in speaches-cache):
curl -X POST http://localhost:63059/v1/models/speaches-ai/Kokoro-82M-v1.0-ONNX
curl http://localhost:63059/v1/audio/speech \
  -X POST -H "Content-Type: application/json" \
  -d '{"model":"speaches-ai/Kokoro-82M-v1.0-ONNX","input":"hello","voice":"af_heart"}' \
  --output speech.wav
```

Chatterbox (voice cloning, GPU required):

```bash
./start.sh --tts-provider-source chatterbox-container-gpu
```

Chatterbox on host (macOS native via MPS / Linux):

```bash
# Terminal 1 — install from git (no PyPI package):
git clone https://github.com/travisvn/chatterbox-tts-api
cd chatterbox-tts-api && uv sync
PORT=63044 uv run main.py

# Terminal 2
./start.sh --tts-provider-source chatterbox-localhost
```

Disable:

```bash
./start.sh --tts-provider-source disabled
```

## How Open WebUI is wired

The bootstrapper sets these env vars on the open-web-ui container based on
the chosen source:

- `AUDIO_TTS_ENGINE=openai`
- `AUDIO_TTS_OPENAI_API_BASE_URL=${TTS_ENDPOINT}/v1`
- `AUDIO_TTS_MODEL` = `SPEACHES_TTS_MODEL` (Speaches — use an executor-valid id like `speaches-ai/Kokoro-82M-v1.0-ONNX`) or `chatterbox-tts-1` (Chatterbox)
- `AUDIO_TTS_VOICE=af_heart` or `alloy`

You can override the model / voice in the Open WebUI admin panel after
startup — Audio settings.

## Full configuration reference

See [services/tts-provider/README.md](../../../services/tts-provider/README.md).

## References

- [Speaches](https://github.com/speaches-ai/speaches) — bundled Kokoro + Piper + Faster-Whisper
- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M)
- [Chatterbox upstream](https://github.com/resemble-ai/chatterbox)
- [chatterbox-tts-api server](https://github.com/travisvn/chatterbox-tts-api)
- [OpenAI Audio API spec](https://platform.openai.com/docs/guides/text-to-speech)
