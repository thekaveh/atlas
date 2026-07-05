# Neo4j LLM Graph Builder

## 1. Overview

`llm-graph-builder` is an Atlas service family in the `apps` category. Its implementation and service-owned documentation live under `services/llm-graph-builder/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `apps`
- Kind: `container`
- Tracks: `all, gen-ai-rag`

## 4. Access

- Kong aliases: `graphbuilder.localhost, graphbuilder-api.localhost`
- Port variables: `LLM_GRAPH_BUILDER_PORT`

## 5. Configuration

- SOURCE variables: `LLM_GRAPH_BUILDER_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `neo4j, litellm, kong`
- Optional dependencies: `minio, docling`
- Runtime calls: `neo4j, litellm, minio, docling`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| LLM_GRAPH_BUILDER_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `neo4j, litellm, minio, docling`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/llm-graph-builder/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/llm-graph-builder/architecture.svg)
- Diagram HTML: [`services/llm-graph-builder/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/llm-graph-builder/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/llm-graph-builder/README.md](https://github.com/thekaveh/atlas/blob/main/services/llm-graph-builder/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
