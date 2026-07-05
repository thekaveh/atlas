# Open WebUI (chat interface)

## 1. Overview

`open-webui` is an Atlas service family in the `apps` category. Its implementation and service-owned documentation live under `services/open-webui/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `apps`
- Kind: `container`
- Tracks: `all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng`

## 4. Access

- Kong aliases: `chat.localhost`
- Port variables: `OPEN_WEB_UI_PORT`

## 5. Configuration

- SOURCE variables: `OPEN_WEB_UI_SOURCE`
- Default SOURCE values: `container`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `supabase, redis, litellm`
- Optional dependencies: `hermes`
- Runtime calls: `litellm, supabase, redis, backend, comfyui, stt-provider, tts-provider, local-deep-researcher`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| OPEN_WEB_UI_SOURCE | container | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `litellm, supabase, redis, backend, comfyui, stt-provider, tts-provider, local-deep-researcher`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/open-webui/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/open-webui/architecture.svg)
- Diagram HTML: [`services/open-webui/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/open-webui/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/open-webui/README.md](https://github.com/thekaveh/atlas/blob/main/services/open-webui/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
