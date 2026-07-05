# Redpanda (Kafka API streaming)

## 1. Overview

`redpanda` is an Atlas service family in the `data` category. Its implementation and service-owned documentation live under `services/redpanda/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `data`
- Kind: `container`
- Tracks: `all, data-eng`

## 4. Access

- Kong aliases: `redpanda.localhost`
- Port variables: `REDPANDA_KAFKA_PORT, REDPANDA_CONSOLE_PORT`

## 5. Configuration

- SOURCE variable: `REDPANDA_SOURCE`
- Default SOURCE: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `-`
- Optional dependencies: `spark, jupyterhub, zeppelin, airflow, iceberg-rest, minio`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| REDPANDA_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/redpanda/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/redpanda/architecture.svg)
- Diagram HTML: [`services/redpanda/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/redpanda/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/redpanda/README.md](https://github.com/thekaveh/atlas/blob/main/services/redpanda/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
