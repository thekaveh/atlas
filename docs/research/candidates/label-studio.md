---
category-fit: apps
generated: 2026-05-19
license: Apache-2.0
name: Label Studio
referenced-by: [jupyterhub]
slug: label-studio
type: external-service
upstream: https://github.com/HumanSignal/label-studio
---

# Label Studio

## 1. Headline
A web-based annotation studio for text, image, audio, and document labeling that produces supervised datasets the rest of the stack can consume.

## 2. Problem it solves
The stack has rich generation surfaces (LLM, image, TTS/STT) but no first-class way to *label* outputs for evaluation, fine-tuning, or RAG-curation tasks. Notebooks currently bolt this together with pandas + manual review, which doesn't scale beyond a single user and loses provenance. Label Studio adds a multi-user UI, task queues, and structured export (JSON/COCO/CONLL) consumable by notebooks and Weaviate ingestion.

## 3. Stack wiring sketch
- jupyterhub → label-studio via `LABEL_STUDIO_URL=http://label-studio:8080` and the `label-studio-sdk` Python client (pre-installed in the notebook image) for project creation, task push, and annotation export.
- label-studio → minio via `LABEL_STUDIO_S3_ENDPOINT_URL=http://minio:9000` for storing raw media (images, audio clips, PDFs) so tasks reference S3 URIs, not local disk.
- label-studio → supabase via Postgres backend store (`postgres://...@supabase:5432/label_studio` schema).
- backend → label-studio via REST API to enqueue model-prediction tasks (active learning loop).
- weaviate ingestion pipeline reads exported annotations to upsert labeled vectors.

## 4. Effort
medium — One compose fragment, a Postgres schema, a MinIO bucket, ML-backend SDK integration in backend. The web UI is upstream-maintained; the integration glue (export → notebook → weaviate) is the real cost.

## 5. Risks & open questions
- Auth model: Label Studio has its own user system separate from Supabase Auth — SSO via OAuth would close that gap but is non-trivial.
- License: AGPL for the enterprise edition vs Apache-2.0 for the community core; confirm we're on the Apache build for redistribution.
- Resource footprint: idle Label Studio is heavier than a typical sidecar (~500 MB RAM); should be a `disabled`-by-default source.
- Overlap with Open WebUI's RLHF buttons is partial — Label Studio is more general but heavier.

## 6. Why now (and why not sooner)
Earlier in the stack's life there was no S3-style store for media artifacts and no canonical Postgres for app state — Label Studio would have needed its own infra. With MinIO + Supabase already in place and notebook workflows maturing, the integration shrinks to wiring.

## 7. Upstream evidence
- https://github.com/HumanSignal/label-studio — README confirms Postgres backend, S3-compatible storage, and Python SDK.
- https://labelstud.io/guide/storage.html — S3 cloud-storage docs (endpoint URL + bucket config).
