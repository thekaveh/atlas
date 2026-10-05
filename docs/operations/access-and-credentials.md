# 6.4. Access and Credentials

Supabase is Atlas's shared infrastructure: the Postgres database about twenty services store their data in, object storage, and an authentication API. It is **not** a shared login. Supabase identity backs the Backend API's user-scoped routes and Supabase's own APIs, and nothing else; every bundled dashboard keeps its own login, and there is no single sign-on between them. Open WebUI lists OIDC / SSO via Supabase Auth as future work (see its [service guide](../../services/open-webui/README.md)).

The table below says which credential opens each surface. It was checked row by row against each service's README and the Kong routes Atlas generates (`bootstrapper/utils/kong_config_generator.py`); the generated dashboard at `http://localhost:${KONG_HTTP_PORT}` shows the same Kong gate on each card.

## 1. Credential kinds

- **Supabase identity** — a user in Supabase Auth, and the JWT Supabase issues for that user. Only the surfaces in §4 accept it.
- **Atlas-generated secret** — a password, key or token the bootstrapper writes into `.env` on first start (and whenever a value is still a shipped placeholder). Read it from `.env`, for example `grep '^GRAFANA_ADMIN_PASSWORD=' .env`. This includes the shared **Kong dashboard basic-auth** pair, `DASHBOARD_USERNAME` (default `kong_admin`) / `DASHBOARD_PASSWORD`, which Kong asks for in front of several dashboards.
- **Service-native account or token** — an account you create inside the service, or a token the service generates itself. Atlas does not set it.
- **No login** — anyone who can reach the port can use it. Published ports are loopback-bound by default (`HOST_BIND_IP`), so "reach" means this machine unless you change that.

A Kong gate applies only through the `*.localhost` alias. The service's direct port, where one is published, bypasses it.

## 2. Access table

| Service | Entry point | Kind | What opens it | Checked against |
|---|---|---|---|---|
| Backend API | `api.localhost` | Supabase identity + Atlas-generated secret | User-scoped routes take a Supabase user JWT (§4). Operator and integration routes take the generated `BACKEND_INTERNAL_API_TOKEN`, `BACKEND_N8N_API_TOKEN`, `BACKEND_NOTEBOOK_API_TOKEN` or `BACKEND_OPEN_WEBUI_API_TOKEN`; `/api/ray/*` takes `RAY_JOB_API_TOKEN`. `/`, `/health`, `/ready`, `/metrics` and the API docs are public. Optional `BACKEND_KONG_AUTH=key-auth` adds an `apikey: ${BACKEND_KONG_API_KEY}` gate. | `services/backend/README.md`, `services/backend/app/app/backend_identity.py` |
| Supabase APIs | `localhost` paths `/auth/v1`, `/rest/v1`, `/graphql/v1`, `/realtime/v1`, `/storage/v1` | Supabase identity + Atlas-generated secret | Kong key-auth with `apikey: ${SUPABASE_ANON_KEY}` (or `SUPABASE_SERVICE_KEY`), then a Supabase user JWT where the API requires one. | `services/supabase/README.md`, `services/kong/README.md` |
| Supabase Studio | `supabase-studio.localhost` | Atlas-generated secret | Kong dashboard basic-auth. Studio has no login of its own. | `services/supabase/README.md` |
| Open WebUI | `chat.localhost` | Atlas-generated secret (admin); service-native (other users) | The seeded admin: `OPEN_WEB_UI_ADMIN_EMAIL` (default `admin@localhost`) / `OPEN_WEB_UI_ADMIN_PASSWORD`. Other users are Open WebUI accounts; they are stored in the Supabase database but are not Supabase Auth users. | `services/open-webui/README.md`, `services/open-webui/service.yml` |
| LiteLLM | `litellm.localhost` | Atlas-generated secret | `/ui`: `LITELLM_UI_USERNAME` (default `admin`) with `LITELLM_MASTER_KEY` as the password. `/v1/*` and `/spend/*`: `Authorization: Bearer ${LITELLM_MASTER_KEY}`. | `services/litellm/README.md` |
| Grafana | `grafana.localhost` | Atlas-generated secret | `GRAFANA_ADMIN_USERNAME` (default `admin`) / `GRAFANA_ADMIN_PASSWORD`. Sign-up and anonymous access are off. | `services/grafana/README.md` |
| Airflow | `airflow.localhost` | Atlas-generated secret | `admin` / `AIRFLOW_ADMIN_PASSWORD`. The `/api/v2` REST API takes an Airflow JWT obtained from `POST /auth/token` with the same credentials. | `services/airflow/README.md` |
| MinIO console | `minio.localhost` | Atlas-generated secret | `MINIO_ROOT_USER` (default `minioadmin`) / `MINIO_ROOT_PASSWORD`. | `services/minio/README.md` |
| MinIO S3 API | `s3.minio.localhost` | Atlas-generated secret | S3 signatures with the root pair, or a generated per-consumer `MINIO_<NAME>_ACCESS_KEY` / `MINIO_<NAME>_SECRET_KEY`. | `services/minio/README.md` |
| Neo4j Browser | `graph.localhost` | Atlas-generated secret (container source); service-native (localhost source) | `GRAPH_DB_USER` (default `neo4j`) / `GRAPH_DB_PASSWORD`. With a host-installed Neo4j, that install's own credentials. | `services/neo4j/README.md` |
| Langfuse | `langfuse.localhost` | Atlas-generated secret | Kong dashboard basic-auth, then `LANGFUSE_INIT_USER_EMAIL` (default `admin@atlas.localhost`) / `LANGFUSE_INIT_USER_PASSWORD`. | `services/langfuse/README.md` |
| Label Studio | `label-studio.localhost` | Atlas-generated secret | Kong dashboard basic-auth, then `LABEL_STUDIO_USERNAME` (default `admin@atlas.local`) / `LABEL_STUDIO_PASSWORD`. Open sign-up is off. | `services/label-studio/README.md` |
| Jenkins | `jenkins.localhost` | Atlas-generated secret | `JENKINS_ADMIN_USER` (default `admin`) / `JENKINS_ADMIN_PASSWORD`. Sign-up is off. | `services/jenkins/README.md` |
| Celery Flower | `flower.localhost` | Atlas-generated secret | Kong dashboard basic-auth; Flower's own basic-auth uses the same pair. | `services/celery/README.md` |
| Crawl4AI | `crawl4ai.localhost` | Atlas-generated secret | Kong dashboard basic-auth, then `Authorization: Bearer ${CRAWL4AI_API_TOKEN}` on every route except `/health`. | `services/crawl4ai/README.md` |
| Docling | `docling.localhost` | Atlas-generated secret | `Authorization: Bearer ${DOCLING_API_TOKEN}` except `/health`. | `services/doc-processor/README.md`, `services/docling/README.md` |
| LightRAG API | `lightrag.localhost` | Atlas-generated secret | API routes take the `X-API-Key: ${LIGHTRAG_API_KEY}` header (a Bearer token is not accepted). | `services/lightrag/README.md` |
| Asset Baker | `asset-baker.localhost` | Atlas-generated secret | `Authorization: Bearer ${ASSET_BAKER_API_TOKEN}`; `/health` and `/metrics` are public. | `services/asset-baker/README.md` |
| Asset Worker | `asset-worker.localhost` | Atlas-generated secret | `Authorization: Bearer ${ASSET_WORKER_API_TOKEN}`; `/health` and `/metrics` are public. | `services/asset-worker/README.md` |
| MLflow | `mlflow.localhost` | Atlas-generated secret | Kong dashboard basic-auth. MLflow has no login of its own. | `services/mlflow/README.md` |
| Apache Tika | `tika.localhost` | Atlas-generated secret | Kong dashboard basic-auth. Tika has no login of its own. | `services/tika/README.md` |
| Trino | `trino.localhost` | Atlas-generated secret | Kong dashboard basic-auth. Trino has no authenticator; any user name is accepted. | `services/trino/README.md` |
| Redpanda Console | `redpanda.localhost` | Atlas-generated secret | Kong dashboard basic-auth. The console has no login of its own. | `services/redpanda/README.md` |
| Ray dashboard | `ray.localhost` | Atlas-generated secret | Kong dashboard basic-auth. The dashboard has no login of its own. | `services/ray/README.md` |
| TrueForge | `trueforge.localhost` | Atlas-generated secret | Kong dashboard basic-auth. TrueForge has no login: anyone who reaches it is its admin. | `services/trueforge/README.md` |
| Verba | `verba.localhost` | Atlas-generated secret | Kong dashboard basic-auth. Verba has no login of its own. | `services/verba/README.md` |
| LLM Graph Builder | `graphbuilder.localhost`, `graphbuilder-api.localhost` | Atlas-generated secret | Kong dashboard basic-auth on both aliases. The app has no login of its own. | `services/llm-graph-builder/README.md` |
| MCP servers | `mcp.localhost` | Atlas-generated secret | Kong dashboard basic-auth. The MCP server itself only checks Host and Origin. | `services/mcp-servers/README.md` |
| STT (Parakeet) | `stt.localhost` | Atlas-generated secret | `Authorization: Bearer ${PARAKEET_API_TOKEN}` except `/health`, when the engine is Parakeet. Speaches and whisper.cpp have no login. | `services/stt-provider/README.md` |
| n8n | `n8n.localhost` | Service-native | The owner account you create on the first visit. | `services/n8n/README.md` |
| JupyterHub | `jupyter.localhost` | Service-native | A Jupyter token (`?token=…`). With `JUPYTERHUB_TOKEN` empty, the default, Jupyter generates one and prints it in the container log on every start. | `services/jupyterhub/README.md` |
| OpenClaw | `openclaw.localhost` | Atlas-generated secret | `OPENCLAW_GATEWAY_TOKEN`, generated into `.env` on start when empty and kept once set. | `services/openclaw/README.md` |
| Prometheus | `prometheus.localhost` | No login | The route adds no auth; the scrape paths stay internal. | `services/prometheus/README.md` |
| Hermes dashboard | `hermes.localhost` | Atlas-generated secret | Kong dashboard basic-auth. The dashboard itself runs in upstream's unauthenticated mode, so its direct host port has no login. | `services/hermes/README.md` |
| Spark | `spark.localhost`, `spark-history.localhost` | No login | Master and History Server UIs are open. | `services/spark/README.md` |
| Zeppelin | direct `localhost:${ZEPPELIN_PORT}` (no Kong alias) | No login | Loopback-bound. | `services/zeppelin/README.md` |
| Weaviate | `weaviate.localhost` | No login | Anonymous access is enabled. | `services/weaviate/README.md` |
| ComfyUI | `comfyui.localhost` | No login | | `services/comfyui/README.md` |
| SearXNG | `search.localhost` | No login | Kong rate-limits it; that is not authentication. | `services/searxng/README.md` |
| Local Deep Researcher | `research.localhost` | No login | | `services/local-deep-researcher/README.md` |
| Ollama | `ollama.localhost` | No login | | `services/ollama/README.md` |
| TEI reranker | `rerank.localhost` | No login | | `services/tei-reranker/README.md` |
| TTS | `tts.localhost` | No login | Speaches and Chatterbox are open. | `services/tts-provider/README.md` |
| Atlas dashboard | `localhost` | No login | Static page served by Kong. | `bootstrapper/utils/atlas_dashboard.py` |

All entry points are `http://<alias>:${KONG_HTTP_PORT}` (default `63000`); see [Ports and Routes](ports-and-routes.md) for the routing details.

## 3. First login

The surfaces a new user usually opens first, and what each needs:

1. **The Atlas dashboard**, `http://localhost:63000` — nothing. Each card names the gate for that service.
2. **Open WebUI**, `chat.localhost` — an Atlas-generated secret: sign in as `OPEN_WEB_UI_ADMIN_EMAIL` with `OPEN_WEB_UI_ADMIN_PASSWORD` from `.env`.
3. **LiteLLM UI**, `litellm.localhost/ui` — an Atlas-generated secret: `admin` with `LITELLM_MASTER_KEY`.
4. **Supabase Studio**, `supabase-studio.localhost` — an Atlas-generated secret: the Kong dashboard pair, `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`.
5. **n8n**, `n8n.localhost` — a service-native account: create the owner on first visit.
6. **JupyterHub**, `jupyter.localhost` — a service-native token: `docker logs <PROJECT_NAME>-jupyterhub 2>&1 | grep token` (Atlas runs Compose under `-p <PROJECT_NAME>`, so a bare `docker compose logs` from another directory or an `infra/` submodule targets the wrong project).
7. **Grafana**, `grafana.localhost` — an Atlas-generated secret: `admin` with `GRAFANA_ADMIN_PASSWORD`.
8. **The Backend API**, `api.localhost` — Supabase identity for your own data (§4), or `BACKEND_INTERNAL_API_TOKEN` for operator routes.

## 4. What Supabase identity covers

A Supabase user JWT is accepted in exactly two places:

- **Supabase's own APIs** through Kong (`/auth/v1`, `/rest/v1`, `/graphql/v1`, `/realtime/v1`, `/storage/v1`), which also require the `apikey` header. Sign-up and email auto-confirm are on; get a token with `POST /auth/v1/token?grant_type=password`. No bundled UI hosts a Supabase login page.
- **The Backend's user-scoped routes**: research jobs, memory, media generation and its operations and spend, document extraction, `/api/chunk`, `/api/rag/evaluate`, the ComfyUI health and model listings, and plugin routes declared `auth: inherit`. The Backend accepts it only when it is signed with `SUPABASE_JWT_SECRET` and carries the `authenticated` role; every other Backend route takes one of the Atlas-generated tokens in §2 instead.

No bundled dashboard validates a Supabase JWT, and nothing here provides single sign-on. The authoritative identity rules are in `services/backend/app/app/backend_identity.py` and [Security, Auth, and Secrets Boundary](../architecture/security-auth-secrets-boundary.md).
