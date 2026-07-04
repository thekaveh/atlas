---
service: local-deep-researcher
category: apps
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - https://github.com/langchain-ai/local-deep-researcher
  - https://raw.githubusercontent.com/langchain-ai/local-deep-researcher/main/README.md
  - services/local-deep-researcher/service.yml
  - services/local-deep-researcher/compose.yml
  - services/local-deep-researcher/build/scripts/docker-entrypoint.sh
  - services/local-deep-researcher/build/scripts/init-config.py
  - services/backend/app/app/research_client.py
  - services/redis/service.yml
  - services/local-deep-researcher/README.md
  - https://github.com/firecrawl/firecrawl/blob/main/SELF_HOST.md
  - https://github.com/firecrawl/firecrawl/blob/main/LICENSE
  - https://docs.firecrawl.dev/use-cases/developers-mcp
---

# local-deep-researcher — Integration Research

## 1. Missing-pair integrations

- **local-deep-researcher ↔ backend**
  - Why valuable: `services/backend/app/app/research_client.py` already targets `http://local-deep-researcher:2024` and ships a `ResearchRequest`/`ResearchResult` Pydantic surface, but the LDR manifest does not declare backend as a consumer and there is no backend route exposing it. Wiring closes a half-implemented API.
  - Mechanism sketch: backend FastAPI route → LangGraph `POST /threads/{id}/runs` on `http://local-deep-researcher:2024`; persist `ResearchResult` rows in supabase `public.research_sessions`.
  - Effort: small
  - Risks / open questions: LangGraph dev-server endpoint shape (`/threads`, `/runs/stream`) is unstable across versions; pin `langgraph-cli`.
  - Confidence: high (client code already references the URL and schema)

- **local-deep-researcher ↔ redis**
  - Why valuable: LDR runs `langgraph dev` with the in-memory checkpointer, so thread state (running_summary, sources_gathered, loop_count) is lost on container restart. `services/redis/service.yml` already documents reserving db `/3` for LDR, but nothing consumes it.
  - Mechanism sketch: swap checkpointer to `langgraph.checkpoint.redis.RedisSaver` pointed at `redis://:${REDIS_PASSWORD}@redis:6379/3`; add `REDIS_URL` to LDR env.
  - Effort: small
  - Risks / open questions: `langgraph dev` does not accept a `--checkpointer` flag; needs `langgraph.json` or a thin wrapper. RedisSaver is a separate `langgraph-checkpoint-redis` package.
  - Confidence: medium

- **local-deep-researcher ↔ neo4j**
  - Why valuable: each research run yields `sources_gathered` (URL + snippet) and a `running_summary`. Writing these as `(Topic)-[CITES]->(Source)` triples lets later runs (or n8n flows) detect overlap and reuse evidence across sessions.
  - Mechanism sketch: post-`finalize_summary` callback writes Cypher `MERGE` via `bolt://neo4j-graph-db:7687` using `NEO4J_USER`/`NEO4J_PASSWORD`.
  - Effort: medium
  - Risks / open questions: LangGraph node injection requires editing the cloned upstream src; alternative is a backend-side ingester reading from the LangGraph state endpoint.
  - Confidence: medium

- **local-deep-researcher ↔ minio**
  - Why valuable: the final markdown report and any `fetch_full_page` HTML dumps live only in `/app/data` inside the LDR container; no other service can consume them. A bucket like `research-reports/` makes outputs visible to Open WebUI uploads, JupyterHub notebooks, and n8n workflows.
  - Mechanism sketch: on `finalize_summary`, S3 `PutObject` to `${MINIO_ENDPOINT}` bucket `research-reports` keyed by `session_id`.
  - Effort: small
  - Risks / open questions: bucket creation belongs in `services/minio/init/` to keep ownership boundaries clean.
  - Confidence: medium

- **local-deep-researcher ↔ hermes**
  - Why valuable: Hermes is the agent runtime but currently has no path to invoke multi-step web research; today it would have to re-derive the search loop. Exposing LDR as a Hermes tool turns "deep research" into a single tool call.
  - Mechanism sketch: Hermes custom tool POSTs to `http://local-deep-researcher:2024/threads`/`runs/stream` and returns the final summary; configured in `services/hermes/init/templates/config.yaml.tmpl`.
  - Effort: medium
  - Risks / open questions: Hermes tool definitions are stable per `reference_hermes_provider_config.md`; streaming LangGraph SSE through Hermes needs verification.
  - Confidence: medium

- **local-deep-researcher ↔ jupyterhub**
  - Why valuable: notebooks today have no programmatic way to trigger or consume research runs. A thin Python helper plus access to the persisted reports on MinIO would let DS notebooks evaluate, fine-tune, or grade LDR outputs.
  - Mechanism sketch: JupyterHub container env `LOCAL_DEEP_RESEARCHER_URL=http://local-deep-researcher:2024`; notebook calls `/threads/{id}/runs`.
  - Effort: small
  - Risks / open questions: blocked behind the LDR/backend pair landing first (notebook would prefer the backend wrapper).
  - Confidence: low

## 2. Candidate new services

- **Langfuse** → `../candidates/langfuse.md`
  - Headline: Self-hosted LLM observability and prompt-trace store with a first-class LangChain/LangGraph callback.
  - Other consumers in stack: litellm, hermes, n8n, comfyui

- **Firecrawl** → `../candidates/firecrawl.md`
  - Headline: Deferred AGPL web-context API for search, scrape, crawl, browser interaction, agent workflows, and MCP access when Crawl4AI leaves a proven gap.
  - Other consumers in stack: n8n, backend, hermes

- **Crawl4AI** → `../candidates/crawl4ai.md`
  - Headline: LLM-native open-source crawler with built-in chunking and markdown extraction, deployable as a sidecar to LDR.
  - Other consumers in stack: n8n, weaviate, backend

## 3. Per-service feature gaps

- **Persistent LangGraph checkpointer** — Why pursue: dev-server inmem checkpointer drops thread history on restart, so resumable research is impossible. Effort: small.
- **Tavily / Perplexity search backends** — Why pursue: upstream supports both via `SEARCH_API=tavily|perplexity` + API keys; manifest only exposes searxng/duckduckgo. Effort: small.
- **`USE_TOOL_CALLING` for gpt-oss models** — Why pursue: enables structured tool calls instead of JSON mode for gpt-oss family, improving reliability with LiteLLM-routed local models. Effort: small.
- **`STRIP_THINKING_TOKENS` toggle** — Why pursue: hermes-style reasoning models leak `<think>` blocks into the report; upstream env var hides them. Effort: small.
- **`FETCH_FULL_PAGE` toggle** — Why pursue: hard-coded false in init-config.py; not exposed in `service.yml`. Effort: small.
- **LangSmith tracing** — Why pursue: `LANGSMITH_API_KEY` ships upstream; superseded if Langfuse lands but useful as a stopgap. Effort: small.
- **LangGraph Studio UI via Kong** — Why pursue: `/threads`/`/runs/stream` UI is reachable on port 2024 but has no Kong alias (e.g. `research.localhost`), forcing direct-port access. Effort: small.
