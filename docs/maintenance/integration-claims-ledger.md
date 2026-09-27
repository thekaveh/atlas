# Integration Claims Review Ledger

Durable ledger of reviews that check the runtime integration claims in the
service manifests against the code, configuration and Compose wiring that are
supposed to carry them. It sits beside the
[external contract ledger](./external-contract-ledger.md), which checks the
contracts Atlas consumes from upstream projects. This ledger checks what Atlas
claims about itself.

Each `services/<name>/service.yml` lists `data_flow.calls`: the services it
calls at runtime. Those edges drive every generated architecture diagram and
every "Dependencies & Integrations" table. Nothing else verifies them:

- `_check_data_flow_targets` in `bootstrapper/services/manifest_validator.py`
  checks only that the target name exists.
- The docs drift gate proves that regeneration is deterministic, not that an
  edge is accurate.

A review pass samples edges, traces each one to the place that makes the call,
and records a verdict. Corrections go into the manifest and are regenerated.
Generated READMEs, SVGs and HTML files are never edited by hand.

## 1. Method

**Edge total.** Count the declared edges with:

```bash
python3 -c "import yaml,glob;print(sum(len(((yaml.safe_load(open(p)) or {}).get('data_flow') or {}).get('calls') or []) for p in glob.glob('services/*/service.yml')))"
```

It printed 207 at the start of the 2026-09-27 pass: 34 caller manifests, from
1 to 39 edges each. The pass corrected one edge (§2.2), which leaves 206.

**Sampling rule.** The sample is stratified so that every caller is covered and
the densest callers get a second draw:

- the first edge each caller manifest declares under `data_flow.calls`, 34 edges;
- the last declared edge of the three densest callers (Kong 39, JupyterHub 21,
  Backend 19), 3 edges.

That makes 37 of 207 edges (17.9%). A later pass should sample from §3, starting
with each caller's second declared edge, and record its rule the same way.

**Columns.**

- *Caller manifest line*: the `- <target>` entry under `data_flow.calls`.
- *Call site*: the repo file and line that issues the call, or that configures
  the process which does. The kind is given in parentheses:
  - *code*: Atlas-owned code that makes the request;
  - *init*: an init script or init container;
  - *loaded config*: a config file the caller's container loads;
  - *route generator*: Kong's generated route to the target;
  - *compose env only*: an address handed to an upstream image Atlas does not
    vendor, so the request itself is not visible in the repo.
- *Address variable*: the variable or literal that points the caller at the
  target, and where the caller receives it.
- *Receiving API*: the protocol and path on the target.
- *SOURCE variants*: the `*_SOURCE` values under which the edge exists.

**Status.**

- *current*: the edge exists whenever both services are enabled.
- *optional*: the edge also depends on a flag, a third service, operator setup,
  or a data condition.
- *future*: the edge is planned or stubbed but nothing reaches it today.

**Verdict.**

- *confirmed*: a concrete call site, or config that the caller loads, reaches the
  target.
- *unconfirmed*: the edge is plausible, but the only evidence is an address
  passed to an unvendored image or to operator-owned setup. These edges are
  recorded as unconfirmed. They are not dropped, and they are not asserted as
  fact.
- *incorrect*: the evidence says the caller does not call the target.

The manifest schema says that init-time bootstrap calls do not count as edges.
Where an edge's only in-repo call site is an init script, the row also names
the runtime path, usually a connection string handed to the upstream image.

## 2. 2026-09-27 Review Pass

Reviewed on the `batch/2026-09-top10` integration branch on 2026-09-27. Every
file and line reference is as of the commit that added this pass. Like the
external contract ledger, this is a dated record: later edits move lines
without invalidating it.

### 2.1. Sampled edges

| Edge | Caller manifest line | Call site (kind) | Address variable | Receiving API | SOURCE variants | Status | Verdict | Notes |
|---|---|---|---|---|---|---|---|---|
| `airflow → supabase` | `services/airflow/service.yml:337` | `services/airflow/init/scripts/init-airflow.sh:17` psql, `:21` `airflow db migrate` (init code); metadata DSN (loaded config) | `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN` at `services/airflow/compose.yml:20` | Postgres wire protocol on supabase-db:5432, databases `airflow` and `${SUPABASE_DB_NAME}` | `AIRFLOW_SOURCE=container` (`services/airflow/service.yml:43-50`); Supabase always on | current | confirmed | Seeded `postgres_supabase` Connection is unused by vendored DAGs |
| `asset-baker → minio` | `services/asset-baker/service.yml:152` | `services/asset-baker/app/asset_baker/storage.py:30` `get_object`, `:58` `put_object` (code) | `ASSET_BAKER_MINIO_ENDPOINT` at `services/asset-baker/compose.yml:27` | S3 API on minio:9000 (GetObject/PutObject) | `ASSET_BAKER_SOURCE=container-cpu`; MinIO required (`services/asset-baker/service.yml:109-111`) | current | confirmed | Writes gated by `ASSET_BAKER_MINIO_ENABLED` (default true) |
| `asset-worker → minio` | `services/asset-worker/service.yml:135` | `services/asset-worker/app/asset_worker/storage.py:27` `get_object`, `:53` `put_object` (code) | `ASSET_WORKER_MINIO_ENDPOINT` at `services/asset-worker/compose.yml:29` | S3 API on minio:9000 (GetObject/PutObject) | `ASSET_WORKER_SOURCE=container`; MinIO required (`services/asset-worker/service.yml:93-95`) | current | confirmed | Writes gated by `ASSET_WORKER_MINIO_ENABLED` (default true) |
| `backend → supabase` | `services/backend/service.yml:356` | `services/backend/app/app/db_connection.py:147` `asyncpg.create_pool` (code); Storage client via Kong at `main.py:508-511` | `DATABASE_URL` at `services/backend/compose.yml:81` (from `bootstrapper/services/service_config.py:1358-1380`); `KONG_URL` at `:43` | Postgres wire protocol on supabase-db:5432; HTTP `/storage/v1/*` via Kong | Both container-only; Postgres via supavisor:6543 when `SUPAVISOR_SOURCE` is enabled | current | confirmed | Storage leg is backend → kong → supabase |
| `backend → neo4j` | `services/backend/service.yml:374` (removed by this pass, §2.2) | none found: no Bolt connection in backend code; `services/backend/app/app/graphiti_experiment.py:102` only reads `NEO4J_URI` to report configuration | `NEO4J_URI` at `services/backend/compose.yml:85` | Would be Bolt on neo4j-graph-db:7687; never opened | `NEO4J_URI` injected unconditionally; Neo4j optional in runtime_deps (`services/backend/service.yml:341`) | future | incorrect | Manifest marks the capability stubbed (`service.yml:28-31`); README lists it as future work |
| `backup → supabase` | `services/backup/service.yml:161` | `services/backup/init/scripts/backup-all.sh:234` `pg_dump -h supabase-db` (script) | Host literal `supabase-db`; credentials at `services/backup/compose.yml:31-33` | Postgres wire protocol on supabase-db:5432 | `BACKUP_SOURCE=container`; scale 0, runs on demand only (`services/backup/service.yml:136`) | optional | confirmed | On-demand runner, not a resident caller |
| `celery → redis` | `services/celery/service.yml:139` | `services/backend/app/app/celery_app.py:60-63` `Celery(broker=...)` run by `services/celery/compose.yml:90-94` (code) | `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` at `services/celery/compose.yml:52-53` | Redis protocol on redis:6379, DB 4 | `CELERY_SOURCE=container`; Redis always on | current | confirmed | Flower is a second Redis client |
| `cloudflared → kong` | `services/cloudflared/service.yml:84` | none in repo: `cloudflared tunnel run` (`services/cloudflared/compose.yml:12`) loads routing from the Cloudflare dashboard | none in repo; origin set in the dashboard; `TUNNEL_TOKEN` at `compose.yml:14` authenticates only | HTTP on kong-api-gateway:8000 with Host override | `CLOUDFLARED_SOURCE=container` (default disabled) | optional | unconfirmed | Exists only after operator dashboard configuration |
| `docling-lightrag-adapter → docling` | `services/docling-lightrag-adapter/service.yml:40` | `services/docling/provider/adapter/upstream.py:58-64` httpx POST (code) | `DOCLING_ADAPTER_UPSTREAM_ENDPOINT` at `services/docling/compose.yml:83` (from `bootstrapper/services/service_config.py:686-700`) | HTTP POST `/internal/lightrag/bundle` on docling-gpu:8000 or host Docling | `LIGHTRAG_SOURCE=container` and `DOC_PROCESSOR_SOURCE` in `docling-container-gpu`/`docling-localhost` | optional | confirmed | Virtual manifest; container in `services/docling/compose.yml:71` |
| `grafana → prometheus` | `services/grafana/service.yml:103` | `services/grafana/config/provisioning/datasources/prometheus.yml:12` `url: ${PROMETHEUS_ENDPOINT}` (loaded datasource config) | `PROMETHEUS_ENDPOINT` at `services/grafana/compose.yml:15` | Prometheus HTTP query API on prometheus:9090 | Both `container` (both default disabled); hard depends_on prometheus | current | confirmed | Proxy-mode datasource; compose fallback re-adds URL when Prometheus disabled |
| `hermes → litellm` | `services/hermes/service.yml:241` | `services/hermes/init/templates/config.yaml.tmpl:19` `base_url: http://litellm:4000/v1` (config the image loads); init curl `services/hermes/init/scripts/init-hermes.sh:95-99` | Hardcoded in template; hermes-init `LITELLM_BASE_URL` at `services/hermes/compose.yml:25` | HTTP POST `/v1/chat/completions`, GET `/v1/models` on litellm:4000 | HERMES `container` only (`bootstrapper/services/service_config.py:1701-1706`); LiteLLM always on | current | confirmed | Init + runtime callers |
| `iceberg-rest → minio` | `services/iceberg-rest/service.yml:110` | `services/iceberg-rest/compose.yml:31-32` `CATALOG_IO__IMPL=S3FileIO`, `CATALOG_S3_ENDPOINT` (FileIO config read by upstream image) | `CATALOG_S3_ENDPOINT` at `services/iceberg-rest/compose.yml:32` | S3 API on minio:9000, bucket `s3://lakehouse/` | ICEBERG_REST `container` requires MINIO `container` (`service_config.py:1466-1473`) | current | confirmed | — |
| `jenkins → minio` | `services/jenkins/service.yml:108` | none found in shipped code; sample Jenkinsfile `mc cp` at `services/jenkins/README.md:44-45` | `MINIO_ENDPOINT` at `services/jenkins/compose.yml:23` | S3 API (`mc cp`) on minio:9000, jars bucket | JENKINS `container` requires MINIO `container` (`service_config.py:1001-1007`) | optional | unconfirmed | Only env, creds and `mc` binary ship; no job does the upload |
| `jupyterhub → litellm` | `services/jupyterhub/service.yml:197` | `services/jupyterhub/build/notebooks/01_litellm_basics.ipynb:30,78` (notebook code) | `LITELLM_BASE_URL` at `services/jupyterhub/compose.yml:36`; exported at `build/scripts/startup.sh:31` | HTTP `/v1/chat/completions`, `/v1/embeddings`, `/v1/models` on litellm:4000 | JUPYTERHUB `container` (default); LiteLLM always on | current | confirmed | Fires when a user runs a notebook |
| `jupyterhub → mcp-servers` | `services/jupyterhub/service.yml:217` | `services/jupyterhub/build/notebooks/15_mcp_clients.ipynb:86,129` `Client(MCP_SERVERS_URL)` (notebook code) | `MCP_SERVERS_URL` at `services/jupyterhub/compose.yml:74` (resolved at `bootstrapper/services/service_config.py:1943-1946`) | MCP Streamable HTTP `/mcp` on mcp-servers:8000 | JUPYTERHUB `container` + MCP_SERVERS `container` (default disabled); URL blank otherwise | optional | confirmed | — |
| `kong → backend` | `services/kong/service.yml:83` | `bootstrapper/utils/kong_config_generator.py:1795-1802` service `backend-api` → `http://backend:8000/` (route generator) | none (hardcoded upstream); host `api.localhost` | HTTP any path on backend:8000 | Kong always on; BACKEND `container` only | current | confirmed | Proxy direction |
| `kong → ray` | `services/kong/service.yml:127` | `bootstrapper/utils/kong_config_generator.py:1370-1386` service `ray-dashboard` → `http://ray-head:8265/` (route generator) | none (hardcoded upstream); host `ray.localhost`; gate `RAY_SOURCE` | HTTP Ray dashboard on ray-head:8265 (basic-auth) | RAY `ray-container-cpu`/`ray-container-gpu` only (default disabled) | optional | confirmed | Manifest comment cites stale generator lines |
| `label-studio → supabase` | `services/label-studio/service.yml:138` | `services/label-studio/init/scripts/init-label-studio.sh:28-29` psql (init); app `POSTGRE_*` at `services/label-studio/compose.yml:44-49` | `PGHOST` at `services/label-studio/compose.yml:19`; `POSTGRE_HOST` at `:48` | Postgres wire protocol on supabase-db:5432, database `label_studio` | LABEL_STUDIO `container` (default disabled); Supabase always on | current | confirmed | — |
| `langfuse → supabase` | `services/langfuse/service.yml:209` | `services/langfuse/init/scripts/init-langfuse.sh:26` psql `SELECT 1` (init code); runtime `DATABASE_URL` consumed by upstream image | `DATABASE_URL` at `services/langfuse/compose.yml:89`; init `PGHOST` at `compose.yml:19` | Postgres wire protocol on supabase-db:5432, database `langfuse` | Caller `container` (default disabled); Supabase always on | current | confirmed | Init probe + upstream web/worker connection |
| `lightrag → litellm` | `services/lightrag/service.yml:312` | `services/lightrag/init/scripts/resolve-models.py:41-46` GET `/v1/models`, `:64-73` POST `/v1/embeddings` (init code); runtime OpenAI binding in upstream image | `LLM_BINDING_HOST`/`EMBEDDING_BINDING_HOST` at `services/lightrag/compose.yml:37,86`; init hardcodes `resolve-models.py:24` | HTTP `/v1/models`, `/v1/embeddings`, `/v1/chat/completions` on litellm:4000 | Caller `container` only; `localhost` scales lightrag to 0 (`bootstrapper/services/service_config.py:804-809`); LiteLLM always on | current | confirmed | Init ignores `LIGHTRAG_*_BINDING_HOST` overrides |
| `litellm → supabase` | `services/litellm/service.yml:137` | `services/litellm/init/scripts/init.py:125` `psycopg2.connect` (init code); runtime Prisma `DATABASE_URL` in upstream image | `DATABASE_URL` at `services/litellm/compose.yml:136`; init `PGHOST` at `compose.yml:13` | Postgres wire protocol on supabase-db:5432, databases `postgres` then `litellm` | Always (LiteLLM locked to container; Supabase always on) | current | confirmed | Spend/control data |
| `llm-graph-builder → neo4j` | `services/llm-graph-builder/service.yml:189` | Compose env only; upstream backend built from GitHub source (`compose.yml:8`), not vendored | `NEO4J_URI` at `services/llm-graph-builder/compose.yml:18` (set at `bootstrapper/services/service_config.py:1650-1652`) | Bolt on neo4j-graph-db:7687 | Caller `container` requires `NEO4J_GRAPH_DB_SOURCE=container` (`service_config.py:1291-1304`) | current | unconfirmed | Hard requirement + healthy depends_on, but no in-repo call site |
| `local-deep-researcher → litellm` | `services/local-deep-researcher/service.yml:151` | `services/local-deep-researcher/build/scripts/patch-litellm-openai-provider.py:80-89` `ChatOpenAI(base_url=...)`; `build/scripts/docker-entrypoint.sh:126` curl (code) | `LITELLM_BASE_URL` at `services/local-deep-researcher/compose.yml:24`; written as `OPENAI_API_BASE` at `build/scripts/init-config.py:63` | HTTP POST `/v1/chat/completions`, GET `/health/liveliness` on litellm:4000 | Caller `container` (default); LiteLLM always on | current | confirmed | — |
| `mcp-servers → supabase` | `services/mcp-servers/service.yml:112` | `services/mcp-servers/runtime/atlas_mcp_server.py:125` `psycopg.connect` in `postgres_query` (code) | `MCP_POSTGRES_DB_HOST` at `services/mcp-servers/compose.yml:22` | Postgres wire protocol on supabase-db:5432, READ ONLY transaction | Caller `container` (default disabled); Supabase always on | current | confirmed | Fires per tool call; read-only role `atlas_mcp` |
| `mlflow → supabase` | `services/mlflow/service.yml:132` | `services/mlflow/init/scripts/init-mlflow.sh:28` psql; `services/mlflow/atlas_server.py:95-98` `initialize_backend_stores` (code) | `_MLFLOW_SERVER_FILE_STORE`/`_MLFLOW_SERVER_REGISTRY_STORE` at `services/mlflow/compose.yml:55-56` | Postgres wire protocol on supabase-db:5432, database `mlflow` | Caller `container` (default disabled); Supabase always on | current | confirmed | — |
| `n8n → supabase` | `services/n8n/service.yml:218` | Compose `DB_TYPE=postgresdb` for upstream n8n; vendored `services/n8n/init/scripts/seed-workflows.js:199` runs `n8n import:workflow` against it | `DB_POSTGRESDB_HOST: ${SUPAVISOR_DB_HOST:-supabase-db}` at `services/n8n/compose.yml:23` | Postgres wire protocol on supabase-db:5432, schema `n8n` | `N8N_SOURCE=container`; direct only when `SUPAVISOR_SOURCE=disabled`, else via supavisor:6543 (`service_config.py:1362,1373`) | current | confirmed | Borderline: main connection lives in the upstream image |
| `open-webui → litellm` | `services/open-webui/service.yml:199` | Compose env only; `ENABLE_OPENAI_API=true` consumed by upstream image; vendored extras/init never call LiteLLM | `OPENAI_API_BASE_URLS` at `services/open-webui/compose.yml:30` | HTTP OpenAI API on litellm:4000/v1 | `OPEN_WEB_UI_SOURCE=container` (default); LiteLLM always on | current | unconfirmed | `ENABLE_OLLAMA_API=false` makes LiteLLM its only LLM backend |
| `openclaw → litellm` | `services/openclaw/service.yml:157` | Compose env only (`services/openclaw/compose.yml:54-55`) to the unvendored upstream image; init writes no provider config | `LITELLM_BASE_URL` at `services/openclaw/compose.yml:54` | OpenAI-compatible HTTP on litellm:4000 (path not in repo) | `OPENCLAW_SOURCE=container` only; bypassed by `OPENCLAW_*_API_KEY` overrides | current | unconfirmed | README §7 has operators set `baseUrl` by hand (`services/openclaw/README.md:177`) |
| `otel-collector → tempo` | `services/otel-collector/service.yml:91` | `services/otel-collector/config/config.yaml:32-33` exporter `otlp_http/tempo`, used at `:55-58` (loaded config) | none: literal `http://tempo:4318` (`config.yaml:33`) | OTLP/HTTP POST `/v1/traces` on tempo:4318 | Collector `container` hard-requires `TEMPO_SOURCE=container` (`bootstrapper/services/service_config.py:1207-1209`) | current | confirmed | `TEMPO_ENDPOINT` is passed but unused |
| `prometheus → kong` | `services/prometheus/service.yml:164` | `services/prometheus/config/prometheus.yml:32-38` scrape job `kong` (loaded config) | none: literal `kong-api-gateway:8100` | HTTP GET `/metrics` on kong-api-gateway:8100 | `PROMETHEUS_SOURCE=container`; Kong always on | current | confirmed | — |
| `spark → minio` | `services/spark/service.yml:201` | `services/spark/compose.yml:297-298` spark-init `mc alias set`/`mc mb` (init); s3a config at `:106-115`, `:259-264` (loaded config) | none: literal `http://minio:9000` (e.g. `AWS_ENDPOINT_URL_S3` at `services/spark/compose.yml:98`) | S3 API on minio:9000, `s3a://spark-history/` and lakehouse bucket | `SPARK_SOURCE=container` hard-requires MinIO (`service_config.py:941-944`) | current | confirmed | Four containers, two credential sets |
| `supavisor → supabase` | `services/supavisor/service.yml:129` | `pooler.exs:4` `select version()`, `:9-33` tenant registration, run by `services/supavisor/compose.yml:36` (code) | `DATABASE_URL` at `services/supavisor/compose.yml:15`; `POSTGRES_HOST` at `:23` | Postgres wire protocol on supabase-db:5432 | `SUPAVISOR_SOURCE=container`; Supabase always on | current | confirmed | — |
| `trino → iceberg-rest` | `services/trino/service.yml:93` | `services/trino/catalog/lakehouse.properties:1-3` REST catalog (loaded config) | none: literal `iceberg.rest-catalog.uri=http://iceberg-rest:8181` | Iceberg REST catalog HTTP API on iceberg-rest:8181 | `TRINO_SOURCE=container` hard-requires `ICEBERG_REST_SOURCE=container` (`service_config.py:1502-1505`) | current | confirmed | — |
| `trueforge → supabase` | `services/trueforge/service.yml:159` | Compose env only (`services/trueforge/compose.yml:36,90`) to the unvendored upstream image | `DATABASE_URL` at `services/trueforge/compose.yml:36` | Postgres wire protocol on supabase-db:5432, database `trueforge` | `TRUEFORGE_SOURCE=container`; Supabase always on | current | unconfirmed | DB is provisioned and `STANDALONE=false`; no in-repo call site |
| `verba → weaviate` | `services/verba/service.yml:112` | Compose env only (`services/verba/compose.yml:22`) to the unvendored upstream image | `WEAVIATE_URL_VERBA` at `services/verba/compose.yml:22` (from `service_config.py:1079-1097`) | Weaviate HTTP `/v1/*` on weaviate:8080 | `VERBA_SOURCE=container`; Weaviate container or localhost; disabled refused | current | unconfirmed | Tests assert wiring, not a call |
| `weaviate → litellm` | `services/weaviate/service.yml:212` | Backend writes `moduleConfig.text2vec-openai.baseURL` (`services/backend/app/app/memory_store.py:650-670`); Weaviate vectorizes on insert/nearText (loaded class config) | `baseURL` from the backend's `LITELLM_BASE_URL`; key `OPENAI_APIKEY` at `services/weaviate/compose.yml:69` | HTTP POST `/v1/embeddings` on litellm:4000 | `WEAVIATE_SOURCE=container`; only collections pointed at LiteLLM (backend LangMem) | optional | confirmed | weaviate-init receives `LITELLM_*` but never calls it |
| `zeppelin → spark` | `services/zeppelin/service.yml:200` | `services/zeppelin/init/scripts/seed-spark-interpreter.py:39,276` sets `spark.master` (loaded interpreter config) | `SPARK_MASTER` at `services/zeppelin/compose.yml:26` | Spark standalone master RPC on spark-master:7077 | `ZEPPELIN_SOURCE=container` hard-requires Spark (`service_config.py:978-980`) | current | confirmed | Lazy: opens on first `%spark` paragraph |

### 2.2. Verdicts and corrections

The pass found 29 edges confirmed, 7 unconfirmed and 1 incorrect.

**Incorrect: `backend → neo4j`, now removed from `data_flow.calls`.** No Backend
code opens a Bolt connection. `graphiti_experiment.py` only reports whether
`NEO4J_URI` is set, and the Backend manifest already classes these Neo4j
variables as stubbed placeholders. The edge was removed from
`services/backend/service.yml`, with a comment saying why, and the Backend and
Neo4j READMEs, SVGs and HTML files were regenerated. The planned integration remains in the Backend
README's user-authored "Future — Missing pair integrations" block.

**Unconfirmed, left in place and marked here.** Five edges depend on an
upstream image Atlas does not vendor, so the call itself cannot be seen in the
repo:

- `llm-graph-builder → neo4j`
- `open-webui → litellm`
- `openclaw → litellm`
- `trueforge → supabase`
- `verba → weaviate`

Two exist only after operator setup outside the repo:

- `cloudflared → kong`: the tunnel's public hostnames are configured in the
  Cloudflare dashboard.
- `jenkins → minio`: no Jenkins job ships with Atlas.

**Documentation and comment corrections found on the way:**

- `services/grafana/README.md` said the Prometheus datasource URL becomes empty
  when Prometheus is disabled. That is not what happens. Compose's
  `${PROMETHEUS_ENDPOINT:-http://prometheus:9090}` falls back on an empty
  value, so Grafana still points at `prometheus:9090`, a host that does not
  resolve. The README now says so.
- `services/kong/service.yml` cited `kong_config_generator.py` line numbers
  that had drifted, and `services/jupyterhub/service.yml` cited stale
  `compose.yml` lines. Both comments now name the generator methods and the
  Compose keys instead of line numbers.

**Observations recorded without a change:**

- `lightrag-init` probes `http://litellm:4000` directly
  (`services/lightrag/init/scripts/resolve-models.py:24`) and ignores the
  `LIGHTRAG_*_BINDING_HOST` overrides.
- The OTel Collector ignores the `TEMPO_ENDPOINT` it is given; its exporter
  address is a literal in `config/config.yaml`.
- `weaviate-init` receives `LITELLM_*` but never calls LiteLLM.
- Weaviate's default `text2vec-openai` module has no default `baseURL`, so a
  collection created without one might target OpenAI directly with the LiteLLM
  key. This was not verified at runtime and is not asserted.

### 2.3. What `data_flow.calls` cannot express

`calls` is a flat list of names (`bootstrapper/schemas/service.schema.json`,
`data_flow.calls`). Every edge renders the same way, so the generated output
cannot tell these apart:

- a current edge from an optional one: `jupyterhub → mcp-servers` needs
  `MCP_SERVERS_SOURCE=container`, and `kong → ray` needs a Ray source;
- a mandatory edge from a best-effort one: Spark, Trino, Zeppelin and the OTel
  Collector refuse to start without their target;
- a direct edge from one that is routed elsewhere under another SOURCE:
  `n8n → supabase` goes through Supavisor when `SUPAVISOR_SOURCE` is enabled;
- a planned edge from a live one;
- an edge that an operator must first set up;
- where the evidence for an edge lives.

This ledger therefore records status and verdict itself. Qualifying the edges
in the schema is tracked as a separate follow-up ([#1273](https://github.com/thekaveh/atlas/issues/1273)), so that the
generated diagrams and tables can show the difference.

## 3. Unsampled edges by caller

These 170 edges were not reviewed in the 2026-09-27 pass. Each line number is
the edge's position in its caller's manifest as of the commit that added this
pass, so the next pass can resume from here. `backend → neo4j` is not listed:
it was sampled and removed.

| Caller | Unsampled | Edges (manifest line) |
|---|---:|---|
| `airflow` | 8 | spark (:338), redpanda (:339), minio (:340), iceberg-rest (:341), litellm (:342), weaviate (:343), neo4j (:344), redis (:345) |
| `backend` | 17 | weaviate (:357), litellm (:358), comfyui (:359), fal (:360), n8n (:361), ray (:362), local-deep-researcher (:363), celery (:364), supavisor (:365), tika (:366), docling (:367), lightrag (:368), tei-reranker (:369), minio (:370), redis (:371), otel-collector (:372), kong (:373) |
| `backup` | 3 | minio (:162), neo4j (:163), weaviate (:164) |
| `celery` | 9 | supabase (:140), litellm (:141), weaviate (:142), supavisor (:143), docling (:144), tika (:145), lightrag (:146), minio (:147), otel-collector (:148) |
| `grafana` | 2 | tempo (:104), loki (:105) |
| `hermes` | 6 | stt-provider (:242), tts-provider (:243), comfyui (:244), searxng (:245), airflow (:253), lightrag (:254) |
| `iceberg-rest` | 1 | supabase (:111) |
| `jupyterhub` | 19 | hermes (:198), weaviate (:199), neo4j (:200), supabase (:201), ray (:202), spark (:203), redpanda (:204), redis (:205), comfyui (:206), n8n (:207), backend (:208), searxng (:209), minio (:210), iceberg-rest (:211), mlflow (:212), label-studio (:213), docling (:214), stt-provider (:215), tts-provider (:216) |
| `kong` | 37 | open-webui (:84), jupyterhub (:85), n8n (:86), hermes (:87), openclaw (:88), local-deep-researcher (:89), minio (:90), supabase (:91), weaviate (:92), neo4j (:93), comfyui (:94), searxng (:95), stt-provider (:96), tts-provider (:97), doc-processor (:98), litellm (:99), ollama (:100), airflow (:101), spark (:102), lightrag (:103), tei-reranker (:104), verba (:105), trino (:108), redpanda (:109), tika (:110), crawl4ai (:111), langfuse (:112), mlflow (:113), label-studio (:114), jenkins (:115), llm-graph-builder (:116), mcp-servers (:117), celery (:118), asset-baker (:119), asset-worker (:120), grafana (:125), prometheus (:126) |
| `label-studio` | 1 | minio (:139) |
| `langfuse` | 2 | redis (:210), minio (:211) |
| `lightrag` | 5 | supabase (:313), neo4j (:314), redis (:315), docling-lightrag-adapter (:316), backend (:317) |
| `litellm` | 10 | redis (:138), ollama (:139), cloud-providers (:140), hermes (:141), lightrag (:142), vllm-metal (:143), fal (:144), tei-reranker (:145), otel-collector (:146), langfuse (:147) |
| `llm-graph-builder` | 1 | litellm (:190) |
| `local-deep-researcher` | 2 | searxng (:152), crawl4ai (:153) |
| `mcp-servers` | 2 | neo4j (:113), searxng (:114) |
| `mlflow` | 1 | minio (:133) |
| `n8n` | 13 | redis (:222), weaviate (:223), backend (:228), doc-processor (:229), tika (:230), hermes (:231), litellm (:232), stt-provider (:233), tts-provider (:234), searxng (:235), lightrag (:236), crawl4ai (:237), supavisor (:238) |
| `open-webui` | 7 | supabase (:200), redis (:201), backend (:202), comfyui (:203), stt-provider (:204), tts-provider (:205), local-deep-researcher (:206) |
| `otel-collector` | 1 | loki (:92) |
| `prometheus` | 10 | litellm (:165), backend (:166), asset-worker (:167), asset-baker (:168), n8n (:169), weaviate (:170), minio (:171), supabase (:172), redis (:173), grafana (:174) |
| `spark` | 2 | iceberg-rest (:202), redpanda (:203) |
| `trino` | 1 | minio (:94) |
| `trueforge` | 3 | redis (:160), litellm (:161), mcp-servers (:162) |
| `verba` | 1 | litellm (:113) |
| `weaviate` | 1 | multi2vec-clip (:213) |
| `zeppelin` | 5 | supabase (:201), minio (:202), iceberg-rest (:203), redpanda (:204), trino (:205) |
