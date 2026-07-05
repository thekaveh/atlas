# Trino

## 1. Overview

`trino` is an Atlas service family in the `data` category. Its implementation and service-owned documentation live under `services/trino/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `data`
- Kind: `container`
- Tracks: `all, data-eng`

## 4. Access

- Kong aliases: `trino.localhost`
- Port variables: `TRINO_PORT`

## 5. Configuration

- SOURCE variables: `TRINO_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `minio, iceberg-rest`
- Optional dependencies: `spark, zeppelin, jupyterhub, airflow`
- Runtime calls: `iceberg-rest, minio`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| TRINO_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `iceberg-rest, minio`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/trino/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/trino/architecture.svg)
- Diagram HTML: [`services/trino/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/trino/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/trino/README.md](https://github.com/thekaveh/atlas/blob/main/services/trino/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
