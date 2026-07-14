# LLM Provider Flow

Ollama, LiteLLM, cloud passthroughs, Open WebUI, backend, MCP/tool access, and trace hooks.

## 1. Diagram

[Open the interactive diagram](./llm-provider-flow.html).

## 2. How To Read This View

Open WebUI, Backend routes, agents, and tools call LiteLLM rather than binding to a provider. LiteLLM dispatches to local Ollama or enabled cloud providers and exposes one model catalog. Tracing observes requests without becoming part of the inference data path.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `services/topology.py`
- `docs/deployment/source-configuration.md`

## 4. Maintenance

Regenerate this page and `llm-provider-flow.html` after changing a represented service,
route, SOURCE mode, track, dependency, or data-flow boundary.
