# 5.3.6. TTS Localhost Provider

Run [Resemble AI Chatterbox](https://github.com/resemble-ai/chatterbox) on the
host with the [chatterbox-tts-api](https://github.com/travisvn/chatterbox-tts-api)
server. Atlas containers reach it through `host.docker.internal`. Select it
with `TTS_PROVIDER_SOURCE=chatterbox-localhost`.

Use this source for zero-shot voice cloning without an NVIDIA container.
The server runs on macOS (MPS) and on Linux (CPU or CUDA).

## 1. When to use it

- **macOS:** Docker Desktop containers cannot use MPS. A host install can.
- **No large GPU:** the `chatterbox-container-gpu` source needs an NVIDIA GPU
  with at least 8 GB of VRAM. The host server can run on the CPU, but slowly.
- **Voice samples on the host:** the voice library is a host directory, not a
  container volume.

For TTS with no host setup, use `TTS_PROVIDER_SOURCE=speaches-container-cpu`.
Speaches provides Kokoro voices but no voice cloning.

Atlas publishes no speed figures for Chatterbox. Benchmark representative
text on the deployment host.

## 2. Install

The `chatterbox-tts-api` 1.0.0 package on PyPI contains no code, so install
from the repository. Its `pyproject.toml` requires Python 3.11.

```bash
git clone https://github.com/travisvn/chatterbox-tts-api
cd chatterbox-tts-api

# With uv (creates the virtual environment):
uv sync

# Or with pip:
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

The `chatterbox-tts` model library installs from a git source, so the first
install can take a few minutes.

## 3. Run the server

`main.py` is the entry point. It listens on `PORT` (default `4123`) and `HOST`
(default `0.0.0.0`).

```bash
# 63044 is the CHATTERBOX_LOCALHOST_PORT default. It is independent of the
# container CHATTERBOX_PORT, which is 63059.
PORT=63044 uv run main.py
# or, in an activated virtual environment:
PORT=63044 python main.py
```

The server starts, then loads the model in the background. The first start
downloads the weights from Hugging Face (about 2 GB). `GET /health` responds
during the load and reports the initialization state.

The server has no authentication. With the default `HOST=0.0.0.0`, it is
reachable from the local network as well as from Docker. Use the host firewall
when the network is not trusted.

The device setting `DEVICE=auto` selects MPS on Apple Silicon. Set
`DEVICE=mps` if detection fails, or `DEVICE=cpu` to force the CPU.

In another terminal, from the Atlas repository root, select the source:

```bash
./start.sh --tts-provider-source chatterbox-localhost
```

If the server uses another port, set it in `.env`. Atlas derives the URL
`http://host.docker.internal:${CHATTERBOX_LOCALHOST_PORT:-63044}`:

```bash
CHATTERBOX_LOCALHOST_PORT=9000
```

## 4. Verify

```bash
curl http://localhost:63044/health         # expect "status": "healthy" once loaded
curl http://localhost:63044/v1/models      # expect chatterbox-tts-1 in the list
curl -X POST http://localhost:63044/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"chatterbox-tts-1","input":"hello world","voice":"alloy"}' \
  --output /tmp/test.wav
file /tmp/test.wav   # expect: RIFF (little-endian) data, WAVE audio
```

## 5. Voice cloning

Chatterbox has two voice-cloning paths. Neither uses a `reference_audio`
JSON field.

**1) Add a voice to the server's voice library**, then use its name:

```bash
# Upload once (multipart). Use a short, clean speech clip.
curl -X POST http://localhost:63044/voices \
  -F "voice_name=alice" \
  -F "voice_file=@ALICE.wav"

# Use the registered name as the voice:
curl -X POST http://localhost:63044/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"chatterbox-tts-1","input":"Synthesize in this voice.","voice":"alice"}' \
  --output cloned.wav
```

**2) Upload the reference clip with the request** to the `/upload` route:

```bash
curl -X POST http://localhost:63044/v1/audio/speech/upload \
  -F "input=Synthesize in this voice." \
  -F "voice_file=@ALICE.wav" \
  --output cloned.wav
```

The upstream [voice library guide](https://github.com/travisvn/chatterbox-tts-api/blob/main/docs/VOICE_LIBRARY_MANAGEMENT.md)
covers the other voice operations.

The Chatterbox model is MIT-licensed. The chatterbox-tts-api server is
AGPL-3.0.

## 6. Troubleshooting

**MPS not detected on macOS:** the server log reports the CPU device. Set
`DEVICE=mps`. If that fails, reinstall PyTorch with MPS support, for example
`pip install --upgrade --force-reinstall torch torchaudio`.

**Port already in use:** start on another port, then set it in `.env`:

```bash
PORT=9000 uv run main.py
# then in .env:
CHATTERBOX_LOCALHOST_PORT=9000
```

**First requests fail after the first start:** the model is still
downloading or loading. Wait until `/health` returns `"status": "healthy"`.

## 7. References

- [Chatterbox upstream](https://github.com/resemble-ai/chatterbox)
- [chatterbox-tts-api server](https://github.com/travisvn/chatterbox-tts-api)
- [Chatterbox model card](https://huggingface.co/ResembleAI/chatterbox)
