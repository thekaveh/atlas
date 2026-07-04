---
category-fit: data
generated: 2026-05-19
license: Apache-2.0
name: SigLIP 2 Vectorizer
referenced-by: [multi2vec-clip]
slug: siglip2-vectorizer
type: external-service
upstream: https://github.com/weaviate/multi2vec-clip-inference
---

# SigLIP 2 Vectorizer

## Headline
Expose a safe opt-in path from the pinned `sentence-transformers-clip-ViT-B-32` image to Weaviate's current SigLIP 2 `so400m` multi2vec image (`semitechnologies/multi2vec-clip:google-siglip2-so400m-patch16-512-1.5.1`) for sharper, multilingual, higher-resolution multimodal embeddings while reusing the existing container slot.

## Problem it solves
The current CLIP ViT-B-32 model is English-only, capped at 224x224 image input, and trails newer encoders by a wide margin on retrieval benchmarks. SigLIP 2 (released 2025 by Google) handles 100+ languages, accepts 512x512 inputs, and uses sigmoid pairwise loss for sharper retrieval — directly improving every consumer of Weaviate's `multi2vec-clip` module without changing the API surface. Because the inference container's HTTP contract (`/vectorize`, `/meta`, `/.well-known/ready`) is identical, this is a runtime image swap inside the existing `multi2vec-clip` service slot.

## Stack wiring sketch
- weaviate → siglip2-vectorizer via `CLIP_INFERENCE_API=http://multi2vec-clip:8080` (unchanged)
- backend → siglip2-vectorizer via `POST /vectorize` (same endpoint, same payload)
- jupyterhub → siglip2-vectorizer via `POST /vectorize` for notebook experiments

(Every bullet names a real service in the current topology.)

## Effort
small — add a second opt-in image/env value in `services/weaviate/service.yml`, plus an embedding-dimension audit on existing Weaviate collections. Do not change the default `MULTI2VEC_CLIP_IMAGE`: Weaviate lists the SigLIP 2 `so400m` 512 image as 1152-d, while Atlas's default ViT-B/32 path is documented as 512-d; existing vector spaces are incompatible and require revectorization.

## Risks & open questions
- Vector dimension changes from 512 to 1152 for the current Weaviate-published `google-siglip2-so400m-patch16-512` image. Existing Weaviate collections vectorized with ViT-B/32 cannot be queried with the new model — a recreate or re-index migration path is required.
- Larger image size (224 → 512) increases CPU latency materially; recommend pairing with `MULTI2VEC_CLIP_SOURCE=container-gpu` for production.
- Container image sizes are larger; pull time on first start increases.
- SigLIP 2 license: published by Google under Apache 2.0 but verify the specific Hugging Face weights' license at adoption time.

## Why now (and why not sooner)
SigLIP 2 weights and Weaviate-published inference images shipped in 2025; the upstream `multi2vec-clip-inference` repo and model list now show SigLIP 2 variants as official images. Sooner would have meant building a custom inference container; with the official image now available, Atlas can expose a safe opt-in reference value without changing the default.

## Upstream evidence
- https://github.com/weaviate/multi2vec-clip-inference (inference container source for Weaviate's `multi2vec-clip` module)
- https://hub.docker.com/r/semitechnologies/multi2vec-clip/tags (lists `google-siglip2-so400m-patch16-512-1.5.1`)
- https://docs.weaviate.io/weaviate/model-providers/transformers/embeddings-multimodal (documents `CLIP_INFERENCE_API`, `ENABLE_CUDA`, available model choices, and model-license responsibility)
