# Neo4j (graph database)

## 1. Overview

`neo4j` is an Atlas service family in the `data` category. Its implementation and service-owned documentation live under `services/neo4j/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `data`
- Kind: `container`
- Tracks: `all, data-eng, gen-ai-eng, gen-ai-rag`

## 4. Access

- Kong aliases: `graph.localhost`
- Port variables: `GRAPH_DB_PORT, GRAPH_DB_DASHBOARD_PORT, NEO4J_LOCALHOST_HTTP_PORT, NEO4J_LOCALHOST_BOLT_PORT`

## 5. Configuration

- SOURCE variable: `NEO4J_GRAPH_DB_SOURCE`
- Default SOURCE: `container`
- Available SOURCE values: `container, localhost, disabled`

## 6. Dependencies And Topology

- Required dependencies: `supabase`
- Optional dependencies: `-`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| NEO4J_GRAPH_DB_SOURCE | container | container, localhost, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/neo4j/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/neo4j/architecture.svg)
- Diagram HTML: [`services/neo4j/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/neo4j/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/neo4j/README.md](https://github.com/thekaveh/atlas/blob/main/services/neo4j/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
