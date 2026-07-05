# Local Deep Researcher (LangGraph research agent)

## 1. Overview

`local-deep-researcher` is an Atlas service family in the `apps` category. Its implementation and service-owned documentation live under `services/local-deep-researcher/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `apps`
- Kind: `container`
- Tracks: `all, gen-ai-eng, gen-ai-rag`

## 4. Access

- Kong aliases: `research.localhost`
- Port variables: `LOCAL_DEEP_RESEARCHER_PORT`

## 5. Configuration

- SOURCE variables: `LOCAL_DEEP_RESEARCHER_SOURCE`
- Default SOURCE values: `container`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `searxng, litellm`
- Optional dependencies: `crawl4ai`
- Runtime calls: `litellm, searxng, crawl4ai`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| LOCAL_DEEP_RESEARCHER_SOURCE | container | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `litellm, searxng, crawl4ai`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/local-deep-researcher/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/local-deep-researcher/architecture.svg)
- Diagram HTML: [`services/local-deep-researcher/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/local-deep-researcher/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/local-deep-researcher/README.md](https://github.com/thekaveh/atlas/blob/main/services/local-deep-researcher/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
