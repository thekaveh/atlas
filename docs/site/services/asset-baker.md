# Asset Baker (Blender HP→LP bake)

## 1. Overview

`asset-baker` is an Atlas service family in the `media` category. Its implementation and service-owned documentation live under `services/asset-baker/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `media`
- Kind: `container`
- Tracks: `all, gen-ai-creative`

## 4. Access

- Kong aliases: `asset-baker.localhost`
- Port variables: `ASSET_BAKER_PORT`

## 5. Configuration

- SOURCE variables: `ASSET_BAKER_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `container-cpu, disabled`

## 6. Dependencies And Topology

- Required dependencies: `minio`
- Optional dependencies: `backend, comfyui, fal, blender-mcp, asset-worker`
- Runtime calls: `minio`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| ASSET_BAKER_SOURCE | disabled | container-cpu, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `minio`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/asset-baker/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/asset-baker/architecture.svg)
- Diagram HTML: [`services/asset-baker/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/asset-baker/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/asset-baker/README.md](https://github.com/thekaveh/atlas/blob/main/services/asset-baker/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
