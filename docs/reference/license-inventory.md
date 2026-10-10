# 10.7. Supply-Chain License Inventory

<!-- Generated from docs/reference/license-inventory.yaml by scripts/docs/canonical_references.py. Edit the YAML, not this page. -->

Atlas is licensed under Apache 2.0, and that grant relicenses none of the container images, dependencies or model weights it pins. This inventory records, for every image Atlas pins and every model weight a catalogue downloads, its license, the notices it requires, and what its terms allow for each way it can be used. It is a review record, not a legal conclusion: an open item is a question nobody has adjudicated, and nothing here is marked compatible by assumption.

Reviewed 2026-09-29: 85 images and 20 models, 48 of them with at least one open item.

## 1. Reading the inventory

Each artifact is judged in three modes, because many licenses treat them differently:

- **Hosted use**: operating the software for your own or other people's users.
- **Source integration**: incorporating or modifying its source in another work.
- **Redistribution**: shipping the artifact itself, for example in an appliance bundle.

| Value | Meaning |
|---|---|
| permitted | No condition beyond keeping the license with the software. |
| with notices | Ship the license text and the upstream NOTICE file, where there is one. |
| with source | Copyleft: offer the complete corresponding source. |
| conditional | The terms restrict this mode; the license's required notices say how. |
| unresolved | Not adjudicated; one of the row's open items names the question. |

License links point at the license file in the upstream repository at the exact revision the pinned version was built from, so a change of terms shows up as a different file rather than a silent edit. Redistribution values describe the upstream project's own terms. Shipping an image also ships its operating-system packages; that notice set is open item R-BASE and applies to every image.

## 2. Open review items

| Item | Question | Applies to | Settles |
|---|---|---|---|
| R-BASE | Which license texts and source offers does each image's operating-system layer need? Every image also ships its base distribution's packages (Debian, Ubuntu or Alpine), several under GPL-2.0 or LGPL. A notice bundle generated from an SBOM of each exact digest is required before any image ships in an appliance bundle. | The redistribution mode of every image row. | redistribution |
| R-PY | Which licenses apply to the Python packages installed into the locally built images? The compiled runtime locks (for example services/backend/app/app/requirements-locked.txt) pin every package, but their licenses are not inventoried. | Every locally built image. | redistribution |
| R-BUILD | Which licenses apply to binaries a local Dockerfile downloads or copies on top of its base image? Known cases are the headless Blender that asset-baker adds, which has no image row of its own, and the MinIO mc client that the backup and Jenkins images copy from the digest-pinned pgsty/mc image (its minio MINIO_INIT_IMAGE row records AGPL-3.0); what that license asks of those images when redistributed is not settled. | asset-baker, backup, jenkins. | redistribution |
| R-CUDA | Do the NVIDIA CUDA and cuDNN runtime layers in the GPU images fall within the components their license agreements allow to be redistributed, and on what conditions? | Every CUDA-based image. | redistribution |
| R-NGC | What does the NVIDIA Deep Learning Container License allow for this image? NVIDIA publishes the terms without a revision-pinned copy, so no mode is recorded as permitted. | parakeet. | hosted, integration, redistribution |
| R-N8N | Can an Atlas bundle that includes n8n be distributed, or offered to others, commercially? The Sustainable Use License allows distribution only free of charge for non-commercial purposes. | n8n. | redistribution |
| R-REDPANDA | Can an Atlas bundle that includes Redpanda be redistributed commercially, and does any operator's use amount to a Streaming or Queuing Service for third parties? | redpanda. | redistribution |
| R-OPENWEBUI | Is dyrnq/open-webui an unmodified rebuild of Open WebUI v0.6.32, and would a bundle keep the Open WebUI branding the license requires above 50 end users? | open-webui. | redistribution |
| R-FLOAT | Which upstream revision does the pinned digest build from? The tag floats (latest, lts, gpu, a major version, a date or no tag), so the license is recorded at the upstream default branch or newest matching release on the review date rather than at the image's own source revision. | Images pinned by a floating tag. | context only |
| R-TRUEFORGE | What license applies to TrueForge? The image comes from the TrueFoundry registry, which the review environment could not reach, and no public license file was found. | trueforge. | hosted, integration, redistribution |
| R-MODELS | What are the weight licenses of the catalogue entries that record none? Record license_name, a revision-pinned license_url and license_restrictions for each in services/comfyui/models.yaml; the model cards were not reachable from the review environment. This also covers the CLIP ViT-B-32 weights the multi2vec-clip image bundles. | ComfyUI catalogue entries without license fields; multi2vec-clip. | hosted, integration, redistribution |
| R-KREA | Can Krea 2 weights ship in a bundle, and on what terms? The Krea 2 Community License requires an enterprise license at or above USD 1M annual revenue for commercial use and content filtering for deployments; its redistribution terms are not recorded, and krea2-identity-edit-v1-2's license URL points at main rather than a revision. | Krea 2 catalogue entries. | redistribution |
| R-HUNYUAN | May Hunyuan3D-2 weights ship in a bundle offered in the European Union, the United Kingdom or South Korea, which the Tencent Hunyuan Community License excludes, or to a service above 100 million monthly active users? | hunyuan3d-2. | redistribution |
| R-SVC-MODELS | Which licenses apply to the weights services download by their own configuration rather than from a catalogue? Examples are the TEI reranker model (TEI_RERANKER_MODEL_ID, pinned to mixedbread-ai/mxbai-rerank-base-v1 at a revision) and the speech and document-conversion models speaches, parakeet, chatterbox and docling fetch; none has a row yet. | Service-configured model weights. | hosted, integration, redistribution |
| R-OLLAMA | Which licenses apply to the Ollama library models, and which revision is pulled? Tags such as qwen3.8:latest float, and ollama.com was not reachable from the review environment. | Every Ollama catalogue model. | hosted, integration, redistribution |
| R-BUNDLE | Which licenses apply to the extra files a bundle entry downloads besides its main weights? The Krea 2 entries also fetch a Qwen3-VL text encoder and a Qwen-Image VAE, which the Krea 2 Community License does not cover by name. | ComfyUI entries that download more than one file. | redistribution |

## 3. Licenses and required notices

| License | Hosted use | Source integration | Redistribution | Required notices |
|---|---|---|---|---|
| Apache License 2.0 (`Apache-2.0`) | permitted | permitted | with notices | Ship the license text and, where the upstream has one, its NOTICE file; mark modified files. |
| MIT License (`MIT`) | permitted | permitted | with notices | Ship the copyright notice and license text. |
| BSD 3-Clause License (`BSD-3-Clause`) | permitted | permitted | with notices | Ship the copyright notice, conditions and disclaimer; do not use contributors' names to endorse. |
| Python Software Foundation License 2.0 (`PSF-2.0`) | permitted | permitted | with notices | Ship the PSF copyright notice and license agreement. |
| PostgreSQL License (`PostgreSQL`) | permitted | permitted | with notices | Ship the copyright notice and the permission paragraphs. |
| GNU Affero General Public License v3.0 (`AGPL-3.0`) | permitted | with source | with source | Ship the license and offer the complete corresponding source; a modified version run as a network service must offer its source to its users (section 13). |
| GNU General Public License v3.0 (`GPL-3.0`) | permitted | with source | with source | Ship the license and offer the complete corresponding source with every conveyed copy. |
| Sustainable Use License 1.0 (n8n) (`Sustainable-Use-1.0`) | conditional | conditional | unresolved | Use or modify only for internal business, non-commercial or personal purposes; distribute only free of charge for non-commercial purposes; keep every licensing notice. Files marked .ee. need an n8n Enterprise License. |
| Business Source License 1.1 with Redpanda's Additional Use Grant (`BSL-1.1-Redpanda`) | conditional | conditional | unresolved | Any use except a commercial Streaming or Queuing Service offered to third parties; each release converts to Apache 2.0 four years after it ships. Enterprise features fall under the Redpanda Community License. |
| Open WebUI License (BSD 3-Clause with a branding clause) (`Open-WebUI`) | conditional | conditional | conditional | Keep the BSD-3 notices and every "Open WebUI" branding element, unless the deployment has at most 50 end users in any rolling 30 days, a merged contributor has written permission, or an enterprise license allows otherwise. |
| Alpine Linux package set (build scripts MIT) (`Alpine-packages`) | permitted | permitted | unresolved | The image is the Alpine base layer itself; its packages carry their own licenses, recorded under R-BASE. |
| NVIDIA Deep Learning Container License (`NVIDIA-DLC`) | unresolved | unresolved | unresolved | Not reviewed; see R-NGC. |
| Not established (`unknown`) | unresolved | unresolved | unresolved | No license could be read; see the row's open item. |

## 4. Container images

One row per image in a service manifest's `images:` list. Locally built images appear as the base image their Dockerfile builds on; what the build installs on top is open items R-PY and R-BUILD. A row overrides its license's mode values only where its own terms differ.

| Service · variable | Image | Upstream | License | Hosted use | Source integration | Redistribution | Open items |
|---|---|---|---|---|---|---|---|
| `airflow` · `AIRFLOW_IMAGE` | `apache/airflow:3.3.2` | Apache Airflow 3.3.2 | [Apache-2.0](https://github.com/apache/airflow/blob/aa19d2dbb9ed187ec01957a92848d9b005e9ba9d/LICENSE) · [NOTICE](https://github.com/apache/airflow/blob/aa19d2dbb9ed187ec01957a92848d9b005e9ba9d/NOTICE) | permitted | permitted | with notices | — |
| `asset-baker` · `ASSET_BAKER_IMAGE` | `python:3.12.9-slim` | Python 3.12.9 (build base) | [PSF-2.0](https://github.com/python/cpython/blob/fdb81425a9ad683f8c24bf5cbedc9b96baf00cd2/LICENSE) | permitted | permitted | unresolved | R-BUILD |
| `asset-worker` · `ASSET_WORKER_IMAGE` | `python:3.12.9-slim` | Python 3.12.9 (build base) | [PSF-2.0](https://github.com/python/cpython/blob/fdb81425a9ad683f8c24bf5cbedc9b96baf00cd2/LICENSE) | permitted | permitted | with notices | — |
| `backend` · `BACKEND_IMAGE` | `python:3.12.14-bookworm@sha256:581429e3df12…` | Python 3.12.14 (build base) | [PSF-2.0](https://github.com/python/cpython/blob/2abcf904b8dac8c999d2b3aac76681abb333798a/LICENSE) | permitted | permitted | with notices | — |
| `backup` · `BACKUP_IMAGE` | `postgres:17.11-alpine@sha256:b0f9560a2de0…` | PostgreSQL 17.11 (build base) | [PostgreSQL](https://github.com/postgres/postgres/blob/083ac033419f690758508e08c1736089384bbee8/COPYRIGHT) | permitted | permitted | unresolved | R-BUILD |
| `celery` · `FLOWER_IMAGE` | `mher/flower:2.0.1` | Flower 2.0.1 | [BSD-3-Clause](https://github.com/mher/flower/blob/cf39575472e84648aa2d00c73826a60b1f6d828e/LICENSE) | permitted | permitted | with notices | — |
| `chatterbox` · `CHATTERBOX_IMAGE` | `travisvn/chatterbox-tts-api:gpu@sha256:8c0b379172d4…` | Chatterbox TTS API (travisvn), floating gpu tag pinned by digest | [AGPL-3.0](https://github.com/travisvn/chatterbox-tts-api/blob/a5f466128e4baa8e4cceb3bba9b7ca9de6f7ec6b/LICENSE) | permitted | with source | unresolved | R-CUDA, R-FLOAT |
| `cloudflared` · `CLOUDFLARED_IMAGE` | `cloudflare/cloudflared:2026.6.1` | cloudflared 2026.6.1 | [Apache-2.0](https://github.com/cloudflare/cloudflared/blob/81a53555aa827fca88605d7e67ad5c03cda468d2/LICENSE) | permitted | permitted | with notices | — |
| `comfyui` · `COMFYUI_IMAGE` | `ghcr.io/ai-dock/comfyui:v2-cpu-22.04-v0.2.7` | ComfyUI v0.2.7 in the ai-dock image (ai-dock wrapper: MIT) | [GPL-3.0](https://github.com/comfyanonymous/ComfyUI/blob/696672905fd17af2654ced11e3ab590d6a555996/LICENSE) | permitted | with source | with source | — |
| `comfyui` · `COMFYUI_INIT_IMAGE` | `alpine:3.24.2@sha256:294b683cb724…` | Alpine Linux 3.24.2 (packages under their own licenses) | [Alpine-packages](https://github.com/alpinelinux/docker-alpine/blob/2c36a79aaaec3582d3b1961c9245b1a8a5a144dc/LICENSE) | permitted | permitted | unresolved | R-BASE |
| `crawl4ai` · `CRAWL4AI_IMAGE` | `unclecode/crawl4ai:0.9.0` | Crawl4AI 0.9.0 | [Apache-2.0](https://github.com/unclecode/crawl4ai/blob/c66f3276fd355031c8632500911fe7041ad6fc14/LICENSE) | permitted | permitted | with notices | — |
| `docling` · `DOCLING_ADAPTER_IMAGE` | `python:3.12.14-slim` | Python 3.12.14 (build base) | [PSF-2.0](https://github.com/python/cpython/blob/2abcf904b8dac8c999d2b3aac76681abb333798a/LICENSE) | permitted | permitted | with notices | — |
| `docling` · `DOCLING_GPU_IMAGE` | `pytorch/pytorch:2.13.0-cuda12.6-cudnn9-runtime@sha256:6acf597eeb8e…` | PyTorch 2.13.0 with CUDA 12.6 and cuDNN 9 (build base) | [BSD-3-Clause](https://github.com/pytorch/pytorch/blob/cf30153c4c131c8164ee7798e5022d810682e2cb/LICENSE) · [NOTICE](https://github.com/pytorch/pytorch/blob/cf30153c4c131c8164ee7798e5022d810682e2cb/NOTICE) | permitted | permitted | unresolved | R-CUDA |
| `grafana` · `GRAFANA_IMAGE` | `grafana/grafana:11.4.3` | Grafana 11.4.3 | [AGPL-3.0](https://github.com/grafana/grafana/blob/d620e09f8cb4994d15e046d2c373e23f34196abe/LICENSE) · [NOTICE](https://github.com/grafana/grafana/blob/d620e09f8cb4994d15e046d2c373e23f34196abe/NOTICE.md) | permitted | with source | with source | — |
| `hermes` · `HERMES_IMAGE` | `nousresearch/hermes-agent:v2026.6.19` | Hermes Agent v2026.6.19 | [MIT](https://github.com/NousResearch/hermes-agent/blob/2bd1977d8fad185c9b4be47884f7e87f1add0ce3/LICENSE) | permitted | permitted | with notices | — |
| `hermes` · `HERMES_INIT_IMAGE` | `alpine:3.24.2@sha256:294b683cb724…` | Alpine Linux 3.24.2 (packages under their own licenses) | [Alpine-packages](https://github.com/alpinelinux/docker-alpine/blob/2c36a79aaaec3582d3b1961c9245b1a8a5a144dc/LICENSE) | permitted | permitted | unresolved | R-BASE |
| `iceberg-rest` · `ICEBERG_REST_IMAGE` | `apache/iceberg-rest-fixture:1.10.1` | Apache Iceberg REST fixture 1.10.1 | [Apache-2.0](https://github.com/apache/iceberg/blob/ccb8bc435062171e64bc8b7e5f56e6aed9c5b934/LICENSE) · [NOTICE](https://github.com/apache/iceberg/blob/ccb8bc435062171e64bc8b7e5f56e6aed9c5b934/NOTICE) | permitted | permitted | with notices | — |
| `iceberg-rest` · `ICEBERG_REST_INIT_IMAGE` | `postgres:15.19-alpine` | PostgreSQL 15.19 client | [PostgreSQL](https://github.com/postgres/postgres/blob/2ff1375b5dd8bf09d8cb0e795974528180fd75ca/COPYRIGHT) | permitted | permitted | with notices | — |
| `jenkins` · `JENKINS_IMAGE` | `jenkins/jenkins:lts-jdk21@sha256:c1e4c349365f…` | Jenkins LTS (JDK 21), floating lts-jdk21 tag pinned by digest | [MIT](https://github.com/jenkinsci/jenkins/blob/2b42c2ddaf112f8bc6d0f2a1227417208f716592/LICENSE.txt) | permitted | permitted | unresolved | R-FLOAT, R-BUILD |
| `jupyterhub` · `JUPYTERHUB_IMAGE` | `quay.io/jupyter/datascience-notebook:2026-08-24@sha256:e5029672ab8a…` | Jupyter Docker Stacks datascience-notebook 2026-08-24 | [BSD-3-Clause](https://github.com/jupyter/docker-stacks/blob/ac62373f9f60a6b4aa68735e76d0feb2eadf7bd1/LICENSE.md) | permitted | permitted | with notices | R-FLOAT |
| `kong` · `KONG_API_GATEWAY_IMAGE` | `kong:3.9.3` | Kong Gateway OSS 3.9.3 | [Apache-2.0](https://github.com/Kong/kong/blob/a643428bc4d5397152164a63bcc0f8bc65fce69d/LICENSE) | permitted | permitted | with notices | — |
| `label-studio` · `LABEL_STUDIO_IMAGE` | `heartexlabs/label-studio:1.23.0` | Label Studio 1.23.0 | [Apache-2.0](https://github.com/HumanSignal/label-studio/blob/2a9bfbcbf0a844b999de97e601d16050a893f5fb/LICENSE) · [NOTICE](https://github.com/HumanSignal/label-studio/blob/2a9bfbcbf0a844b999de97e601d16050a893f5fb/NOTICE) | permitted | permitted | with notices | — |
| `label-studio` · `LABEL_STUDIO_INIT_IMAGE` | `alpine:3.24.2` | Alpine Linux 3.24.2 (packages under their own licenses) | [Alpine-packages](https://github.com/alpinelinux/docker-alpine/blob/2c36a79aaaec3582d3b1961c9245b1a8a5a144dc/LICENSE) | permitted | permitted | unresolved | R-BASE |
| `langfuse` · `LANGFUSE_CLICKHOUSE_IMAGE` | `clickhouse/clickhouse-server:25.8.33.6` | ClickHouse 25.8.33.6 LTS | [Apache-2.0](https://github.com/ClickHouse/ClickHouse/blob/65eaf18b4a939f3e02aad50b7fbb2711b09495a0/LICENSE) | permitted | permitted | with notices | — |
| `langfuse` · `LANGFUSE_IMAGE` | `langfuse/langfuse:3.115.0` | Langfuse 3.115.0 (ee/ directories under ee/LICENSE) | [MIT](https://github.com/langfuse/langfuse/blob/506d6706258ed91dcb3c8ffa3bfcffb0ab3e675e/LICENSE) | permitted | permitted | with notices | — |
| `langfuse` · `LANGFUSE_INIT_IMAGE` | `alpine:3.24.2` | Alpine Linux 3.24.2 (packages under their own licenses) | [Alpine-packages](https://github.com/alpinelinux/docker-alpine/blob/2c36a79aaaec3582d3b1961c9245b1a8a5a144dc/LICENSE) | permitted | permitted | unresolved | R-BASE |
| `langfuse` · `LANGFUSE_WORKER_IMAGE` | `langfuse/langfuse-worker:3.115.0` | Langfuse worker 3.115.0 (ee/ directories under ee/LICENSE) | [MIT](https://github.com/langfuse/langfuse/blob/506d6706258ed91dcb3c8ffa3bfcffb0ab3e675e/LICENSE) | permitted | permitted | with notices | — |
| `lightrag` · `LIGHTRAG_IMAGE` | `ghcr.io/hkuds/lightrag:v1.5.4` | LightRAG v1.5.4 | [MIT](https://github.com/HKUDS/LightRAG/blob/9a45b64c2ee25b1d806e90db926a8af37480bb16/LICENSE) | permitted | permitted | with notices | — |
| `lightrag` · `LIGHTRAG_INIT_IMAGE` | `alpine:3.24.2@sha256:294b683cb724…` | Alpine Linux 3.24.2 (packages under their own licenses) | [Alpine-packages](https://github.com/alpinelinux/docker-alpine/blob/2c36a79aaaec3582d3b1961c9245b1a8a5a144dc/LICENSE) | permitted | permitted | unresolved | R-BASE |
| `litellm` · `LITELLM_IMAGE` | `ghcr.io/berriai/litellm:v1.83.14-stable.patch.2` | LiteLLM v1.83.14-stable.patch.2 (enterprise/ directory under enterprise/LICENSE) | [MIT](https://github.com/BerriAI/litellm/blob/b36fb1dc191620e8ff2b3a43854c05f5189cd813/LICENSE) | permitted | permitted | with notices | — |
| `local-deep-researcher` · `LOCAL_DEEP_RESEARCHER_IMAGE` | `python:3.11.15-slim` | Python 3.11.15 (build base) | [PSF-2.0](https://github.com/python/cpython/blob/2340a037f7450e70fccfe411e6531afb4d57a312/LICENSE) | permitted | permitted | with notices | — |
| `loki` · `LOKI_IMAGE` | `grafana/loki:3.7.0` | Grafana Loki 3.7.0 | [AGPL-3.0](https://github.com/grafana/loki/blob/3361de24b692875d77bd7433cd6baa7c68dc0ef9/LICENSE) | permitted | with source | with source | — |
| `mcp-servers` · `MCP_SERVERS_IMAGE` | `python:3.12.14-slim` | Python 3.12.14 (build base) | [PSF-2.0](https://github.com/python/cpython/blob/2abcf904b8dac8c999d2b3aac76681abb333798a/LICENSE) | permitted | permitted | with notices | — |
| `minio` · `MINIO_IMAGE` | `pgsty/silo:RELEASE.2026-09-16T00-00-00Z@sha256:635197cb9f36…` | Pigsty Silo (MinIO fork) RELEASE.2026-09-16T00-00-00Z | [AGPL-3.0](https://github.com/pgsty/silo/blob/2a4d51406b7ed87af5fe6fe0f801f3290f96eb3c/LICENSE) · [NOTICE](https://github.com/pgsty/silo/blob/2a4d51406b7ed87af5fe6fe0f801f3290f96eb3c/NOTICE) | permitted | with source | with source | — |
| `minio` · `MINIO_INIT_IMAGE` | `pgsty/mc:RELEASE.2026-09-16T00-00-00Z@sha256:cfc83108c3ab…` | Pigsty mc (MinIO client fork) RELEASE.2026-09-16T00-00-00Z | [AGPL-3.0](https://github.com/pgsty/mc/blob/e952aa78f10a2b77dd525a2b7e3143bcda0cd377/LICENSE) · [NOTICE](https://github.com/pgsty/mc/blob/e952aa78f10a2b77dd525a2b7e3143bcda0cd377/NOTICE) | permitted | with source | with source | — |
| `mlflow` · `MLFLOW_IMAGE` | `ghcr.io/mlflow/mlflow:v3.16.1` | MLflow v3.16.1 | [Apache-2.0](https://github.com/mlflow/mlflow/blob/32792afe5b0183fce10532d3a023f5cfa8612d09/LICENSE.txt) | permitted | permitted | with notices | — |
| `mlflow` · `MLFLOW_INIT_IMAGE` | `alpine:3.24.2` | Alpine Linux 3.24.2 (packages under their own licenses) | [Alpine-packages](https://github.com/alpinelinux/docker-alpine/blob/2c36a79aaaec3582d3b1961c9245b1a8a5a144dc/LICENSE) | permitted | permitted | unresolved | R-BASE |
| `n8n` · `N8N_IMAGE` | `n8nio/n8n:2.28.2` | n8n 2.28.2 (.ee. files under the n8n Enterprise License) | [Sustainable-Use-1.0](https://github.com/n8n-io/n8n/blob/99c6e0295dbde56de917f1df2d6a508c2b98dd9d/LICENSE.md) | conditional | conditional | unresolved | R-N8N |
| `n8n` · `N8N_INIT_IMAGE` | `n8nio/n8n:2.28.2` | n8n 2.28.2 (.ee. files under the n8n Enterprise License) | [Sustainable-Use-1.0](https://github.com/n8n-io/n8n/blob/99c6e0295dbde56de917f1df2d6a508c2b98dd9d/LICENSE.md) | conditional | conditional | unresolved | R-N8N |
| `neo4j` · `NEO4J_GRAPH_DB_IMAGE` | `neo4j:5.26.31` | Neo4j Community 5.26.31 | [GPL-3.0](https://github.com/neo4j/neo4j/blob/191241718f1c09b6e0f7a55d4b4ed31327d95158/LICENSE.txt) | permitted | with source | with source | — |
| `ollama` · `LLM_PROVIDER_IMAGE` | `ollama/ollama:0.30.11` | Ollama v0.30.11 | [MIT](https://github.com/ollama/ollama/blob/d26a58557d83aa8892bdfa79b3550f5ee969cd1c/LICENSE) | permitted | permitted | with notices | — |
| `ollama` · `OLLAMA_PULL_IMAGE` | `alpine:3.24.2@sha256:294b683cb724…` | Alpine Linux 3.24.2 (packages under their own licenses) | [Alpine-packages](https://github.com/alpinelinux/docker-alpine/blob/2c36a79aaaec3582d3b1961c9245b1a8a5a144dc/LICENSE) | permitted | permitted | unresolved | R-BASE |
| `open-webui` · `OPEN_WEB_UI_IMAGE` | `dyrnq/open-webui:v0.6.32` | Open WebUI v0.6.32, as rebuilt by the dyrnq mirror | [Open-WebUI](https://github.com/open-webui/open-webui/blob/37d1c85c996e1bdcd505e1e6d62b2f17acd8df23/LICENSE) | conditional | conditional | unresolved | R-OPENWEBUI |
| `open-webui` · `OPEN_WEB_UI_INIT_IMAGE` | `python:3.12.14-slim-bookworm@sha256:0f5b26b9518d…` | Python 3.12.14 | [PSF-2.0](https://github.com/python/cpython/blob/2abcf904b8dac8c999d2b3aac76681abb333798a/LICENSE) | permitted | permitted | with notices | — |
| `openclaw` · `OPENCLAW_IMAGE` | `ghcr.io/openclaw/openclaw:2026.6.10` | OpenClaw v2026.6.10 | [MIT](https://github.com/openclaw/openclaw/blob/aa69b12d0086b631b139c1435c9621a5783e3a40/LICENSE) | permitted | permitted | with notices | — |
| `openclaw` · `OPENCLAW_INIT_IMAGE` | `alpine:3.24.2@sha256:294b683cb724…` | Alpine Linux 3.24.2 (packages under their own licenses) | [Alpine-packages](https://github.com/alpinelinux/docker-alpine/blob/2c36a79aaaec3582d3b1961c9245b1a8a5a144dc/LICENSE) | permitted | permitted | unresolved | R-BASE |
| `otel-collector` · `OTEL_COLLECTOR_IMAGE` | `otel/opentelemetry-collector-contrib:0.154.0` | OpenTelemetry Collector Contrib v0.154.0 | [Apache-2.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/853eda0e12b3ba142552ecd2b63cec07062904f0/LICENSE) · [NOTICE](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/853eda0e12b3ba142552ecd2b63cec07062904f0/NOTICE) | permitted | permitted | with notices | — |
| `parakeet` · `PARAKEET_GPU_IMAGE` | `nvcr.io/nvidia/pytorch:26.06-py3` | NVIDIA PyTorch container 26.06 (NGC) | NVIDIA-DLC | unresolved | unresolved | unresolved | R-NGC |
| `prometheus` · `CADVISOR_IMAGE` | `gcr.io/cadvisor/cadvisor:v0.55.1` | cAdvisor v0.55.1 | [Apache-2.0](https://github.com/google/cadvisor/blob/f5bec3744d92f01556c6ca528ee52f3a26402d6d/LICENSE) | permitted | permitted | with notices | — |
| `prometheus` · `NODE_EXPORTER_IMAGE` | `prom/node-exporter:v1.11.1` | Node Exporter v1.11.1 | [Apache-2.0](https://github.com/prometheus/node_exporter/blob/0dd664dece3f8319f6bec5a221acd2c7ad13a23d/LICENSE) · [NOTICE](https://github.com/prometheus/node_exporter/blob/0dd664dece3f8319f6bec5a221acd2c7ad13a23d/NOTICE) | permitted | permitted | with notices | — |
| `prometheus` · `PROMETHEUS_IMAGE` | `prom/prometheus:v2.55.1` | Prometheus v2.55.1 | [Apache-2.0](https://github.com/prometheus/prometheus/blob/6d7569113f1ca814f1e149f74176656540043b8d/LICENSE) · [NOTICE](https://github.com/prometheus/prometheus/blob/6d7569113f1ca814f1e149f74176656540043b8d/NOTICE) | permitted | permitted | with notices | — |
| `ray` · `RAY_GPU_IMAGE` | `rayproject/ray:2.56.0-gpu` | Ray 2.56.0 with CUDA | [Apache-2.0](https://github.com/ray-project/ray/blob/637fd062205393b9e1929996bfe1d49bd3f8469d/LICENSE) | permitted | permitted | unresolved | R-CUDA |
| `ray` · `RAY_IMAGE` | `rayproject/ray:2.56.0` | Ray 2.56.0 | [Apache-2.0](https://github.com/ray-project/ray/blob/637fd062205393b9e1929996bfe1d49bd3f8469d/LICENSE) | permitted | permitted | with notices | — |
| `redis` · `REDIS_EXPORTER_IMAGE` | `oliver006/redis_exporter:v1.86.0` | redis_exporter v1.86.0 | [MIT](https://github.com/oliver006/redis_exporter/blob/c4dac6ba37ea9c7da7652beb329612af977106aa/LICENSE) | permitted | permitted | with notices | — |
| `redis` · `REDIS_IMAGE` | `redis:7.2.14-alpine` | Redis 7.2.14 | [BSD-3-Clause](https://github.com/redis/redis/blob/f2262eccb855eadd1afb0c457ea583ef9d5400b5/COPYING) | permitted | permitted | with notices | — |
| `redpanda` · `REDPANDA_CONSOLE_IMAGE` | `docker.redpanda.com/redpandadata/console:v3.8.0` | Redpanda Console v3.8.0 | [BSL-1.1-Redpanda](https://github.com/redpanda-data/console/blob/d73b703d9ceba9d02630db2a361e480df681c489/licenses/bsl_header.txt) | conditional | conditional | unresolved | R-REDPANDA |
| `redpanda` · `REDPANDA_IMAGE` | `docker.redpanda.com/redpandadata/redpanda:v26.1.12` | Redpanda v26.1.12 (enterprise features under the Redpanda Community License) | [BSL-1.1-Redpanda](https://github.com/redpanda-data/redpanda/blob/767244b93538578716e4d89af455365691767526/licenses/bsl.md) | conditional | conditional | unresolved | R-REDPANDA |
| `searxng` · `SEARXNG_IMAGE` | `searxng/searxng:2026.6.28-357662d86` | SearXNG 2026.6.28 (commit 357662d86) | [AGPL-3.0](https://github.com/searxng/searxng/blob/357662d86/LICENSE) | permitted | with source | with source | — |
| `spark` · `SPARK_IMAGE` | `apache/spark:4.1.2` | Apache Spark 4.1.2 | [Apache-2.0](https://github.com/apache/spark/blob/f0bb2e6a47d0ebda424ffd633fcea8644a597954/LICENSE) · [NOTICE](https://github.com/apache/spark/blob/f0bb2e6a47d0ebda424ffd633fcea8644a597954/NOTICE) | permitted | permitted | with notices | — |
| `speaches` · `SPEACHES_IMAGE` | `ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu` | Speaches 0.9.0-rc.3 | [MIT](https://github.com/speaches-ai/speaches/blob/24f209c90218187747a9205f0b84bc06b42ce775/LICENSE) | permitted | permitted | with notices | — |
| `supabase` · `POSTGRES_EXPORTER_IMAGE` | `prometheuscommunity/postgres-exporter:v0.19.1` | postgres_exporter v0.19.1 | [Apache-2.0](https://github.com/prometheus-community/postgres_exporter/blob/333363aeb548b66340a61a7fb2b3f0e8a8d39f90/LICENSE) · [NOTICE](https://github.com/prometheus-community/postgres_exporter/blob/333363aeb548b66340a61a7fb2b3f0e8a8d39f90/NOTICE) | permitted | permitted | with notices | — |
| `supabase` · `SUPABASE_API_IMAGE` | `postgrest/postgrest:v12.2.10` | PostgREST v12.2.10 | [MIT](https://github.com/PostgREST/postgrest/blob/a7f91814622d197aadfcf680f4327024d56bf780/LICENSE) | permitted | permitted | with notices | — |
| `supabase` · `SUPABASE_AUTH_IMAGE` | `supabase/gotrue:v2.191.0` | Supabase Auth (GoTrue) v2.191.0 | [MIT](https://github.com/supabase/auth/blob/712c037897ee918897996a8f71b62c0e4e2f48ef/LICENSE) | permitted | permitted | with notices | — |
| `supabase` · `SUPABASE_DB_IMAGE` | `supabase/postgres:17.6.1.139` | Supabase Postgres 17.6.1.139 | [PostgreSQL](https://github.com/supabase/postgres/blob/5a1b75bc2807d6a72641d0a824f7a6c96142fa9e/LICENSE) | permitted | permitted | with notices | — |
| `supabase` · `SUPABASE_DB_INIT_IMAGE` | `postgres:15.19-alpine` | PostgreSQL 15.19 client | [PostgreSQL](https://github.com/postgres/postgres/blob/2ff1375b5dd8bf09d8cb0e795974528180fd75ca/COPYRIGHT) | permitted | permitted | with notices | — |
| `supabase` · `SUPABASE_META_IMAGE` | `supabase/postgres-meta:v0.96.6` | postgres-meta v0.96.6 | [Apache-2.0](https://github.com/supabase/postgres-meta/blob/f21a4da30484c6146a6ac4a3f260d992e7330bf1/LICENSE) | permitted | permitted | with notices | — |
| `supabase` · `SUPABASE_REALTIME_IMAGE` | `supabase/realtime:v2.112.0` | Supabase Realtime v2.112.0 | [Apache-2.0](https://github.com/supabase/realtime/blob/8fc71b2914f7d60c42c333460b45859242bb875f/LICENSE) | permitted | permitted | with notices | — |
| `supabase` · `SUPABASE_STORAGE_IMAGE` | `supabase/storage-api:v1.61.5` | Supabase Storage v1.61.5 | [Apache-2.0](https://github.com/supabase/storage/blob/485fddf59e66c2d11b0e8be191ae384cbba56f87/LICENSE) | permitted | permitted | with notices | — |
| `supabase` · `SUPABASE_STUDIO_IMAGE` | `supabase/studio:2026.06.22-sha-2207d7f` | Supabase Studio (commit 2207d7f) | [Apache-2.0](https://github.com/supabase/supabase/blob/2207d7f6651b8731143e74fd91130d362dd0cf93/LICENSE) | permitted | permitted | with notices | — |
| `supavisor` · `SUPAVISOR_IMAGE` | `supabase/supavisor:2.9.5` | Supavisor v2.9.5 | [Apache-2.0](https://github.com/supabase/supavisor/blob/9cf7560b2b2d6e2f72711c1e2a131dc0b2ea6463/LICENSE) | permitted | permitted | with notices | — |
| `tei-reranker` · `TEI_RERANKER_CPU_ARM64_IMAGE` | `ghcr.io/huggingface/text-embeddings-inference:cpu-arm64-latest@sha256:35c50d7494de…` | Text Embeddings Inference (CPU arm64), floating latest tag pinned by digest | [Apache-2.0](https://github.com/huggingface/text-embeddings-inference/blob/5699247f57e46aa09eb4f8c4cf74114099372fe7/LICENSE) | permitted | permitted | with notices | R-FLOAT |
| `tei-reranker` · `TEI_RERANKER_CPU_IMAGE` | `ghcr.io/huggingface/text-embeddings-inference:cpu-1.9@sha256:ad950d30878e…` | Text Embeddings Inference 1.9 (CPU) | [Apache-2.0](https://github.com/huggingface/text-embeddings-inference/blob/5699247f57e46aa09eb4f8c4cf74114099372fe7/LICENSE) | permitted | permitted | with notices | — |
| `tei-reranker` · `TEI_RERANKER_GPU_IMAGE` | `ghcr.io/huggingface/text-embeddings-inference:1.9@sha256:536efce2a0dc…` | Text Embeddings Inference 1.9 (CUDA) | [Apache-2.0](https://github.com/huggingface/text-embeddings-inference/blob/5699247f57e46aa09eb4f8c4cf74114099372fe7/LICENSE) | permitted | permitted | unresolved | R-CUDA |
| `tempo` · `TEMPO_IMAGE` | `grafana/tempo:3.0.0` | Grafana Tempo v3.0.0 | [AGPL-3.0](https://github.com/grafana/tempo/blob/d399842f5d699627e49cdb7034b70e29e0e29567/LICENSE) | permitted | with source | with source | — |
| `tika` · `TIKA_IMAGE` | `apache/tika:3.3.1.0` | Apache Tika 3.3.1 | [Apache-2.0](https://github.com/apache/tika/blob/bf9aa9249a4f9ff14f53bcd419efdebd92253351/LICENSE.txt) · [NOTICE](https://github.com/apache/tika/blob/bf9aa9249a4f9ff14f53bcd419efdebd92253351/NOTICE.txt) | permitted | permitted | with notices | — |
| `trino` · `TRINO_IMAGE` | `trinodb/trino:482` | Trino 482 | [Apache-2.0](https://github.com/trinodb/trino/blob/f04d222fbeedaf888ac3c907748209c7e716a4c2/LICENSE) | permitted | permitted | with notices | — |
| `trueforge` · `TRUEFORGE_IMAGE` | `tfy.jfrog.io/tfy-images/trueforge:0.2.0-8b1d98e` | TrueForge 0.2.0 (TrueFoundry registry) | unknown | unresolved | unresolved | unresolved | R-TRUEFORGE |
| `verba` · `VERBA_IMAGE` | `semitechnologies/verba@sha256:0947d289ebff…` | Verba, untagged image pinned by digest | [BSD-3-Clause](https://github.com/weaviate/Verba/blob/70b6cfb8ef59c9f178ffccfaf3aadcf737757a18/LICENSE) | permitted | permitted | with notices | R-FLOAT |
| `weaviate` · `MULTI2VEC_CLIP_IMAGE` | `semitechnologies/multi2vec-clip:sentence-transformers-clip-ViT-B-32-1.5.1` | Weaviate multi2vec-clip inference 1.5.1 (bundles CLIP ViT-B-32 weights) | [BSD-3-Clause](https://github.com/weaviate/multi2vec-clip-inference/blob/75168ebc1881fd1c754e9b9c3fe4b142598f4a5a/LICENSE) | permitted | permitted | unresolved | R-MODELS |
| `weaviate` · `WEAVIATE_IMAGE` | `cr.weaviate.io/semitechnologies/weaviate:1.38.17` | Weaviate v1.38.17 | [BSD-3-Clause](https://github.com/weaviate/weaviate/blob/3f1ddfea60470ea89254b34802ca6c5c169ee390/LICENSE) | permitted | permitted | with notices | — |
| `weaviate` · `WEAVIATE_INIT_IMAGE` | `alpine:3.24.2` | Alpine Linux 3.24.2 (packages under their own licenses) | [Alpine-packages](https://github.com/alpinelinux/docker-alpine/blob/2c36a79aaaec3582d3b1961c9245b1a8a5a144dc/LICENSE) | permitted | permitted | unresolved | R-BASE |
| `zeppelin` · `ZEPPELIN_IMAGE` | `apache/zeppelin:0.12.1` | Apache Zeppelin 0.12.1 | [Apache-2.0](https://github.com/apache/zeppelin/blob/ae9cb72ffaa6b3c007f57b152e2a3eb2208b910a/LICENSE) · [NOTICE](https://github.com/apache/zeppelin/blob/ae9cb72ffaa6b3c007f57b152e2a3eb2208b910a/NOTICE) | permitted | permitted | with notices | — |
| `zeppelin` · `ZEPPELIN_INIT_IMAGE` | `python:3.12.13-alpine` | Python 3.12.13 | [PSF-2.0](https://github.com/python/cpython/blob/3bb231a6a5dc02b95658877318bf61501a7209e9/LICENSE) | permitted | permitted | with notices | — |

### 4.1. Images only a Dockerfile pins

An external image a Dockerfile builds from or copies out of (`FROM`, `COPY --from`, `RUN --mount=from`, resolved through ARG defaults) that no manifest declares, such as a multi-stage build's runtime stage. The container-security scan reads Dockerfiles the same way.

| Dockerfile | Image | Upstream | License | Hosted use | Source integration | Redistribution | Open items |
|---|---|---|---|---|---|---|---|
| `services/asset-worker/app/Dockerfile` | `node:22-bookworm-slim@sha256:f32b81066cde…` | Node.js 22 runtime stage, floating 22-bookworm-slim tag pinned by digest | [MIT](https://github.com/nodejs/node/blob/80dc632040e6bada37aac1220dde9c79581c9c22/LICENSE) | permitted | permitted | with notices | R-FLOAT |
| `services/jupyterhub/build/Dockerfile` | `node:22.23.2-bookworm-slim@sha256:83f487e0a634…` | Node.js 22.23.2 runtime stage (the license file also lists bundled dependencies) | [MIT](https://github.com/nodejs/node/blob/aa4c77582be995286fc6e00aaf530dc7ade102a9/LICENSE) | permitted | permitted | with notices | — |

## 5. Model weights

One row per model in `services/comfyui/models.yaml` and `services/ollama/models.yaml`, listing every file the entry downloads. A ComfyUI model's license comes from its catalogue entry's `license_name`, `license_url` and `license_restrictions` fields, which the setup wizard also shows.

| Catalogue | Model | Pinned source | License | Hosted use | Source integration | Redistribution | Open items |
|---|---|---|---|---|---|---|---|
| comfyui | `v1-5-pruned-emaonly` | revision `451f4fe16113` | not recorded | unresolved | unresolved | unresolved | R-MODELS |
| comfyui | `sd_xl_base_1.0` | revision `462165984030` | not recorded | unresolved | unresolved | unresolved | R-MODELS |
| comfyui | `vae-ft-mse-840000-ema-pruned` | revision `629b3ad3030c` | not recorded | unresolved | unresolved | unresolved | R-MODELS |
| comfyui | `sdxl-vae` | revision `6f5909a7e596` | not recorded | unresolved | unresolved | unresolved | R-MODELS |
| comfyui | `ip-adapter-plus_sdxl_vit-h` | revision `018e402774ae` | not recorded | unresolved | unresolved | unresolved | R-MODELS |
| comfyui | `ip-adapter-faceid-plusv2_sdxl` | revision `43907e6f44d0` | not recorded | unresolved | unresolved | unresolved | R-MODELS |
| comfyui | `instantid-ip-adapter` | revision `57b32dfee076` | not recorded | unresolved | unresolved | unresolved | R-MODELS |
| comfyui | `real-esrgan-x4plus` | revision `982e7edaec38` | not recorded | unresolved | unresolved | unresolved | R-MODELS |
| comfyui | `4x-ultrasharp` | revision `1856559b50de` | not recorded | unresolved | unresolved | unresolved | R-MODELS |
| comfyui | `easynegative` | revision `05278ab48b52` | not recorded | unresolved | unresolved | unresolved | R-MODELS |
| comfyui | `clip-vit-large-patch14` | revision `32bd64288804` | not recorded | unresolved | unresolved | unresolved | R-MODELS |
| comfyui | `animatediff-camera-zoomin-lora` | revision `6a8aaab90169` | not recorded | unresolved | unresolved | unresolved | R-MODELS |
| comfyui | `krea2-turbo-bf16` | revision `8038ce89b91b` (+2 files) | [Krea 2 Community License](https://huggingface.co/krea/Krea-2-Turbo/blob/1161245028ef398cd0a951101b2bbf486464f841/LICENSE.pdf). Restrictions: Enterprise license required at or above $1,000,000 USD ($1M) annual revenue for commercial use. Reasonable and appropriate content filtering is required for deployments. | conditional | conditional | unresolved | R-KREA, R-BUNDLE |
| comfyui | `krea2-raw-bf16` | revision `8038ce89b91b` (+2 files) | [Krea 2 Community License](https://huggingface.co/krea/Krea-2-Turbo/blob/1161245028ef398cd0a951101b2bbf486464f841/LICENSE.pdf). Restrictions: Enterprise license required at or above $1,000,000 USD ($1M) annual revenue for commercial use. Reasonable and appropriate content filtering is required for deployments. | conditional | conditional | unresolved | R-KREA, R-BUNDLE |
| comfyui | `krea2-identity-edit-v1-2` | revision `89e9e7a09ee2` | [Krea 2 Community License](https://huggingface.co/krea/Krea-2-Raw/blob/main/LICENSE.pdf) (not pinned: R-KREA). Restrictions: Enterprise license required at or above $1,000,000 USD ($1M) annual revenue for commercial use. Reasonable and appropriate content filtering is required for deployments. | conditional | conditional | unresolved | R-KREA |
| comfyui | `hunyuan3d-2` | revision `9cd649ba6913` | [Tencent Hunyuan Community License](https://huggingface.co/tencent/Hunyuan3D-2/blob/9cd649ba6913f7a852e3286bad86bfa9a2d83dcf/LICENSE). Restrictions: Territory-restricted — not licensed for use in the European Union, the United Kingdom, or South Korea. Products or services with over 100 million monthly active users require a separate license from Tencent. Use is subject to the Tencent Hunyuan Community License Agreement and its Acceptable Use Policy. | conditional | conditional | unresolved | R-HUNYUAN |
| ollama | `qwen3.8:latest` | floating `qwen3.8:latest` | not recorded | unresolved | unresolved | unresolved | R-OLLAMA |
| ollama | `nomic-embed-text` | floating `nomic-embed-text` | not recorded | unresolved | unresolved | unresolved | R-OLLAMA |
| ollama | `qwen3-embedding:0.6b` | floating `qwen3-embedding:0.6b` | not recorded | unresolved | unresolved | unresolved | R-OLLAMA |
| ollama | `bge-m3` | floating `bge-m3` | not recorded | unresolved | unresolved | unresolved | R-OLLAMA |

## 6. Not in this inventory

- Cloud models in `services/litellm/models.yaml`: Atlas calls them as APIs under each provider's terms of service and downloads no weights.
- Models an operator adds in `services/comfyui/custom-models.yaml`: the operator chooses them and owns their terms.
- Weights a service downloads by its own configuration rather than from a catalogue, such as the TEI reranker model: open item R-SVC-MODELS.
- Atlas's own code, which is licensed under Apache 2.0 (the repository's `LICENSE`).

## 7. Keeping it current

`make docs-check` runs the same check as `python -m scripts.docs.license_inventory --check`, which fails when a manifest image default, a Dockerfile image, or a catalogue model's download URLs or license URL differ from their row, when an artifact has no row, when a license URL is not pinned to a revision, and when an unresolved mode has no open item that concerns it. When a pin moves, read the license at the new revision, update the row's `image` or `sources`, `url`, `notice` and mode values in `docs/reference/license-inventory.yaml`, then regenerate this page with `uv run --project bootstrapper python -m scripts.docs.canonical_references`.

Reviewing this inventory is a pre-tag step of every release; see [Releasing](../operations/releasing.md) §3.
