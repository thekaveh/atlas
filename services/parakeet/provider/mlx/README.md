# 5.3.3. Parakeet MLX Provider

OpenAI-compatible Speech-to-Text API server for Apple Silicon using the official `parakeet-mlx` library.

## 1. Architecture

This server wraps the pinned `parakeet-mlx==0.5.2` [aligned-result API](https://github.com/senstella/parakeet-mlx) with a FastAPI-based OpenAI-compatible REST API.

**Why not use parakeet-mlx CLI directly?**

- `parakeet-mlx` is a batch transcription tool (processes files, outputs results)
- The Atlas stack needs a persistent web server with REST API endpoints
- Atlas consumers (Open WebUI, n8n, JupyterHub, Hermes) expect OpenAI-compatible `/v1/audio/transcriptions` endpoint

## 2. Quick Start

### 2.1. Install Dependencies

Run these installation commands from `services/parakeet/provider/mlx`:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-locked.txt
```

This installs:

- `parakeet-mlx` - Official transcription library
- `fastapi` - Web framework
- `uvicorn` - ASGI server

### 2.2. Run Server

Before launch, prepare the active Atlas env file (`.env` by default; copy
`.env.example` only if the target does not exist). Set a nonempty
`PARAKEET_API_TOKEN` there; preserve an existing token. To generate a new value,
run `python3.12 -c 'import secrets; print(secrets.token_urlsafe(32))'`.

Run the preflight from the Atlas repository root. Use the same `ATLAS_ENV_FILE`
setting in both terminals. The preflight reads the configured value and stops
if it is missing or blank. Restart the native provider after changing its token.

Keep the MLX virtual environment active when you return to the repository root.

```bash
# The module entry point respects
# PARAKEET_LOCALHOST_BIND_HOST and PARAKEET_LOCALHOST_PORT. Docker Desktop
# containers reach this loopback bind through host.docker.internal. Linux
# engines need the bridge IP or 0.0.0.0. The bearer token read below is
# required.
PARAKEET_API_TOKEN=$(
  PYTHONPATH=bootstrapper python - <<'PYTOKEN'
import sys
from core.config_parser import ConfigParser

token = ConfigParser().parse_env_file().get("PARAKEET_API_TOKEN", "")
if not token:
    sys.exit("Set a nonempty PARAKEET_API_TOKEN in the active Atlas env file.")
print(token, end="")
PYTOKEN
) &&
export PARAKEET_API_TOKEN &&
export PARAKEET_LOCALHOST_BIND_HOST=127.0.0.1 &&
export PARAKEET_LOCALHOST_PORT=63042 &&
cd services/parakeet/provider &&
python -m mlx.api_server
```

**First run:** Downloads the default model (~2.5 GB) from Hugging Face
**Subsequent runs:** Reuse the cached download; model initialization still runs

### 2.3. Test

Run these checks in a second terminal from the Atlas repository root. Activate
the MLX virtual environment from §2.1 first
(`. services/parakeet/provider/mlx/.venv/bin/activate`). Then repeat the
`PARAKEET_API_TOKEN` assignment and its `export` from §2.2.

```bash
# Health check
curl http://localhost:63042/health

# Transcribe audio
curl -X POST http://localhost:63042/v1/audio/transcriptions \
  -H "Authorization: Bearer ${PARAKEET_API_TOKEN}" \
  -F "file=@audio.mp3" \
  -F "response_format=json"
```

## 3. Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PARAKEET_MODEL` | `mlx-community/parakeet-tdt-0.6b-v3` | HuggingFace model ID |
| `PARAKEET_LOCALHOST_PORT` | `63042` | Host port Atlas containers use for `parakeet-localhost`. |
| `PARAKEET_LOCALHOST_BIND_HOST` | `127.0.0.1` | Listen address. Keep `127.0.0.1` on Docker Desktop/macOS. Use a non-loopback bind only for remote clients, and keep bearer auth enabled. |
| `PARAKEET_API_TOKEN` | (required) | Bearer token; must match the value in Atlas `.env`. |

The upload, concurrency, timeout, authentication and CORS variables
(`PARAKEET_MAX_UPLOAD_BYTES`, `PARAKEET_CONCURRENCY` and others) apply to this
server too. The [STT Provider README](../../../stt-provider/README.md) lists
them with their defaults.

A non-loopback bind such as `0.0.0.0` is reachable from the local network.
Keep bearer authentication enabled and use the host firewall when the network
is not trusted.

## 4. API Endpoints

### 4.1. GET /health
Health check endpoint

**Response:**
```json
{
  "status": "healthy",
  "backend": "mlx",
  "device": "mps",
  "model": "mlx-community/parakeet-tdt-0.6b-v3",
  "model_loaded": true
}
```

### 4.2. POST /v1/audio/transcriptions
OpenAI-compatible transcription endpoint

**Parameters:**

- `file` (required): Audio file
- `model`: Model name (informational)
- `response_format`: `json`, `verbose_json`, or `text`
- `language`: Language code (optional)
- `temperature`: Sampling temperature (not used)
- `prompt`: Context prompt (not used)

**Response (json):**
```json
{
  "text": "Transcribed text appears here."
}
```

### 4.3. POST /v1/audio/transcriptions/advanced
Advanced endpoint with timestamps

**Parameters:**

- `file` (required): Audio file
- `return_timestamps`: Include segment timestamps (default: `false`, consistent across providers)
- `word_timestamps`: Include word-level timestamps

The provider serializes `AlignedResult.sentences` as segments and each
sentence's nested tokens as words. `has_timestamps` is true only when the
requested timestamp collection contains aligned data; it is not inferred from
the request flags alone.

## 5. Performance

Performance depends on the Apple Silicon generation, model revision, audio
codec, duration, and timestamp options. Benchmark representative audio on the
target host and record real-time factor plus peak resident memory before using
the provider for capacity planning. MLX uses Apple Silicon acceleration; Atlas
does not publish a hardware-independent throughput guarantee.

## 6. Integration with Atlas

Select this server as the STT engine:

```bash
./start.sh --stt-provider-source parakeet-localhost
```

Atlas then sets `STT_ENDPOINT` to
`http://host.docker.internal:${PARAKEET_LOCALHOST_PORT:-63042}`. These services
receive it:

- **open-web-ui** - voice input in chat
- **n8n** - audio transcription workflows
- **backend** - receives `STT_ENDPOINT` but does not call it
- **jupyterhub** - `STT_ENDPOINT` in notebooks
- **hermes** - `STT_INTERNAL_URL` for its speech-to-text tool

## 7. Troubleshooting

### 7.1. Model download fails
```bash
# Set HuggingFace token if needed
export HUGGING_FACE_HUB_TOKEN=your_token_here
```

### 7.2. Import errors
```bash
# Ensure you're in the right directory
cd services/parakeet/provider
python -m mlx.api_server
```

### 7.3. Port already in use
```bash
# Use different port (if 63042 is in use)
PARAKEET_LOCALHOST_PORT=18142 python -m mlx.api_server

# Update .env to match (URL is derived inline as
# http://host.docker.internal:${PARAKEET_LOCALHOST_PORT:-63042})
PARAKEET_LOCALHOST_PORT=18142
```

## 8. References

- [parakeet-mlx GitHub](https://github.com/senstella/parakeet-mlx)
- [NVIDIA Parakeet-TDT v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
- [OpenAI Whisper API](https://platform.openai.com/docs/guides/speech-to-text)
