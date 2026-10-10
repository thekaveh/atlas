<h1 align="center">Atlas</h1>

<p align="center">
  <strong>A self-hosted, pre-integrated gen-AI, ML, and data platform — one Docker Compose stack</strong>
</p>

<p align="center">
  Chat, RAG, agents, distributed compute, and a full data platform — integrated services, each selectable among the deployment modes it supports.
</p>

Atlas is a self-hosted engineering platform that bundles 58 service families behind a Kong gateway and an adaptive FastAPI backend. They cover LLM inference and a gateway, vector and graph databases, workflow and DAG automation, distributed compute, object storage, notebooks, and observability.

The services are integrated, not only co-located. Kong routes every `*.localhost` host, and LiteLLM puts local and cloud models behind one API. The backend connects to whichever vector, graph, workflow and media services you enable. Supabase supplies the shared database, storage and API identity.

**Who it is for**. Developers and engineers who want a local or single-host stack for gen-AI, RAG, ML and data work, with each service configurable.

**What it does not cover**. Atlas runs on one Docker host with Docker Compose. It has no multi-host clustering, failover or Kubernetes deployment, and no single sign-on across dashboards. Every service family is currently `experimental`: selectable, but not yet qualified with cited evidence (see the [service catalog](docs/services.md)). The trading track supports research and paper portfolios only, not live trading.

## 1. Quick start

Before you start, install:

- Docker with Docker Compose v2.20.3 or newer (v2.26+ recommended).
- `uv`, or Python 3.10 or newer.
- Git.

The wizard preselects the `gen-ai-rag` track: chat UI, workflow automation, vector and graph databases, private web search, and deep research on a CPU Ollama engine. The `all` track (the `.env.example` defaults) adds ComfyUI, JupyterHub, Hermes, MinIO, speech-to-text, text-to-speech and CLIP. Measured setup time, memory and disk per track are not published yet; larger tracks need more of each.

Clone the repository and run `./start.sh` from the repository root (`atlas/`):

```bash
git clone https://github.com/thekaveh/atlas && cd atlas
./start.sh
```

`./start.sh` with no arguments opens the setup wizard. It asks for track and profile, base port and project name, per-service SOURCE choices and host aliases, then shows a launch summary.

When the launch finishes, open the root dashboard at `http://localhost:<BASE_PORT>`. With the default base port this is `http://localhost:63000`. `KONG_HTTP_PORT` in `.env` holds the dashboard port, which is derived from `BASE_PORT`.

Next: the [first-run walkthrough](docs/quick-start/index.md) and the [wizard guide](docs/quick-start/interactive-setup-wizard.md).

## 2. At a glance

- **58 service families across 7 tracks**. Only configurable services belong to tracks, and tracks can overlap. A track preselects the services for one workload. The tracks are Generative AI · RAG, Generative AI · Engineering, Generative AI · Creative, ML Engineering, Data Engineering, Trading / Financial Research, and All / Custom. See [Tracks](docs/tracks.md).
- **Core services**. The always-on core is Kong, Supabase, Redis, LiteLLM and the Backend API.
- **Per-service SOURCE**. Each configurable service runs in a container, uses an instance on your host, or is disabled. The variants differ by family; see the [SOURCE Configuration Guide](docs/operations/source-configuration.md).
- **Ports**. Every host port derives from one `BASE_PORT` (default `63000`). Both profiles bind published ports to loopback unless you set `HOST_BIND_IP`. See [Ports and Routes](docs/operations/ports-and-routes.md) and [Access and Credentials](docs/operations/access-and-credentials.md).
- **Profiles**. `--profile dev` (alias `default`) or `--profile prod`, which also enables Prometheus and Grafana. See [Configuration](docs/configuration.md).
- **Logins**. Each dashboard keeps its own login; there is no single sign-on. See [Access and Credentials](docs/operations/access-and-credentials.md).

<p align="center">
  <img src="./assets/atlas-poster-blue-banner.jpg" alt="Atlas poster: a blue wireframe Titan holds a glowing gold globe above the ATLAS-PLATFORM wordmark, on a dark starfield" width="720">
</p>

<p align="center">
  <img alt="Docker Compose: orchestration" src="https://img.shields.io/badge/Docker%20Compose-orchestration-2496ED?logo=docker&logoColor=white">
  <img alt="Ollama: local LLMs" src="https://img.shields.io/badge/Ollama-local%20LLMs-000000?logo=ollama&logoColor=white">
  <img alt="LiteLLM: LLM gateway" src="https://img.shields.io/badge/LiteLLM-LLM%20gateway-2563EB">
  <img alt="Kong: API gateway" src="https://img.shields.io/badge/Kong-API%20gateway-003459?logo=kong&logoColor=white">
</p>

<p align="center">
  <img alt="vLLM: inference" src="https://img.shields.io/badge/vLLM-inference-0F8041">
  <img alt="Supabase: Postgres" src="https://img.shields.io/badge/Supabase-Postgres-3FCF8E?logo=supabase&logoColor=white">
  <img alt="Weaviate: vector" src="https://img.shields.io/badge/Weaviate-vector-262C30?logo=weaviate">
  <img alt="Neo4j: graph" src="https://img.shields.io/badge/Neo4j-graph-4581FF?logo=neo4j&logoColor=white">
  <img alt="Redis: cache" src="https://img.shields.io/badge/Redis-cache-DC382D?logo=redis&logoColor=white">
  <img alt="MinIO: object" src="https://img.shields.io/badge/MinIO-object-C72E49?logo=minio">
  <img alt="Ray: compute" src="https://img.shields.io/badge/Ray-compute-028CF0?logo=ray">
  <img alt="Spark: compute" src="https://img.shields.io/badge/Spark-compute-E25A1C?logo=apachespark&logoColor=white">
  <img alt="n8n: workflow" src="https://img.shields.io/badge/n8n-workflow-EA4B71?logo=n8n&logoColor=white">
  <img alt="Airflow: orchestrator" src="https://img.shields.io/badge/Airflow-orchestrator-017CEE?logo=apacheairflow&logoColor=white">
  <img alt="JupyterHub: notebooks" src="https://img.shields.io/badge/JupyterHub-notebooks-F37626?logo=jupyter&logoColor=white">
  <img alt="Zeppelin: notebooks" src="https://img.shields.io/badge/Zeppelin-notebooks-FFD700">
  <img alt="Prometheus: metrics" src="https://img.shields.io/badge/Prometheus-metrics-E6522C?logo=prometheus&logoColor=white">
  <img alt="Grafana: dashboards" src="https://img.shields.io/badge/Grafana-dashboards-F46800?logo=grafana&logoColor=white">
  <img alt="Langfuse: tracing" src="https://img.shields.io/badge/Langfuse-tracing-FF7E29">
</p>

[![Terminal screenshot of the setup wizard during a launch: a 36-service overview above a live Docker log pane](./docs/screenshots/wizard-running.png)](./docs/screenshots/wizard-running.png)

*The setup wizard during a live `./start.sh` launch of Atlas v0.1.0 on 2026-06-19. Its overview lists 36 services, 34 of them enabled, on base port 64075. Ollama and ComfyUI use host (`localhost`) sources, and the cloud APIs are off. The capture does not record the host hardware.*

## 3. Service topology

<!-- TOPOLOGY:BEGIN -->
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
| Data | Supabase Meta | — | — |
| Data | Supabase Storage | 63015 | — |
| Data | Supabase Auth | — | — |
| Data | Supabase API | 63017 | — |
| Data | Supabase Realtime | 63018 | — |
| Data | Supabase Studio | — (Kong only) | supabase-studio.localhost |
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
| LLM Core | vLLM (Metal) | — | — |
| Media | Blender MCP | — | — |
| Media | Crawl4AI | 63050 | crawl4ai.localhost |
| Media | Document Processor | 63051 | docling.localhost |
| Media | FAL Cloud Media | — | — |
| Media | Asset Baker | 63052 | asset-baker.localhost |
| Media | Asset Worker | 63053 | asset-worker.localhost |
| Media | ComfyUI | 63054 | comfyui.localhost |
| Media | STT Provider | 63055 | stt.localhost |
| Media | SearxNG | 63056 | search.localhost |
| Media | Apache Tika | 63057 | tika.localhost |
| Media | TTS Provider | 63058 | tts.localhost |
| Agents & Workflows | Apache Airflow | 63070 | airflow.localhost |
| Agents & Workflows | Celery Worker | — | — |
| Agents & Workflows | Flower | 63071 | flower.localhost |
| Agents & Workflows | Hermes Agent | 63072 | hermes.localhost |
| Agents & Workflows | LightRAG | 63074 | lightrag.localhost |
| Agents & Workflows | n8n | 63075 | n8n.localhost |
| Agents & Workflows | OpenClaw | 63076 | openclaw.localhost |
| Agents & Workflows | Curated MCP Servers | 63078 | mcp.localhost |
| Agents & Workflows | TrueForge | 63079 | trueforge.localhost |
| Apps & UIs | Jenkins | 63090 | jenkins.localhost |
| Apps & UIs | Label Studio | 63091 | label-studio.localhost |
| Apps & UIs | MLflow | 63092 | mlflow.localhost |
| Apps & UIs | Backend API | 63093 | api.localhost |
| Apps & UIs | JupyterHub | 63094 | jupyter.localhost |
| Apps & UIs | Neo4j LLM Graph Builder | 63095 | graphbuilder.localhost |
| Apps & UIs | Open WebUI | 63096 | chat.localhost |
| Apps & UIs | Local Deep Researcher | 63097 | research.localhost |
| Apps & UIs | Verba | 63098 | verba.localhost |
| Apps & UIs | Apache Zeppelin | 63099 | — |
<!-- TOPOLOGY:END -->

[![Atlas architecture: clients reach the Kong gateway, which fronts apps, agents, the LLM core, media services and data stores, plus opt-in compute and observability](./docs/diagrams/architecture.svg)](./docs/diagrams/architecture.svg)

*Kong routes services with declared host aliases; loopback-only interfaces such as Zeppelin bypass the gateway.*

Full port + Kong-route detail: [docs/reference/ports-routes.md](docs/reference/ports-routes.md) and [docs/operations/ports-and-routes.md](docs/operations/ports-and-routes.md). Per-service documentation: [docs/services.md](docs/services.md).

## 4. Documentation

[docs/README.md](docs/README.md) is the full documentation index. Key entry points:

- **Getting started** — [Quick Start](docs/quick-start/index.md), [Interactive Setup Wizard](docs/quick-start/interactive-setup-wizard.md), [Quick Start Troubleshooting](docs/quick-start/troubleshooting.md), [Sudo recovery](docs/TROUBLESHOOTING.md)
- **Core concepts** — [Core Concepts](docs/core-concepts.md) (SOURCE values, tracks, manifests, gateway access), [SOURCE reference](docs/reference/source-values.md), [Tracks](docs/tracks.md)
- **Operating the stack** — [Service catalog](docs/services.md), [SOURCE configuration](docs/operations/source-configuration.md), [Ports and routes](docs/operations/ports-and-routes.md), [Architecture diagrams](docs/architecture/index.md)
- **Running Atlas for another project** — [Reusing Atlas as Infrastructure](docs/operations/reusing-atlas.md), [Using as a submodule](docs/operations/submodule-usage.md)
- **Contributing** — [Contributing guide](CONTRIBUTING.md) (setup, one safe test per area, branch target, required checks), [Development](docs/development.md) (repository and consumer layout, docs checks), [Adding a service](docs/CONTRIBUTING-services.md), [Security policy](SECURITY.md)
- **Release history** — [ROADMAP](docs/ROADMAP.md), [CHANGELOG](docs/CHANGELOG.md), [Releasing & version tags](docs/operations/releasing.md)
- **Project & internal docs** — research, strategy, and maintenance notes live under `docs/`: [docs/research/README.md](docs/research/README.md), [docs/strategy/README.md](docs/strategy/README.md), [docs/maintenance/README.md](docs/maintenance/README.md)

## 5. Contributing

Contributions are welcome. Start with the [contributing guide](CONTRIBUTING.md). It covers setup, one safe test for each code area, and the Docker and live-test boundary. It also covers the pull request against `develop` and its four required checks. For anything larger than a typo, open an issue first so the scope is agreed.

## 6. License

[Apache License 2.0](LICENSE)

## 7. Support

- Check the [documentation](docs/README.md)
- Report bugs and request features on [GitHub Issues](https://github.com/thekaveh/atlas/issues)
- Ask questions by [opening an issue with the `question` label](https://github.com/thekaveh/atlas/issues/new?labels=question)
- Report security vulnerabilities privately through the [security policy](SECURITY.md), never as a public issue
