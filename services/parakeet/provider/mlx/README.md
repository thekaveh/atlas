# Parakeet MLX Server - OpenAI-Compatible STT API

OpenAI-compatible Speech-to-Text API server for Apple Silicon using the official `parakeet-mlx` library.

## Architecture

This server wraps the official [parakeet-mlx](https://github.com/senstella/parakeet-mlx) library with a FastAPI-based OpenAI-compatible REST API.

**Why not use parakeet-mlx CLI directly?**
- `parakeet-mlx` is a batch transcription tool (processes files, outputs results)
- The Atlas stack needs a persistent web server with REST API endpoints
- Our services (n8n, open-web-ui, backend, etc.) expect OpenAI-compatible `/v1/audio/transcriptions` endpoint

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `parakeet-mlx` - Official transcription library
- `fastapi` - Web framework
- `uvicorn` - ASGI server

### 2. Run Server

```bash
# From services/parakeet/provider directory
python -m uvicorn mlx.api_server:app --host 0.0.0.0 --port 63042
```

**First run:** Downloads model (~1.2GB) from HuggingFace
**Subsequent runs:** Model loaded from cache, starts instantly

### 3. Test

```bash
# Health check
curl http://localhost:63042/health

# Transcribe audio
curl -X POST http://localhost:63042/v1/audio/transcriptions \
  -F "file=@audio.mp3" \
  -F "response_format=json"
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PARAKEET_MODEL` | `mlx-community/parakeet-tdt-0.6b-v3` | HuggingFace model ID |
| `PARAKEET_LOCALHOST_PORT` | `63042` | Host port Atlas containers use for `parakeet-localhost`. |

## API Endpoints

### GET /health
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

### POST /v1/audio/transcriptions
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

### POST /v1/audio/transcriptions/advanced
Advanced endpoint with timestamps

**Parameters:**
- `file` (required): Audio file
- `return_timestamps`: Include segment timestamps
- `word_timestamps`: Include word-level timestamps

## Performance

**Apple Silicon (M1/M2/M3/M4):**
- Speed: 100-300x real-time
- Memory: ~2GB RAM
- Device: Metal Performance Shaders (MPS)

**Example:**
- M2 Ultra: 3-hour podcast → 1 minute transcription
- M1: 1-hour audio → 36 seconds transcription

## Integration with Atlas

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

## Troubleshooting

### Model download fails
```bash
# Set HuggingFace token if needed
export HUGGING_FACE_HUB_TOKEN=your_token_here
```

### Import errors
```bash
# Ensure you're in the right directory
cd services/parakeet/provider
python -m uvicorn mlx.api_server:app --host 0.0.0.0 --port 63042
```

### Port already in use
```bash
# Use different port (if 63042 is in use)
python -m uvicorn mlx.api_server:app --host 0.0.0.0 --port 63099

# Update .env to match (URL is derived inline as
# http://host.docker.internal:${PARAKEET_LOCALHOST_PORT:-63042})
PARAKEET_LOCALHOST_PORT=63099
```

## References

- [parakeet-mlx GitHub](https://github.com/senstella/parakeet-mlx)
- [NVIDIA Parakeet-TDT v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
- [OpenAI Whisper API](https://platform.openai.com/docs/guides/speech-to-text)
