# 6.8. LLM Provider Flow

Ollama, LiteLLM, cloud passthroughs, Open WebUI, backend, MCP/tool access, and trace hooks.

## 1. Diagram

[Open the interactive diagram](./llm-provider-flow.html).

## 2. Notes

Disabling a cloud provider in `.env` doesn't error — the model resolver silently produces zero catalog entries for it. Ollama models get two aliases (`ollama/<name>` and the bare name); either works. Tracing observes requests out-of-band and isn't part of the inference call path, so a tracing-backend outage doesn't affect completions.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `bootstrapper/services/topology.py`
- `docs/deployment/source-configuration.md`
