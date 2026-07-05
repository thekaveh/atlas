# Curated MCP Servers

## 1. Overview

`mcp-servers` is an Atlas service family in the `agents` category. Its implementation and service-owned documentation live under `services/mcp-servers/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `agents`
- Kind: `container`
- Tracks: `all, gen-ai-eng, gen-ai-rag`

## 4. Access

- Kong aliases: `mcp.localhost`
- Port variables: `MCP_SERVERS_PORT`

## 5. Configuration

- SOURCE variable: `MCP_SERVERS_SOURCE`
- Default SOURCE: `disabled`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `supabase, neo4j, searxng`
- Optional dependencies: `-`
- Runtime calls: `supabase, neo4j, searxng`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| MCP_SERVERS_SOURCE | disabled | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `supabase, neo4j, searxng`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/mcp-servers/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/mcp-servers/architecture.svg)
- Diagram HTML: [`services/mcp-servers/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/mcp-servers/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/mcp-servers/README.md](https://github.com/thekaveh/atlas/blob/main/services/mcp-servers/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
