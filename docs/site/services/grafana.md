# Grafana (observability UI + alerting)

## 1. Overview

`grafana` is an Atlas service family in the `infra` category. Its implementation and service-owned documentation live under `services/grafana/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `infra`
- Kind: `container`
- Tracks: `all`

## 4. Access

- Kong aliases: `grafana.localhost`
- Port variables: `GRAFANA_PORT`

## 5. Configuration

- SOURCE variables: `GRAFANA_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `prometheus, supabase, kong, ray`
- Optional dependencies: `-`
- Runtime calls: `prometheus, tempo, loki`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| GRAFANA_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `prometheus, tempo, loki`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/grafana/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/grafana/architecture.svg)
- Diagram HTML: [`services/grafana/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/grafana/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/grafana/README.md](https://github.com/thekaveh/atlas/blob/main/services/grafana/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
