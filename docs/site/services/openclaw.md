# OpenClaw (AI agent gateway)

## 1. Overview

`openclaw` is an Atlas service family in the `agents` category. Its implementation and service-owned documentation live under `services/openclaw/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `agents`
- Kind: `container`
- Tracks: `all, gen-ai-eng`

## 4. Access

- Kong aliases: `openclaw.localhost`
- Port variables: `OPENCLAW_GATEWAY_PORT, OPENCLAW_BRIDGE_PORT, OPENCLAW_LOCALHOST_PORT`

## 5. Configuration

- SOURCE variables: `OPENCLAW_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `disabled, container, localhost`

## 6. Dependencies And Topology

- Required dependencies: `litellm`
- Optional dependencies: `-`
- Runtime calls: `litellm`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| OPENCLAW_SOURCE | disabled | disabled, container, localhost |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `litellm`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/openclaw/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/openclaw/architecture.svg)
- Diagram HTML: [`services/openclaw/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/openclaw/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/openclaw/README.md](https://github.com/thekaveh/atlas/blob/main/services/openclaw/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
