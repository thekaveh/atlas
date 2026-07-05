# Celery + Flower (async jobs)

## 1. Overview

`celery` is an Atlas service family in the `agents` category. Its implementation and service-owned documentation live under `services/celery/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `agents`
- Kind: `container`
- Tracks: `all, gen-ai-eng, gen-ai-rag`

## 4. Access

- Kong aliases: `flower.localhost`
- Port variables: `FLOWER_PORT`

## 5. Configuration

- SOURCE variables: `CELERY_SOURCE`
- Default SOURCE values: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `redis, backend, supabase, litellm`
- Optional dependencies: `weaviate, supavisor`
- Runtime calls: `redis, supabase, litellm, weaviate, supavisor`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| CELERY_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `redis, supabase, litellm, weaviate, supavisor`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/celery/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/celery/architecture.svg)
- Diagram HTML: [`services/celery/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/celery/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/celery/README.md](https://github.com/thekaveh/atlas/blob/main/services/celery/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
