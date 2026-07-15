---
category-fit: agents
generated: 2026-05-19
license: MIT
name: Docling MCP Server
referenced-by: [doc-processor]
slug: docling-mcp
type: external-service
upstream: https://github.com/docling-project/docling-mcp
---

# Docling MCP Server

## 1. Headline
A first-party Model Context Protocol server that exposes Docling's convert, extract, and document-generation tools to MCP-capable agent runtimes.

## 2. Problem it solves
Hermes and (future) openclaw agents have no native way to "read this PDF". Today they would have to learn the Docling REST surface and handle multipart uploads themselves. Docling MCP wraps the same engine behind the standard MCP tool protocol so any MCP-aware runtime gets document understanding "for free" with no bespoke HTTP plumbing.

## 3. Stack wiring sketch
- hermes → docling-mcp via streamable-HTTP MCP transport (registered as a custom provider tool)
- docling-mcp → doc-processor (docling) via remote mode — delegates heavy work to the existing `http://docling-gpu:8000` engine instead of re-loading models
- openclaw → docling-mcp (future) — same MCP transport, allows messaging-platform agents to summarize attachments
- backend → docling-mcp (optional) — if backend grows an MCP client, can replace its current direct-HTTP call

(Every bullet names a real service in the current topology.)

## 4. Effort
medium — packaging is straightforward (upstream ships a container image and supports streamable-HTTP transport), but Hermes provider wiring + Kong alias routing + auth between MCP server and docling-serve need design.

## 5. Risks & open questions
- MCP transport choice: SSE vs streamable-HTTP — streamable-HTTP is friendlier behind Kong but newer.
- Auth surface: docling-mcp inherits whatever auth docling-serve has (currently none); fronting via Kong with a shared header is the likely answer.
- Tool naming collisions: if multiple MCP servers expose `convert_document`, agents need namespacing.
- Resource overhead: running both docling-serve and docling-mcp doubles the container count; remote-mode mitigates by avoiding model reload.

## 6. Why now (and why not sooner)
Docling MCP only reached v2.0 with hybrid local/remote mode and streamable-HTTP transport in 2026; earlier versions required co-locating models with the MCP process, which would have conflicted with our GPU container layout. The new remote mode aligns naturally with our existing `docling-gpu` engine.

## 7. Upstream evidence
- https://github.com/docling-project/docling-mcp — README documents tools, transports (stdio/SSE/streamable-HTTP), and remote-mode wiring to docling-serve.
- https://github.com/DS4SD/docling — confirms Docling itself ships an MCP server as an official integration point.
