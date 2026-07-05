# Kong (API gateway)

## 1. Overview

`kong` is an Atlas service family in the `infra` category. Its implementation and service-owned documentation live under `services/kong/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `infra`
- Kind: `container`
- Tracks: `all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading`

## 4. Access

- Kong aliases: `-`
- Port variables: `KONG_HTTP_PORT, KONG_HTTPS_PORT`

## 5. Configuration

- SOURCE variables: `KONG_API_GATEWAY_SOURCE`
- Default SOURCE values: `container`
- Available SOURCE values: `container`

## 6. Dependencies And Topology

- Required dependencies: `supabase, redis`
- Optional dependencies: `-`
- Runtime calls: `backend, open-webui, jupyterhub, n8n, hermes, openclaw, local-deep-researcher, minio, supabase, weaviate, neo4j, comfyui, searxng, stt-provider, tts-provider, doc-processor, litellm, ollama, airflow, spark, zeppelin, lightrag, tei-reranker, verba, grafana, prometheus, ray`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| KONG_API_GATEWAY_SOURCE | container | container |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `backend, open-webui, jupyterhub, n8n, hermes, openclaw, local-deep-researcher, minio, supabase, weaviate, neo4j, comfyui, searxng, stt-provider, tts-provider, doc-processor, litellm, ollama, airflow, spark, zeppelin, lightrag, tei-reranker, verba, grafana, prometheus, ray`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/kong/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/kong/architecture.svg)
- Diagram HTML: [`services/kong/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/kong/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/kong/README.md](https://github.com/thekaveh/atlas/blob/main/services/kong/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
