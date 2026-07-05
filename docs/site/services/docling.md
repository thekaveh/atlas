# Docling (document processor)

## 1. Overview

`docling` is an Atlas service family in the `media` category. Its implementation and service-owned documentation live under `services/docling/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `media`
- Kind: `container`
- Tracks: `all`

## 4. Access

- Kong aliases: `docling.localhost`
- Port variables: `DOC_PROCESSOR_PORT, DOCLING_LOCALHOST_PORT`

## 5. Configuration

- SOURCE variables: `DOC_PROCESSOR_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `disabled, docling-localhost, docling-container-gpu`

## 6. Dependencies And Topology

- Required dependencies: `-`
- Optional dependencies: `-`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| DOC_PROCESSOR_SOURCE | disabled | disabled, docling-localhost, docling-container-gpu |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/docling/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/docling/architecture.svg)
- Diagram HTML: [`services/docling/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/docling/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/doc-processor/README.md](https://github.com/thekaveh/atlas/blob/main/services/doc-processor/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
