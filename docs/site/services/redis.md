# Redis (cache & queue)

## 1. Overview

`redis` is an Atlas service family in the `data` category. Its implementation and service-owned documentation live under `services/redis/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `data`
- Kind: `container`
- Tracks: `all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading`

## 4. Access

- Kong aliases: `-`
- Port variables: `REDIS_PORT, REDIS_EXPORTER_PORT`

## 5. Configuration

- SOURCE variables: `REDIS_SOURCE`
- Default SOURCE values: `container`
- Available SOURCE values: `container`

## 6. Dependencies And Topology

- Required dependencies: `supabase`
- Optional dependencies: `-`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| REDIS_SOURCE | container | container |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/redis/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/redis/architecture.svg)
- Diagram HTML: [`services/redis/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/redis/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/redis/README.md](https://github.com/thekaveh/atlas/blob/main/services/redis/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
