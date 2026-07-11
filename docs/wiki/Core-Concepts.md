# Core Concepts

## 1. SOURCE Values

SOURCE variables choose how Atlas obtains a service. Common values include `container`, `localhost`, `disabled`, and service-specific variants such as CPU/GPU modes or cloud-provider enablement.

SOURCE choices are stored in `.env`, surfaced through the setup wizard, and consumed by the bootstrapper when it synthesizes Compose configuration.

Typical SOURCE behavior:

- `container` runs the service inside the Atlas Compose project.
- `localhost` connects Atlas to a host-managed service.
- `disabled` excludes the service from the active Compose graph.
- `none` is used where a local provider is intentionally absent.
- Cloud providers use provider-specific enablement flags and API keys.

## 2. Tracks

Tracks select workflow-oriented groups of services. Out-of-track services are force-disabled unless the user explicitly overrides them with a SOURCE flag.

The track system keeps first launch manageable. A RAG user should not have to answer every data-engineering service prompt, and a data-engineering user should not have to enable creative-AI services.

## 3. Manifests

Each manifest owns service metadata, environment variables, SOURCE choices, dependencies, runtime slices, adaptive-service behavior, and data-flow calls.

Manifest fields feed generated `.env.example`, the docs site, wiki tables, route references, and CI validation.

## 4. Topology

The topology registry defines category, ports, aliases, display names, descriptions, and dependency shape used by the wizard and generated references.

Topology is where service categories, port assignment, and Kong alias visibility become consistent across the UI, docs, and generated routes.

## 5. Gateway Routing

Kong exposes the main local entrypoint and generated service aliases. Direct ports remain available for selected service UIs and APIs.

The root dashboard is the preferred entrypoint for humans. Direct service ports are still documented because they matter for smoke tests, local development, and troubleshooting.

## 6. User Overlays

Atlas starts from `.env.example`, writes or preserves the active `.env`, then merges user-owned overlays before backfilling missing keys and applying CLI flags. The sibling `.env.user` file is useful for local checkout-owned values. `ATLAS_ENV_USER_FILE` points at a parent-owned overlay outside the Atlas checkout and is the preferred submodule-consumer pattern.

Overlay precedence is `.env.example` baseline, generated or existing `.env`, sibling `.env.user`, `ATLAS_ENV_USER_FILE`, then explicit flags such as `--project` and `--<svc>-source`. Both overlays are merged on every start, including `--cold`. Relative `ATLAS_ENV_USER_FILE` values resolve against the directory that invoked `start.sh`.

## 7. Hosted Media Gateway

The backend exposes `POST /media/generate` and `GET /media/operations/{operation_id}` as the provider-neutral hosted media surface. Requests dispatch by `provider`, `modality`, and `model`; the initial registry supports `provider=fal` with `modality=image`.

Provider API keys stay in the backend environment, and responses normalize status, artifacts, cost, license, and provenance for downstream consumers.

## 8. RAG Chunking Gateway

The backend exposes `POST /api/chunk` as the shared Chonkie-powered text-splitting surface for RAG ingestion clients. The endpoint supports token, recursive, and semantic strategies and returns stable character offsets plus strategy metadata so n8n workflows, notebooks, and future ingestion services can share one chunking contract.

JupyterHub also installs Chonkie for exploratory notebook work, including `13_chonkie_chunking.ipynb`. Production workflows should still call the Backend endpoint instead of each service adding its own Chonkie dependency.

## 9. RAG Evaluation Gateway

The backend exposes `POST /api/rag/evaluate` as the shared Ragas-powered quality-evaluation surface for supplied RAG question, answer, context, and optional reference records. The endpoint supports faithfulness, answer relevancy, context precision, and context recall metrics while routing evaluator calls through Atlas LiteLLM configuration.

JupyterHub also installs Ragas for exploratory evaluation work, including `14_ragas_evaluation.ipynb`. Production workflows should call the Backend endpoint so n8n, notebooks, and future ingestion jobs share one metric contract without each service carrying its own evaluator package.

## 10. Adaptive Services

Backend and Open WebUI adapt to whichever upstream services are enabled. This keeps the stack useful when a user chooses a smaller track or disables optional services.

Adaptive behavior prevents broken integrations from appearing when their upstream service is disabled.

## 11. Generated Documentation

Service READMEs remain the service-owned source of truth. The `.io` site and wiki are generated publishing layers that keep navigation, tables, and references synchronized.

The docs generator reads the same model used by tests, so docs drift becomes visible before merge.

## 12. Init Companions

Some services use init containers or first-run scaffolding for schema setup, bucket creation, workflow import, model pulls, or catalog bootstrapping.

Init companions should be documented with the service they prepare and represented in dependency/topology notes when they affect startup order.

## 13. Service Categories

Service categories describe the role of the service family in Atlas. They also influence wizard grouping, generated references, and visual grouping in the docs.

Current categories include infra, data, llm, media, agents, apps, and aggregate/doc-only surfaces.

## 14. Model Capability Contract

Atlas catalog entries can carry `metadata_version: 1` metadata so adapter selection and downstream model assignment do not depend on model-name guesses. The contract is provider-neutral and travels with each typed catalog entry through resolution into LiteLLM `model_info`.

### 14.1 Fields

| Field | Purpose |
|---|---|
| `kind` | Distinguishes `chat` from `embedding` models. |
| `adapter` | Selects the LiteLLM provider adapter, including `ollama_chat` and `ollama`. |
| `capabilities` | Declares chat, embedding, tools, vision, reasoning, and structured-output support. |
| `request_defaults` | Applies model-specific defaults such as `think: false`; defaults are never imposed on every chat model. |
| `recommended_roles` | Recommends consumer roles: `extract`, `keyword`, `query`, `judge`, `embedding`, and `vision`. |
| `dim` | Records the output dimension required for embedding compatibility checks. |

### 14.2 Resolution And Compatibility

Curated metadata is authoritative. Metadata-free custom or live-discovered models remain compatible through a conservative, visibly warned fallback heuristic. Embedding entries require `dim`, cannot carry chat request defaults, and are emitted with LiteLLM `mode: embedding` plus `output_vector_size`.

LightRAG and other consumers can inspect the namespaced `atlas_model_metadata` block to map models to roles such as `extract` and `query` without hard-coding a provider, model family, or hardware assumption.

Retrieve the detailed records from authenticated `GET /v1/model/info`; the compatibility-oriented `GET /v1/models` response does not expose the complete `model_info` payload. Select a role deterministically by filtering for `inferred: false`, the required `kind` or capability, and a matching `recommended_roles` value. Apply an explicit operator preference when configured. Otherwise use lexical `(provider, catalog_name, model_name)` order as a provider-neutral fallback. For Ollama's dual aliases, deduplicate rows by `(provider, catalog_name)` and retain the operator's preferred alias.
