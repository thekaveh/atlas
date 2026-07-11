# LiteLLM gateway (LLM router)

## 1. Overview

`litellm` is an Atlas service family in the `llm` category. Its implementation and service-owned documentation live under `services/litellm/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `llm`
- Kind: `container`
- Tracks: `all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading`

## 4. Access

- Kong aliases: `litellm.localhost`
- Port variables: `LITELLM_PORT`

## 5. Configuration

- SOURCE variables: `LITELLM_SOURCE`
- Default SOURCE values: `container`
- Available SOURCE values: `container`

## 6. Dependencies And Topology

- Required dependencies: `supabase, redis`
- Optional dependencies: `-`
- Runtime calls: `supabase, redis, ollama, cloud-providers, hermes, lightrag, vllm-metal, otel-collector`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| LITELLM_SOURCE | container | container |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `supabase, redis, ollama, cloud-providers, hermes, lightrag, vllm-metal, otel-collector`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/litellm/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/litellm/architecture.svg)
- Diagram HTML: [`services/litellm/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/litellm/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/litellm/README.md](https://github.com/thekaveh/atlas/blob/main/services/litellm/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)

## 12. Model Capability Contract

Atlas catalog entries can carry `metadata_version: 1` metadata so adapter selection and downstream model assignment do not depend on model-name guesses. The contract is provider-neutral and travels with each typed catalog entry through resolution into LiteLLM `model_info`.

### 12.1 Fields

| Field | Purpose |
|---|---|
| `kind` | Distinguishes `chat` from `embedding` models. |
| `adapter` | Selects the LiteLLM provider adapter, including `ollama_chat` and `ollama`. |
| `capabilities` | Declares chat, embedding, tools, vision, reasoning, and structured-output support. |
| `request_defaults` | Applies model-specific defaults such as `think: false`; defaults are never imposed on every chat model. |
| `recommended_roles` | Recommends consumer roles: `extract`, `keyword`, `query`, `judge`, `embedding`, and `vision`. |
| `dim` | Records the output dimension required for embedding compatibility checks. |

### 12.2 Resolution And Compatibility

Curated metadata is authoritative. Metadata-free custom or live-discovered models remain compatible through a conservative, visibly warned fallback heuristic. Embedding entries require `dim`, cannot carry chat request defaults, and are emitted with LiteLLM `mode: embedding` plus `output_vector_size`.

LightRAG and other consumers can inspect the namespaced `atlas_model_metadata` block to map models to roles such as `extract` and `query` without hard-coding a provider, model family, or hardware assumption.

Retrieve the detailed records from authenticated `GET /v1/model/info`; the compatibility-oriented `GET /v1/models` response does not expose the complete `model_info` payload. Select a role deterministically by filtering for `inferred: false`, the required `kind` or capability, and a matching `recommended_roles` value. Apply an explicit operator preference when configured. Otherwise use lexical `(provider, catalog_name, model_name)` order as a provider-neutral fallback. For Ollama's dual aliases, deduplicate rows by `(provider, catalog_name)` and retain the operator's preferred alias.
