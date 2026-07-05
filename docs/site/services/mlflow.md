# MLflow (experiment tracking + artifacts)

## 1. Overview

`mlflow` is an Atlas service family in the `apps` category. Its implementation and service-owned documentation live under `services/mlflow/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `apps`
- Kind: `container`
- Tracks: `all, ml-eng, trading`

## 4. Access

- Kong aliases: `mlflow.localhost`
- Port variables: `MLFLOW_PORT`

## 5. Configuration

- SOURCE variable: `MLFLOW_SOURCE`
- Default SOURCE: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `supabase, minio`
- Optional dependencies: `jupyterhub`
- Runtime calls: `supabase, minio`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| MLFLOW_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `supabase, minio`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/mlflow/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/mlflow/architecture.svg)
- Diagram HTML: [`services/mlflow/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/mlflow/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/mlflow/README.md](https://github.com/thekaveh/atlas/blob/main/services/mlflow/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
