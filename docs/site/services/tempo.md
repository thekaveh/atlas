# Tempo (distributed trace store)

## 1. Overview

`tempo` is an Atlas service family in the `infra` category. Its implementation and service-owned documentation live under `services/tempo/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `infra`
- Kind: `container`
- Tracks: `all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng`

## 4. Access

- Kong aliases: `-`
- Port variables: `-`

## 5. Configuration

- SOURCE variable: `TEMPO_SOURCE`
- Default SOURCE: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `kong, ray`
- Optional dependencies: `-`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| TEMPO_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/tempo/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/tempo/architecture.svg)
- Diagram HTML: [`services/tempo/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/tempo/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/tempo/README.md](https://github.com/thekaveh/atlas/blob/main/services/tempo/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
