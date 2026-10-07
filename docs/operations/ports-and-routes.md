# 6.3. Ports and Routes

Ports and Kong hostnames are derived from `BASE_PORT` in `.env` (default `63000`) and the per-category slot allocator in `bootstrapper/services/topology.py`. Move the whole stack with `./start.sh --base-port <port>` or by editing `BASE_PORT`.

## 1. Canonical reference

The full per-service port-variable-to-Kong-alias mapping is generated from the service manifests and lives in [docs/reference/ports-routes.md](../reference/ports-routes.md) — that page, not this one, is the single authoritative source for which port variables and Kong aliases a service uses. The README's generated [Service topology](../../README.md#2-service-topology) block presents the same mapping as a browsable table. `bootstrapper/services/topology.py` is the code-level source both are generated from.

This page documents route *behavior* the generated tables don't carry: per-alias auth mechanisms and routing notes (§2), per-engine port quirks (§3), and localhost-mode port overrides (§4).

## 2. Kong hostnames

Run once to add them to `/etc/hosts`:

```bash
./start.sh --setup-hosts
```

The flag is part of a normal start: after the hosts write it launches the stack with the current `.env` (without the wizard). On a fresh checkout, run `./start.sh` once first so the wizard picks the track and sources.

Active aliases (every `*-localhost` source also routes through `host.docker.internal`):

- `airflow.localhost` → Airflow Web UI + REST API (`AIRFLOW_SOURCE != disabled`; same alias serves UI at `/` and REST API under `/api/v2/`). Web UI auth: `admin` / auto-generated `AIRFLOW_ADMIN_PASSWORD` (FAB session cookie). REST API auth: JWT bearer — POST credentials to `/auth/token` first, then attach `Authorization: Bearer <jwt>` to `/api/v2/...` calls. See [services/airflow/README.md](https://github.com/thekaveh/atlas/blob/main/services/airflow/README.md) §6 for the full two-step curl.
- `api.localhost` → Backend API (always-on adaptive; protected routes always enforce application bearer identity, while optional `BACKEND_KONG_AUTH=key-auth` adds an outer `apikey: ${BACKEND_KONG_API_KEY}` gateway gate)
- `asset-baker.localhost` → Asset Baker API (`ASSET_BAKER_SOURCE != disabled`)
- `asset-worker.localhost` → Asset Worker API (`ASSET_WORKER_SOURCE != disabled`)
- `chat.localhost` → Open WebUI (`OPEN_WEB_UI_SOURCE != disabled`)
- `comfyui.localhost` → ComfyUI (`COMFYUI_SOURCE != disabled`)
- `crawl4ai.localhost` → Crawl4AI extraction API (`CRAWL4AI_SOURCE=container`; Kong basic-auth/ACL plus Crawl4AI bearer token for API calls)
- `docling.localhost` → Document processor (`DOC_PROCESSOR_SOURCE != disabled`)
- `flower.localhost` → Flower Celery monitor (`CELERY_SOURCE=container`; Kong dashboard basic-auth/ACL plus Flower basic-auth)
- `graph.localhost` → Neo4j Browser (`NEO4J_GRAPH_DB_SOURCE != disabled`)
- `graphbuilder.localhost` / `graphbuilder-api.localhost` → LLM Graph Builder UI / API (`LLM_GRAPH_BUILDER_SOURCE != disabled`; Kong dashboard basic-auth/ACL)
- `hermes.localhost` → Hermes Agent dashboard (`HERMES_SOURCE != disabled` AND `HERMES_DASHBOARD_ENABLED=true`; Kong requires the dashboard Basic credential)
- `jenkins.localhost` → Jenkins CI (`JENKINS_SOURCE != disabled`)
- `jupyter.localhost` → JupyterHub (`JUPYTERHUB_SOURCE != disabled`)
- `label-studio.localhost` → Label Studio (`LABEL_STUDIO_SOURCE != disabled`; Kong dashboard basic-auth/ACL, then the Label Studio login)
- `langfuse.localhost` → Langfuse LLM-trace UI (`LANGFUSE_SOURCE != disabled`; Kong dashboard basic-auth/ACL, then the Langfuse login)
- `lightrag.localhost` → LightRAG API + WebUI (`LIGHTRAG_SOURCE != disabled`; `preserve_host: True` for the SPA)
- `litellm.localhost` → LiteLLM gateway + admin dashboard (always-on; same alias exposes `/ui/`, `/v1/*`, `/spend/*`)
- `mcp.localhost` → MCP servers gateway (`MCP_SERVERS_SOURCE != disabled`; Kong dashboard basic-auth/ACL)
- `minio.localhost` → MinIO admin console (`MINIO_SOURCE != disabled`)
- `mlflow.localhost` → MLflow tracking UI (`MLFLOW_SOURCE != disabled`; Kong dashboard basic-auth/ACL)
- `s3.minio.localhost` → MinIO S3 API (`MINIO_SOURCE != disabled`; S3 clients can also use the direct `MINIO_PORT`)
- `n8n.localhost` → n8n (`N8N_SOURCE != disabled`)
- `ollama.localhost` → Ollama upstream (`LLM_PROVIDER_SOURCE` is `ollama-container-*` or `ollama-localhost`)
- `openclaw.localhost` → OpenClaw gateway (`OPENCLAW_SOURCE != disabled`)
- `ray.localhost` → Ray dashboard (`RAY_SOURCE != disabled`; Kong dashboard basic-auth/ACL)
- `redpanda.localhost` → Redpanda Console (`REDPANDA_SOURCE=container`; Kong dashboard basic-auth/ACL; Kafka API clients use direct `REDPANDA_KAFKA_PORT` or in-network `redpanda:9092`, not Kong)
- `rerank.localhost` → TEI rerank API (`TEI_RERANKER_SOURCE != disabled`)
- `research.localhost` → Local Deep Researcher (`LOCAL_DEEP_RESEARCHER_SOURCE != disabled`)
- `search.localhost` → SearxNG (`SEARXNG_SOURCE != disabled`)
- `spark.localhost` → Spark Master Web UI — routes to in-container `spark-master:8080` (`SPARK_SOURCE != disabled`)
- `spark-history.localhost` → Spark History Server UI — routes to in-container `spark-history:18080` (`SPARK_SOURCE != disabled`)
- `stt.localhost` → STT engine — container resolves to `parakeet-gpu` or `speaches`; localhost routes via `host.docker.internal`
- `tika.localhost` → Apache Tika extraction fallback (`TIKA_SOURCE != disabled`; Kong basic-auth/ACL)
- `trino.localhost` → Trino coordinator UI/API (`TRINO_SOURCE=container`; Kong dashboard basic-auth/ACL)
- `localhost` → Atlas service directory and health dashboard (generated from topology: category-grouped service cards with per-category accents and click-through to each service's Kong alias, dark/light themes with a toggle + `prefers-color-scheme` default, SOURCE state, auth notes, and lightweight browser reachability checks)
- `supabase-studio.localhost` → Supabase Studio dashboard (basic-auth: `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` from `.env`)
- `trueforge.localhost` → TrueForge agent runtime UI + API (`TRUEFORGE_SOURCE=container`; Kong dashboard basic-auth/ACL — TrueForge itself has no login)
- `tts.localhost` → TTS engine — container resolves to `speaches:8000` or `chatterbox:4123`; localhost routes via `host.docker.internal`
- `verba.localhost` → Verba RAG UI (`VERBA_SOURCE != disabled`; Kong dashboard basic-auth/ACL)
- `weaviate.localhost` → Weaviate REST API (`WEAVIATE_SOURCE != disabled`)
- `prometheus.localhost` → Prometheus UI + API (`PROMETHEUS_SOURCE != disabled`; no auth — the route adds no login, and the scrape paths stay internal-only)
- `grafana.localhost` → Grafana dashboards + alerting UI (`GRAFANA_SOURCE != disabled`; admin login via `GRAFANA_ADMIN_USERNAME` / auto-generated `GRAFANA_ADMIN_PASSWORD`)

"Kong dashboard basic-auth/ACL" is the shared `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` pair from `.env`. It gates only the `*.localhost` alias; a direct port bypasses it. Which credential opens each surface is tabulated in [Access and Credentials](access-and-credentials.md).

The Kong gateway listens on `KONG_HTTP_PORT` (default `63000` under topology v1, i.e. `BASE_PORT + 0`). All aliases above resolve to `http://<alias>:${KONG_HTTP_PORT}`.

## 3. Per-engine port quirks

A few services have engine-specific listen ports that won't match a naive `*_PORT` env-var lookup:

- **Chatterbox TTS** — container listens on `4123` internally; the host-facing port is `CHATTERBOX_PORT`. Kong routes `tts.localhost` to `http://chatterbox:4123/` when `TTS_PROVIDER_SOURCE=chatterbox-container-*`.
- **Speaches** — container listens on `8000`; host-facing on `SPEACHES_PORT`. Used by both `tts.localhost` and `stt.localhost` when source is `speaches-container-*`.
- **Parakeet GPU** — container listens on `8000`; host-facing on `STT_PROVIDER_PORT`.
- **Neo4j Browser** — container listens on `7474` regardless of the `GRAPH_DB_DASHBOARD_PORT` mapping.
- **Ollama** — container listens on `11434`; same on host for `ollama-localhost`.
- **Weaviate** — container listens on `8080`; same on host for `WEAVIATE_SOURCE=localhost`.

## 4. Localhost-mode port overrides

Localhost and managed-host sources expose explicit port variables, but they do
not all share one transport or routing contract. Some HTTP endpoints have a
Kong alias, while others are consumed directly by another service; a single
service family can expose both kinds. TCP protocols are never rewritten as
HTTP.

| Category | Service or mode | Port variable(s) | Transport / consumer | Kong route |
|---|---|---|---|---|
| HTTP + Kong | ComfyUI localhost and managed MPS | `COMFYUI_LOCALHOST_PORT`, `COMFYUI_MPS_LOCALHOST_PORT` | HTTP via `host.docker.internal` | `comfyui.localhost` |
| HTTP + Kong | Docling and Tika | `DOCLING_LOCALHOST_PORT`, `TIKA_LOCALHOST_PORT` | HTTP via `host.docker.internal` | `docling.localhost`, `tika.localhost` |
| Direct HTTP localhost | Hermes API | `HERMES_LOCALHOST_PORT` | `HERMES_ENDPOINT` consumers reach the API via `host.docker.internal` | No Kong route |
| HTTP + Kong | Hermes dashboard | `HERMES_LOCALHOST_DASHBOARD_PORT` | HTTP via `host.docker.internal` | `hermes.localhost` |
| HTTP + Kong | LightRAG, OpenClaw, Ollama, TEI reranker, Weaviate | `LIGHTRAG_LOCALHOST_PORT`, `OPENCLAW_LOCALHOST_PORT`, `OLLAMA_LOCALHOST_PORT`, `TEI_RERANKER_LOCALHOST_PORT`, `WEAVIATE_LOCALHOST_PORT` | HTTP via `host.docker.internal` | `lightrag.localhost`, `openclaw.localhost`, `ollama.localhost`, `rerank.localhost`, `weaviate.localhost` |
| HTTP + Kong | STT/TTS localhost engines | `PARAKEET_LOCALHOST_PORT`, `WHISPER_CPP_LOCALHOST_PORT`, `CHATTERBOX_LOCALHOST_PORT` | HTTP via `host.docker.internal` | `stt.localhost`, `tts.localhost` |
| HTTP + Kong | Neo4j Browser/API | `NEO4J_LOCALHOST_HTTP_PORT` | HTTP via `host.docker.internal` | `graph.localhost` |
| Direct HTTP managed host | vLLM Metal | `VLLM_METAL_LOCALHOST_PORT` | LiteLLM reaches the OpenAI-compatible API via `host.docker.internal` | No Kong route |
| TCP, no Kong | Blender MCP | `BLENDER_MCP_LOCALHOST_PORT` | `tcp://host:port` MCP bridge endpoint | No Kong route |
| TCP, no Kong | Neo4j Bolt | `NEO4J_LOCALHOST_BOLT_PORT` | Bolt clients reach the host database directly | No Kong route |

See the generated [port and route reference](../reference/ports-routes.md) for
the complete host-published inventory and PR #10 / the localhost-port-override
entry in `docs/CHANGELOG.md` for the design rationale.

## 5. Advanced overrides

`BASE_PORT` is the only supported mechanism for moving ports. Every `./start.sh` recomputes all `*_PORT` variables from `BASE_PORT` (`port_manager.update_env_ports`), so a hand-edited single `*_PORT` in `.env`, `.env.user` or a consumer manifest's `env.values` is reset on the next start; change `BASE_PORT` (or pass `--base-port`) instead. Localhost-source `*_LOCALHOST_PORT` variables are not derived from `BASE_PORT` and stay as set. The port migration framework (`bootstrapper/services/migrations/`) handles cross-version layout shifts; on a bump like topology v1, your `.env` is auto-rewritten with the new defaults (a backup is taken to `.env.backup.v<N>.<timestamp>.<random>`; user-customized values are preserved). Pass `--no-port-migrate` to opt out. Both the wizard and `--no-tui` migrate before applying the run's source, profile, key and model overrides (#1391).

Base-port changes and env migrations snapshot `.env` first, mode `0600`. Supabase JWT key generation (`generate_supabase_keys`, which auto-runs at startup only when all three keys are blank and rewrites all three when run by hand) does not, so copy `.env` yourself before running it by hand. Atlas keeps the five most recent snapshots per migration version and prunes older ones, so a rotated secret does not stay readable on disk indefinitely. `.env.backup.*` is gitignored and never committed.
