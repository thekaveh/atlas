# Prometheus (metrics scraper + TSDB)

## 1. Overview

`prometheus` is an Atlas service family in the `infra` category. Its implementation and service-owned documentation live under `services/prometheus/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `infra`
- Kind: `container`
- Tracks: `all`

## 4. Access

- Kong aliases: `prometheus.localhost`
- Port variables: `PROMETHEUS_PORT, NODE_EXPORTER_PORT, CADVISOR_PORT`

## 5. Configuration

- SOURCE variable: `PROMETHEUS_SOURCE`
- Default SOURCE: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `supabase, redis, kong, ray`
- Optional dependencies: `-`
- Runtime calls: `kong, litellm, backend, n8n, weaviate, minio, supabase, redis, grafana`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| PROMETHEUS_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `kong, litellm, backend, n8n, weaviate, minio, supabase, redis, grafana`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/prometheus/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/prometheus/architecture.svg)
- Diagram HTML: [`services/prometheus/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/prometheus/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/prometheus/README.md](https://github.com/thekaveh/atlas/blob/main/services/prometheus/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
