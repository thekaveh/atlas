# Backend API (FastAPI)

## 1. Overview

`backend` is an Atlas service family in the `apps` category. Its implementation and service-owned documentation live under `services/backend/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `apps`
- Kind: `container`
- Tracks: `all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading`

## 4. Access

- Kong aliases: `api.localhost`
- Port variables: `BACKEND_PORT`

## 5. Configuration

- SOURCE variable: `BACKEND_SOURCE`
- Default SOURCE: `container`
- Available SOURCE values: `-`

## 6. Dependencies And Topology

- Required dependencies: `supabase, redis, litellm`
- Optional dependencies: `weaviate, kong, celery, supavisor`
- Runtime calls: `supabase, weaviate, litellm, comfyui, n8n, ray, local-deep-researcher, celery, supavisor, tika, otel-collector`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| BACKEND_SOURCE | container | - |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `supabase, weaviate, litellm, comfyui, n8n, ray, local-deep-researcher, celery, supavisor, tika, otel-collector`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/backend/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/backend/architecture.svg)
- Diagram HTML: [`services/backend/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/backend/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/backend/README.md](https://github.com/thekaveh/atlas/blob/main/services/backend/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
