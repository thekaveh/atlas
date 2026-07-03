# Ports and Routes

Ports and Kong hostnames are derived from `BASE_PORT` in `.env` (default `63000`) and the per-category slot allocator in `bootstrapper/services/topology.py`. Move the whole stack with `./start.sh --base-port <port>` or by editing `BASE_PORT`.

## 1. Authoritative sources

The full per-service port + Kong-alias mapping is maintained in three places:

- **`.env.example`** at the repo root — every `*_PORT` env var with its default; the auto-regenerated baseline.
- **README generated topology block** — the tested, generated service/port/Kong-host table near the top of the README.
- **README "Service overview" table** — manually curated browser-facing URLs and auth notes (§ 4.1); review it when route or auth behavior changes.
- **`bootstrapper/services/topology.py`** — code-level source of truth; `Topology.port_defaults` and `Topology.aliases`.

## 2. Kong hostnames

Run once to add them to `/etc/hosts`:

```bash
./start.sh --setup-hosts
```

Active aliases (every `*-localhost` source also routes through `host.docker.internal`):

- `airflow.localhost` → Airflow Web UI + REST API (`AIRFLOW_SOURCE != disabled`; same alias serves UI at `/` and REST API under `/api/v2/`). Web UI auth: `admin` / auto-generated `AIRFLOW_ADMIN_PASSWORD` (FAB session cookie). REST API auth: JWT bearer — POST credentials to `/auth/token` first, then attach `Authorization: Bearer <jwt>` to `/api/v2/...` calls. See [services/airflow/README.md](../../services/airflow/README.md) §6 for the full two-step curl.
- `api.localhost` → Backend API (always-on adaptive)
- `chat.localhost` → Open WebUI (`OPEN_WEB_UI_SOURCE != disabled`)
- `comfyui.localhost` → ComfyUI (`COMFYUI_SOURCE != disabled`)
- `docling.localhost` → Document processor (`DOC_PROCESSOR_SOURCE != disabled`)
- `graph.localhost` → Neo4j Browser (`NEO4J_GRAPH_DB_SOURCE != disabled`)
- `hermes.localhost` → Hermes Agent dashboard (`HERMES_SOURCE != disabled` AND `HERMES_DASHBOARD_ENABLED=true`)
- `jupyter.localhost` → JupyterHub (`JUPYTERHUB_SOURCE != disabled`)
- `lightrag.localhost` → LightRAG API + WebUI (`LIGHTRAG_SOURCE != disabled`; `preserve_host: True` for the SPA)
- `litellm.localhost` → LiteLLM gateway + admin dashboard (always-on; same alias exposes `/ui/`, `/v1/*`, `/spend/*`)
- `minio.localhost` → MinIO admin console (`MINIO_SOURCE != disabled`)
- `s3.minio.localhost` → MinIO S3 API (`MINIO_SOURCE != disabled`; S3 clients can also use the direct `MINIO_PORT`)
- `n8n.localhost` → n8n (`N8N_SOURCE != disabled`)
- `ollama.localhost` → Ollama upstream (`LLM_PROVIDER_SOURCE` is `ollama-container-*` or `ollama-localhost`)
- `openclaw.localhost` → OpenClaw gateway (`OPENCLAW_SOURCE != disabled`)
- `ray.localhost` → Ray dashboard (`RAY_SOURCE != disabled`)
- `rerank.localhost` → TEI rerank API (`TEI_RERANKER_SOURCE != disabled`)
- `research.localhost` → Local Deep Researcher (`LOCAL_DEEP_RESEARCHER_SOURCE != disabled`)
- `search.localhost` → SearxNG (`SEARXNG_SOURCE != disabled`)
- `spark.localhost` → Spark Master Web UI — routes to in-container `spark-master:8080` (`SPARK_SOURCE != disabled`)
- `spark-history.localhost` → Spark History Server UI — routes to in-container `spark-history:18080` (`SPARK_SOURCE != disabled`)
- `stt.localhost` → STT engine — container resolves to `parakeet-gpu` or `speaches`; localhost routes via `host.docker.internal`
- `localhost` → Atlas service directory and health dashboard (generated from topology, SOURCE state, route aliases, auth notes, and lightweight browser reachability checks)
- `supabase-studio.localhost` → Supabase Studio dashboard (basic-auth: `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` from `.env`)
- `tts.localhost` → TTS engine — container resolves to `speaches:8000` or `chatterbox:4123`; localhost routes via `host.docker.internal`
- `weaviate.localhost` → Weaviate REST API (`WEAVIATE_SOURCE != disabled`)
- `zeppelin.localhost` → Zeppelin notebook UI — routes to in-container `zeppelin:8080` (`ZEPPELIN_SOURCE != disabled`; gated on `SPARK_SOURCE != disabled`)
- `prometheus.localhost` → Prometheus UI + API (`PROMETHEUS_SOURCE != disabled`; no auth — Kong-gated, internal-only scrape paths)
- `grafana.localhost` → Grafana dashboards + alerting UI (`GRAFANA_SOURCE != disabled`; admin login via `GRAFANA_ADMIN_USERNAME` / auto-generated `GRAFANA_ADMIN_PASSWORD`)

The Kong gateway listens on `KONG_HTTP_PORT` (default `63000` under topology v1, i.e. `BASE_PORT + 0`). All aliases above resolve to `http://<alias>:${KONG_HTTP_PORT}`.

## 3. Per-engine port quirks

A few services have engine-specific listen ports that won't match a naive `*_PORT` env-var lookup:

- **Chatterbox TTS** — container listens on `4123` internally; the host-facing port is `CHATTERBOX_PORT`. Kong routes `tts.localhost` to `http://chatterbox:4123/` when `TTS_PROVIDER_SOURCE=chatterbox-container-*`.
- **Speaches** — container listens on `8000`; host-facing on `SPEACHES_PORT`. Used by both `tts.localhost` and `stt.localhost` when source is `speaches-container-*`.
- **Parakeet GPU** — container listens on `8000`; host-facing on `STT_PROVIDER_PORT`.
- **Neo4j Browser** — container listens on `7474` regardless of the `GRAPH_DB_DASHBOARD_PORT` mapping.
- **Ollama** — container listens on `11434`; same on host for `ollama-localhost`.
- **Weaviate** — container listens on `8080`; same on host for `weaviate-localhost`.

## 4. Localhost-mode port overrides

Every localhost-source service exposes a `<SVC>_LOCALHOST_PORT` integer env var. The URL is derived inline as `http://host.docker.internal:${<SVC>_LOCALHOST_PORT:-<default>}` at compose-render time AND by `bootstrapper/utils/kong_config_generator.py`, so both consumers stay in sync. Today: `COMFYUI_LOCALHOST_PORT`, `DOCLING_LOCALHOST_PORT`, `HERMES_LOCALHOST_PORT` (API) / `HERMES_LOCALHOST_DASHBOARD_PORT`, `LIGHTRAG_LOCALHOST_PORT`, `OPENCLAW_LOCALHOST_PORT`, `OLLAMA_LOCALHOST_PORT`, `NEO4J_LOCALHOST_HTTP_PORT` / `NEO4J_LOCALHOST_BOLT_PORT`, `TEI_RERANKER_LOCALHOST_PORT`, `WEAVIATE_LOCALHOST_PORT`, `PARAKEET_LOCALHOST_PORT`, `WHISPER_CPP_LOCALHOST_PORT`, `CHATTERBOX_LOCALHOST_PORT`. See PR #10 and the localhost-port-override entry in `docs/CHANGELOG.md` for the design rationale.

## 5. Advanced overrides

`BASE_PORT` is the preferred mechanism for moving the whole stack. Individual `*_PORT` variables are advanced overrides; if you change one, the wizard / Kong / dependent services need a `./start.sh` to re-emit `kong-dynamic.yml` and pick up the new value. The port migration framework (`bootstrapper/services/migrations/`) handles cross-version layout shifts; on a bump like topology v1, your `.env` is auto-rewritten with the new defaults (a backup is taken to `.env.backup.<timestamp>`; user-customized values are preserved). Pass `--no-port-migrate` to opt out.
