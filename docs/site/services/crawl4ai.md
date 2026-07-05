# Crawl4AI (JS-capable web extraction)

## 1. Overview

`crawl4ai` is an Atlas service family in the `media` category. Its implementation and service-owned documentation live under `services/crawl4ai/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `media`
- Kind: `container`
- Tracks: `all, gen-ai-rag`

## 4. Access

- Kong aliases: `crawl4ai.localhost`
- Port variables: `CRAWL4AI_PORT`

## 5. Configuration

- SOURCE variables: `CRAWL4AI_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `-`
- Optional dependencies: `local-deep-researcher, n8n, backend, weaviate`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| CRAWL4AI_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/crawl4ai/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/crawl4ai/architecture.svg)
- Diagram HTML: [`services/crawl4ai/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/crawl4ai/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/crawl4ai/README.md](https://github.com/thekaveh/atlas/blob/main/services/crawl4ai/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
