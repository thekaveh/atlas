# Langfuse (LLM traces + evals)

## 1. Overview

`langfuse` is an Atlas service family in the `infra` category. Its implementation and service-owned documentation live under `services/langfuse/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `infra`
- Kind: `container`
- Tracks: `all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading`

## 4. Access

- Kong aliases: `langfuse.localhost`
- Port variables: `LANGFUSE_PORT`

## 5. Configuration

- SOURCE variable: `LANGFUSE_SOURCE`
- Default SOURCE: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `supabase, redis, minio, litellm, kong, ray`
- Optional dependencies: `-`
- Runtime calls: `supabase, redis, minio, litellm`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| LANGFUSE_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `supabase, redis, minio, litellm`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/langfuse/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/langfuse/architecture.svg)
- Diagram HTML: [`services/langfuse/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/langfuse/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/langfuse/README.md](https://github.com/thekaveh/atlas/blob/main/services/langfuse/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
