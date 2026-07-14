# Atlas Documentation

Documentation index for Atlas.

## 1. Documentation structure

### 1.1 Quick Start guides
- [Atlas documentation home](index.md) — generated MkDocs home for the Atlas `.io` / GitHub Pages documentation surface at <https://thekaveh.github.io/atlas/>
- [Interactive Setup Wizard](quick-start/interactive-setup-wizard.md) — step-by-step guided configuration
- [Troubleshooting](quick-start/troubleshooting.md) — common issues and solutions across the full stack
- [Startup Troubleshooting](TROUBLESHOOTING.md) — quick fixes for first-launch errors (sudo recovery, Airflow ResolutionImpossible, n8n restart-loops); linked from `start.sh`'s own error output

### 1.2 Service documentation
- [Backend API](../services/backend/README.md) — always-on adaptive FastAPI service
- [Open WebUI](../services/open-webui/README.md) — main chat UI
- [LiteLLM Gateway](../services/litellm/README.md) — always-on OpenAI-compatible front door for every LLM provider
- [Ollama (LiteLLM upstream)](../services/ollama/README.md) — local LLM engine modes (container CPU/GPU, localhost, none)
- [ComfyUI](../services/comfyui/README.md) — image generation workflows
- [Weaviate](../services/weaviate/README.md) — vector database
- [MinIO](../services/minio/README.md) — S3-compatible artifact-tier object storage
- [n8n](../services/n8n/README.md) — workflow automation
- [SearxNG](../services/searxng/README.md) — privacy metasearch
- [Redis](../services/redis/README.md) — cache/queue infrastructure
- [Local Deep Researcher](../services/local-deep-researcher/README.md) — research/orchestration service
- [Multi2Vec CLIP](../services/multi2vec-clip/README.md) — multimodal vectorization
- [STT Provider (Speech-to-Text)](../services/stt-provider/README.md) — pluggable: Speaches (Faster-Whisper, default), Parakeet-TDT, whisper.cpp
- [TTS Provider (Text-to-Speech)](../services/tts-provider/README.md) — pluggable: Speaches (Kokoro/Piper, default), Chatterbox (voice cloning)
- [Document Processor (Docling)](../services/doc-processor/README.md) — document processing
- [Supabase Ecosystem](../services/supabase/README.md) — database, auth, and storage services
- [Neo4j (Graph Database)](../services/neo4j/README.md) — graph database service
- [Kong (API Gateway)](../services/kong/README.md) — dynamic API gateway configuration
- [JupyterHub (Data Science IDE)](../services/jupyterhub/README.md) — interactive Jupyter Lab environment
- [LightRAG](../services/lightrag/README.md) — graph-augmented RAG server with WebUI + multimodal ingestion
- [OpenClaw (AI Agent)](../services/openclaw/README.md) — AI agent for messaging platforms
- [Hermes Agent](../services/hermes/README.md) — programmable AI agent runtime (Nous Research)
- [Ray](../services/ray/README.md) — distributed compute substrate (head + workers, opt-in via `RAY_SOURCE`)
- [Prometheus](../services/prometheus/README.md) — observability scraper + TSDB with bundled node-exporter and cAdvisor (opt-in via `PROMETHEUS_SOURCE`)
- [Grafana](../services/grafana/README.md) — observability dashboards + unified alerting on top of Prometheus (opt-in via `GRAFANA_SOURCE`)
- [Apache Spark](../services/spark/README.md) — standalone Spark cluster (5-container family: master + workers + history + Spark Connect gRPC sidecar + minio/mc bucket init) for batch / SQL / DataFrame workloads (opt-in via `SPARK_SOURCE`)
- [Apache Zeppelin](../services/zeppelin/README.md) — Spark-first notebook UI; Spark interpreter pre-configured, JDBC interpreter requires a one-time UI setup (opt-in via `ZEPPELIN_SOURCE`; gated on Spark)
- [Apache Airflow](../services/airflow/README.md) — code-defined DAG orchestrator (webserver + scheduler + dag-processor + init) with LiteLLM-wired LLM operators (opt-in via `AIRFLOW_SOURCE`)
- [TEI Reranker](../services/tei-reranker/README.md) — Cross-encoder reranker (default `mxbai-rerank-base-v1`) for RAG quality lift
- [Cloudflared Tunnel](../services/cloudflared/README.md) — Cloudflare Tunnel for zero-config public TLS ingress fronting Kong (opt-in via `CLOUDFLARED_SOURCE`)
- [Backup / Restore](../services/backup/README.md) — on-demand Postgres dump + volume tarballs to S3/MinIO with a restore path (opt-in via `BACKUP_SOURCE`)

### 1.3 Deployment guides
- [SOURCE Configuration](deployment/source-configuration.md) — SOURCE-based deployment, including GPU variants
- [Ports and Routes](deployment/ports-and-routes.md) — canonical port offsets, direct URLs, and Kong routes
- [Reusing Atlas as Infrastructure](deployment/reusing-atlas.md) — overview + decision guide: use Atlas as the backing infra for another project (which method, is it ready, how to wire + customize)
- [Using as a Submodule](deployment/submodule-usage.md) — deep-dive for the Git-submodule reuse method
- [Releasing & version tags](deployment/releasing.md) — semver tag convention for pinning a vendored Atlas
- [Expected Startup Warnings](deployment/expected-startup-warnings.md) — known-benign log lines on `./start.sh`

### 1.4 Contributors
- [Adding a service runbook](CONTRIBUTING-services.md) — six-decision walkthrough + the regen + lint chain
- [Security policy](../SECURITY.md) — threat tiers, supported versions, responsible-disclosure address
- [External dependency contract ledger](maintenance/external-contract-ledger.md) — durable record of consumed external API/CLI/config contract checks from maintenance passes

### 1.5 Architecture diagrams
- [Diagrams README](diagrams/README.md) — top-level diagram update workflow + the per-service auto-generation chain
- [Split architecture catalog](architecture/README.md) — MkDocs-linked high-level architecture perspectives generated for the publishable docs site
- The top-level diagram itself lives at [diagrams/architecture.svg](diagrams/architecture.svg) (embedded in the project README) and [diagrams/architecture.html](diagrams/architecture.html) (standalone view)

### 1.6 Cross-service research (Phase B corpus)
- [Research corpus guide](research/README.md) — layout, authoring rules, and the schema the validator enforces
- [Integration matrix](research/integration-matrix.md) — auto-generated index linking every service to its candidate integrations
- [Per-service rows](research/rows/) — missing-pair integrations, candidate new services, per-service feature gaps
- [Candidate one-pagers](research/candidates/) — design notes per candidate service

### 1.7 Feature-track plans and specs
- [superpowers/plans](superpowers/plans/) + [superpowers/specs](superpowers/specs/) — point-in-time implementation plans and specs for the larger 2026-05/06 feature tracks (consult when archaeology on a past track is needed; CHANGELOG entries link the relevant ones)

### 1.8 Numbering-policy notes
- Generated research files keep schema-fixed headings such as `## Headline`; see [research/README.md](research/README.md) for the explicit exemption.
- Provider implementation notes under `services/*/provider/` are operational backend-specific runbooks. They may keep compact unnumbered headings when numbering would make command-oriented maintenance notes harder to scan.
- Conventional history/planning artifacts such as [CHANGELOG](CHANGELOG.md), [ROADMAP](ROADMAP.md), and `docs/plans/` may keep their established release-note or planning heading style when renumbering would obscure chronology.
- Literal UI/output glyphs may appear only when the documentation is naming an actual terminal control, status marker, tree connector, or generated output. Do not use glyphs as decorative prose. Prefer words such as `Warning:` in explanatory text, and keep flow arrows or tree characters inside technical notation or literal examples.

## 2. Related documentation

- [Main README](../README.md) — project overview and quick start
- [ROADMAP](ROADMAP.md) — future development plans
- [CHANGELOG](CHANGELOG.md) — release history and completed features

## 3. Getting help

If you can't find what you're looking for:

1. Check the [Troubleshooting Guide](quick-start/troubleshooting.md)
2. Search through the service-specific documentation
3. Open an issue on GitHub if you need additional help

## 4. Contributing to documentation

- Found a typo or error? Open a PR.
- Missing information? Open an issue.

## 5. Maintainer checks

Run the local documentation drift and audit checks before committing docs changes:

```bash
make docs-check
uv run --project bootstrapper python -m scripts.notebook_reproducibility
uv run --project bootstrapper python scripts/check_doc_links.py
uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check
uv run --project bootstrapper python scripts/check-docs-drift.py
uv run --project bootstrapper python scripts/check-compose-source-deps.py
uv run --project bootstrapper python scripts/check-kong-routes.py
uv run --project bootstrapper python scripts/validate_research_schema.py --all
uv run --project bootstrapper python scripts/check-track-membership.py
(cd services/docling/provider/localhost && uv lock --locked)
```

## 6. Maintainer publication

After the Pages publish and wiki sync are merged and green, update the repository About URL if needed:

```bash
gh repo edit thekaveh/atlas --homepage https://thekaveh.github.io/atlas/
```
