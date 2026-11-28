---
service: doc-processor
category: media
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - https://github.com/DS4SD/docling
  - https://github.com/docling-project/docling-serve
  - https://github.com/docling-project/docling-mcp
  - https://github.com/docling-project/docling/blob/main/docs/integrations/index.md
  - https://github.com/docling-project/docling/tree/main/docs/examples
  - services/docling/service.yml
  - services/doc-processor/README.md
---

# doc-processor — Integration Research

## 1. Missing-pair integrations

- **doc-processor ↔ weaviate**
  - Why valuable: Docling already emits structure-aware chunks with rich metadata (page, section, chunk_type). Weaviate is the stack's vector store. Closing this loop turns "convert document" into a single RAG-ingest call instead of a per-consumer reimplementation.
  - Mechanism sketch: post-convert callback writes chunks to `http://weaviate:8080/v1/objects` (REST) or via the `weaviate-client` SDK; class auto-created from chunk metadata schema. Upstream ships `rag_weaviate.ipynb` showing the exact pattern.
  - Effort: medium
  - Risks / open questions: embedding model selection (multi2vec-clip vs LiteLLM-hosted embedder); idempotency on re-ingest; large-doc memory.
  - Confidence: high (Docling docs ship a Weaviate RAG example — see sources).

- **doc-processor ↔ silo**
  - Why valuable: today every consumer POSTs the raw file over HTTP; processed markdown/JSON is discarded. Silo already exists for artifacts; persisting `(source_hash → converted output)` removes re-processing cost (1–8 s/page) and lets n8n / backend reference results by S3 key.
  - Mechanism sketch: Docling service mounts the Silo S3 endpoint; on convert, write `s3://docling-cache/<sha256>.json` (DocTags) via boto3; subsequent requests with same hash short-circuit.
  - Effort: medium
  - Risks / open questions: cache invalidation when processing options change; bucket lifecycle policy.
  - Confidence: medium (mechanism standard; no upstream reference impl).

- **doc-processor ↔ n8n**
  - Why valuable: README already documents "use HTTP Request node → docling-gpu:8000" but there is no shipped workflow or credential. A first-party n8n workflow (PDF → markdown → vector store) would let users wire RAG in two clicks.
  - Mechanism sketch: ship a workflow JSON under `services/n8n/init/workflows/docling-rag.json`; uses HTTP Request → `POST http://docling-gpu:8000/v1/document/convert` with chunking enabled, then Weaviate node.
  - Effort: small
  - Risks / open questions: file upload size limits inside n8n; binary handling in multipart node.
  - Confidence: high (README explicitly invites this; n8n init container already supports workflow seeding).

- **doc-processor ↔ hermes**
  - Why valuable: Hermes agents currently lack a "read this document" tool. Docling's MCP server exposes convert/extract directly to MCP-capable runtimes — Hermes can call it without bespoke HTTP plumbing.
  - Mechanism sketch: run `docling-mcp` (see candidate) as a streamable-HTTP MCP endpoint; register as a Hermes custom provider tool.
  - Effort: medium
  - Risks / open questions: MCP transport choice (SSE vs streamable-HTTP); auth surface.
  - Confidence: medium (docling-mcp upstream supports streamable-HTTP + remote-to-docling-serve mode).

- **doc-processor ↔ neo4j**
  - Why valuable: Docling preserves section hierarchy, tables, formulas and figure links — perfect for a document → entity graph pipeline feeding Neo4j. Today neo4j has no ingest path.
  - Mechanism sketch: post-conversion DocTags → entity/relation extraction (LLM via litellm) → Cypher batch into `bolt://neo4j-graph-db:7687`.
  - Effort: large
  - Risks / open questions: ontology choice; extraction prompt drift; cost.
  - Confidence: medium (Docling's structured-info-extraction is beta; LLM step adds variance).

- **doc-processor ↔ redis**
  - Why valuable: convert is slow (~1–8 s/page) and frequently re-requested by open-webui / local-deep-researcher. Redis is already the stack's cache.
  - Mechanism sketch: response cache keyed on `sha256(file)+options`, TTL 24 h, stored in `redis://redis:6379/2`.
  - Effort: small
  - Risks / open questions: value size (use compressed JSON or pointer-to-Silo for >512 KB).
  - Confidence: medium.

## 2. Candidate new services

- **Docling MCP Server** → `../candidates/docling-mcp.md`
  - Headline: First-party MCP wrapper exposing Docling convert/extract tools to agent runtimes.
  - Other consumers in stack: hermes, openclaw, backend

- **Apache Tika** → `../candidates/apache-tika.md`
  - Headline: Fallback extractor for legacy/exotic formats Docling does not cover (RTF, ODT, EML, MSG, ZIP archives).
  - Other consumers in stack: n8n, backend

## 3. Per-service feature gaps

- **Audio/ASR pipeline** — Why pursue: Docling natively parses WAV/MP3/WebVTT to DoclingDocument. We currently route audio only through stt-provider; Docling adds document-style structure (timestamps + sections). Effort: medium.
- **HybridChunker (tokenizer-aware)** — Why pursue: replaces the current naive `chunk_size`/`chunk_overlap` knobs with embedding-model-aware boundaries, materially improving RAG recall. Effort: small.
- **DocTags lossless output** — Why pursue: enables round-trip editing and full-fidelity caching; currently we only consume markdown. Effort: small.
- **VLM pipeline (GraniteDocling 258M)** — Why pursue: better layout + chart understanding than the default DocLayNet/TableFormer pair, at low VRAM cost. Effort: medium.
- **Structured information extraction (beta)** — Why pursue: enables doc → entities/relations without a separate LLM step, feeding the proposed Neo4j integration. Effort: large.
