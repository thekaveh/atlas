# Apache Iceberg REST Catalog

## 1. Overview

`iceberg-rest` is an Atlas service family in the `data` category. Its implementation and service-owned documentation live under `services/iceberg-rest/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `data`
- Kind: `container`
- Tracks: `all, data-eng`

## 4. Access

- Kong aliases: `-`
- Port variables: `ICEBERG_REST_PORT`

## 5. Configuration

- SOURCE variable: `ICEBERG_REST_SOURCE`
- Default SOURCE: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `minio, supabase`
- Optional dependencies: `-`
- Runtime calls: `minio, supabase`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| ICEBERG_REST_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `minio, supabase`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/iceberg-rest/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/iceberg-rest/architecture.svg)
- Diagram HTML: [`services/iceberg-rest/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/iceberg-rest/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/iceberg-rest/README.md](https://github.com/thekaveh/atlas/blob/main/services/iceberg-rest/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
