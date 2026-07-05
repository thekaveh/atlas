# LiteLLM gateway (LLM router)

## 1. Overview

`litellm` is an Atlas service family in the `llm` category. Its implementation and service-owned documentation live under `services/litellm/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `llm`
- Kind: `container`
- Tracks: `all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading`

## 4. Access

- Kong aliases: `litellm.localhost`
- Port variables: `LITELLM_PORT`

## 5. Configuration

- SOURCE variable: `LITELLM_SOURCE`
- Default SOURCE: `container`
- Available SOURCE values: `container`

## 6. Dependencies And Topology

- Required dependencies: `supabase, redis`
- Optional dependencies: `-`
- Runtime calls: `supabase, redis, ollama, cloud-providers, hermes, lightrag, otel-collector`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| LITELLM_SOURCE | container | container |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `supabase, redis, ollama, cloud-providers, hermes, lightrag, otel-collector`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/litellm/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/litellm/architecture.svg)
- Diagram HTML: [`services/litellm/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/litellm/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/litellm/README.md](https://github.com/thekaveh/atlas/blob/main/services/litellm/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
