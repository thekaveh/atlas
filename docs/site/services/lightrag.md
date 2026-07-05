# LightRAG (graph-augmented RAG server)

## 1. Overview

`lightrag` is an Atlas service family in the `agents` category. Its implementation and service-owned documentation live under `services/lightrag/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `agents`
- Kind: `container`
- Tracks: `all, gen-ai-rag`

## 4. Access

- Kong aliases: `lightrag.localhost`
- Port variables: `LIGHTRAG_API_PORT, LIGHTRAG_LOCALHOST_PORT`

## 5. Configuration

- SOURCE variables: `LIGHTRAG_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `container, localhost, disabled`

## 6. Dependencies And Topology

- Required dependencies: `litellm`
- Optional dependencies: `supabase, neo4j, redis, docling`
- Runtime calls: `litellm, supabase, neo4j, redis, docling`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| LIGHTRAG_SOURCE | disabled | container, localhost, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `litellm, supabase, neo4j, redis, docling`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/lightrag/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/lightrag/architecture.svg)
- Diagram HTML: [`services/lightrag/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/lightrag/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/lightrag/README.md](https://github.com/thekaveh/atlas/blob/main/services/lightrag/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
