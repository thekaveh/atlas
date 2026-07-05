# Weaviate (vector database)

## 1. Overview

`weaviate` is an Atlas service family in the `data` category. Its implementation and service-owned documentation live under `services/weaviate/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `data`
- Kind: `container`
- Tracks: `all, data-eng, gen-ai-rag`

## 4. Access

- Kong aliases: `weaviate.localhost`
- Port variables: `WEAVIATE_PORT, WEAVIATE_GRPC_PORT, WEAVIATE_LOCALHOST_PORT`

## 5. Configuration

- SOURCE variable: `WEAVIATE_SOURCE`
- Default SOURCE: `container`
- Available SOURCE values: `container, localhost, disabled`

## 6. Dependencies And Topology

- Required dependencies: `supabase, litellm`
- Optional dependencies: `-`
- Runtime calls: `litellm, multi2vec-clip`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| WEAVIATE_SOURCE | container | container, localhost, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `litellm, multi2vec-clip`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/weaviate/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/weaviate/architecture.svg)
- Diagram HTML: [`services/weaviate/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/weaviate/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/weaviate/README.md](https://github.com/thekaveh/atlas/blob/main/services/weaviate/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
