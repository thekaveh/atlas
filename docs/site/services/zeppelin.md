# Apache Zeppelin (Spark-first notebook)

## 1. Overview

`zeppelin` is an Atlas service family in the `apps` category. Its implementation and service-owned documentation live under `services/zeppelin/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `apps`
- Kind: `container`
- Tracks: `all, data-eng, ml-eng`

## 4. Access

- Kong aliases: `zeppelin.localhost`
- Port variables: `ZEPPELIN_PORT`

## 5. Configuration

- SOURCE variables: `ZEPPELIN_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `spark`
- Optional dependencies: `supabase, minio, iceberg-rest, redpanda, trino`
- Runtime calls: `spark, supabase, minio, iceberg-rest, redpanda, trino`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| ZEPPELIN_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `spark, supabase, minio, iceberg-rest, redpanda, trino`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/zeppelin/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/zeppelin/architecture.svg)
- Diagram HTML: [`services/zeppelin/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/zeppelin/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/zeppelin/README.md](https://github.com/thekaveh/atlas/blob/main/services/zeppelin/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
