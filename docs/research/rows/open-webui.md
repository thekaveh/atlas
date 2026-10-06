---
service: open-webui
category: apps
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - https://github.com/open-webui/open-webui
  - https://docs.openwebui.com/features/
  - https://github.com/open-webui/pipelines
  - https://github.com/open-webui/mcpo
  - services/open-webui/service.yml
  - services/open-webui/compose.yml
  - services/open-webui/extras/tools/memory_tool.py
  - services/open-webui/README.md
---

# open-webui — Integration Research

## 1. Missing-pair integrations

- **open-webui ↔ searxng**
  - Why valuable: Open WebUI natively consumes SearXNG as a first-class web-search provider for in-chat grounding; the stack already runs SearXNG but only wires it to local-deep-researcher, so chat users have no web search.
  - Mechanism sketch: set `ENABLE_WEB_SEARCH=true`, `WEB_SEARCH_ENGINE=searxng`, `SEARXNG_QUERY_URL=http://searxng:8080/search?q=<query>&format=json` (the same internal endpoint local-deep-researcher uses).
  - Effort: small
  - Risks / open questions: SearXNG JSON output must be enabled in `searxng/settings.yml`; rate-limiting under bursty chat traffic.
  - Confidence: high (Open WebUI docs list SearXNG among 15+ built-in providers — https://github.com/open-webui/open-webui).

- **open-webui ↔ jupyterhub**
  - Why valuable: Open WebUI ships a Jupyter-backed code-execution engine that runs LLM-emitted Python in a real kernel with persistent state; the stack already runs JupyterHub but the WebUI falls back to the in-browser Pyodide sandbox.
  - Mechanism sketch: set `CODE_EXECUTION_ENGINE=jupyter`, `CODE_EXECUTION_JUPYTER_URL=http://jupyterhub:8888`, `CODE_EXECUTION_JUPYTER_AUTH=token`, `CODE_EXECUTION_JUPYTER_AUTH_TOKEN=${JUPYTERHUB_API_TOKEN}` (mirror for `CODE_INTERPRETER_*`).
  - Effort: medium
  - Risks / open questions: JupyterHub spawns per-user servers — Open WebUI needs a service-account token or a single-user fallback; kernel lifecycle and quota management.
  - Confidence: high (Jupyter engine documented in Open WebUI env reference — https://docs.openwebui.com/).

- **open-webui ↔ minio**
  - Why valuable: Chat file uploads, generated images, and TTS audio currently live in the `open-web-ui-data` Docker volume — losing them on `--cold`. Open WebUI supports S3 storage natively, and MinIO is already in the stack.
  - Mechanism sketch: `STORAGE_PROVIDER=s3`, `S3_ENDPOINT_URL=http://minio:9000`, `S3_BUCKET_NAME=openwebui`, `S3_ACCESS_KEY_ID=${MINIO_ROOT_USER}`, `S3_SECRET_ACCESS_KEY=${MINIO_ROOT_PASSWORD}`; add a `mc mb` step to the MinIO init.
  - Effort: small
  - Risks / open questions: bucket lifecycle/quotas; signed-URL behaviour behind Kong; path-style addressing for MinIO.
  - Confidence: high (S3 storage provider is a documented Open WebUI feature).

- **open-webui ↔ n8n**
  - Why valuable: n8n workflows could be exposed to chat as Open WebUI Tools via the OpenAPI tool-server integration, letting users trigger automations ("email this summary", "create a Jira ticket") directly from chat without writing a Python tool.
  - Mechanism sketch: n8n webhook node publishes an OpenAPI spec at `http://n8n:5678/webhook/openapi.json`; register as an OpenAPI tool server in Open WebUI admin settings (or via the `register-tools.py` init script).
  - Effort: medium
  - Risks / open questions: n8n does not auto-emit OpenAPI specs — a workflow-to-schema generator or a thin wrapper is required; auth header propagation.
  - Confidence: medium (OpenAPI tool servers are documented; n8n side needs glue).

- **open-webui ↔ neo4j**
  - Why valuable: The existing `memory_tool.py` stores user memories via the backend in Postgres — replacing/augmenting that with a Neo4j-backed graph would let memories link (entity → fact → source-conversation) and power richer recall queries.
  - Mechanism sketch: extend `extras/tools/memory_tool.py` to call a backend endpoint that writes to `bolt://neo4j-graph-db:7687`; Cypher queries for entity-linked recall.
  - Effort: medium
  - Risks / open questions: schema design; overlap with the simpler Postgres memory store; backend-side endpoint does not yet exist.
  - Confidence: medium (Neo4j is in-stack and the memory tool already exists as the integration surface).

## 2. Candidate new services

- **Open WebUI Pipelines** → `../candidates/open-webui-pipelines.md`
  - Headline: First-party plugin server for filters (rate-limit, toxicity, Langfuse tracing) and custom pipe providers.
  - Other consumers in stack: litellm (alternative provider routing), hermes (filter chain)

- **mcpo** → `../candidates/mcpo.md`
  - Headline: Open WebUI's MCP-to-OpenAPI proxy — exposes any stdio/SSE MCP server as a REST tool server consumable by Open WebUI and LiteLLM.
  - Other consumers in stack: hermes, litellm, n8n

- **Langfuse** → `../candidates/langfuse.md`
  - Headline: Self-hostable LLM observability (traces, evals, prompt management) plugged in via the Pipelines filter.
  - Other consumers in stack: litellm, hermes, local-deep-researcher

## 3. Per-service feature gaps

- **OIDC / SSO via Supabase Auth** — Why pursue: stack already runs Supabase (GoTrue); Open WebUI supports generic OAuth/OIDC and would unify identity across Open WebUI, n8n, JupyterHub. Effort: medium.
- **Native MCP client** — Why pursue: Open WebUI now ships a built-in MCP client (parallel to the mcpo proxy); wiring it to stack-local MCP servers (filesystem, git) gives chat real tool surfaces without per-tool Python. Effort: small.
- **Hybrid BM25 + vector reranking** — Why pursue: Weaviate is wired but Open WebUI's built-in hybrid search + cross-encoder reranker is not enabled; better recall on the knowledge base. Effort: small.
- **Channels / multi-user workspaces** — Why pursue: turn the WebUI into a team space with `@model` mentions; pairs naturally with the Supabase auth gap above. Effort: medium.
- **Notes with agentic access** — Why pursue: rich-text notes editor that LLMs can read/write, replacing ad-hoc scratchpads; zero new infra. Effort: small.
