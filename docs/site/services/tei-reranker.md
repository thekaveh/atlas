# TEI Reranker (mxbai-rerank-base-v1)

## 1. Overview

`tei-reranker` is an Atlas service family in the `llm` category. Its implementation and service-owned documentation live under `services/tei-reranker/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `llm`
- Kind: `container`
- Tracks: `all, gen-ai-rag, ml-eng`

## 4. Access

- Kong aliases: `rerank.localhost`
- Port variables: `TEI_RERANKER_PORT, TEI_RERANKER_LOCALHOST_PORT`

## 5. Configuration

- SOURCE variables: `TEI_RERANKER_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `container-cpu, container-gpu, localhost, disabled`

## 6. Dependencies And Topology

- Required dependencies: `-`
- Optional dependencies: `-`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| TEI_RERANKER_SOURCE | disabled | container-cpu, container-gpu, localhost, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/tei-reranker/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/tei-reranker/architecture.svg)
- Diagram HTML: [`services/tei-reranker/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/tei-reranker/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/tei-reranker/README.md](https://github.com/thekaveh/atlas/blob/main/services/tei-reranker/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
