# Blender MCP

## 1. Overview

`blender-mcp` is an Atlas service family in the `media` category. Its implementation and service-owned documentation live under `services/blender-mcp/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `media`
- Kind: `virtual`
- Tracks: `all, gen-ai-creative`

## 4. Access

- Kong aliases: `-`
- Port variables: `BLENDER_MCP_LOCALHOST_PORT`

## 5. Configuration

- SOURCE variable: `BLENDER_MCP_SOURCE`
- Default SOURCE: `disabled`
- Available SOURCE values: `localhost, disabled`

## 6. Dependencies And Topology

- Required dependencies: `-`
- Optional dependencies: `-`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| BLENDER_MCP_SOURCE | disabled | localhost, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/blender-mcp/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/blender-mcp/architecture.svg)
- Diagram HTML: [`services/blender-mcp/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/blender-mcp/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/blender-mcp/README.md](https://github.com/thekaveh/atlas/blob/main/services/blender-mcp/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
