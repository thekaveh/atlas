# Ollama (local LLM engine)

## 1. Overview

`ollama` is an Atlas service family in the `llm` category. Its implementation and service-owned documentation live under `services/ollama/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `llm`
- Kind: `container`
- Tracks: `all`

## 4. Access

- Kong aliases: `ollama.localhost`
- Port variables: `OLLAMA_LOCALHOST_PORT`

## 5. Configuration

- SOURCE variables: `LLM_PROVIDER_SOURCE`
- Default SOURCE values: `ollama-container-cpu`
- Available SOURCE values: `ollama-container-cpu, ollama-container-gpu, ollama-localhost, none`

## 6. Dependencies And Topology

- Required dependencies: `supabase, litellm`
- Optional dependencies: `-`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| LLM_PROVIDER_SOURCE | ollama-container-cpu | ollama-container-cpu, ollama-container-gpu, ollama-localhost, none |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/ollama/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/ollama/architecture.svg)
- Diagram HTML: [`services/ollama/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/ollama/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/ollama/README.md](https://github.com/thekaveh/atlas/blob/main/services/ollama/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
