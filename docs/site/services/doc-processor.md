# doc-processor

## 1. Overview

`doc-processor` is an Atlas service family in the `aggregate` category. Its implementation and service-owned documentation live under `services/doc-processor/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `aggregate`
- Kind: `doc-only`
- Tracks: `all, gen-ai-creative, gen-ai-rag`

## 4. Access

- Kong aliases: `-`
- Port variables: `-`

## 5. Configuration

- SOURCE variables: `-`
- Default SOURCE values: `-`
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

- Diagram SVG: [`services/doc-processor/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/doc-processor/architecture.svg)
- Diagram HTML: [`services/doc-processor/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/doc-processor/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/doc-processor/README.md](https://github.com/thekaveh/atlas/blob/main/services/doc-processor/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
