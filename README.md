<p align="center">
  <img src="./assets/atlas-poster.png" alt="Atlas — the Titan holding the globe, with the ATLAS-PLATFORM wordmark" width="66%">
</p>

# Atlas

A self-hosted, source-configurable, multi-disciplinary engineering platform — gen-AI, ML, and data — composable from a single Docker Compose stack.

[![Atlas — interactive setup wizard streaming the launch phase, with the ASCII brand banner pinned at the top of the terminal](./docs/screenshots/wizard-running.png)](./docs/screenshots/wizard-running.png)

*The Textual TUI wizard mid-launch: ASCII brand banner pinned at the top, stack overview + cloud-API status, filter + log-source chips, and the live `docker compose` log stream below. Captured during a normal `./start.sh` run.*

[![Atlas — topologically-ordered architecture diagram](./docs/diagrams/architecture.svg)](./docs/diagrams/architecture.svg)

*Topologically-ordered architecture: external clients enter via Kong, the gateway routes to Apps and Agents, which call the LLM Core (LiteLLM → Ollama + cloud) and the Media + Data tiers. Hand-authored via the [`architecture-diagram` skill](https://github.com/anthropics/claude-code/tree/main/skills/architecture-diagram); the per-service diagrams under `services/<name>/architecture.svg` share the same design system but are auto-regenerated from each manifest's `data_flow.calls`.*

## 1. Quick Start

### 1.1 First-time setup

```bash
# 1. Clone the repository
git clone https://github.com/thekaveh/atlas && cd atlas

# 2. Start with the interactive setup wizard (no configuration needed)
./start.sh

# 3. Wait ~5 minutes for AI models to download, then access:
# Atlas Dashboard:       http://localhost:63000
# Open WebUI (Chat):     http://localhost:63096
# n8n (Workflows):       http://localhost:63075
# Supabase Studio:       http://localhost:63019
# SearxNG (Search):      http://localhost:63054
# ComfyUI:               http://localhost:63052
# JupyterHub (IDE):      http://localhost:63094
# MinIO Console:         http://localhost:63021
#
# Optional Kong host routes after ./start.sh --setup-hosts:
# Chat:                  http://chat.localhost:63000
# n8n:                   http://n8n.localhost:63000
# Supabase Studio:       http://supabase-studio.localhost:63000
# ComfyUI:               http://comfyui.localhost:63000
# LiteLLM Dashboard:     http://litellm.localhost:63000/ui/
# MinIO Console:         http://minio.localhost:63000

# Default credentials:
# Supabase Studio: kong_admin / DASHBOARD_PASSWORD (auto-generated into .env on first launch)
# n8n:             owner account created in the web UI on first visit
```

The default configuration runs a CPU starter stack: chat UI, workflow automation, vector database, and privacy search. Additional services such as Ray, Airflow, Spark, Zeppelin, LightRAG, and observability are opt-in through tracks, CLI flags, or the wizard.

### 1.2 Common option combinations

```bash
# Local AI services (faster, less memory)
./start.sh --llm-provider-source ollama-localhost --comfyui-source localhost

# GPU acceleration
./start.sh --llm-provider-source ollama-container-gpu --comfyui-source container-gpu --stt-provider-source speaches-container-gpu

# Custom project namespace (container family) — isolate from a base Atlas stack,
# e.g. when running Atlas as a submodule. Persists to PROJECT_NAME in .env, so a
# later bare ./stop.sh tears down exactly this stack (start & stop both honor it).
./start.sh --project myproject     # or -p myproject
./stop.sh                          # reads PROJECT_NAME=myproject from .env

# Pick a different STT engine (Speaches is the default)
./start.sh --stt-provider-source parakeet-container-gpu   # SOTA NVIDIA (CC-BY-4.0)
./start.sh --stt-provider-source whisper-cpp-localhost    # Best on macOS — Metal + Core ML
./start.sh --stt-provider-source parakeet-localhost       # Parakeet-MLX (macOS) or native Linux

# Pick a different TTS engine (Speaches is the default)
./start.sh --tts-provider-source chatterbox-container-gpu  # Voice cloning, NVIDIA
./start.sh --tts-provider-source chatterbox-localhost      # Voice cloning, macOS MPS / Linux

# Minimal setup (chat only)
./start.sh --n8n-source disabled --searxng-source disabled --weaviate-source disabled

# Cloud-only LLMs (no local Ollama; LiteLLM gateway routes to cloud providers)
# The bootstrapper auto-disables a cloud provider whose API key is empty,
# so pass --openai-api-key on the command line OR set OPENAI_API_KEY in .env first.
./start.sh --llm-provider-source none --cloud-openai-source enabled --openai-api-key sk-... --comfyui-source disabled

# Observability bundle (Prometheus + Grafana + node-exporter + cAdvisor + per-service exporters)
# Off by default; opt in to scrape Kong, LiteLLM, Weaviate, n8n, MinIO, Backend,
# Postgres, and Redis. 7 starter dashboards in the "Atlas" Grafana folder.
# Grafana admin password is auto-generated on first run (see .env after launch).
./start.sh --prometheus-source container --grafana-source container
./start.sh --prometheus-source container --grafana-source container --prometheus-retention-days 30
```

### 1.3 Troubleshooting tips

- **Don't prefix with `sudo`.** The wizard runs as your user; only `--setup-hosts` needs root, and it handles that internally. Running the whole script under sudo creates root-owned files that block future non-sudo runs. See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for recovery.
- **Port conflicts?** → `./start.sh --base-port 64000`
- **Out of memory?** → Increase Docker memory to 10GB+
- **Can't access *.localhost?** → Run `./start.sh --setup-hosts`
- **Want fresh start?** → `./stop.sh --cold && ./start.sh --cold`
- **Other startup errors?** → See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

### 1.4 Interactive setup wizard

Running `./start.sh` with no arguments launches an interactive setup wizard that walks through configuring every service step by step:

- Step-by-step service configuration with descriptions and contextual hints (GPU requirements, localhost options, etc.)
- LLM cluster spliced immediately after the LLM Engine step: single unified Ollama models multiselect (source-aware — localhost rows are badged `[pulled]` / `[library]`) + a free-text "additional models to pull" step → OpenAI / Anthropic / OpenRouter key + multiselect pairs (live `/v1/models` fetch), keeping engine + local + cloud adjacent in the wizard flow
- Live `ollama.com/library` scrape (a few hundred entries; exact count depends on the upstream catalog at fetch time) for both container and host-side Ollama sources
- Real-time command preview showing the equivalent CLI command as you make selections
- Dependency validation that warns if you enable a service without its required dependencies
- Pre-launch summary table with all endpoints and access URLs before starting
- Color-coded streaming logs once the stack launches, with the full session (wizard warnings + launch phase) tee'd to `/tmp/atlas-launch-<YYYYMMDDTHHMMSS>.log`
- Keyboard shortcuts: `Esc` returns to the previous step, `Space` toggles multiselect rows, `Ctrl+Q` quits

The wizard covers all configurable services, base port selection, cold start option, and hosts file setup. After reviewing the configuration summary, confirm to launch the stack.

> For users who prefer CLI flags, all options remain available. See [Command Line Interface](#51-command-line-interface) or the [Interactive Setup Wizard Guide](docs/quick-start/interactive-setup-wizard.md).

### 1.5 Quickstart by track

For RAG-focused work:

```bash
./start.sh --track gen-ai-rag
```

For agentic-app engineering:

```bash
./start.sh --track gen-ai-eng
```

Other tracks: `gen-ai-creative`, `ml-eng`, `data-eng`, `trading`. Run `./start.sh --list-tracks` to see service membership for each.

Omit `--track` entirely to get the full interactive wizard (with the
track picker as step 1). Pass `--track all` to skip the picker and
prompt for every configurable service.

### 1.6 Table of contents

- [Quick Start](#1-quick-start)
- [Overview](#2-overview)
- [Getting Started](#3-getting-started)
- [Core Services](#4-core-services)
- [Usage Guide](#5-usage-guide)
- [Advanced Configuration](#6-advanced-configuration)
- [Development](#7-development)
- [Troubleshooting](#8-troubleshooting)
- [Documentation](#9-documentation)

## 2. Overview

### 2.1 What is Atlas?

Atlas is a self-hosted engineering platform — designed for the breadth of modern generative AI, ML, and data work. It bundles 30+ integrated services (LLM gateway + inference, vector + graph DBs, workflow + DAG automation, distributed compute, object storage, notebooks, observability) wired together via a Kong gateway and an adaptive FastAPI backend. Each service is independently switchable between `container`, `localhost`, or `disabled` (the LLM provider adds a cloud-API path via LiteLLM); the **tracks system** picks sensible defaults for the work you're doing:

- `gen-ai-rag` — retrieval-augmented generation (vectors, graph, reranker, doc ingest, web search)
- `gen-ai-eng` — agentic apps + workflows with voice, vision, and search
- `gen-ai-creative` — multimodal generation (image, voice, vision, doc)
- `ml-eng` — distributed training/inference + notebooks + experiment storage
- `data-eng` — batch + lakehouse + graph + vector with orchestration
- `trading` — read-only financial research and paper portfolios in notebooks
- `all` — every configurable service, full wizard, no filtering

Key technical traits:

- **Dynamic service configuration**: SOURCE-based deployment with CLI overrides
- **Kong gateway**: auto-generated routes based on active services
- **Cross-platform support**: runs natively on Linux and macOS (Intel/Apple Silicon); on Windows via WSL2 or Git Bash (`start.sh`/`stop.sh` are POSIX shell — there is no native PowerShell/cmd wrapper)
- **Flexible deployment**: mix containerized and localhost-installed services (with cloud LLM providers wired through LiteLLM)
- **GPU support**: container variants with NVIDIA GPU access for inference services
- **Always-on core**: Supabase ecosystem, Redis, LiteLLM gateway (fronts Ollama + cloud LLM providers), FastAPI backend, Kong Gateway
- **Opt-in**: Ray, OpenClaw, Airflow, Spark, Zeppelin, LightRAG, TEI Reranker, the observability bundle (Prometheus + Grafana), the Cloudflare Tunnel public edge (`cloudflared`), and the on-demand backup runner (`backup`) ship disabled; everything is switchable per service via SOURCE flags or the interactive wizard

### 2.2 Key features

- **API gateway (Kong)**: centralized API management with dynamic routing
- **Real-time data sync**: live database notifications via Supabase Realtime
- **Flexible service sources**: switch between container and localhost variants per service, plus cloud LLM providers via LiteLLM
- **Modular architecture**: choose service combinations via SOURCE variables
- **Environment-based config**: configuration through environment variables
- **Cross-platform Python core**: the bootstrapper is OS-aware; Linux and macOS run natively, Windows runs under WSL2 / Git Bash

### 2.3 Architecture overview

The canonical architecture diagram is embedded at the top of this README; the source lives at [`docs/diagrams/architecture.svg`](docs/diagrams/architecture.svg) — hand-authored via the [`architecture-diagram` skill](https://github.com/anthropics/claude-code/tree/main/skills/architecture-diagram) (cyan / emerald / violet / amber / rose / orange palette, JetBrains Mono, layered topological flow). See [`docs/diagrams/README.md`](docs/diagrams/README.md) for update instructions.

The diagram summarizes the full Atlas topology around Kong, Open WebUI, the always-on Backend API, the always-on LiteLLM gateway (fronting Ollama and any enabled cloud LLM providers), Supabase/PostgreSQL, Redis, Neo4j, Weaviate, n8n, ComfyUI, JupyterHub, SearxNG, Ray, and optional Hermes Agent / OpenClaw / STT/TTS/document-processing / LightRAG + TEI Reranker / Airflow + Spark + Zeppelin / Prometheus + Grafana services. Per-service diagrams (auto-regenerated from each manifest's `data_flow.calls`) live next to each service folder at `services/<name>/architecture.{svg,html}`.

## 3. Getting Started

### 3.1 Getting started summary

- **New to the stack?** → Use containers with `./start.sh` for the easiest experience
- **Have local Ollama?** → Use `./start.sh --llm-provider-source ollama-localhost` for better performance (LiteLLM still fronts the upstream)
- **Have NVIDIA GPU?** → Use `./start.sh --comfyui-source container-gpu` for image generation acceleration
- **Need cloud APIs?** → Use `./start.sh --llm-provider-source none --cloud-openai-source enabled` (or the matching `--cloud-anthropic-source` / `--cloud-openrouter-source` flag) — LiteLLM routes to whichever providers are enabled
- **Limited resources?** → Disable services with `--n8n-source disabled --searxng-source disabled`

The SOURCE-based configuration system controls how each service is deployed.

### 3.2 Prerequisites

- **Docker & Docker Compose** — container orchestration
- **Python 3.10+** — for start/stop scripts
- **8GB+ RAM** allocated to Docker (12GB recommended)
- **10GB+ disk space** for Docker volumes

**Install UV (recommended)** for Python dependency management:
```bash
pip install uv
```

### 3.3 Installation

#### 3.3.1 Quick install (recommended)
```bash
git clone https://github.com/thekaveh/atlas.git
cd atlas
./start.sh
```

#### 3.3.2 Custom configuration
```bash
# Edit configuration before starting
cp .env.example .env
# Edit .env to customize SOURCE variables
./start.sh
```

### 3.4 SOURCE system

The stack uses **SOURCE variables** to control how services are deployed.

**LLM access (always through LiteLLM):**
- **LiteLLM gateway** — always-on; every consumer reads `LITELLM_BASE_URL` + `LITELLM_API_KEY`
- **Ollama upstream** (`LLM_PROVIDER_SOURCE=ollama-container-cpu|ollama-container-gpu|ollama-localhost|none`) — what LiteLLM forwards to for local inference
- **Cloud upstreams** (`CLOUD_OPENAI_SOURCE`, `CLOUD_ANTHROPIC_SOURCE`, `CLOUD_OPENROUTER_SOURCE`) — independent `enabled`/`disabled` toggles; each requires the matching API key

**Other services that support localhost:**
- **ComfyUI** (`COMFYUI_SOURCE=localhost`) — use local ComfyUI; the bootstrapper resolves `host.docker.internal:${COMFYUI_LOCALHOST_PORT}` (default `8000`; override to `8188` or whatever port your host install uses)
- **Weaviate** (`WEAVIATE_SOURCE=localhost`) — use local Weaviate instance
- **OpenClaw** (`OPENCLAW_SOURCE=localhost`) — use local OpenClaw installation
- **Hermes Agent** (`HERMES_SOURCE=localhost`) — use a host-installed Hermes; the bootstrapper resolves `host.docker.internal:${HERMES_LOCALHOST_PORT}` (default `8642`) for the API and `host.docker.internal:${HERMES_LOCALHOST_DASHBOARD_PORT}` (default `9119`) for the dashboard; useful when Hermes should drive your real shell, browser, or microphone
- **LightRAG** (`LIGHTRAG_SOURCE=localhost`) — use a host-installed LightRAG; the bootstrapper resolves `host.docker.internal:${LIGHTRAG_LOCALHOST_PORT}` (default `63068`)
- **TEI Reranker** (`TEI_RERANKER_SOURCE=localhost`) — use a host-installed TEI Reranker; the bootstrapper resolves `host.docker.internal:${TEI_RERANKER_LOCALHOST_PORT}` (default `63049`)

**Container-only services:**
- **n8n** (`N8N_SOURCE=container|disabled`) — workflow automation
- **SearxNG** (`SEARXNG_SOURCE=container|disabled`) — privacy search
- **Open WebUI** (`OPEN_WEB_UI_SOURCE=container|disabled`) — chat interface
- **Backend API** (`BACKEND_SOURCE=container`) — always-on adaptive FastAPI backend
- **JupyterHub** (`JUPYTERHUB_SOURCE=container|disabled`) — data science IDE

**Observability bundle (opt-in, disabled by default):**
- **Prometheus** (`PROMETHEUS_SOURCE=container|disabled`) — metrics scraper + TSDB bundled with `node-exporter` and `cAdvisor`. When enabled, 13 scrape jobs cover Kong, LiteLLM, Weaviate, n8n (web + worker), MinIO, Backend, Postgres (via `postgres-exporter` sidecar in the Supabase family), and Redis (via `redis-exporter` sidecar in the Redis family), plus four self / infrastructure targets (prometheus, grafana, node-exporter, cAdvisor). JupyterHub and Hermes are deferred — see `services/prometheus/README.md` §4. Retention is user-configurable via `--prometheus-retention-days` (default 7).
- **Grafana** (`GRAFANA_SOURCE=container|disabled`) — dashboards + unified alerting UI on top of Prometheus. Pre-provisions the Prometheus datasource and 7 starter dashboards (stack overview, LiteLLM, Kong, Postgres+Redis, Containers+Host, n8n, app-tier). Admin login is auto-generated on first bootstrap (`GRAFANA_ADMIN_USERNAME` / `GRAFANA_ADMIN_PASSWORD` in `.env`).

<!-- TOPOLOGY:BEGIN -->
_Auto-generated by `bootstrapper/tools/generate_readme_topology.py`._

_Engine-only manifests (speaches, chatterbox) are not listed — they're selected as source variants of their parent (STT Provider / TTS Provider) rather than as standalone services._

| Category | Service | Default port | Alias |
|---|---|---:|---|
| Infra | Backup / restore | — | — |
| Infra | Kong API Gateway | 63000 | — |
| Infra | Cloudflare Tunnel | — | — |
| Infra | Ray | 63002 | ray.localhost |
| Infra | Langfuse | 63005 | langfuse.localhost |
| Infra | Loki | — | — |
| Infra | Prometheus | 63006 | prometheus.localhost |
| Infra | Grafana | 63009 | grafana.localhost |
| Infra | Tempo | — | — |
| Infra | OpenTelemetry Collector | — | — |
| Data | Redpanda Console | 63011 | redpanda.localhost |
| Data | Supabase DB | 63012 | — |
| Data | Supabase Meta | 63014 | — |
| Data | Supabase Storage | 63015 | — |
| Data | Supabase Auth | 63016 | — |
| Data | Supabase API | 63017 | — |
| Data | Supabase Realtime | 63018 | — |
| Data | Supabase Studio | 63019 | supabase-studio.localhost |
| Data | MinIO Console | 63021 | minio.localhost |
| Data | Apache Iceberg REST Catalog | 63022 | — |
| Data | Neo4j Graph DB | 63024 | graph.localhost |
| Data | Redis | 63025 | — |
| Data | Apache Spark | 63027 | spark.localhost |
| Data | Apache Spark — History Server | 63028 | spark-history.localhost |
| Data | Supavisor | — | — |
| Data | Trino | 63029 | trino.localhost |
| Data | Weaviate | 63030 | weaviate.localhost |
| Data | Multi2Vec CLIP | — | — |
| LLM Core | LiteLLM | 63040 | litellm.localhost |
| LLM Core | LLM Engine | — | ollama.localhost |
| LLM Core | TEI Reranker | 63041 | rerank.localhost |
| Media | Blender MCP | — | — |
| Media | Crawl4AI | 63050 | crawl4ai.localhost |
| Media | Document Processor | 63051 | docling.localhost |
| Media | FAL Cloud Media | — | — |
| Media | ComfyUI | 63052 | comfyui.localhost |
| Media | STT Provider | 63053 | stt.localhost |
| Media | SearxNG | 63054 | search.localhost |
| Media | Apache Tika | 63055 | tika.localhost |
| Media | TTS Provider | 63056 | tts.localhost |
| Agents & Workflows | Apache Airflow | 63070 | airflow.localhost |
| Agents & Workflows | Celery Worker | — | — |
| Agents & Workflows | Flower | 63071 | flower.localhost |
| Agents & Workflows | Hermes Agent | 63072 | hermes.localhost |
| Agents & Workflows | LightRAG | 63074 | lightrag.localhost |
| Agents & Workflows | n8n | 63075 | n8n.localhost |
| Agents & Workflows | OpenClaw | 63076 | openclaw.localhost |
| Agents & Workflows | Curated MCP Servers | 63078 | mcp.localhost |
| Apps & UIs | Jenkins | 63090 | jenkins.localhost |
| Apps & UIs | Label Studio | 63091 | label-studio.localhost |
| Apps & UIs | MLflow | 63092 | mlflow.localhost |
| Apps & UIs | Backend API | 63093 | api.localhost |
| Apps & UIs | JupyterHub | 63094 | jupyter.localhost |
| Apps & UIs | Neo4j LLM Graph Builder | 63095 | graphbuilder.localhost |
| Apps & UIs | Open WebUI | 63096 | chat.localhost |
| Apps & UIs | Local Deep Researcher | 63097 | research.localhost |
| Apps & UIs | Verba | 63098 | verba.localhost |
| Apps & UIs | Apache Zeppelin | 63099 | zeppelin.localhost |
<!-- TOPOLOGY:END -->

## 4. Core Services

### 4.1 Service overview

| Service | Direct URL | Kong URL | Purpose | Auth required |
|---------|------------|----------|---------|---------------|
| **Atlas Dashboard** | http://localhost:63000 | http://localhost:63000 | Generated service directory with SOURCE state, track context, route links, auth notes, and browser-side reachability checks | None |
| **Open WebUI** | http://localhost:63096 | http://chat.localhost:63000 | AI chat interface | Create account |
| **n8n** | http://localhost:63075 | http://n8n.localhost:63000 | Workflow automation | Owner setup on first visit |
| **Supabase Studio** | http://localhost:63019 | http://supabase-studio.localhost:63000 | Database management | Kong route: `kong_admin` / `DASHBOARD_PASSWORD` from `.env` (direct port is ungated) |
| **ComfyUI** | http://localhost:63052 | http://comfyui.localhost:63000 | Image generation | None |
| **SearxNG** | http://localhost:63054 | http://search.localhost:63000 | Privacy search | None |
| **JupyterHub** | http://localhost:63094 | http://jupyter.localhost:63000 | Data science IDE — ships Python + Scala 2.13 + Scala 3 kernels; configured for VS Code remote-Jupyter (see [services/jupyterhub/README.md](services/jupyterhub/README.md) §10). | Token (optional; auto-generated if `JUPYTERHUB_TOKEN` is empty — grep from `docker logs ${PROJECT_NAME}-jupyterhub`) |
| **Neo4j Browser** | http://localhost:63024 | http://graph.localhost:63000 | Graph database | `neo4j` / `GRAPH_DB_PASSWORD` from `.env` |
| **Backend API** | http://localhost:63093 | http://api.localhost:63000 | REST API | None by default (local/dev surface; add gateway auth before exposing beyond a trusted host) |
| **LiteLLM Gateway** | http://localhost:63040 | http://litellm.localhost:63000 | OpenAI-compatible LLM front door (Ollama + cloud). The same alias 302-redirects `/` → `/ui/` (admin dashboard). | API: `LITELLM_MASTER_KEY` (Bearer). Dashboard: `admin` / `${LITELLM_MASTER_KEY}` |
| **Audio (TTS + STT)** | TTS: http://localhost:63056, STT: http://localhost:63053 | http://tts.localhost:63000, http://stt.localhost:63000 | Default install: Speaches serves both `/v1/audio/speech` (Kokoro/Piper) and `/v1/audio/transcriptions` (Faster-Whisper). Engine-specific overrides — Chatterbox on `:63057`, Speaches on `:63058`, host-side variants resolved via `*_LOCALHOST_PORT`. See [services/tts-provider/README.md](services/tts-provider/README.md) and [services/stt-provider/README.md](services/stt-provider/README.md). | None |
| **Docling Processor** | http://localhost:63051 | http://docling.localhost:63000 | Document processing | None |
| **Apache Tika** | http://localhost:63055 | http://tika.localhost:63000 | Long-tail fallback text extraction for formats Docling should not own | Kong route: `kong_admin` / `DASHBOARD_PASSWORD` from `.env` (direct port is ungated) |
| **OpenClaw Agent** | http://localhost:63076 | http://openclaw.localhost:63000 | AI agent (messaging) | Token (optional) |
| **Hermes Agent** | http://localhost:63072 (API), http://localhost:63073 (dashboard) | http://hermes.localhost:63000 | Programmable AI agent runtime (Nous Research) | `HERMES_API_KEY` (Bearer) |
| **MinIO Console** | http://localhost:63021 | http://minio.localhost:63000 | S3-compatible object storage admin UI (gated on `MINIO_SOURCE != disabled`). S3 API at `:63020` is NOT aliased — S3 clients use the direct port. | `minioadmin` / `MINIO_ROOT_PASSWORD` |
| **Ray Dashboard** | http://localhost:63002 | http://ray.localhost:63000 | Distributed-compute substrate (cluster head + workers). Disabled by default; opt-in via `--ray-source ray-container-cpu` / `ray-container-gpu`. | None |
| **Apache Spark** | http://localhost:63027 | http://spark.localhost:63000 | Standalone Spark cluster for batch / SQL / DataFrame work. Spark Connect on `:15002`. Disabled by default; opt-in via `--spark-source container --spark-workers N`. | None |
| **Trino** | http://localhost:63029 | http://trino.localhost:63000 | SQL query engine over the Iceberg REST + MinIO lakehouse catalog. Disabled by default; opt-in via `--trino-source container --iceberg-rest-source container --minio-source container`. | Kong route: `kong_admin` / `DASHBOARD_PASSWORD` from `.env` (direct port is ungated) |
| **Redpanda Console** | http://localhost:63011 | http://redpanda.localhost:63000 | Kafka-compatible streaming broker console. Kafka clients use `redpanda:9092` in-network or `localhost:63010` from the host. Disabled by default; opt-in via `--redpanda-source container`. | Kong route: `kong_admin` / `DASHBOARD_PASSWORD` from `.env` (direct port is ungated) |
| **Apache Zeppelin** | http://localhost:63099 | http://zeppelin.localhost:63000 | Spark-first notebook UI. Spark interpreter pre-configured for the in-stack standalone Spark master plus MinIO S3A and Iceberg REST; JDBC interpreter ships with credentials in env vars but needs a one-time UI-driven `postgres` profile setup. Requires Spark (gated). Disabled by default. | None |
| **Apache Airflow** | http://localhost:63070 | http://airflow.localhost:63000 | Code-defined DAG orchestrator. LocalExecutor + AI/ML SDK with LiteLLM-wired LLM operators. Disabled by default. | `admin` / auto-generated `AIRFLOW_ADMIN_PASSWORD` |
| **Prometheus** | http://localhost:63006 | http://prometheus.localhost:63000 | Metrics scraper + TSDB. Disabled by default; opt-in via `--prometheus-source container`. Bundled with `node-exporter` and `cAdvisor`. 13 scrape jobs cover the application + infra tiers — see [services/prometheus/README.md](services/prometheus/README.md). | None |
| **Grafana** | http://localhost:63009 | http://grafana.localhost:63000 | Observability dashboards + unified alerting on top of Prometheus. Disabled by default; opt-in via `--grafana-source container`. 7 starter dashboards ship pre-provisioned. | `admin` / auto-generated `GRAFANA_ADMIN_PASSWORD` (first-run) |
| **LightRAG** | `http://lightrag.localhost:${KONG_HTTP_PORT}` (WebUI), `http://localhost:${LIGHTRAG_API_PORT}/webui` | http://lightrag.localhost:63000 | Graph-augmented RAG server. KG + vector + multimodal ingestion. Disabled by default. | None |
| **TEI Reranker** | `http://localhost:${TEI_RERANKER_PORT}/rerank` (API only) | http://rerank.localhost:63000 | Cross-encoder reranker (default `mixedbread-ai/mxbai-rerank-base-v1`) for RAG quality lift. Disabled by default. | None |

### 4.2 Database layer
- **PostgreSQL (Supabase)** — primary database with auth, storage, realtime
- **Neo4j** — graph database for relationships and graph queries
- **Weaviate** — vector database for embeddings and semantic search
- **MinIO** — S3-compatible artifact-tier object storage (ComfyUI outputs, Backend blobs, n8n files, JupyterHub datasets, Doc Processor output). Complements Supabase Storage rather than replacing it.
- **Redis** — cache and message queue

### 4.3 AI services
- **LiteLLM Gateway** — always-on OpenAI-compatible front door for every LLM provider in the stack (one URL, one key)
- **Ollama** — local LLM inference engine behind LiteLLM (supports CPU/GPU/localhost/none)
- **ComfyUI** — image generation with workflows
- **STT layer** — pluggable speech-to-text: Speaches (default, Faster-Whisper inside, CPU-friendly), NVIDIA Parakeet-TDT (SOTA EN/EU), whisper.cpp localhost (best on Apple Silicon)
- **TTS layer** — pluggable text-to-speech: Speaches (default, Kokoro + Piper voices), Chatterbox (voice cloning, MIT-licensed)
- **Docling** — document processing with table extraction (IBM Docling, GPU-accelerated)
- **OpenClaw** — AI agent for messaging platforms (WhatsApp, Telegram, Discord), file management, and task automation
- **Hermes Agent** — programmable AI agent runtime (Nous Research) with skills, memory, voice, and tool use; routes reasoning through LiteLLM and appears as the `hermes-agent` model to every consumer
- **Deep Researcher** — research assistant
- **LangMem** — persistent conversation memory with automated fact extraction, semantic recall, and consolidation (embedded in Backend)
- **Ray** — distributed-compute substrate (head + workers) for parallelizing Python workloads (data prep, fine-tuning, batch inference). Disabled by default; enable via `--ray-source ray-container-cpu` (or `ray-container-gpu`). Consumed by Backend / JupyterHub via `RAY_ADDRESS`.
- **LightRAG** — graph-augmented RAG server. KG + vector + multimodal ingestion. Default disabled.
- **TEI Reranker** — Cross-encoder reranker (default `mixedbread-ai/mxbai-rerank-base-v1`) for RAG quality lift. Default disabled.

## 5. Usage Guide

### 5.1 Command line interface

```bash
# Interactive wizard (recommended for first-time setup)
./start.sh                    # Launches step-by-step configuration wizard

# Direct commands (skip wizard)
./start.sh --llm-provider-source ollama-localhost  # Any configuration flag skips the wizard
./start.sh --help            # Show all options
./stop.sh                    # Stop services, keep data
./stop.sh --cold             # Stop and remove all data (also runs a global docker system prune)

# Port and network
./start.sh --base-port 64000  # Custom port range
./start.sh --setup-hosts      # Configure *.localhost domains
./start.sh --skip-hosts       # Skip hosts file checks and setup
./start.sh --no-splash        # Skip the opening splash (also: set ATLAS_NO_SPLASH=1)
                              # Note: Atlas startup artwork is OFF by default; set ATLAS_SPLASH=1 to enable
./start.sh --no-port-migrate  # Skip the chained .env migrations for this run (sentinels not stamped)
./start.sh --profile prod     # Production hardening: localhost-only port binding (HOST_BIND_IP),
                              # json-file log rotation, observability defaulted on, localhost sources
                              # hidden. (Per-service resource limits are always-on .env defaults, not
                              # gated by this flag.) Does not bypass the wizard.

# SOURCE overrides (temporary)
./start.sh --llm-provider-source ollama-localhost
./start.sh --comfyui-source container-gpu
./start.sh --stt-provider-source whisper-cpp-localhost  # Best on Apple Silicon
./start.sh --tts-provider-source chatterbox-localhost   # Voice cloning, native
./start.sh --doc-processor-source docling-container-gpu # Enable document processing
./start.sh --openclaw-source container                 # Enable OpenClaw agent
./start.sh --hermes-source localhost                   # Use a host-installed Hermes
./start.sh --hermes-source disabled                    # Skip Hermes entirely
./start.sh --n8n-source disabled

# Enable LightRAG with default backends (uses Supabase + Neo4j + Redis)
./start.sh --lightrag-source=container --tei-reranker-source=container-cpu

# Combined examples
./start.sh --cold --base-port 55666 --llm-provider-source ollama-localhost

# Cloud provider keys + models (skip the wizard for cloud config)
./start.sh \
  --cloud-openai-source enabled --openai-api-key sk-... \
  --openai-models "gpt-5,gpt-5-mini,text-embedding-3-large"

# Multi-provider cloud setup
./start.sh \
  --cloud-openai-source enabled --openai-api-key sk-... --openai-models gpt-5 \
  --cloud-anthropic-source enabled --anthropic-api-key sk-ant-... --anthropic-models "claude-opus-4-7,claude-sonnet-4-6" \
  --cloud-openrouter-source enabled --openrouter-api-key sk-or-... --openrouter-models openrouter/auto

# Ollama model selection (CLI parity with the wizard's multiselects)
./start.sh \
  --llm-provider-source ollama-container-cpu \
  --ollama-models "qwen3.6:latest,nomic-embed-text" \
  --ollama-custom-models "mistral:7b,phi4:latest"
```

`*_USER_MODELS` env vars persist across runs, so you only need to pass the model flags once unless you want to change the active set. Cloud-provider key/source flags also imply `--cloud-X-source enabled` when paired with `--X-api-key`.

#### 5.1.1 Stop script options

```bash
# Basic stop commands
./stop.sh                    # Stop services, keep data
./stop.sh --cold             # Stop and remove all data (destructive; includes a global `docker system prune -f --volumes` that ALSO touches unused images/volumes belonging to OTHER docker projects on this host)
./stop.sh --clean-hosts      # Remove *.localhost entries from hosts file
./stop.sh --help             # Show all options

# The --cold option removes all Docker volumes (data loss). The `docker
# system prune -f --volumes` step is host-wide — any unrelated Docker
# project's dangling volumes / images on the same machine are pruned too.
# Use with caution — all database data will be permanently deleted.
```

### 5.2 Service access patterns

**Direct access:**
- Most services accessible directly via `http://localhost:PORT`
- Suitable for development and debugging

**Kong gateway routing:**
- Services with `*.localhost` URLs route through Kong
- Provides centralized authentication and rate limiting
- Requires hosts file setup: `./start.sh --setup-hosts`

## 6. Advanced Configuration

### 6.1 Custom deployments

See [docs/deployment/source-configuration.md](docs/deployment/source-configuration.md) for detailed SOURCE configuration guides.

### 6.2 GPU setup

For NVIDIA GPU acceleration, set the relevant SOURCE variables to a `*-container-gpu` variant (e.g., `LLM_PROVIDER_SOURCE=ollama-container-gpu`, `COMFYUI_SOURCE=container-gpu`, `STT_PROVIDER_SOURCE=speaches-container-gpu` or `parakeet-container-gpu`, `TTS_PROVIDER_SOURCE=speaches-container-gpu` or `chatterbox-container-gpu`). See [docs/deployment/source-configuration.md](docs/deployment/source-configuration.md) for the full list of GPU variants per service.

### 6.3 Using as infrastructure foundation

Atlas is designed to back other projects (e.g. a RAG-showcase app) as shared infrastructure. The two ready-today paths are **standalone + shared network** (your project is a separate repo that joins `${PROJECT_NAME}-network`) and **Git submodule** (vendor Atlas into your repo). For the full decision guide — which method, what's ready, how to wire and customize it — see **[Reusing Atlas as Infrastructure](docs/deployment/reusing-atlas.md)**.

The submodule path in brief:

```bash
# Add as submodule in your project (swap in your fork URL if you vendored one)
git submodule add https://github.com/thekaveh/atlas.git infra

# Configure and start
cd infra
cp .env.example .env
# Edit .env: Set PROJECT_NAME=myproject
./start.sh

# Access from your application
# - Docker network: myproject-network
# - Kong gateway: http://localhost:63000
# - Direct ports: derived from BASE_PORT, default http://localhost:63000+
```

**Capabilities:**
- Infrastructure code separated from application code
- Upstream updates can be pulled while maintaining local settings
- Project-specific environment configurations
- Standard git workflow for contributing changes upstream
- Multiple isolated instances with separate Docker resources

**Integration patterns:**
1. **Docker network** — connect your services to `${PROJECT_NAME}-network`
2. **Kong gateway** — use Kong as single entry point (port 63000)
3. **Direct access** — access services via exposed ports

See [docs/deployment/submodule-usage.md](docs/deployment/submodule-usage.md) for the complete guide including:
- Detailed setup instructions
- Integration patterns with code examples
- Contributing back to atlas
- Troubleshooting submodule issues

## 7. Development

### 7.1 Project structure
```
atlas/
├── bootstrapper/              # Python startup, SOURCE parsing, port/Kong generation, wizard
│   ├── services/              # Manifest loader, validator, env_assembler, hooks, sc_synthesizer
│   ├── schemas/               # JSON Schemas for service.yml manifests
│   ├── tests/                 # 1,300+ tests (loader, validator, byte-equiv, source-permutation, hooks)
│   ├── tools/                 # validate_fragments CLI lint
│   └── start.py / stop.py     # Entry points
├── services/                  # One folder per service family — single source of truth
│   ├── globals/               # Project-wide vars (PROJECT_NAME, BASE_PORT, BRAND_*, tier ordering)
│   ├── supabase/              # supabase-db, db-init, meta, storage, auth, api, realtime, studio
│   │   ├── service.yml        # Manifest: env vars, source variants, deps, runtime_sc slice
│   │   ├── compose.yml        # Compose fragment for the family
│   │   └── db/                # SQL init scripts + snapshots (bind-mounted into supabase-db-init)
│   ├── litellm/               # LiteLLM gateway + init
│   │   ├── service.yml
│   │   ├── compose.yml
│   │   ├── init/              # litellm-init Dockerfile + scripts (config.yaml renderer)
│   │   └── models.yaml        # Curated cloud-provider model catalog (per-service SoT)
│   ├── ollama/                # ollama + ollama-pull (pull/ scripts); models.yaml = Ollama catalog SoT
│   ├── redis/                 # Redis cache/queue substrate (AOF persistence, shared by n8n/Kong/LiteLLM/owui/LightRAG)
│   ├── weaviate/              # weaviate + multi2vec-clip + weaviate-init
│   ├── comfyui/               # comfyui + comfyui-init (init/ scripts); models.yaml + custom-models.yaml = ComfyUI catalog SoT
│   ├── n8n/                   # n8n + n8n-worker + n8n-init (with init/ assets, workflows-stage/)
│   ├── open-webui/            # open-web-ui + open-webui-init (with extras/ tools+functions)
│   ├── hermes/                # hermes + hermes-init (with init/ scripts & templates)
│   ├── minio/                 # minio + minio-init (with init/ bucket provisioning scripts)
│   ├── backend/               # FastAPI backend (with app/ source code)
│   ├── jupyterhub/            # JupyterHub (with build/ Dockerfile + notebooks)
│   ├── neo4j/                 # Neo4j (with build/ Dockerfile + scripts)
│   ├── parakeet/              # STT engine (parakeet-gpu, with provider/ source code)
│   ├── speaches/              # Unified TTS+STT engine (CPU/GPU)
│   ├── chatterbox/            # TTS engine (GPU, voice cloning)
│   ├── tts-provider/          # Virtual manifest — TTS source selector (with provider/ host notes)
│   ├── docling/               # Document processor (with provider/ source code)
│   ├── searxng/               # SearXNG (with config/ settings.yml)
│   ├── local-deep-researcher/ # Research agent (with build/ Dockerfile)
│   ├── lightrag/              # LightRAG graph-RAG server + init (opt-in via LIGHTRAG_SOURCE)
│   ├── tei-reranker/          # TEI reranker (opt-in via TEI_RERANKER_SOURCE)
│   ├── openclaw/              # OpenClaw agent gateway + init
│   ├── kong/                  # Kong API gateway
│   ├── ray/                   # Ray distributed-compute substrate (head + workers)
│   ├── prometheus/            # Metrics scraper + TSDB (with config/ scrape jobs, opt-in via PROMETHEUS_SOURCE)
│   ├── grafana/               # Observability dashboards + unified alerting (with config/ provisioning, opt-in via GRAFANA_SOURCE)
│   ├── spark/                 # Apache Spark standalone cluster — master + worker + history + init (opt-in via SPARK_SOURCE)
│   ├── zeppelin/              # Apache Zeppelin Spark-first notebook UI (opt-in via ZEPPELIN_SOURCE; gated on Spark)
│   ├── airflow/               # Apache Airflow 3.x DAG orchestrator (with build/ Dockerfile + dags/, opt-in via AIRFLOW_SOURCE)
│   ├── cloud-providers/       # Virtual manifest — OpenAI/Anthropic/OpenRouter toggles
│   ├── stt-provider/          # Doc-only — aggregate STT provider documentation
│   ├── doc-processor/         # Doc-only — aggregate doc-processor documentation
│   ├── multi2vec-clip/        # Doc-only — aggregate multi2vec-clip documentation (container ships inside weaviate/)
│   └── _user/                 # (Gitignored) downstream submodule consumers' overlay slot
├── docs/                      # User, service, deployment, diagram, and planning docs
│   ├── CONTRIBUTING-services.md  # How to add a new service to the modular layout
│   └── …
├── scripts/                   # Top-level utility scripts (e.g. migration helpers)
├── docker-compose.yml         # ~70-line thin shell — include: list pulling each fragment
├── .env.example               # Configuration template (auto-generated from manifests via env_assembler; byte-equivalence enforced by tests)
├── start.sh / stop.sh         # Entry points
└── .github/workflows/         # CI: services-lint (validator, byte-equiv, source-permutation)
```

Top-level is intentionally minimal: `bootstrapper/`, `docs/`, `scripts/`, `services/`. Every service lives entirely under its `services/<name>/` folder — init scripts, source code, build context, config files — so opening a service folder shows everything that defines it.

### 7.2 Adding services

New services are declared by creating `services/<name>/{service.yml, compose.yml}` and adding the fragment to the `include:` list in `docker-compose.yml`. The manifest's `runtime_sc:` block declares per-source scale/environment/deploy/extra_hosts; `bootstrapper/services/sc_synthesizer.py` concatenates these slices into the dict that the bootstrapper consumes at startup. See [docs/CONTRIBUTING-services.md](docs/CONTRIBUTING-services.md) for the full walkthrough.

## 8. Troubleshooting

### 8.1 Common issues

**Port conflicts:**
```bash
./start.sh --base-port 64000  # Use different port range
lsof -i :63000               # Check what's using port
```

**Memory issues:**
```bash
# Increase Docker memory in Docker Desktop
# Settings → Resources → Memory (set to 10-12GB)
```

**Service health:**
```bash
docker compose ps              # Check service status
docker logs ${PROJECT_NAME}-litellm -f   # Check LLM gateway logs
docker logs ${PROJECT_NAME}-ollama -f    # Check Ollama upstream logs (if enabled)
```

### 8.2 Detailed troubleshooting

For longer-form troubleshooting guides, see [docs/quick-start/troubleshooting.md](docs/quick-start/troubleshooting.md).

## 9. Documentation

The [documentation index](docs/README.md) is the top-level navigation hub.
Key entry points by audience:

### 9.1 First-time users
- [Interactive setup wizard](docs/quick-start/interactive-setup-wizard.md) — step-by-step guided configuration on first `./start.sh`
- [Quick Start guides](docs/quick-start/) — installation and first-run
- [Troubleshooting](docs/quick-start/troubleshooting.md) — common issues and known-benign warnings
- [Expected startup warnings](docs/deployment/expected-startup-warnings.md) — log lines that look scary but aren't

### 9.2 Operators
- [SOURCE configuration](docs/deployment/source-configuration.md) — every service's container / localhost / disabled modes (and GPU variants for the LLM/embedding engines)
- [Ports and routes](docs/deployment/ports-and-routes.md) — canonical port offsets, direct URLs, and Kong routes
- [Reusing Atlas as infrastructure](docs/deployment/reusing-atlas.md) — overview + decision guide for backing another project with Atlas
- [Using as a submodule](docs/deployment/submodule-usage.md) — deep-dive for the Git-submodule reuse method
- [Service documentation](services/) — per-service READMEs (each owns its manifest, compose fragment, and architecture diagram)

### 9.3 Contributors
- [CONTRIBUTING-services.md](docs/CONTRIBUTING-services.md) — adding a new service to the stack (manifest schema, compose fragment, docs regen)
- [SECURITY.md](SECURITY.md) — threat model, supported versions, responsible-disclosure address
- [Cross-service integration matrix](docs/research/integration-matrix.md) — Phase B research index linking every service to its candidate integrations
- [Research rows](docs/research/rows/) and [candidates](docs/research/candidates/) — per-service / per-candidate integration design notes

### 9.4 Release history
- [ROADMAP.md](docs/ROADMAP.md) — future development plans and tier-status of every service
- [CHANGELOG.md](docs/CHANGELOG.md) — release history and the `[Unreleased]` known-follow-ups block
- [Releasing & version tags](docs/deployment/releasing.md) — semver tag convention for pinning a vendored Atlas

## 10. Contributing

Contributions welcome. Open a PR or an issue to propose changes.

## 11. License

[Apache License 2.0](LICENSE)

## 12. Support

- Check the [documentation](docs/README.md)
- Report issues on [GitHub Issues](https://github.com/thekaveh/atlas/issues)
- Ask questions in [GitHub Discussions](https://github.com/thekaveh/atlas/discussions)
