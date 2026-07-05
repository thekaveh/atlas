# Apache Spark (standalone cluster)

## 1. Overview

`spark` is an Atlas service family in the `data` category. Its implementation and service-owned documentation live under `services/spark/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `data`
- Kind: `container`
- Tracks: `all, data-eng, ml-eng`

## 4. Access

- Kong aliases: `spark.localhost, spark-history.localhost`
- Port variables: `SPARK_MASTER_UI_PORT, SPARK_HISTORY_PORT`

## 5. Configuration

- SOURCE variables: `SPARK_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `minio`
- Optional dependencies: `supabase, iceberg-rest, redpanda`
- Runtime calls: `minio, iceberg-rest, redpanda`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| SPARK_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `minio, iceberg-rest, redpanda`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/spark/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/spark/architecture.svg)
- Diagram HTML: [`services/spark/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/spark/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/spark/README.md](https://github.com/thekaveh/atlas/blob/main/services/spark/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
