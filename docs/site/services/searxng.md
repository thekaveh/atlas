# SearXNG (privacy metasearch)

## 1. Overview

`searxng` is an Atlas service family in the `media` category. Its implementation and service-owned documentation live under `services/searxng/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `media`
- Kind: `container`
- Tracks: `all, gen-ai-eng, gen-ai-rag`

## 4. Access

- Kong aliases: `search.localhost`
- Port variables: `SEARXNG_PORT`

## 5. Configuration

- SOURCE variables: `SEARXNG_SOURCE`
- Default SOURCE values: `container`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `redis`
- Optional dependencies: `-`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| SEARXNG_SOURCE | container | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/searxng/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/searxng/architecture.svg)
- Diagram HTML: [`services/searxng/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/searxng/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/searxng/README.md](https://github.com/thekaveh/atlas/blob/main/services/searxng/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
