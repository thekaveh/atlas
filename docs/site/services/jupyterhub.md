# JupyterHub (DS/ML + LLM notebooks)

## 1. Overview

`jupyterhub` is an Atlas service family in the `apps` category. Its implementation and service-owned documentation live under `services/jupyterhub/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `apps`
- Kind: `container`
- Tracks: `all, data-eng, gen-ai-eng, ml-eng, trading`

## 4. Access

- Kong aliases: `jupyter.localhost`
- Port variables: `JUPYTERHUB_PORT`

## 5. Configuration

- SOURCE variable: `JUPYTERHUB_SOURCE`
- Default SOURCE: `container`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `supabase, redis, litellm`
- Optional dependencies: `minio, iceberg-rest, spark, redpanda`
- Runtime calls: `litellm, hermes, weaviate, neo4j, supabase, ray, spark, redpanda, comfyui, n8n, backend, searxng, minio, iceberg-rest, mlflow, label-studio`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| JUPYTERHUB_SOURCE | container | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `litellm, hermes, weaviate, neo4j, supabase, ray, spark, redpanda, comfyui, n8n, backend, searxng, minio, iceberg-rest, mlflow, label-studio`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/jupyterhub/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/jupyterhub/architecture.svg)
- Diagram HTML: [`services/jupyterhub/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/jupyterhub/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/jupyterhub/README.md](https://github.com/thekaveh/atlas/blob/main/services/jupyterhub/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
