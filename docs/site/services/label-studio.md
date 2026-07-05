# Label Studio (dataset review + annotation)

## 1. Overview

`label-studio` is an Atlas service family in the `apps` category. Its implementation and service-owned documentation live under `services/label-studio/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `apps`
- Kind: `container`
- Tracks: `all, ml-eng`

## 4. Access

- Kong aliases: `label-studio.localhost`
- Port variables: `LABEL_STUDIO_PORT`

## 5. Configuration

- SOURCE variables: `LABEL_STUDIO_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `supabase, minio`
- Optional dependencies: `jupyterhub, mlflow`
- Runtime calls: `supabase, minio`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| LABEL_STUDIO_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `supabase, minio`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/label-studio/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/label-studio/architecture.svg)
- Diagram HTML: [`services/label-studio/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/label-studio/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/label-studio/README.md](https://github.com/thekaveh/atlas/blob/main/services/label-studio/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
