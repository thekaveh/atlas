# Apache Airflow (DAG orchestrator)

## 1. Overview

`airflow` is an Atlas service family in the `agents` category. Its implementation and service-owned documentation live under `services/airflow/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `agents`
- Kind: `container`
- Tracks: `all, data-eng`

## 4. Access

- Kong aliases: `airflow.localhost`
- Port variables: `AIRFLOW_PORT`

## 5. Configuration

- SOURCE variable: `AIRFLOW_SOURCE`
- Default SOURCE: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `supabase, litellm, redis`
- Optional dependencies: `spark, minio, iceberg-rest, redpanda, weaviate, neo4j`
- Runtime calls: `supabase, spark, redpanda, minio, iceberg-rest, litellm, weaviate, neo4j, redis`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| AIRFLOW_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `supabase, spark, redpanda, minio, iceberg-rest, litellm, weaviate, neo4j, redis`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/airflow/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/airflow/architecture.svg)
- Diagram HTML: [`services/airflow/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/airflow/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/airflow/README.md](https://github.com/thekaveh/atlas/blob/main/services/airflow/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
