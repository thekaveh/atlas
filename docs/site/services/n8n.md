# n8n (workflow automation)

## 1. Overview

`n8n` is an Atlas service family in the `agents` category. Its implementation and service-owned documentation live under `services/n8n/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `agents`
- Kind: `container`
- Tracks: `all, gen-ai-eng`

## 4. Access

- Kong aliases: `n8n.localhost`
- Port variables: `N8N_PORT`

## 5. Configuration

- SOURCE variables: `N8N_SOURCE`
- Default SOURCE values: `container`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `supabase, redis, litellm`
- Optional dependencies: `supavisor`
- Runtime calls: `supabase, redis, weaviate, backend, doc-processor, tika, hermes, litellm, stt-provider, tts-provider, searxng, lightrag, crawl4ai, supavisor`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| N8N_SOURCE | container | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `supabase, redis, weaviate, backend, doc-processor, tika, hermes, litellm, stt-provider, tts-provider, searxng, lightrag, crawl4ai, supavisor`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/n8n/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/n8n/architecture.svg)
- Diagram HTML: [`services/n8n/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/n8n/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/n8n/README.md](https://github.com/thekaveh/atlas/blob/main/services/n8n/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
