# Asset Worker (glTF post-processing)

## 1. Overview

`asset-worker` is an Atlas service family in the `media` category. Its implementation and service-owned documentation live under `services/asset-worker/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `media`
- Kind: `container`
- Tracks: `all, gen-ai-creative`

## 4. Access

- Kong aliases: `asset-worker.localhost`
- Port variables: `ASSET_WORKER_PORT`

## 5. Configuration

- SOURCE variables: `ASSET_WORKER_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `minio`
- Optional dependencies: `backend, comfyui, fal, blender-mcp`
- Runtime calls: `minio`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| ASSET_WORKER_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `minio`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/asset-worker/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/asset-worker/architecture.svg)
- Diagram HTML: [`services/asset-worker/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/asset-worker/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/asset-worker/README.md](https://github.com/thekaveh/atlas/blob/main/services/asset-worker/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
