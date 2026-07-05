# Ray (distributed compute substrate)

## 1. Overview

`ray` is an Atlas service family in the `infra` category. Its implementation and service-owned documentation live under `services/ray/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `infra`
- Kind: `container`
- Tracks: `all, ml-eng`

## 4. Access

- Kong aliases: `ray.localhost`
- Port variables: `RAY_DASHBOARD_PORT, RAY_GCS_PORT, RAY_CLIENT_PORT`

## 5. Configuration

- SOURCE variables: `RAY_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `ray-container-cpu, ray-container-gpu, disabled`

## 6. Dependencies And Topology

- Required dependencies: `supabase, redis`
- Optional dependencies: `-`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| RAY_SOURCE | disabled | ray-container-cpu, ray-container-gpu, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/ray/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/ray/architecture.svg)
- Diagram HTML: [`services/ray/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/ray/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/ray/README.md](https://github.com/thekaveh/atlas/blob/main/services/ray/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
