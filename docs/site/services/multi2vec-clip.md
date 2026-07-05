# multi2vec-clip

## 1. Overview

`multi2vec-clip` is an Atlas service family in the `aggregate` category. Its implementation and service-owned documentation live under `services/multi2vec-clip/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `aggregate`
- Kind: `doc-only`
- Tracks: `all, gen-ai-creative`

## 4. Access

- Kong aliases: `-`
- Port variables: `-`

## 5. Configuration

- SOURCE variable: `none`
- Default SOURCE: `none`
- Available SOURCE values: `-`

## 6. Dependencies And Topology

- Required dependencies: `-`
- Optional dependencies: `-`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| none | none | - |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/multi2vec-clip/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/multi2vec-clip/architecture.svg)
- Diagram HTML: [`services/multi2vec-clip/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/multi2vec-clip/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/multi2vec-clip/README.md](https://github.com/thekaveh/atlas/blob/main/services/multi2vec-clip/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
