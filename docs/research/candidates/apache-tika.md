---
category-fit: media
generated: 2026-05-19
license: Apache-2.0
name: Apache Tika
referenced-by: [doc-processor]
slug: apache-tika
type: external-service
upstream: https://tika.apache.org/
---

# Apache Tika

## Headline
Battle-tested content-detection and text-extraction server that covers the long-tail formats Docling deliberately does not handle.

## Problem it solves
Docling is excellent for PDFs, Office files, and images but explicitly does not target RTF, ODT, EML, MSG, ZIP archives, or thousands of niche MIME types. n8n workflows and the backend often need a generic "give me text out of any blob" fallback. Today there is no such service — failed Docling calls become user-visible errors.

## Stack wiring sketch
- backend → apache-tika via `http://tika:9998/tika` (PUT body) as the fallback when doc-processor returns 415 / unsupported-format
- n8n → apache-tika via HTTP Request node for email-attachment workflows (EML/MSG dominate inbox automations)
- doc-processor (docling) is unaffected — it remains the primary path; tika is the fallback tier
- kong → apache-tika via `tika.localhost` alias (consistent with other media services)

(Every bullet names a real service in the current topology.)

## Effort
small — Tika ships a stock server image, single port (9998), no auth, no GPU. Atlas should pin the stable 3.x image (`apache/tika:3.3.1.0` in the first implementation) rather than `latest`; Tika 4.x server changes should be evaluated separately before adoption. The core work is the manifest, Kong alias, and the fallback branch in the backend doc endpoint.

## Risks & open questions
- Java footprint: Tika is JVM-based and idle-uses ~300 MB; non-trivial on small dev boxes. Mitigated by making it `disabled` by default.
- Quality gap: Tika's output is plain-text-only — no chunks, no table structure. Consumers must treat its output as a degraded fallback, not a peer.
- Overlap: some formats (HTML, simple PDFs) work in both; need a clear routing rule to avoid ambiguity.
- Security: Tika has had CVEs around malicious documents — pin a recent tag and keep dependabot enabled.

## Why now (and why not sooner)
Until the doc-processor was added in 2026, there was no document-extraction surface at all, so a fallback was meaningless. Now that Docling is the primary path and several consumers (open-webui uploads, n8n email workflows, local-deep-researcher) depend on it, the absence of a fallback is the next bottleneck.

## Upstream evidence
- https://tika.apache.org/ — official project page documents supported formats and the `/tika` REST endpoint.
- https://hub.docker.com/r/apache/tika — official container image with `/tika` and `/meta` endpoints.
