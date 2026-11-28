---
service: weaviate
category: data
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - https://docs.weaviate.io/weaviate/configuration/backups
  - https://docs.weaviate.io/weaviate/configuration/monitoring
  - https://docs.weaviate.io/weaviate/concepts/data
  - https://docs.weaviate.io/weaviate/config-refs/schema/multi-vector
  - https://docs.weaviate.io/weaviate/model-providers
  - https://docs.weaviate.io/weaviate/modules
  - https://github.com/weaviate/Verba
  - services/weaviate/service.yml
  - services/weaviate/compose.yml
  - services/weaviate/init/scripts/init-weaviate.sh
  - services/weaviate/README.md
---

# weaviate — Integration Research

## 1. Missing-pair integrations

- **weaviate ↔ silo**
  - Why valuable: Weaviate currently has no backup strategy; `weaviate-data` is a single local volume. The `backup-s3` module turns Silo into the durable backup target without any new infra.
  - Mechanism sketch: enable `backup-s3` in `ENABLE_MODULES`; set `BACKUP_S3_BUCKET`, `BACKUP_S3_ENDPOINT=silo:9000`, `BACKUP_S3_USE_SSL=false`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` from Silo root creds; trigger via `POST /v1/backups/s3`.
  - Effort: small
  - Risks / open questions: bucket lifecycle/retention policy; restore-on-cold-start tooling.
  - Confidence: high (docs explicitly confirm Silo compatibility for `backup-s3`).

- **weaviate ↔ doc-processor**
  - Why valuable: Closes the RAG loop. Docling already extracts structured text+tables from PDFs; today nothing routes that output into Weaviate, so n8n/backend re-implement chunking ad hoc.
  - Mechanism sketch: n8n flow or a small backend route reads Docling JSON, chunks, then `POST /v1/batch/objects` into a `Document` collection vectorized via `text2vec-openai` (already pointed at LiteLLM).
  - Effort: medium
  - Risks / open questions: chunking strategy; idempotency on re-ingest; multimodal blocks need CLIP path.
  - Confidence: high (both services in stack; vectorizer wiring exists).

- **weaviate ↔ n8n**
  - Why valuable: n8n ships a first-class Weaviate node; workflows can ingest webhook payloads, search, and feed retrieval into the existing AI Agent nodes — currently unused despite both services being co-deployed.
  - Mechanism sketch: n8n Weaviate node → `http://weaviate:8080` (REST) using anonymous-access default; or gRPC on `:50051`.
  - Effort: small
  - Risks / open questions: anonymous access OK in-cluster but should gate via Kong if exposed.
  - Confidence: high (n8n node documented; both services share the backend network).

- **weaviate ↔ hermes**
  - Why valuable: Hermes agent has no long-term memory or retrieval tool. A Weaviate-backed memory skill lets Hermes recall past sessions, store tool outputs, and do semantic lookup over user docs.
  - Mechanism sketch: Hermes custom skill posts/queries via Weaviate Python client to `http://weaviate:8080` with hybrid search; collection seeded by weaviate-init.
  - Effort: medium
  - Risks / open questions: schema design for agent memory; tenant isolation per chat session (multi-tenancy is per-collection).
  - Confidence: medium (Hermes skill system supports custom tools; exact memory schema is open).

- **weaviate ↔ comfyui**
  - Why valuable: ComfyUI generates images but they're write-only artifacts on disk. CLIP-vectorizing them into Weaviate enables similarity search over the user's own generation history ("more like this").
  - Mechanism sketch: ComfyUI custom node or n8n post-execution hook → `POST /v1/objects` to a `Generation` collection vectorized by `multi2vec-clip` (already enabled); image bytes or URL.
  - Effort: medium
  - Risks / open questions: storage of original image (Silo link vs base64); CLIP module must stay enabled.
  - Confidence: medium (CLIP module present; ComfyUI extensibility well-documented).

## 2. Candidate new services

- **Verba** → `../candidates/verba.md`
  - Headline: Weaviate's official RAG chat UI, drop-in over an existing cluster.
  - Other consumers in stack: litellm, doc-processor, kong.

## 3. Per-service feature gaps

- **backup-s3 module** — Why pursue: zero current backup story; Silo is already in-stack. Effort: small.
- **Named vectors** (`vectorConfig` array) — Why pursue: lets one collection carry both a text2vec-openai vector and a multi2vec-clip vector for hybrid text+image search instead of two collections. Effort: medium.
- **Reranker modules** (`reranker-transformers` or `reranker-cohere`) — Why pursue: cheap quality lift on RAG queries; the transformers variant runs in-cluster with no extra API costs. Effort: medium.
- **Multi-tenancy** (per-collection tenant shards) — Why pursue: backend/n8n/Hermes could share one Weaviate cluster with per-user isolation instead of single-tenant anonymous access. Effort: medium.
- **Generative modules** beyond OpenAI/Ollama (e.g. `generative-anthropic`, `generative-cohere`) — Why pursue: LiteLLM already fronts these providers; matching Weaviate's generative module list widens GraphQL-side RAG options. Effort: small.
