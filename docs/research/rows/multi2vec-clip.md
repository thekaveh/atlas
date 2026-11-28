---
service: multi2vec-clip
category: data
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - https://github.com/weaviate/multi2vec-clip-inference
  - https://docs.weaviate.io/weaviate/model-providers/transformers/embeddings-multimodal
  - https://docs.weaviate.io/weaviate/modules/multi2vec-clip
  - services/weaviate/service.yml
  - services/weaviate/compose.yml
  - services/multi2vec-clip/README.md
---

# multi2vec-clip — Integration Research

## 1. Missing-pair integrations

- **multi2vec-clip ↔ backend**
  - Why valuable: Backend currently has no direct path to multimodal embeddings; today it can only reach CLIP indirectly by writing through Weaviate. Direct `/vectorize` calls unlock zero-shot image tagging, image-vs-text similarity scoring, and ad-hoc embedding without round-tripping through a Weaviate collection.
  - Mechanism sketch: HTTP `POST http://multi2vec-clip:8080/vectorize` with `{"texts": [...], "images": [b64...]}`; returns `{textVectors, imageVectors}`.
  - Effort: small
  - Risks / open questions: Couples backend to a Weaviate-internal module; if `MULTI2VEC_CLIP_SOURCE=disabled`, backend feature must degrade gracefully.
  - Confidence: high (endpoint confirmed in upstream `app.py`).

- **multi2vec-clip ↔ silo**
  - Why valuable: Silo already hosts artifact buckets (comfyui, backend, n8n, jupyter, docling). None of those image artifacts are indexed for semantic retrieval. A small ingest worker can stream new objects through CLIP into Weaviate, enabling cross-bucket image search.
  - Mechanism sketch: Silo bucket-notification webhook (or polling worker) → fetch object → base64 → `POST /vectorize` → upsert into a `MediaAssets` Weaviate collection vectorized by `multi2vec-clip`.
  - Effort: medium
  - Risks / open questions: Where the worker lives (backend vs. dedicated init) is undecided; Silo event config not currently scripted in `silo-init`.
  - Confidence: medium (Silo events documented; no existing wiring).

- **multi2vec-clip ↔ comfyui**
  - Why valuable: ComfyUI continuously generates images that vanish into Silo/local volumes. Auto-embedding each generation into Weaviate enables prompt-similarity search, dedup, and "find prior renders that look like X" workflows.
  - Mechanism sketch: ComfyUI custom "SaveImage" post-hook → call backend ingest endpoint → backend forwards image bytes to `multi2vec-clip:8080/vectorize` and upserts.
  - Effort: medium
  - Risks / open questions: Requires either a Comfy custom node or an out-of-band watcher; throughput on CPU-only CLIP (`ENABLE_CUDA=0`) may bottleneck batch generations.
  - Confidence: medium.

- **multi2vec-clip ↔ jupyterhub**
  - Why valuable: Notebook users today have to spin up their own CLIP model to experiment with multimodal embeddings; the stack already runs one. Exposing it removes a redundant install and keeps embedding semantics identical to what Weaviate indexes.
  - Mechanism sketch: JupyterHub user pods reach `http://multi2vec-clip:8080/vectorize` over `backend-network`; document a one-cell helper in the notebook starter image.
  - Effort: small
  - Risks / open questions: Network reachability needs verification (Jupyter user pods on `backend-network`).
  - Confidence: high.

- **multi2vec-clip ↔ n8n**
  - Why valuable: n8n workflows that handle inbound email/Slack attachments or webhook-uploaded images can vectorize on-the-fly for routing, classification, or RAG. No CLIP node exists today.
  - Mechanism sketch: n8n HTTP Request node → `POST http://multi2vec-clip:8080/vectorize` → branch on cosine-similarity to label-vectors.
  - Effort: small
  - Risks / open questions: Base64 payload size limits in n8n; large workflow images may need streaming.
  - Confidence: high.

- **multi2vec-clip ↔ doc-processor**
  - Why valuable: doc-processor extracts figures/diagrams from PDFs but currently discards the visual signal. CLIP-embedding extracted figures alongside text chunks enables true multimodal RAG over document corpora.
  - Mechanism sketch: doc-processor post-extraction step → for each figure, base64 → `POST /vectorize` → store vector with the parent chunk metadata in Weaviate.
  - Effort: medium
  - Risks / open questions: doc-processor's chunking schema may need a `figure_vector` field; figure-text alignment heuristics are non-trivial.
  - Confidence: medium.

## 2. Candidate new services

- **SigLIP 2 vectorizer image** → `../candidates/siglip2-vectorizer.md`
  - Headline: Drop-in upgrade of the multi2vec-clip container to a Google SigLIP 2 image for stronger multilingual + higher-resolution multimodal retrieval.
  - Other consumers in stack:
    - weaviate (vectorizer module)
    - backend (direct `/vectorize` calls)
    - jupyterhub (notebook experiments)

## 3. Per-service feature gaps

- **GPU mode (`MULTI2VEC_CLIP_SOURCE=container-gpu`)** — Why pursue: manifest already declares the source variant but no documentation or smoke-test covers it; users on GPU hosts default to CPU. Effort: small.
- **Model variant selection beyond ViT-B-32** — Why pursue: upstream ships SigLIP 2, multilingual XLM-R+ViT, LAION ViT-B-16; we hard-pin `sentence-transformers-clip-ViT-B-32`. Exposing `MULTI2VEC_CLIP_IMAGE` choices in the wizard unlocks multilingual and higher-recall regimes. Effort: small.
- **Multi-field weighted vectors** — Why pursue: CLIP module supports per-field weights (`image_fields` weight 0.9, `text_fields` weight 0.1). No collection in `weaviate-init` exercises this; the capability is invisible to users. Effort: small.
- **`/meta` health surfacing** — Why pursue: container exposes `/meta` with model config; not scraped or shown in the wizard's service-table health column. Effort: small.
- **`trust_remote_code` for custom CLIP variants** — Why pursue: enables loading community models (Qwen3-VL, ColPali) already supported by the upstream loader. Effort: medium (security review needed).
