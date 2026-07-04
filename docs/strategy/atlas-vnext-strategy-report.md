# Atlas vNext Strategy Report

## 1. Executive Summary

Atlas' strategic position is strongest when it is framed as a self-hosted AI engineering platform, not as another chat UI, model runtime, RAG framework, or one-click app launcher. Its durable advantage is the integrated substrate: SOURCE-configurable services, Kong routing, tracks, manifests, generated docs, notebooks, automation, vector and graph stores, object storage, and observability primitives in one repeatable local stack. Its main weakness is that the product surface has not caught up with the platform architecture.

The top recommendation is to make Atlas easier to enter, observe, and extend before adding heavier vertical engines. The first vNext wave should build the missing product entrypoint and shared tooling layer, then improve ingestion, traces, and long-running job execution.

Top 5 vNext candidates:

1. **Atlas root dashboard:** replace the Kong root's Supabase Studio fallback with an Atlas service directory and health dashboard.
2. **Curated MCP package:** ship a small Postgres, Neo4j, and SearXNG tool package first, then add Docling MCP as the first specialist tool.
3. **Langfuse:** add LLM traces, prompt/eval visibility, and cost/latency attribution beginning with LiteLLM-routed calls.
4. **Crawl4AI:** improve RAG and research quality with full-page web extraction before adding heavier browser infrastructure.
5. **Celery + Flower worker tier:** move one long-running backend flow out of the FastAPI request loop and establish the async-job pattern.

Decision verdicts:

- **MCP:** build a phased hybrid, not one MCP server per service. Start curated and direct-to-native consumers; add MetaMCP later when namespacing and policy justify it.
- **Kong dashboard:** build now. The root route should become the Atlas entrypoint, while Supabase Studio moves to its own explicit route.
- **3D/game generation:** pursue a 3D asset pipeline later, centered on glTF processing and a disabled localhost-only Blender MCP profile, not a default "generate a whole game" promise.
- **Trading/financial AI:** pursue financial research and paper trading later, with read-only data, sandbox modes, secrets management, audit logs, and no live trading by default.

## 2. Current-State Assessment

Current-state evidence inventory:

- Repo inventory snapshot for this report pass: `34` `services/*/service.yml` manifests, `21` integration research row files in `docs/research/rows/`, and `34` candidate one-pagers in `docs/research/candidates/`.
- `docs/research/integration-matrix.md` currently indexes `21` service research rows across categories and `34` candidate services, which gives later sections a repo-local starting point before any external validation.
- Current track registry in `bootstrapper/tracks.yml` defines seven track keys: `gen-ai-rag`, `gen-ai-eng`, `gen-ai-creative`, `ml-eng`, `data-eng`, `trading`, and `all`.
- Scoped track membership snapshot:
  - `gen-ai-rag`: `open-webui`, `weaviate`, `neo4j`, `lightrag`, `doc-processor`, `tei-reranker`, `searxng`, `local-deep-researcher`
  - `gen-ai-eng`: `open-webui`, `n8n`, `hermes`, `openclaw`, `jupyterhub`, `comfyui`, `stt-provider`, `tts-provider`, `searxng`, `local-deep-researcher`
  - `gen-ai-creative`: `open-webui`, `comfyui`, `stt-provider`, `tts-provider`, `multi2vec-clip`, `doc-processor`
  - `ml-eng`: `spark`, `ray`, `jupyterhub`, `zeppelin`, `open-webui`, `minio`, `tei-reranker`
  - `data-eng`: `spark`, `airflow`, `jupyterhub`, `zeppelin`, `minio`, `iceberg-rest`, `trino`, `redpanda`, `weaviate`, `neo4j`
  - `trading`: `jupyterhub`, `minio`, `mlflow`, `langfuse`
  - `all`: no filtering; every configurable service remains available
- Track-context note for later analysis: the registry already expresses broad AI, RAG, creative, ML, and data personas, but only as service-selection profiles; later tasks should assess whether those profiles feel productized enough for onboarding and roadmap positioning.

### 2.1 Strengths

Atlas already has a coherent platform grammar. The repo does not read like an accreted pile of one-off compose services; it reads like a system that standardized its operating model and then kept extending it. That matters because Atlas' main strategic asset is not any one service, but the repeatable way it wires heterogeneous services into one local platform.

- **Source-configurable service model with consistent deployment semantics.** The README's overview and SOURCE-system sections describe a shared contract across the stack: services are selected through SOURCE variables, the common modes are `container`, `localhost`, and `disabled`, and the LLM layer adds a cloud passthrough path through LiteLLM. `docs/CONTRIBUTING-services.md` reinforces the same contract for maintainers by standardizing source variants, localhost port overrides, runtime slices, and per-service manifests. The manifest inventory shows that this is not just documentation language: most configurable services expose the same operational choices, which gives Atlas a broad range of deployment modes inside its current compose-based operating model.

- **Kong-centered routing with predictable `*.localhost` aliases.** Atlas has a clear access model rather than a random port jungle. `docs/deployment/ports-and-routes.md` defines the authoritative route table, and the README mirrors it with both a generated topology table and a browser-facing service overview. Kong fronts chat, notebooks, workflow tools, storage consoles, model gateways, graph and vector stores, and optional observability surfaces behind stable aliases such as `chat.localhost`, `api.localhost`, `litellm.localhost`, and `grafana.localhost`. The Kong service docs also show that route generation is tied to active SOURCE values, so the gateway adapts as services move between container, localhost, and disabled states.

- **Mature manifest, compose-fragment, and docs-generation discipline.** Atlas' internal architecture is explicitly standardized around per-service contracts and generated artifacts. `docs/CONTRIBUTING-services.md` documents the per-service folder contract (`service.yml`, `compose.yml`, local subdirectories), the top-level compose file is intentionally thin, and the contribution workflow requires a regen-and-lint chain before landing changes. The repo guidance also points to concrete safety rails: manifest validation, compose equivalence checks, docs drift tests, Kong route audits, and source-dependency audits. That investment lowers the cost of continuing to expand the platform.

- **Tracks turn breadth into persona-oriented presets.** `bootstrapper/tracks.yml` defines seven track keys covering RAG, agent engineering, creative multimodal work, ML engineering, data engineering, read-only financial research, and a full-custom mode. The README exposes those tracks directly in quickstart commands and in the wizard flow. This is strategically important: Atlas is broad, but it is not forcing every user to boot every domain at once. The track system is an early productization layer over a large service catalog.

- **Strong self-host primitives across the core workload categories.** The shipped stack already covers the hard infrastructure a serious local AI platform needs: Supabase, Redis, LiteLLM, Kong, MinIO, Weaviate, Neo4j, n8n, Open WebUI, JupyterHub, Ray, Spark, Airflow, Prometheus, and Grafana are all represented in the repo's docs, manifests, and service READMEs. `docs/ROADMAP.md` also distinguishes between what is shipped and what remains candidate work, which makes the current-state boundary legible. The result is that Atlas already spans chat, automation, retrieval, storage, notebooks, distributed compute, and observability without depending on a single hosted control plane.

- **Documentation and drift discipline are treated as part of the product surface.** The README is not the only source of truth; it is backed by generated topology, per-service READMEs, route docs, manifest docs, and repo-level audits. `docs/CONTRIBUTING-services.md` explicitly treats READMEs, diagrams, manifests, and compose fragments as a maintained system, while the roadmap records what has shipped versus what is still only proposed. That documentation posture is a real strength because Atlas' surface area is already large enough that undocumented behavior would quickly become unmanageable.

### 2.2 Weaknesses

- **The root Kong entrypoint is still infrastructure-first rather than product-first.** `services/kong/README.md` and `docs/deployment/ports-and-routes.md` both state that `/` and the bare `localhost` root fall through to Supabase Studio. That is a valid operator shortcut, but it is not an Atlas home experience. The first gateway surface currently points users at an admin console for one subsystem, not at a platform dashboard, workspace selector, system health page, or recommended workflow launcher. For a stack trying to become a cohesive product, the front door is still effectively a routing convenience.

- **Authentication and SSO are fragmented across services.** The README's service overview table shows the problem plainly: Open WebUI uses account creation, n8n uses first-visit owner setup, Supabase Studio is gated by Kong basic auth, Neo4j uses its own credentials, Hermes uses a bearer token, JupyterHub uses an optional token, Grafana uses an admin password, and the backend is "None by default" on local/dev surfaces. The repo already knows this is unfinished: `services/open-webui/README.md` lists OIDC/SSO via Supabase Auth as future work, `services/kong/README.md` lists Keycloak and JWT-plugin options, and `services/supabase/README.md` notes OAuth providers as an easy next step. The building blocks exist, but the platform does not yet behave like it has one identity model.

- **MCP is well-theorized in the roadmap but not yet implemented as a shared runtime capability.** `docs/ROADMAP.md` contains a serious MCP architecture discussion, including aggregator options, a phased starter set, and a coverage matrix of likely targets and consumers. `services/open-webui/README.md` also calls out Open WebUI's native MCP client as future work. But that design has not crossed into the shipped stack: there is no MCP gateway manifest, no shared MCP route in the ports-and-routes table, and no current-state docs describing stack-wide MCP availability. Strategically, Atlas understands the opportunity but has not yet converted it into platform leverage.

- **LLM observability, trace correlation, and evaluation loops lag behind the stack's service breadth.** Atlas has solid infrastructure observability today: the README and roadmap document a shipped Prometheus + Grafana bundle with 13 scrape jobs and 7 starter dashboards. But the same repo evidence is explicit that this is not the whole story. `docs/ROADMAP.md` positions Langfuse as the missing LLM-specific layer for traces, prompts, evals, and cost attribution, and both the roadmap and the Prometheus/Grafana READMEs mark Loki, Tempo, and OpenTelemetry as future work. In other words, Atlas can observe containers and system metrics better than it can observe cross-service LLM behavior.

- **The stack's breadth still risks onboarding overload without a dashboard and stronger guided paths.** The repo inventory for this report pass includes 35 service manifests spread across six categories, while the route docs enumerate a long list of hostnames, direct ports, and auth modes. The wizard and tracks help, and that is a real mitigation, but the user still needs to understand a substantial amount of infrastructure vocabulary to orient themselves. The current docs are good; the experience is still cognitively dense.

- **Vertical-track ambition is only partly productized.** The roadmap frames 3D/game-generation, financial/trading-AI, and RAG-enhancement as strategic tracks, and `bootstrapper/tracks.yml` now exposes a guarded `trading` track for read-only notebook research. The larger gap remains: heavier vertical engines still need onboarding-ready product modes, clearer safety boundaries, and service integrations before they become default experiences.

## 3. Competitor Landscape

External competitor research, checked on July 2, 2026, suggests Atlas does not have one exact peer. It sits in the overlap between local AI workbenches, self-hosted app builders, RAG frameworks, coding-agent control planes, and broader AI/data platform stacks. That breadth is an asset, but it also means Atlas loses whenever the user only wants one narrow job done with minimal setup.

### 3.1 Competitor Matrix

| Category | Examples | What they do better | What Atlas does better | Strategic implication |
|---|---|---|---|---|
| Local AI workbenches | [Open WebUI](https://docs.openwebui.com/), [AnythingLLM](https://docs.useanything.com/), [Jan](https://github.com/janhq/jan), [LM Studio](https://lmstudio.ai/docs/developer/core/server) | They deliver a tighter chat-first experience, clearer model UX, and faster first-run success. Open WebUI already ships desktop plus Docker/Python/Kubernetes paths, while Jan and LM Studio expose local OpenAI-compatible APIs from desktop apps. | Atlas is broader: it can bring the chat UI together with notebooks, workflow automation, vector/graph stores, observability, and multiple tracks in one system. | Atlas should not try to beat these on pure chat polish first. It should package a sharper "Atlas Workbench" starting path on top of its wider platform. |
| Model runtime and local inference layer | [Ollama](https://docs.ollama.com/api/introduction), [LM Studio](https://lmstudio.ai/docs/developer/core/server), [Jan](https://github.com/janhq/jan) | They make local model serving feel simple and productized: one install, one local API, one clear model catalog, and compatibility with OpenAI-style clients. | Atlas can orchestrate runtimes together with gateways, upstream services, and hybrid localhost/container deployment choices. | Atlas should treat runtime UX as a dependency it packages well, not as the core battleground. Lean into best-of-breed runtime integration. |
| App-store / one-click local launcher | [Pinokio](https://desktop.pinokio.co/), [Open WebUI Desktop](https://docs.openwebui.com/) | Pinokio's strongest move is discoverability and one-click install/launch for local AI apps, with a searchable directory and side-by-side version management. | Atlas is much stronger once the user wants a repeatable multi-service environment rather than one isolated app. | Atlas needs a curated catalog mindset: track bundles, recommended stacks, and install recipes that feel closer to a product shelf than a manifest inventory. |
| Agentic app builders and workflow studios | [Dify](https://docs.dify.ai/en/learn/key-concepts), [Flowise](https://docs.flowiseai.com/), [Langflow](https://docs.langflow.org/) | These products give users a visual canvas, app publishing flow, and opinionated developer UX. Dify also spans [cloud](https://docs.dify.ai/en/quick-start), [self-hosting](https://docs.dify.ai/en/self-host/deploy/overview), and a [plugin distribution model](https://docs.dify.ai/en/cloud/use-dify/workspace/plugins). Langflow and Flowise both push hard on agent tooling and low-code iteration speed. | Atlas is better as the substrate underneath these builders: databases, gateways, notebooks, automation, and service composition are already part of the platform. | Atlas should integrate with this category, not rebuild it from scratch. One default visual builder per track would close a major product-gap quickly. |
| RAG engines and agent frameworks | [RAGFlow](https://ragflow.io/docs/), [LlamaIndex](https://developers.llamaindex.ai/python/framework/), [Haystack](https://haystack.deepset.ai/) | These tools are more specialized around retrieval, ingestion, agent workflows, and evaluation loops than Atlas is. RAGFlow is opinionated about document pipelines; LlamaIndex and Haystack offer deeper framework-level primitives for production RAG and agent design. | Atlas can host the whole surrounding environment: object storage, vector DBs, graph DBs, notebooks, job runners, gateways, and adjacent apps. | Atlas should pick reference RAG stacks and make them first-class options instead of implying Atlas itself is the RAG framework. |
| Coding-agent platforms | [OpenHands](https://docs.openhands.dev/overview/introduction) | OpenHands is more focused on AI-driven software delivery, with a local Agent Canvas plus managed integrations for GitHub, GitLab, Bitbucket, Slack, Jira, and Linear, and an enterprise Kubernetes deployment path. | Atlas covers a much wider set of workloads beyond software agents and can host the surrounding tools those agents need. | In the `gen-ai-eng` track, Atlas should present coding agents as packaged workloads or integrations, not invent a parallel developer-agent product surface. |
| ML, data, and observability layers | [MLflow](https://mlflow.org/docs/latest/), [Langfuse](https://langfuse.com/docs), [OpenMetadata](https://docs.open-metadata.org/v1.13.x), [Dagster](https://docs.dagster.io/), [Superset](https://superset.apache.org/user-docs/intro/) | Each of these products owns a clearer category story than Atlas: MLflow for experiment/LLM lifecycle, Langfuse for tracing/evals, OpenMetadata for catalog/governance, Dagster for data orchestration, Superset for BI. | Atlas can colocate these layers with the rest of the AI stack and reduce integration friction for self-hosted teams. | This is an integration zone, not a head-to-head arena. Atlas should win by bundling and wiring category leaders coherently. |
| Kubernetes-based AI platform stacks | [Kubeflow](https://www.kubeflow.org/docs/started/introduction/), [Open Data Hub](https://opendatahub.io/docs/getting-started-with-open-data-hub/) | They are built for larger-scale platform teams that want Kubernetes-native modularity, portability, and production operations across the AI lifecycle. | Atlas is lighter-weight, faster to stand up on a single machine or small team server, and friendlier to hybrid localhost/container development. | Atlas should position itself as the on-ramp before a Kubernetes program, not as a near-term replacement for enterprise AI platform stacks. |

### 3.2 Local Workbenches And Install UX

The local AI workbench market is moving toward compressed time-to-value. [Open WebUI](https://docs.openwebui.com/) already combines an offline-first self-hosted interface with Docker, Python, Kubernetes, and native desktop install paths, and its getting-started docs call out plugins, tool calling, RAG, Open Terminal, and agent connections directly in the onboarding path. [Ollama](https://docs.ollama.com/api/introduction) and [LM Studio](https://lmstudio.ai/docs/developer/core/server) do a similar thing one layer lower: they make local inference feel like a clean product with a predictable local API and compatibility endpoints instead of a system-integration project. [Jan](https://github.com/janhq/jan) packages that same local-first model story into a desktop app with custom assistants, a local OpenAI-compatible server, and MCP support, while [AnythingLLM](https://docs.useanything.com/) bundles agents, API access, browser tooling, chat modes, and vector-database controls into one opinionated application.

[Pinokio](https://desktop.pinokio.co/) sharpens a different user expectation: AI apps should be discoverable and one-click installable, more like a curated launcher ecosystem than a repo you hand-configure. Atlas is much stronger once the user wants an integrated environment rather than one app, but it is still weaker than this category on first impressions, app discovery, and "it just works" local ergonomics. The strategic implication is straightforward: Atlas needs a stronger default workbench surface and curated install bundles before it tries to market its full breadth.

### 3.3 Agent Builders And RAG Products

The agent-builder layer is crowded by tools that are narrower than Atlas but more legible to buy or adopt. [Dify](https://docs.dify.ai/en/learn/key-concepts) presents itself as an agentic app builder with drag-and-drop workflows that can publish to API, web, or MCP server surfaces, and it spans both [Dify Cloud](https://docs.dify.ai/en/quick-start) and [self-hosted deployment](https://docs.dify.ai/en/self-host/deploy/overview). Its [integration model](https://docs.dify.ai/en/cloud/use-dify/workspace/plugins) is especially notable because it gives users an official Marketplace path, GitHub-based installs, and local package upload. [Flowise](https://docs.flowiseai.com/) pushes on the same low-code territory with a visual editor, 100+ integrations, execution logs, visual debugging, and air-gapped self-hosted options. [Langflow](https://docs.langflow.org/) is similarly strong on visual iteration, real-time testing, flow serving, custom components, and both MCP-server and MCP-client support.

On the RAG side, [RAGFlow](https://github.com/infiniflow/ragflow) is explicitly optimized around retrieval and document understanding, with current repo/docs evidence for Docker deployment, orchestrable ingestion, newer document parsers, and MCP-oriented agent workflows. [LlamaIndex](https://developers.llamaindex.ai/python/framework/) and [Haystack](https://haystack.deepset.ai/) sit one level down as frameworks: they provide deeper primitives for RAG, agents, branching logic, retrieval strategies, and evaluation than Atlas should try to own itself. Atlas' advantage is that it can host all the surrounding services these frameworks need, but that is a platform advantage, not a framework advantage. Strategically, Atlas should choose a few blessed integrations and templates here instead of broad, generic parity claims.

### 3.4 Coding Agents, Data Systems, And Observability

[OpenHands](https://docs.openhands.dev/overview/introduction) is a reminder that "AI platform" increasingly includes software agents, not just chat and RAG. Its local Agent Canvas starts a browser-based agent stack with one command, while OpenHands Cloud adds multi-user collaboration, permissions, reporting, budgeting, and integrations across source control and project-management systems. Atlas can host or route to a tool like this, but it is not yet a coding-agent control plane in its own right.

The same pattern appears in adjacent platform layers. [MLflow](https://mlflow.org/docs/latest/) already unifies LLM tracing, prompt management, evaluation, classic experiment tracking, model packaging, registry, and deployment. [Langfuse](https://langfuse.com/docs) has a much tighter story for LLM traces, latency/cost visibility, and collaborative debugging. [OpenMetadata](https://docs.open-metadata.org/v1.13.x) is pushing hard on context, governance, quality, and MCP-aware metadata access. [Dagster](https://docs.dagster.io/) has a clearer asset-centric orchestration and lineage model than Atlas, and [Superset](https://superset.apache.org/user-docs/intro/) remains a stronger analytics/dashboard product than anything Atlas currently exposes at the root experience. Atlas should not compete feature-for-feature with these systems; it should make them easy to enable, discover, authenticate, and connect.

### 3.5 Market Position Summary

The clearest market position for Atlas is between desktop/local workbenches and heavyweight Kubernetes-native AI platforms. [Kubeflow](https://www.kubeflow.org/docs/started/introduction/) and [Open Data Hub](https://opendatahub.io/docs/getting-started-with-open-data-hub/) are optimized for platform teams that want a modular, scalable AI foundation on Kubernetes. Atlas is not there today, and it does not need to be there to matter. Its stronger wedge is: broader than a single local AI app, easier to self-host than a full Kubernetes stack, and more configurable than a polished SaaS-first builder.

That position is defensible if Atlas behaves like the control plane for small teams, labs, consultants, and regulated internal groups that want a serious self-hosted AI environment without immediately taking on a Kubernetes program. The main competitive lesson from this scan is that Atlas wins on composability and scope, but the market rewards products that hide that complexity behind curated entrypoints. The strategic implication is to invest next in packaging, defaults, onboarding, and integration quality more than in raw service-count expansion.

## 4. Strategic Gaps

Atlas' strategic gaps cluster around productization, trust, and operational readiness rather than raw service count. The stack already has enough breadth to support serious AI, RAG, creative, ML, and data workflows; the next work is turning that breadth into safer default paths.

- **Product entrypoint:** the bare Kong root still opens Supabase Studio, so Atlas lacks a first-screen product surface that explains what is running, which track is active, what is healthy, and where a user should go next.
- **Identity/auth:** service authentication is fragmented across local accounts, basic auth, bearer tokens, admin passwords, optional notebook tokens, and unauthenticated local/dev routes. SSO should start as a pilot, not a full-stack migration.
- **MCP/tooling:** MCP is now strategically real across current consumers, but Atlas does not yet have a shared curated tool package, namespacing story, or policy threshold for adding an aggregator.
- **LLM observability/traces:** Prometheus and Grafana cover infrastructure better than Atlas covers LLM behavior. The missing layer is gateway-level tracing, prompt/eval history, latency/cost attribution, and eventually cross-service trace correlation.
- **Ingestion quality:** RAG quality depends on reliable extraction, provenance, and fallbacks. Atlas has strong primitives, but needs a clearer path from search and document upload through extraction, storage, vector/graph indexing, and cited retrieval.
- **Async jobs:** long-running backend work should not stay tied to request lifetimes. Atlas needs a worker tier for retries, progress, scheduled work, and operational visibility before deeper automation flows pile up.
- **Data/ML lifecycle:** the ML/data tracks have notebooks, storage, compute, and orchestration, but still lack first-class experiment tracking, labeling/review loops, artifact analytics, and connection pooling for the next wave of Postgres-backed apps.
- **Vertical track readiness:** 3D/game generation and trading/financial AI are plausible Atlas extensions, but both need guardrails before they become selectable product tracks. The first should be an asset pipeline; the second should be research and paper trading.
- **Operational guardrails:** secrets management, SSO, trace/log correlation, route health, resource limits, extractor safety, and explicit reject/defer criteria should come before higher-risk defaults such as live trading, editor-automation routes, or heavyweight model services.

## 5. MCP Recommendation

Atlas should adopt a **phased hybrid** MCP strategy: start with a small curated package of high-value MCP servers, let the MCP-native consumers talk to those servers directly, add an aggregator only once Atlas has enough servers and per-consumer policy needs to justify it, and avoid one-MCP-server-per-service cargo culting.

### 5.1 What Current Sources Actually Confirm

The current official MCP landscape is materially better than the repo's earlier assumptions, but it is also more uneven than "just add an MCP gateway everywhere."

- The current [Model Context Protocol specification](https://modelcontextprotocol.io/specification/2025-06-18) defines MCP as an open protocol between hosts, clients, and servers with a strong emphasis on consent, authorization, and tool safety. That matters because Atlas is not choosing a mere adapter format; it is choosing an execution surface that can reach databases, files, and internal APIs.
- [Open WebUI now natively supports MCP Streamable HTTP starting in v0.6.31](https://docs.openwebui.com/features/extensibility/mcp/). Its own docs also say to use [mcpo](https://github.com/open-webui/mcpo) only when the target server is not directly consumable over HTTP or when an OpenAPI surface is the more practical client contract ([Open WebUI MCP FAQ](https://docs.openwebui.com/faq/), [mcpo docs](https://docs.openwebui.com/features/extensibility/plugin/tools/openapi-servers/mcp/)). That means Atlas no longer needs an OpenAPI bridge just to give chat access to a well-behaved HTTP MCP server.
- [LiteLLM now ships an MCP gateway](https://docs.litellm.ai/docs/mcp) with a fixed endpoint, support for Streamable HTTP, SSE, and stdio, per-key/team/org permissions, and even [OpenAPI-to-MCP conversion](https://docs.litellm.ai/docs/mcp_openapi). This is verified current support, not roadmap. It makes LiteLLM a real MCP consumer and policy surface, but it does not automatically make LiteLLM the best Atlas-wide MCP control plane.
- [Hermes Agent is currently both a strong MCP client and a verified MCP server](https://hermes-agent.nousresearch.com/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-hermes-agent). Its current CLI docs include `hermes mcp serve`, and the [Codex app-server runtime docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/codex-app-server-runtime) explicitly say Hermes "registers itself as an MCP server" so Codex can call back into Hermes tools. Atlas should still treat Hermes as a **consumer-first** integration by default, because Atlas primarily wants Hermes to use Atlas tools rather than expose Hermes itself as a shared tool surface unless there is a concrete reason to do so. The old ["Hermes as MCP server" issue](https://github.com/NousResearch/hermes-agent/issues/342) is closed and should no longer be treated as pending future work.
- [MetaMCP](https://docs.metamcp.com/en) is a genuine aggregator layer, not just a transport shim. Its docs verify [namespaces](https://docs.metamcp.com/en/concepts/namespaces), [public endpoints that can expose SSE, Streamable HTTP, and OpenAPI](https://docs.metamcp.com/en/concepts/endpoints), and a documented [Open WebUI integration path](https://docs.metamcp.com/en/integrations/open-web-ui). That makes it the strongest fit when Atlas needs one curated surface with per-consumer scoping.
- [Docker MCP Gateway](https://docs.docker.com/ai/mcp-catalog-and-toolkit/mcp-gateway/) is also real and open source, but Docker's docs position the managed AI Governance path as invite-only and frame the gateway around dynamically launching MCP server containers from Docker-managed profiles and catalogs. Its official tooling and repo are excellent for broad connector catalogs and Docker-centered lifecycle control, but that is a worse default fit for Atlas' mostly internal, already-composed services.
- [Docling MCP](https://github.com/docling-project/docling-mcp) is now especially relevant for Atlas because v2 added remote mode and `streamable-http`, so it can front an existing Docling service instead of forcing Atlas to co-locate conversion models with every MCP process.

The practical consequence is straightforward: Atlas should not choose between "pure sidecars" and "always-on aggregator" as a religion. It should use direct sidecars where the value is immediate, then introduce aggregation when the number of servers and policy splits make it worthwhile.

### 5.2 MCP Target / Consumer Matrix

| Atlas service | MCP role | Recommendation | Rationale |
|---|---|---|---|
| Supabase / Postgres | Target | **Phase 1 curated server** | High-value structured data surface; Atlas already centralizes product state in Postgres and the roadmap already expects a Postgres MCP path. |
| Neo4j | Target | **Phase 1 curated server** | Graph queries are highly agent-friendly and Hermes/Open WebUI benefit from a graph-native tool more than from bespoke HTTP glue. |
| Weaviate | Target | **Phase 2 curated server** | Valuable, but lower priority than Postgres/Neo4j/SearXNG because Atlas already has non-MCP retrieval paths and community MCP quality should be evaluated before pinning. |
| SearXNG | Target | **Phase 1 curated server** | Clean web-search tool surface with obvious cross-consumer value and low architectural risk. |
| MinIO | Target | **Phase 2 curated server** | Good fit through an S3-shaped MCP server, but the write/delete blast radius is larger, so it should wait for scoped credentials and clearer policy. |
| n8n | Both, but defer | **Phase 2 target/consumer** | Atlas research suggests useful MCP-trigger and workflow-as-tool patterns, but Atlas does not need them to justify the initial architecture; treat as a later expansion. |
| Backend | Both, but mostly target | **Phase 2 custom Atlas MCP server** | A thin first-party MCP wrapper around LangMem and Atlas-specific routes is better than exposing the full backend surface ad hoc. |
| Docling | Target | **Early specialist addition** | Verified current [Docling MCP](https://github.com/docling-project/docling-mcp) remote mode makes document conversion a strong high-signal tool for agents without duplicate model loading. |
| Open WebUI | Consumer | **Use as direct MCP client** | Verified current native Streamable HTTP support means Atlas should connect Open WebUI directly to curated MCP servers or a later aggregator, not force mcpo in front of every server. |
| Hermes | Both (consumer-first) | **Use as direct MCP client by default; expose Hermes-as-server only when a concrete callback/tool-sharing need exists** | Verified current Hermes MCP support includes both client capability and server capability (`hermes mcp serve`, plus Hermes registering itself as an MCP server in the Codex runtime docs), but Atlas' primary value is Hermes consuming Atlas tools rather than making Hermes another shared platform server by default. |
| OpenClaw | Consumer (inferred, not re-verified externally in this pass) | **Treat as a later consumer** | Atlas' internal research says OpenClaw has MCP CLI / external-server support, but the initial decision does not need that path to be live. |
| LiteLLM | Consumer and optional policy gateway | **Consume MCP; do not make it the sole Atlas aggregator yet** | Verified current MCP support is real, but Atlas should avoid coupling every tool-governance concern to its already-critical LLM gateway on day one. |
| Ollama | Neither practical target nor priority consumer | **Do not MCP-wrap** | Ollama is already the model-serving layer; adding MCP around it would be circular and adds little user value. |
| Kong | Infra proxy, not MCP target | **Proxy selected MCP HTTP routes later; do not turn Kong into the MCP brain** | Kong is useful for stable aliases and auth fronts, but Atlas should keep MCP tool semantics in MCP-aware components, not in the HTTP gateway itself. |
| STT / TTS providers | Usually non-target | **Do not wrap current providers in MCP** | Atlas already reaches these services through OpenAI-compatible HTTP. MCP only becomes interesting when a provider's native value is MCP-first, such as the repo's Voicebox research. |
| Virtual services (`cloud-providers`, `globals`, provider virtual manifests) | Neither | **Do not MCP-wrap** | These are configuration surfaces, not runtime tool surfaces. |

### 5.3 Recommended Architecture

The best answer is not "aggregator" or "service-by-service sidecars" in isolation. It is:

1. **Phase 1: curated package, no heavyweight aggregator**
   Stand up a small set of MCP servers with the clearest cross-stack payoff:
   `Postgres`, `Neo4j`, `SearXNG`, and then `Docling MCP` as the first specialist expansion. Keep them as explicit, reviewable services rather than auto-spawning a server for every Atlas service.

2. **Connect native consumers directly first**
   Let Open WebUI and Hermes talk to those MCP servers directly over Streamable HTTP where possible. Let LiteLLM consume them only where Atlas explicitly wants model-facing tool access under LiteLLM policy. This keeps the first rollout simple and proves real demand before Atlas adds another control plane.

3. **Add an aggregator when Atlas crosses the "policy and namespacing" threshold**
   Once Atlas has roughly four or more internal MCP servers, or once chat, agents, and automation need different tool visibility, add **MetaMCP** in front of the curated set. MetaMCP is the best fit for that step because it combines namespaces, public endpoints, and OpenAPI exposure in one place. At that point Atlas can expose one namespace to Open WebUI, a narrower one to Hermes, and a more controlled one to automation clients.

4. **Use `mcpo` as a translator, not as the architecture center**
   `mcpo` is still useful when Atlas needs OpenAPI for a consumer that is not MCP-native, or when a desired MCP server is stdio-only. It should be treated as an edge adapter, not as the long-term Atlas MCP control plane.

5. **Reserve Docker MCP Gateway for a different problem**
   Docker MCP Gateway becomes attractive if Atlas later wants a large SaaS/vendor connector catalog with Docker-managed lifecycle and catalog distribution. That is a legitimate future path, but it is not the right default for Atlas' internal-service-first topology today.

### 5.4 Bottom-Line Decision

Atlas should **not** do one-MCP-server-per-service, should **not** default to Docker MCP Gateway, and should **not** force every tool through `mcpo`. Atlas should use a **phased hybrid**: a curated package of high-value MCP servers now, direct native consumption first, and MetaMCP later when namespacing, policy, and endpoint consolidation become worth the extra operational layer.

## 6. Kong Root Dashboard Recommendation

Yes: the **Kong root** should become an Atlas product entrypoint. But the first version should be a lightweight **service directory** and **health dashboard**, not a replacement for Grafana, Supabase Studio, or the setup wizard.

### 6.1 Why The Root Should Change

Today, Atlas' own route docs and Kong README both say that the bare `localhost` root falls through to Supabase Studio ([ports-and-routes](../deployment/ports-and-routes.md), [Kong README](../../services/kong/README.md)). That is useful for operators who already know Atlas, but it is the wrong first impression for a multi-service platform. The front door currently lands on one subsystem's admin console instead of on Atlas itself.

Atlas' biggest onboarding weakness is not lack of services; it is lack of orientation. A root dashboard solves that directly by answering the first-run questions users actually have:

- What is running right now?
- Which URLs should I use?
- Which services are intentionally disabled by the current track?
- Which credentials or auth modes apply?
- What should I open first?

### 6.2 Minimum Viable Dashboard

The minimum viable dashboard should stay narrow and operationally boring:

- **Service directory:** one row per active service showing display name, category, current SOURCE, Kong URL, direct URL, and a short auth note.
- **Health dashboard:** clear `healthy / degraded / disabled` status, derived from the same resolved SOURCE state and lightweight reachability checks Atlas already uses for routing. This should be an orientation surface, not a Grafana clone.
- **Track context:** show the active track and which services are disabled by track policy versus manually disabled by the operator.
- **Common actions:** prominent launch links for the highest-traffic surfaces such as Open WebUI, LiteLLM, n8n, JupyterHub, Supabase Studio, and any enabled observability pages.
- **Warnings:** disabled dependencies, localhost-mode services that are unreachable, missing `--setup-hosts` state, and services whose route exists but whose upstream is degraded.
- **Docs links:** quick links to the route table, troubleshooting, and the quick-start flow so the dashboard helps users recover rather than merely report failure.

### 6.3 What The First Version Should Explicitly Not Be

The first version should **not** try to become:

- a replacement for Grafana metrics and alerts
- a replacement for Supabase Studio or LiteLLM admin surfaces
- a control plane for editing SOURCE values
- a second setup wizard
- a giant SPA with its own persistence model

Atlas already has several specialist UIs. The root page should be the map, not every destination at once.

### 6.4 Product Recommendation

Atlas should move the root route to an Atlas-branded landing page and keep Supabase Studio on `supabase-studio.localhost`. The first implementation should be a small, generated dashboard built from the same topology, route, and auth metadata Atlas already maintains, so it stays consistent with the rest of the stack instead of becoming a second, drifting inventory.

In short: **use the Kong root as the Atlas entrypoint, and make v1 a service directory plus health dashboard.** That is the smallest change that meaningfully improves onboarding, navigation, and day-two usability without overreaching into a new control plane.

## 7. vNext Top 20

Ranking principle: Atlas should prefer additions that make the existing stack more usable, safer, and more observable before adding shiny vertical engines. The scoring pass considered strategic fit, reuse of Atlas primitives, user value, effort, operational cost, security risk, license fit, maintenance burden, dependency blast radius, and upstream maturity. The result is deliberately conservative: product entrypoints, MCP, ingestion, observability, identity, secrets, and async execution outrank novelty.

| Rank | vNext candidate | Rationale | Effort | Risk | Key dependencies | First slice |
|---:|---|---|---|---|---|---|
| 1 | **Atlas root dashboard** | The Kong root is the clearest product gap and has the best value-to-risk ratio: it turns service sprawl into a navigable platform surface without adding a new datastore. | Medium | Low | Kong route metadata, topology, resolved SOURCE state, lightweight health checks | Replace bare-root Supabase Studio fallback with a generated service directory showing active services, URLs, auth notes, and disabled-by-track state. |
| 2 | **Curated MCP package** | Sections 5.1-5.4 already establish that MCP is now real across Open WebUI, LiteLLM, Hermes, and Docling. A curated package creates shared agent tools without one-off Hermes skills or one-MCP-server-per-service sprawl. | Medium | Medium | Supabase/Postgres, Neo4j, SearXNG, Docling MCP, Hermes/Open WebUI/LiteLLM client config | Ship Postgres, Neo4j, and SearXNG MCP servers first; add Docling MCP next once auth and namespacing are settled. |
| 3 | **Langfuse** | LLM observability is the biggest operational blind spot after the dashboard. LiteLLM has a direct Langfuse integration, and Langfuse self-hosting now expects Redis, S3, and ClickHouse-style ingestion, all near Atlas primitives. | Medium | Medium | LiteLLM callback, Redis, MinIO, Postgres, likely ClickHouse for current Langfuse | Trace all LiteLLM-routed calls first; defer direct ComfyUI/Hermes custom spans until gateway traces are working. |
| 4 | **Crawl4AI** | RAG and research quality depends on extracting real page content, not snippets. Crawl4AI is Apache-2.0, lower license risk than Firecrawl, and fits the existing SearXNG -> extract -> Docling/Weaviate path. | Small | Medium | SearXNG, Local Deep Researcher, n8n, backend, Weaviate | Add disabled-by-default `crawl4ai` service and wire Local Deep Researcher full-page fetch to it behind one env flag. |
| 5 | **Celery + Flower worker tier** | Long-running backend calls are already a mismatch for the FastAPI request loop. Redis is present, so async jobs, retries, scheduled consolidation, and a Flower monitor are high leverage. | Small | Medium | Redis, backend code sharing, LiteLLM, Weaviate, ComfyUI | Move one backend long-running endpoint, ideally memory consolidation or research start, to a Celery task and expose Flower as dev-only. |
| 6 | **SSO pilot: Authentik-first, Keycloak as enterprise alternative** | Identity fragmentation is a platform weakness. Keycloak has the broadest ecosystem but a heavier JVM footprint; Authentik's OIDC and proxy-provider docs make it a better Atlas-first pilot for mixed apps and reverse-proxy auth. | Large | High | Kong, Open WebUI, JupyterHub, n8n, MinIO, Neo4j, Supabase Auth decisions | Protect one non-critical route behind Authentik forward auth, document the Keycloak tradeoff, and do not migrate all services in one pass. |
| 7 | **Secrets manager: Infisical-first, OpenBao watchlist** | Trading, MCP, 3D model downloads, and multi-user tracks all increase secret sprawl. Infisical is developer-friendly and self-hostable; OpenBao is the stronger Vault-lineage option but heavier to operate. | Medium | High | Bootstrapper env injection, service startup order, Postgres/Redis or dedicated storage | Add Infisical as optional, disabled by default, and move only new high-risk credentials there before touching existing `.env` flows. |
| 8 | **Supavisor** | Postgres connection pressure will grow as Langfuse, MLflow, Label Studio, OpenBB, and user notebooks arrive. Supavisor is a focused Supabase-native pressure valve. | Medium | Medium | Supabase Postgres, consumer DB clients, transaction-mode audit | Route backend and n8n through Supavisor first; leave PostgREST and Realtime direct until session-mode behavior is proven. |
| 9 | **Apache Tika fallback** | Docling is the high-quality path, but Atlas still needs a long-tail extractor for EML, MSG, RTF, ODT, ZIP, and obscure MIME types. Tika is boring in the best way. | Small | Medium | Doc processor, backend, n8n, malware/resource limits | Add disabled-by-default Tika and call it only when Docling returns unsupported-format. |
| 10 | **OpenTelemetry Collector + Tempo + Loki** | Prometheus/Grafana are shipped, but traces and queryable logs are still missing. The OTel Collector is the vendor-neutral ingest point; Tempo and Loki complete the Grafana-native observability triangle. | Medium | Medium | Grafana, Prometheus, Kong request IDs, backend/LiteLLM/Hermes instrumentation, MinIO for Tempo object storage | Start with OTel Collector and Tempo for backend/LiteLLM traces, then add Loki log shipping with short default retention. |
| 11 | **MLflow** | The ML track has notebooks, Ray, Spark, MinIO, and Postgres but no durable experiment or model registry. MLflow reuses MinIO and Supabase cleanly. | Medium | Medium | JupyterHub, MinIO, Supabase schema, backend/n8n REST consumers | Expose MLflow tracking URI to JupyterHub and create a MinIO-backed artifact store; leave model promotion automations for later. |
| 12 | **Open WebUI Pipelines** | Pipelines gives Atlas a request-time middleware layer for filters, routing, redaction, and tracing without forking Open WebUI. It is useful only if its place relative to LiteLLM is explicit. | Medium | Medium | Open WebUI, LiteLLM, curated pipeline scripts, Langfuse | Ship one tracing/redaction pipeline as a disabled option and document whether it sits before or behind LiteLLM. |
| 13 | **Neo4j LLM Knowledge Graph Builder** | Atlas already ships Neo4j, LiteLLM, MinIO, and Docling; this is the most direct path to a visible GraphRAG workflow. | Medium | Medium | Neo4j, LiteLLM, MinIO, Docling, Kong | Add the UI/backend pair disabled by default, configured against existing Neo4j and LiteLLM, with a sample document-to-graph path. |
| 14 | **Verba** | Verba provides a vendor-native Weaviate RAG UI. It is less strategic than MCP/ingestion but valuable as an end-to-end demo of Weaviate + LiteLLM + Docling. | Medium | Medium | Weaviate, LiteLLM, optional Docling, Kong auth | Add Verba with a namespaced Weaviate collection and one documented sample ingest. |
| 15 | **Label Studio** | Atlas can generate, transcribe, and retrieve data, but it cannot curate labels or review datasets as a product workflow. Label Studio complements MLflow and MinIO. | Medium | Medium | MinIO, Supabase schema, JupyterHub SDK, optional backend active-learning loop | Ship Label Studio disabled by default with S3-backed media storage and a notebook showing export to Weaviate/MLflow. |
| 16 | **Graphiti** | Temporal graph memory fits Atlas' agent story and reuses Neo4j and LiteLLM. It should augment LangMem for advanced agent memory, not replace it. | Small | Medium | Hermes/backend Python deps, Neo4j schema, LiteLLM embeddings | Add a backend-only experiment with strict `group_id` namespacing before exposing it to Hermes or OpenClaw. |
| 17 | **SigLIP 2 vectorizer upgrade path** | Multimodal retrieval quality matters for creative/RAG tracks, and Weaviate supports SigLIP 2-family multi2vec images. This is high payoff but requires revectorization planning. | Small | Medium | Weaviate multi2vec-clip, existing collection dimensions, GPU option | Add a second opt-in image/env value and a migration note; do not silently change existing vector dimensions. |
| 18 | **Iceberg + DuckDB, with Lakekeeper evaluated as the catalog** | MinIO needs a queryable analytics layer. DuckDB and Iceberg are a natural local-first fit; current DuckDB REST-catalog support makes Lakekeeper worth evaluating against a minimal catalog. | Medium | Medium | MinIO analytics bucket, Supabase or catalog storage, JupyterHub, optional Trino later | Create an `analytics` bucket and one notebook that writes/reads Iceberg tables via DuckDB before adding Trino or Superset. |
| 19 | **OpenBB + CCXT financial research kit** | A financial track should start with data and research, not live trading. OpenBB provides finance data/application APIs and CCXT provides a unified crypto exchange library, both notebook-friendly. | Medium | High | JupyterHub, LiteLLM, MinIO datasets, Infisical for API keys, optional TimescaleDB | Add notebooks and env scaffolding for read-only market data and paper portfolios; explicitly block live exchange keys in the first slice. |
| 20 | **Blender MCP + glTF-Transform asset bridge** | The 3D track should first automate a host desktop tool and optimize assets, not ship large 3D foundation models. Blender MCP is useful but unsafe by default; glTF-Transform is a safer first server-side asset utility. | Medium | High | Host Blender, MCP clients, MinIO asset buckets, ComfyUI, imgproxy, optional Godot | Add a disabled localhost-only Blender MCP profile plus a containerized glTF-Transform postprocess job for GLB inspection/optimization. |

Strong candidates intentionally below the top 20: `imgproxy` is small and useful but follows the dashboard/asset-browser work; its July 4, 2026 watchlist decision keeps it out of the service graph until Atlas has backend-generated signed URLs, MinIO bucket allow-lists, and a route/auth model for thumbnails. `NocoDB` is attractive for human-in-the-loop queues, but its July 4, 2026 watchlist decision keeps it out until Atlas has a concrete review workflow plus credible SSO/route auth. `NeoDash` waits until more services write useful graph data; `WhisperX` waits until meeting/audio ingestion becomes a first-class RAG flow; `Dagster` and `Superset` wait until the MinIO/Iceberg/Trino foundation has proven demand; `TimescaleDB` is best treated as part of the trading data slice rather than a standalone platform bet.

## 8. Track Expansion

### 8.1 3D / Game-Generation Track

Recommendation: build a **3D asset pipeline track**, not a "generate a whole game" track. Atlas already has ComfyUI, MinIO, Weaviate multimodal search, notebooks, and soon a root dashboard; the first 3D expansion should make assets inspectable, transformable, searchable, and agent-assistable.

First wave:

- Add `imgproxy` after the dashboard or asset-browser work so generated images and thumbnails are cheap to browse.
- Add glTF-Transform as a postprocess utility for GLB inspection, optimization, texture compression, and metadata extraction.
- Add a disabled-by-default, localhost-only Blender MCP profile. Blender's own MCP page warns that LLM-generated code executes in Blender without guards, so this cannot be a default-on container service.
- Add a Godot export/template note before adding engine automation. Godot is MIT-licensed and useful for starter projects, but Atlas should not imply it can author production games automatically.

Later wave:

- Evaluate Hunyuan3D and TRELLIS/TRELLIS.2 as local or localhost GPU model integrations after Atlas has the asset store, thumbnails, and glTF processing path. These models are promising, but they are large, fast-moving, and operationally heavier than the platform should absorb first.
- Evaluate Nerfstudio for reconstruction/scanning workflows, not general asset generation.
- Evaluate LiveKit for collaborative realtime review or voice-driven creator workflows only after the voice/audio stack has a clear product use case.
- Evaluate Unreal MCP only as a local developer option. Epic's Unreal MCP is real, but editor automation has the same code-execution risk class as Blender and a heavier workstation assumption.

Safety/defer notes:

- Do not rank 3D novelty above sandboxing, artifact lineage, and asset portability.
- Do not expose Blender or Unreal MCP routes through Kong by default.
- Do not ship Hunyuan3D/TRELLIS as default track members until license, VRAM, model-cache size, and output-format contracts are pinned.

### 8.2 Trading / Financial-AI Track

Recommendation: build a **financial research and paper-trading track** first. Atlas should be candid that live trading is a regulated, high-loss-risk domain and should not be enabled by default.

First wave:

- Add OpenBB and CCXT as notebook libraries for read-only data acquisition, research, and portfolio analysis.
- Add an explicit paper-portfolio notebook path using JupyterHub, MinIO datasets, MLflow experiment runs, and LiteLLM summaries.
- Add Infisical before any exchange-key workflow so credentials are not scattered through `.env`, notebooks, and n8n nodes.
- Evaluate TimescaleDB as a Postgres extension path for tick/order-book/time-series storage; prefer isolated schemas and clear retention policies.

Later wave:

- Evaluate NautilusTrader for deterministic backtesting and multi-venue strategy simulation. It is stronger than ad hoc notebooks for serious quants, but it should come after data acquisition, secrets, and paper-run guardrails.
- Evaluate Hummingbot and Freqtrade only as sandbox/paper-trading services. Their live-trading value is real, but so is their operational and user-harm risk.
- Keep FinRL and FinGPT in research notebooks until Atlas has curated datasets, eval criteria, and disclaimers. These should not become "push-button trading AI" surfaces.

Safety/defer notes:

- No live exchange trading in the first financial track.
- Default to read-only API keys, sandbox venues, and explicit paper modes.
- Require secrets manager integration, audit logs, and clear "not financial advice" docs before any live-trading connector is selectable.
- Do not let n8n workflows hold unrestricted exchange keys by default.

### 8.3 RAG And Content-Ingestion Track

Recommendation: make RAG ingestion a first-class product path. Atlas already has Docling, LightRAG, Weaviate, Neo4j, SearXNG, MinIO, and LiteLLM; the gap is robust extraction, agent access, and a visible RAG/GraphRAG workflow.

First wave:

- Add Crawl4AI as the default web-page extraction path for Local Deep Researcher and n8n ingestion flows.
- Add Apache Tika as a degraded fallback for long-tail document formats that Docling does not target.
- Add Docling MCP after the first curated MCP package so agents can read PDFs and office documents without bespoke HTTP upload code.
- Add Verba and/or Neo4j LLM Knowledge Graph Builder as visible reference UIs, but keep their collections/namespaces isolated.

Later wave:

- Add Browserless only if Crawl4AI leaves important JavaScript-rendered workflows uncovered. Its SSPL posture and Chromium footprint make it a second choice, not a default.
- Add WhisperX when audio/video meeting ingestion becomes a named workflow. Diarization is useful, but the pyannote token/model-terms path needs a separate adoption note.
- Add Graphiti when cross-session temporal graph memory has a concrete Hermes/backend workflow. It should not replace LangMem prematurely.

Safety/defer notes:

- Keep crawler and parser services disabled by default where resource usage or document-security risk is high.
- Put size limits, timeout limits, and content-type routing rules around every extraction path.
- Preserve citations, source URLs, and document provenance in MinIO/Weaviate/Neo4j so RAG quality can be audited.

### 8.4 Data / ML Platform Track

Recommendation: strengthen the ML/data substrate in layers: experiment tracking, labeling, artifact analytics, then optional BI/orchestration expansions. Atlas already ships Airflow, Spark, Ray, MinIO, Supabase, JupyterHub, and Zeppelin; it does not need to add every modern data product at once.

First wave:

- Add MLflow for experiment tracking and model artifacts using Supabase plus MinIO.
- Add Label Studio for dataset labeling and review loops, wired to MinIO and JupyterHub.
- Add Iceberg + DuckDB as the local analytics path over MinIO artifacts, evaluating Lakekeeper as the REST catalog if write/concurrency needs justify it.
- Add Supavisor before several new Postgres-backed apps land.

Later wave:

- Evaluate Dagster as an asset-aware orchestrator only after deciding how it coexists with Airflow. It should not become a second scheduler with no clear ownership.
- Trino has moved into the first data-eng lakehouse implementation slice as a disabled-by-default SQL query engine over Iceberg REST and MinIO.
- Evaluate Superset after Trino/Iceberg or Postgres analytics schemas have useful datasets and SSO is available.
- Redpanda has moved into the data-eng lakehouse implementation slice as a disabled-by-default Kafka API broker for Spark Structured Streaming jobs.

Safety/defer notes:

- Do not duplicate Airflow with Dagster without a migration or separation story.
- Do not add BI surfaces before identity/permissions are credible.
- Keep lakehouse work grounded in MinIO artifact queries, not abstract "big data" positioning.

## 9. Implementation Waves

### 9.1 Build Now

Build now means high strategic value, strong reuse of existing primitives, and tolerable blast radius.

1. **Atlas root dashboard:** one generated service directory/health page at Kong root.
2. **Curated MCP package:** Postgres, Neo4j, and SearXNG MCP servers, then Docling MCP as the first specialist.
3. **Langfuse gateway tracing:** LiteLLM callback first; defer deeper span fan-out.
4. **Crawl4AI:** Local Deep Researcher and n8n web extraction path.
5. **Celery + Flower:** backend async worker for one long-running endpoint.
6. **Supavisor:** transaction-mode pooler for backend/n8n first.
7. **Apache Tika:** Docling fallback for unsupported formats.

### 9.2 Build Next

Build next means valuable but dependent on the first wave's foundations, especially dashboard, observability, secrets, and auth.

1. **SSO pilot:** Authentik-first route protection, with Keycloak documented as the heavier alternative for enterprise OIDC/SAML needs.
2. **Secrets manager:** Infisical optional service for new high-risk credentials; OpenBao remains the Vault-lineage watchlist option.
3. **OpenTelemetry Collector + Tempo + Loki:** traces first, logs second, short retention by default.
4. **MLflow:** notebook experiment tracking and MinIO-backed artifacts.
5. **Open WebUI Pipelines:** one curated middleware path for redaction/tracing/routing, with OpenLIT deferred in favor of the Langfuse plus OTel stack rather than adding a second observability UI now.
6. **Neo4j LLM Knowledge Graph Builder and Verba:** reference GraphRAG/RAG UIs after MCP and ingestion basics exist.
7. **Label Studio:** dataset review loop for ML/RAG/creative outputs.

### 9.3 Build Later

Build later means useful, but the prerequisites or product commitments are not mature enough yet.

1. **Graphiti:** temporal graph memory after a concrete Hermes/backend workflow is selected.
2. **SigLIP 2 vectorizer upgrade:** opt-in migration path after revectorization tooling is clear.
3. **Iceberg + DuckDB + Lakekeeper evaluation:** after MinIO artifact analytics has sample data and ownership.
4. **OpenBB + CCXT financial research kit:** first guarded notebook slice has moved into implementation; keep exchange-key and live/sandbox execution workflows deferred until secrets handling and risk controls are ready.
5. **Blender MCP + glTF-Transform:** after asset storage, dashboard links, and safety notes are in place.
6. **imgproxy, NocoDB, NeoDash, WhisperX:** each is useful, but each benefits from an earlier dashboard/auth/data-foundation pass.
7. **Dagster and Superset:** wait for real lakehouse demand and a clear Airflow/BI coexistence model.

### 9.4 Reject Or Defer For Now

- **Firecrawl:** defer in favor of Crawl4AI because AGPL plus a larger worker/Playwright footprint is not worth it for the first ingestion slice.
- **Browserless:** defer until Crawl4AI proves insufficient for JavaScript-heavy workflows; SSPL and Chromium memory cost keep it out of the default plan.
- **Supabase Edge Functions:** defer because Atlas already has backend, n8n, and Celery for server-side execution; a second Deno function surface adds overlap before there is an edge-specific need.
- **OpenLIT:** defer as a standalone candidate because Langfuse plus the OTel Collector/Tempo/Loki path already covers the first observability slice with less UI overlap.
- **Live trading services:** defer Hummingbot, Freqtrade, and NautilusTrader live execution. Reconsider only after paper mode, secrets, audit logs, and explicit operator risk controls exist.
- **FinRL and FinGPT:** keep in notebooks/research. Do not present them as production trading intelligence.
- **OpenBB + CCXT:** shipped only as a read-only JupyterHub research kit with paper portfolios; keep private exchange credentials, private CCXT methods, and execution engines out of this first slice.
- **Hunyuan3D, TRELLIS/TRELLIS.2, Nerfstudio, Unreal MCP, and LiveKit:** watchlist for the 3D/game track, but not before the asset pipeline and MCP safety posture are real.
- **Voicebox, OmniVoice, and Unmute:** defer as voice-stack watchlist items; Voicebox lacks the OpenAI-compatible endpoint Atlas needs, OmniVoice would require Atlas to own a young HTTP wrapper, and Unmute is still too early for Atlas to standardize on as a core realtime speech layer.
- **Honcho:** defer because Atlas already has LangMem and Graphiti is a lighter first experiment; Honcho's AGPL posture and separate memory service are not yet justified.
- **Redis Stack and RedisInsight:** defer until a concrete Redis module or GUI workflow beats the license/image-size cost.
- **Perplexica/Vane:** defer because it overlaps with Open WebUI plus Local Deep Researcher unless a distinct "single-shot cited answer" product surface becomes a priority.
- **Redpanda:** shipped as a disabled-by-default data-eng broker for Spark Structured Streaming; keep Kafka Connect, Debezium, multi-broker clustering, SASL/TLS, and production retention tuning out of the first slice.

## 10. Appendices

### 10.1 Source Notes

Task 2 current-state evidence notes:

- `README.md`: current product framing, quickstart flows, SOURCE-system explanation, generated service tables, auth surface summary, and track positioning.
- `docs/ROADMAP.md`: shipped-versus-candidate boundary, observability gap notes, MCP architecture options, and future vertical-track language.
- `docs/deployment/ports-and-routes.md`: authoritative Kong alias table, root-route behavior, and direct evidence of route sprawl.
- `docs/CONTRIBUTING-services.md`: per-service manifest/compose contract, regen-and-lint chain, and maintainability posture.
- `services/*/service.yml`: manifest inventory evidence for service count, category spread, virtual manifests, and the recurring SOURCE contract referenced in section 2.
- `bootstrapper/tracks.yml`: current first-class track registry and its limits relative to roadmap verticals.
- `services/kong/README.md`, `services/open-webui/README.md`, `services/supabase/README.md`, `services/prometheus/README.md`, `services/grafana/README.md`: concrete evidence for root-entrypoint behavior, fragmented auth, future SSO work, and current-versus-future observability scope.

Task 3 external competitor notes (official sources checked July 2, 2026):

- Local AI workbenches and launcher surfaces: [Open WebUI docs](https://docs.openwebui.com/), [Ollama docs](https://docs.ollama.com/api/introduction), [Ollama product site](https://ollama.com/), [LM Studio developer docs](https://lmstudio.ai/docs/developer/core/server), [AnythingLLM docs](https://docs.useanything.com/), [Jan GitHub repo](https://github.com/janhq/jan), [Pinokio product site](https://desktop.pinokio.co/).
- Agent-builder platforms: [Dify key concepts](https://docs.dify.ai/en/learn/key-concepts), [Dify cloud quick start](https://docs.dify.ai/en/quick-start), [Dify self-host deploy overview](https://docs.dify.ai/en/self-host/deploy/overview), [Dify plugins docs](https://docs.dify.ai/en/cloud/use-dify/workspace/plugins), [Flowise docs](https://docs.flowiseai.com/), [Langflow docs](https://docs.langflow.org/).
- RAG and framework products: [RAGFlow docs](https://ragflow.io/docs/), [RAGFlow GitHub repo](https://github.com/infiniflow/ragflow), [LlamaIndex framework docs](https://developers.llamaindex.ai/python/framework/), [Haystack docs](https://haystack.deepset.ai/).
- Coding-agent, observability, ML, and data layers: [OpenHands docs](https://docs.openhands.dev/overview/introduction), [MLflow docs](https://mlflow.org/docs/latest/), [Langfuse docs](https://langfuse.com/docs), [OpenMetadata docs](https://docs.open-metadata.org/v1.13.x), [Dagster docs](https://docs.dagster.io/), [Superset docs](https://superset.apache.org/user-docs/intro/).
- Kubernetes-based AI platforms: [Kubeflow introduction](https://www.kubeflow.org/docs/started/introduction/), [Open Data Hub getting started](https://opendatahub.io/docs/getting-started-with-open-data-hub/).

Task 4 MCP and dashboard notes (official/current sources checked July 2, 2026):

- MCP protocol baseline: [Model Context Protocol specification, version 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18).
- MetaMCP current capabilities: [docs home](https://docs.metamcp.com/en), [namespaces](https://docs.metamcp.com/en/concepts/namespaces), [endpoints](https://docs.metamcp.com/en/concepts/endpoints), [Open WebUI integration](https://docs.metamcp.com/en/integrations/open-web-ui), and the [official GitHub repo](https://github.com/metatool-ai/metamcp).
- Docker MCP Gateway current capabilities: [Docker docs](https://docs.docker.com/ai/mcp-catalog-and-toolkit/mcp-gateway/) and the [official GitHub repo](https://github.com/docker/mcp-gateway).
- Open WebUI current MCP position: [native MCP docs](https://docs.openwebui.com/features/extensibility/mcp/), [mcpo docs](https://docs.openwebui.com/features/extensibility/plugin/tools/openapi-servers/mcp/), [tools overview](https://docs.openwebui.com/features/extensibility/plugin/tools/), [FAQ](https://docs.openwebui.com/faq/), and the [mcpo repo](https://github.com/open-webui/mcpo).
- LiteLLM current MCP position: [MCP overview](https://docs.litellm.ai/docs/mcp), [usage](https://docs.litellm.ai/docs/mcp_usage), [permission management](https://docs.litellm.ai/docs/mcp_control), [deployment guide](https://docs.litellm.ai/docs/mcp_deployment), [OpenAPI-to-MCP](https://docs.litellm.ai/docs/mcp_openapi).
- Hermes current MCP position: the bundled-skill / CLI reference showing [`hermes mcp serve`](https://hermes-agent.nousresearch.com/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-hermes-agent), the [Codex app-server runtime docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/codex-app-server-runtime) showing Hermes "registers itself as an MCP server", the broader [feature docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp), the [usage guide](https://hermes-agent.nousresearch.com/docs/guides/use-mcp-with-hermes), the [closed issue #342](https://github.com/NousResearch/hermes-agent/issues/342), and the [official Hermes repo](https://github.com/NousResearch/hermes-agent).
- Docling MCP current capabilities: the [official Docling MCP repo](https://github.com/docling-project/docling-mcp) and [docling-serve releases](https://github.com/DS4SD/docling-serve/releases) for current bundled versions.
- Atlas-internal evidence used for the Kong-root recommendation: [ports-and-routes](../deployment/ports-and-routes.md), [Kong README](../../services/kong/README.md), [Open WebUI README](../../services/open-webui/README.md), [LiteLLM README](../../services/litellm/README.md), [Hermes README](../../services/hermes/README.md), and the current [ROADMAP](../ROADMAP.md).

Task 5 vNext ranking notes (candidate corpus plus current outside sources checked July 2, 2026):

- Existing one-pagers considered for ranking: [Apache Tika](../research/candidates/apache-tika.md), [Browserless](../research/candidates/browserless.md), [Celery + Flower](../research/candidates/celery-flower.md), [Crawl4AI](../research/candidates/crawl4ai.md), [Dagster](../research/candidates/dagster.md), [Docling MCP](../research/candidates/docling-mcp.md), [Firecrawl](../research/candidates/firecrawl.md), [Grafana Loki](../research/candidates/grafana-loki.md), [Graphiti](../research/candidates/graphiti.md), [Honcho](../research/candidates/honcho.md), [Iceberg + DuckDB](../research/candidates/iceberg-duckdb.md), [imgproxy](../research/candidates/imgproxy.md), [Keycloak](../research/candidates/keycloak.md), [Label Studio](../research/candidates/label-studio.md), [Lakekeeper](../research/candidates/lakekeeper.md), [Langfuse](../research/candidates/langfuse.md), [MCP Gateway](../research/candidates/mcp-gateway.md), [mcpo](../research/candidates/mcpo.md), [MLflow](../research/candidates/mlflow.md), [Neo4j LLM Knowledge Graph Builder](../research/candidates/neo4j-llm-graph-builder.md), [NeoDash](../research/candidates/neodash.md), [NocoDB](../research/candidates/nocodb.md), [OmniVoice](../research/candidates/omnivoice.md), [Open WebUI Pipelines](../research/candidates/open-webui-pipelines.md), [OpenLIT](../research/candidates/openlit.md), [Perplexica/Vane](../research/candidates/perplexica.md), [Prometheus](../research/candidates/prometheus.md), [Redis Stack](../research/candidates/redis-stack.md), [RedisInsight](../research/candidates/redisinsight.md), [SigLIP 2 Vectorizer](../research/candidates/siglip2-vectorizer.md), [Supabase Edge Functions](../research/candidates/supabase-edge-functions.md), [Supavisor](../research/candidates/supavisor.md), [Superset](../research/candidates/superset.md), [Unmute](../research/candidates/unmute.md), [Verba](../research/candidates/verba.md), [Voicebox](../research/candidates/voicebox.md), and [WhisperX](../research/candidates/whisperx.md).
- Platform/security outside sources: [Authentik OAuth2/OIDC provider docs](https://docs.goauthentik.io/add-secure-apps/providers/oauth2/), [Authentik proxy-provider docs](https://docs.goauthentik.io/add-secure-apps/providers/proxy/), [Infisical introduction](https://infisical.com/docs/documentation/getting-started/introduction), [Infisical self-hosting overview](https://infisical.com/docs/self-hosting/overview), [OpenBao official site](https://openbao.org/), [OpenTelemetry Collector install docs](https://opentelemetry.io/docs/collector/install/), [OpenTelemetry Collector Docker docs](https://opentelemetry.io/docs/collector/install/docker/), [Grafana Tempo docs](https://grafana.com/docs/tempo/latest/), [Trino docs/site](https://trino.io/), [Apache Superset docs/site](https://superset.apache.org/), [Dagster docs](https://docs.dagster.io/), and [Lakekeeper docs](https://docs.lakekeeper.io/).
- 3D/game outside sources: [Blender MCP server page](https://www.blender.org/lab/mcp-server/), [BlenderMCP community repo](https://github.com/ahujasid/blender-mcp), [Unreal Engine MCP docs](https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor?lang=en-US), [Godot docs](https://docs.godotengine.org/), [Godot license](https://godotengine.org/license/), [Hunyuan3D 2 repo](https://github.com/Tencent-Hunyuan/Hunyuan3D-2), [Hunyuan3D 2.1 repo](https://github.com/tencent-hunyuan/hunyuan3d-2.1), [TRELLIS.2 project page](https://microsoft.github.io/TRELLIS.2/), [Nerfstudio docs](https://docs.nerf.studio/), [glTF-Transform CLI docs](https://gltf-transform.dev/cli), and [LiveKit Agents docs](https://docs.livekit.io/agents/).
- Trading/financial-AI outside sources: [OpenBB GitHub repo](https://github.com/OpenBB-finance/OpenBB), [OpenBB docs](https://docs.openbb.co/), [CCXT docs](https://docs.ccxt.com/), [Hummingbot docs](https://hummingbot.org/docs/), [Freqtrade docs](https://www.freqtrade.io/en/stable/), [NautilusTrader docs](https://nautilustrader.io/docs/latest/), [TimescaleDB GitHub repo](https://github.com/timescale/timescaledb), [Redpanda docs](https://docs.redpanda.com/home/), [FinRL repo](https://github.com/AI4Finance-Foundation/FinRL), [FinRL-Trading repo](https://github.com/AI4Finance-Foundation/FinRL-Trading), and [FinGPT repo](https://github.com/ai4finance-foundation/fingpt).
- Current maturity adjustment: Langfuse self-hosting now documents queued ingestion through S3/Redis into ClickHouse, so the older candidate note about avoiding ClickHouse with v2 should be treated as stale for current vNext planning ([Langfuse self-hosting](https://langfuse.com/self-hosting), [Langfuse configuration](https://langfuse.com/self-hosting/configuration)).

### 10.2 Scoring Rubric

- The ranking in section 7 weighted strategic fit, reuse of Atlas primitives, user value, implementation effort, operational cost, security risk, license fit, maintenance burden, dependency blast radius, and upstream maturity.
- Higher scores went to candidates that compound Atlas' existing strengths: Kong-root entrypoint cleanup, SOURCE-friendly service packaging, reuse of Supabase/Redis/MinIO/LiteLLM/Open WebUI, and clear leverage across multiple tracks.
- Lower scores went to novelty-only expansions, services that duplicate already-shipped capability, and candidates that add large operational or security surface before Atlas has a clearer user-facing workflow to justify them.

### 10.3 Rejected / Deferred Candidates

| Candidate(s) | Disposition | Why it stayed out of vNext |
| --- | --- | --- |
| Firecrawl | Deferred | Crawl4AI is the first ingestion bet because it fits the current stack with less license and worker-footprint risk. |
| Browserless | Deferred | Only needed if Crawl4AI leaves important JavaScript-rendered gaps; SSPL plus Chromium cost keeps it out of the base plan. |
| Supabase Edge Functions | Deferred | Atlas already has backend, n8n, and Airflow for the first server-side workflow slices, so this is not the next leverage point. |
| OpenLIT | Deferred | Langfuse and the OTel/Tempo/Loki path cover the near-term observability gap with a clearer product fit. |
| Hummingbot, Freqtrade, NautilusTrader | Rejected for now | The approved trading posture is research and paper workflows first, not live execution services. |
| FinRL, FinGPT | Deferred to notebooks | Useful as research inputs, but not mature enough for a default production trading layer in Atlas. |
| Hunyuan3D, TRELLIS/TRELLIS.2, Nerfstudio, Unreal MCP, LiveKit | Deferred | The 3D/game track should start with asset pipeline and MCP safety before heavier model, engine, or realtime infrastructure bets. |
| Voicebox, OmniVoice, Unmute | Deferred | Voice remains a watchlist area until Atlas has a clearer realtime speech workflow and safer integration story. |
| Honcho | Deferred | LangMem and Graphiti are lighter first memory experiments; Honcho adds more service and license weight than the current plan needs. |
| Redis Stack, RedisInsight | Deferred | Atlas should wait for a concrete Redis-module or GUI workflow that beats the extra image, license, and maintenance cost. |
| Perplexica/Vane | Deferred | It overlaps with Open WebUI plus Local Deep Researcher unless Atlas decides to build a distinct cited-answer surface. |

### 10.4 Watchlist / Already Shipped

| Candidate | Disposition | Note |
| --- | --- | --- |
| imgproxy | Watchlist | Strong follow-on once dashboard and asset-browsing work lands. |
| NocoDB | Watchlist | July 4, 2026 decision keeps it out of the service graph until Atlas has one concrete human-review queue and a credible SSO/route-auth posture; it must not become a second admin database UI. |
| NeoDash | Watchlist | Useful once Atlas has richer graph-native application data to present. |
| WhisperX | Watchlist | Worth revisiting when meeting/audio ingestion becomes a first-class RAG workflow. |
| Dagster | Watchlist | July 4, 2026 decision keeps it out of the service graph until Atlas has one concrete asset-lineage workflow and an explicit Airflow coexistence or migration plan. |
| Trino | Already shipped | Disabled-by-default data-eng SQL query engine over Iceberg REST and MinIO. |
| Redpanda | Already shipped | Disabled-by-default Kafka API broker and console for Spark Structured Streaming in the data-eng track. |
| Superset | Watchlist | July 4, 2026 decision keeps it out of the service graph until Trino/Iceberg or Postgres analytics schemas have meaningful datasets and SSO/route auth is credible. |
| TimescaleDB | Watchlist | Best treated as part of a later trading-data slice, not a standalone platform bet. |
| OpenBao | Watchlist | Stronger Vault-lineage option than Atlas needs in the first secrets-management slice. |
| Lakekeeper | Watchlist | July 4, 2026 decision keeps it out of the service graph until the current Iceberg REST + MinIO path proves real write/concurrency, vended-credential, or catalog-governance pressure. |
| Prometheus | Already shipped / not vNext | Keep as current-state observability baseline; vNext work is the missing traces/logs layer around it, not Prometheus itself. |
