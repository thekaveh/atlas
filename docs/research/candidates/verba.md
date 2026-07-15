---
category-fit: apps
generated: 2026-05-19
license: BSD-3-Clause
name: Verba
referenced-by: [weaviate]
slug: verba
type: external-service
upstream: https://github.com/weaviate/Verba
---

# Verba

## 1. Headline
Weaviate's official open-source RAG chat frontend ("The Golden RAGtriever") — drop-in retrieval UI over an existing Weaviate instance with file-ingest, chunking, retriever, and generator stages exposed as swappable components.

## 2. Problem it solves
The stack ships Weaviate plus a CLIP multimodal module but has no first-class RAG UI that exercises hybrid search, named vectors, or generative modules end-to-end. Open WebUI is general-purpose chat; n8n is workflow automation; neither demonstrates retrieval quality on user-uploaded corpora. Verba consumes an existing Weaviate cluster, lets users drop in PDFs/docs, and produces a generative answer using a configurable LLM — proving the Weaviate + LiteLLM + doc-processor pipeline works without bespoke code.

## 3. Stack wiring sketch
- verba → weaviate via `WEAVIATE_URL=http://weaviate:8080` (REST) and the gRPC port for fast queries.
- verba → litellm via `OPENAI_API_BASE=http://litellm:4000/v1` for the generator stage (LiteLLM exposes OpenAI-compatible chat completions for every provider).
- verba ← doc-processor: pre-processed Docling output can be POSTed into Verba's ingest endpoint, replacing Verba's built-in PDF reader for higher-quality extraction.
- verba ↔ kong via a new alias (`verba.localhost`) following the existing wildcard-routing pattern.

## 4. Effort
medium — One new manifest (`services/verba/service.yml`), Dockerfile-free (Verba ships a published image `semitechnologies/verba`), one Kong alias, env wiring for `WEAVIATE_URL` + LiteLLM. No new datastore. The non-trivial part is mapping Verba's "generator" config to LiteLLM model IDs and confirming Verba's named-vector support against the collections weaviate-init creates.

## 5. Risks & open questions
- Verba authentication: ships with a single-tenant auth model; may need Kong-level basic auth in front for shared deployments.
- Schema collision: Verba creates its own Weaviate collections (`VERBA_Document`, etc.) — fine alongside backend/n8n collections but worth namespacing.
- Active-development pace: Verba's API has changed across minor versions; pin a specific image tag.

## 6. Why now (and why not sooner)
Weaviate already routes its text vectorizer through LiteLLM, so a RAG UI like Verba inherits whatever embedding/generator models are registered with LiteLLM at no extra wiring cost. Adding Verba lights up the existing Weaviate + LiteLLM + (optional) doc-processor chain end-to-end with a UI that ships from the vector-DB vendor itself.

## 7. Upstream evidence
- https://github.com/weaviate/Verba
- https://hub.docker.com/r/semitechnologies/verba
- https://docs.weaviate.io/weaviate/model-providers
