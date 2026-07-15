# 5.3.6. TTS Localhost Provider

Run [Resemble AI Chatterbox](https://github.com/resemble-ai/chatterbox) natively
on your host machine and have the stack reach it via `host.docker.internal`.

This is the recommended TTS path when you want **zero-shot voice cloning**
(5-second reference audio) without using a GPU container — Chatterbox runs on
macOS MPS (Apple Silicon) and Linux CPU/MPS/CUDA.

## 1. Why localhost instead of container

- **macOS users**: Chatterbox uses MPS for acceleration; that doesn't work
  through Docker Desktop. Running natively is ~10× faster.
- **Limited GPU**: the container variant (`chatterbox-container-gpu`) needs
  ≥8 GB VRAM. The localhost variant can fall back to CPU if MPS / CUDA isn't
  available (slow but functional).
- **Voice management**: keeping voice samples on the host makes them easier
  to manage than mounting volumes into the container.

If you just want a TTS service that works out of the box on any platform with
no setup, use `TTS_PROVIDER_SOURCE=speaches-container-cpu` instead — Speaches
gives you Kokoro voices without any localhost setup, just zero voice cloning.

## 2. Install

Chatterbox-tts-api is NOT published to PyPI — install by cloning the repo.
Python 3.10+ required.

```bash
git clone https://github.com/travisvn/chatterbox-tts-api
cd chatterbox-tts-api

# Recommended (uv handles venv automatically):
uv sync

# Or with stock pip:
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

The transitive `chatterbox-tts` dependency (Resemble AI's model library) is
installed from a git source — first install can take a few minutes. Models
download from HuggingFace on first /v1/audio/speech request (~2 GB total).

## 3. Run the server

The repo's `main.py` is the entry point. Default port is `4123`; override
with the `PORT` env var.

```bash
# Bind on the port the atlas containers reach you on (63044
# matches the CHATTERBOX_LOCALHOST_PORT default — independent of the
# container CHATTERBOX_PORT, which is 63058).
PORT=63044 uv run main.py
# or, after `source .venv/bin/activate`:
PORT=63044 python main.py
# or directly via uvicorn:
uvicorn app.main:app --host 0.0.0.0 --port 63044
```

Apple Silicon users get MPS automatically via Chatterbox's
`DEVICE=auto`. Set `DEVICE=mps` explicitly if auto-detection fails, or
`DEVICE=cpu` to force CPU.

Then in another terminal point the stack at it:

```bash
./start.sh --tts-provider-source chatterbox-localhost
```

Optional: change the host port in `.env` if you used a different one (URL
is derived inline as `http://host.docker.internal:${CHATTERBOX_LOCALHOST_PORT:-63044}`):

```bash
CHATTERBOX_LOCALHOST_PORT=9000
```

## 4. Verify

```bash
curl http://localhost:63044/health         # expect {"status":"healthy"}
curl http://localhost:63044/v1/models      # expect chatterbox-tts-1 in list
curl -X POST http://localhost:63044/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"chatterbox-tts-1","input":"hello world","voice":"alloy"}' \
  --output /tmp/test.wav
file /tmp/test.wav   # expect: RIFF (little-endian) data, WAVE audio
```

## 5. Voice cloning

Chatterbox supports two voice-cloning paths — neither uses a
`reference_audio` JSON field (that was XTTS's convention).

**1) Pre-upload a voice into the server's voice library**, then reference
it by name:

```bash
# Upload once (multipart). Replace ALICE.wav with any 3–30 sec clean clip.
curl -X POST http://localhost:63044/voices \
  -F "name=alice" \
  -F "file=@ALICE.wav"

# Then call /v1/audio/speech with the registered name:
curl -X POST http://localhost:63044/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"chatterbox-tts-1","input":"Synthesize in this voice.","voice":"alice"}' \
  --output cloned.wav
```

**2) Inline upload via multipart form** in the speech call itself:

```bash
curl -X POST http://localhost:63044/v1/audio/speech \
  -F "input=Synthesize in this voice." \
  -F "model=chatterbox-tts-1" \
  -F "voice_file=@ALICE.wav" \
  --output cloned.wav
```

See [voice library management](https://github.com/travisvn/chatterbox-tts-api/blob/main/docs/VOICE_LIBRARY_MANAGEMENT.md)
upstream for the full voice-CRUD surface.

Chatterbox is licensed MIT, so the resulting audio is yours to use commercially.

## 6. Performance reference

| Hardware | Approx. realtime factor (lower is faster) |
|---|---|
| M2 Pro, MPS | 0.4–0.6× (faster than realtime) |
| M2 Pro, CPU only | 4–6× |
| NVIDIA RTX 4090 | 0.1× |

## 7. Troubleshooting

**MPS not detected on macOS** — `chatterbox-tts` will print `Using CPU`.
Reinstall PyTorch with MPS support: `pip install --upgrade --force-reinstall torch torchaudio`.

**Port already in use** — pick a different port:

```bash
chatterbox-tts-api --host 0.0.0.0 --port 9000
# then in .env:
CHATTERBOX_LOCALHOST_PORT=9000
```

**First request times out** — the model downloads on first call (~2 GB).
Pre-warm by running the curl test above with `--max-time 600`.

## 8. References

- [Chatterbox upstream](https://github.com/resemble-ai/chatterbox)
- [chatterbox-tts-api server](https://github.com/travisvn/chatterbox-tts-api)
- [Chatterbox model card](https://huggingface.co/ResembleAI/chatterbox)

## 9. Historical note

This directory previously hosted a server.py wrapper for openedai-speech /
XTTS v2. That stack was retired in this release because the upstream image
(`ghcr.io/matatonic/openedai-speech`) was archived on 2026-01-04 and XTTS-v2
weights are CPML / non-commercial. The old setup lived at top-level
`tts-provider/localhost/` before the configuration-modularization refactor
moved it under `services/tts-provider/provider/localhost/` — to inspect the
pre-retirement code, use
`git log --follow -- services/tts-provider/provider/localhost/` or browse
`git log -- tts-provider/localhost/` for the pre-move history.
