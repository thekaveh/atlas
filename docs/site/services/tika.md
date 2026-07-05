# Apache Tika (fallback extractor)

## 1. Overview

`tika` is an Atlas service family in the `media` category. Its implementation and service-owned documentation live under `services/tika/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `media`
- Kind: `container`
- Tracks: `all, gen-ai-eng, gen-ai-rag`

## 4. Access

- Kong aliases: `tika.localhost`
- Port variables: `TIKA_PORT, TIKA_LOCALHOST_PORT`

## 5. Configuration

- SOURCE variables: `TIKA_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `container, tika-localhost, disabled`

## 6. Dependencies And Topology

- Required dependencies: `-`
- Optional dependencies: `backend, n8n`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| TIKA_SOURCE | disabled | container, tika-localhost, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/tika/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/tika/architecture.svg)
- Diagram HTML: [`services/tika/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/tika/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/tika/README.md](https://github.com/thekaveh/atlas/blob/main/services/tika/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
