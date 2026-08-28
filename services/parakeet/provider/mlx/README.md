# 5.3.3. Parakeet MLX Provider

OpenAI-compatible Speech-to-Text API server for Apple Silicon using the official `parakeet-mlx` library.

## 1. Architecture

This server wraps the pinned `parakeet-mlx==0.5.2` [aligned-result API](https://github.com/senstella/parakeet-mlx) with a FastAPI-based OpenAI-compatible REST API.

**Why not use parakeet-mlx CLI directly?**
- `parakeet-mlx` is a batch transcription tool (processes files, outputs results)
- The Atlas stack needs a persistent web server with REST API endpoints
- Our services (n8n, open-web-ui, backend, etc.) expect OpenAI-compatible `/v1/audio/transcriptions` endpoint

## 2. Quick Start

### 2.1. Install Dependencies

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

```bash
# Configure the same random value in repo-root .env before Atlas starts.
export PARAKEET_API_TOKEN='<same random value configured in repo-root .env>'
export PARAKEET_LOCALHOST_BIND_HOST=0.0.0.0
export PARAKEET_LOCALHOST_PORT=63042

# From services/parakeet/provider directory. The module entry point respects
# PARAKEET_LOCALHOST_BIND_HOST and PARAKEET_LOCALHOST_PORT. Atlas containers
# reach this host service through host.docker.internal, so the integration
# quickstart uses a non-loopback bind and requires the bearer token above.
python -m mlx.api_server
```

**First run:** Downloads model (~1.2GB) from HuggingFace
**Subsequent runs:** Reuse the cached download; model initialization still runs

### 2.3. Test

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
| `PARAKEET_LOCALHOST_BIND_HOST` | `127.0.0.1` | Listen address; use a non-loopback bind only when required for host-gateway access and keep bearer auth enabled. |
| `PARAKEET_API_TOKEN` | (required) | Bearer token; must match the value in Atlas `.env`. |

The `0.0.0.0` integration bind is reachable from the local network as well as
Docker's host gateway. Keep bearer authentication enabled and use the host
firewall when the network is not trusted.

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

The stack automatically uses this server when configured with:
```bash
STT_PROVIDER_SOURCE=parakeet-localhost
```

Services that use STT:
- **n8n** - Audio transcription workflows
- **open-web-ui** - Voice input in chat
- **backend** - Proxy API endpoints
- **jupyterhub** - Notebooks with STT
- **local-deep-researcher** - Audio research sources

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
PARAKEET_LOCALHOST_PORT=63099 python -m mlx.api_server

# Update .env to match (URL is derived inline as
# http://host.docker.internal:${PARAKEET_LOCALHOST_PORT:-63042})
PARAKEET_LOCALHOST_PORT=63099
```

## 8. References

- [parakeet-mlx GitHub](https://github.com/senstella/parakeet-mlx)
- [NVIDIA Parakeet-TDT v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
- [OpenAI Whisper API](https://platform.openai.com/docs/guides/speech-to-text)
