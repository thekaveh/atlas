# Hermes (programmable AI agent)

## 1. Overview

`hermes` is an Atlas service family in the `agents` category. Its implementation and service-owned documentation live under `services/hermes/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `agents`
- Kind: `container`
- Tracks: `all, gen-ai-eng`

## 4. Access

- Kong aliases: `hermes.localhost`
- Port variables: `HERMES_API_PORT, HERMES_DASHBOARD_PORT, HERMES_LOCALHOST_PORT, HERMES_LOCALHOST_DASHBOARD_PORT`

## 5. Configuration

- SOURCE variable: `HERMES_SOURCE`
- Default SOURCE: `container`
- Available SOURCE values: `container, localhost, disabled`

## 6. Dependencies And Topology

- Required dependencies: `litellm`
- Optional dependencies: `-`
- Runtime calls: `litellm, stt-provider, tts-provider, comfyui, searxng, airflow, lightrag`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| HERMES_SOURCE | container | container, localhost, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `litellm, stt-provider, tts-provider, comfyui, searxng, airflow, lightrag`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/hermes/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/hermes/architecture.svg)
- Diagram HTML: [`services/hermes/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/hermes/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/hermes/README.md](https://github.com/thekaveh/atlas/blob/main/services/hermes/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
