# 7.3. Expected Startup Warnings

In the first minute after `./start.sh`, some warnings and one-shot ERROR lines look serious but are harmless. Each is library noise we cannot suppress without a fork, an intentional secure default, or a one-time startup race. This page lists the ones we investigated and accepted, so operators do not chase them again.

If you see a warning *not* on this list, that's a real signal — start there.

> **Note on container names.** The `Service` column shows the container as it appears in `docker ps` / `docker logs` on a default-`PROJECT_NAME` stack (`atlas-…`). If you set `PROJECT_NAME` in `.env`, substitute that prefix in place of `atlas-` when grepping logs.

## 1. Inventory

### 1.1. Library-internal / image-baked deprecations (no action from us)

| Service | Message | Why we live with it |
|---|---|---|
| `atlas-supabase-auth` | `DEPRECATION NOTICE: GOTRUE_JWT_ADMIN_GROUP_NAME not supported by Supabase's GoTrue, will be removed soon` | Atlas does not set this variable. GoTrue prints the notice whether or not it is set. |
| `atlas-supabase-auth` | `DEPRECATION NOTICE: GOTRUE_JWT_DEFAULT_GROUP_NAME not supported by Supabase's GoTrue, will be removed soon` | Atlas sets `GOTRUE_JWT_DEFAULT_GROUP_NAME=authenticated` on purpose, as upstream Supabase compose does. GoTrue v2.191.0 still uses it. Without it, user tokens carry an empty `role` claim that the backend and PostgREST refuse. Do not remove it. |
| `atlas-kong-api-gateway` | `[warn] rate-limiting: config.redis_port / redis_ssl / redis_ssl_verify / redis_timeout / redis_database is deprecated, please use config.redis.<key> instead (deprecated after 4.0)` × ~20 (once per worker) | Fired by Kong's bundled `rate-limiting` plugin schema. We never set any `redis_*` keys — the only `rate-limiting` plugin instance is on the `searxng-api` route with `policy: local` (no Redis at all). Kong's plugin warns about its own schema defaults. Will need attention when Kong 4.0 ships. |
| `atlas-supabase-realtime` | `[warning] Replica region not found, defaulting to Realtime.Repo` × 2 | Single-node mode. Realtime is multi-region aware; on a single-node setup it defaults to the primary Repo, which is what we want. |
| `atlas-open-web-ui` | `WARNING: Running pip as the 'root' user can result in broken permissions ...` × 5 | Emitted by the Open WebUI runtime tool installation. The image runs as root by design. |
| `atlas-jupyterhub` | `WARNING: Use start-notebook.py instead` / `WARNING: container must be started as root to grant sudo permissions!` | Both come from the jupyter/docker-stacks base. We need root for sudo grants to the spawned user notebooks. |
| `atlas-minio` | `WARNING: Host local has more than 0 drives of set. A host failure will result in data becoming unavailable.` | Single-host dev mode. Production multi-node MinIO would silence this. |

### 1.2. Intentional secure defaults

| Service | Message | Why |
|---|---|---|
| `atlas-hermes` | `WARNING gateway.run: No user allowlists configured. All unauthorized users will be denied. Set GATEWAY_ALLOW_ALL_USERS=true ... or configure platform allowlists ...` | Hermes is secure-by-default — operator must explicitly opt in (per-platform allowlists or `GATEWAY_ALLOW_ALL_USERS=true`). |
| `atlas-open-web-ui` | `WARNING: CORS_ALLOW_ORIGIN IS SET TO '*' - NOT RECOMMENDED FOR PRODUCTION DEPLOYMENTS.` | Dev default; production stacks should set an explicit origin list. |

### 1.3. Unavoidable startup races

| Service | Message | Cause / mitigation |
|---|---|---|
| `atlas-supabase-db` | `ERROR: relation "LiteLLM_VerificationTokenView" does not exist` × 8, `ERROR: type "LiteLLM_VerificationTokenView" already exists` × 1 (and similar for `MonthlyGlobalSpend`, `Last30dKeysBySpend`, …) | LiteLLM starts 4 worker processes. The parent runs `prisma migrate deploy` once. Then each worker calls `create_missing_views()` (SELECT, then CREATE). Two views (`LiteLLM_VerificationTokenView`, `Last30dTopEndUsersSpend`) use `CREATE VIEW`, not `CREATE OR REPLACE VIEW`, so one worker wins and the others see "already exists". The race is upstream. The only fixes are patching `litellm/proxy/db/create_views.py`, `--num_workers 1` (kills throughput), or an upstream patch. |
| `atlas-weaviate` | `level":"warning","msg":"heartbeat timeout reached, starting election` (1 line) | Single-node Raft needs a leader election to make the only node the leader. Election takes ~100 ms. The heartbeat timeout is intrinsic to Raft. The related join warning is silenced (§1.8). |
| `atlas-lightrag` | `ERROR: PostgreSQL database, error:relation "lightrag_*" does not exist` × 8 at first boot, each immediately followed by `Creation success` + `Creating HNSW index ...` | LightRAG's PGVectorStorage uses a SELECT-then-CREATE pattern (probe-then-init) for its 8 backing tables. On a fresh DB the SELECT errors (no rows exist), the error is caught, CREATE TABLE runs, success line follows. Self-resolving; only fires on the very first boot against a clean Supabase volume. Same shape as the LiteLLM view-create race above. |
| `atlas-kong-api-gateway` | `[warn] the "user" directive makes sense only if the master process runs with super-user privileges, ignored` × 2 | Kong's bundled nginx config sets a `user` directive, but the container runs as non-root, as Kong recommends. nginx ignores the directive; no functional impact. Kong upstream chose not to silence this. |

### 1.4. macOS Docker Desktop — container expects Linux host paths

The following warnings appear because containers built for Linux look for Linux-specific paths (`/etc/machine-id`, `/run/udev/data`, container-runtime sockets) that don't exist on macOS Docker Desktop's VM. All non-fatal; the affected services run normally.

| Service | Message | Why |
|---|---|---|
| `atlas-cadvisor` | `Failed to get system UUID: open /etc/machine-id: no such file or directory` + Docker / Podman / containerd / crio / mesos `factory` registration failures | Atlas intentionally gives cAdvisor no container-runtime socket and points Docker discovery at an unavailable endpoint. Runtime discovery therefore fails by design; cAdvisor continues exporting CPU, memory, and I/O series from the mounted cgroup hierarchy under `/sys`. These series use cgroup IDs rather than Docker container names or labels. |
| `atlas-cadvisor` | `Error while reading product_name: open /sys/class/dmi/id/product_name: no such file or directory` + `Couldn't collect info from any of the files in "/rootfs/etc/machine-id,/var/lib/dbus/machine-id"` — recurring every ~5 minutes | cAdvisor re-probes DMI and machine identity during housekeeping. Atlas does not mount the host root filesystem, and Docker Desktop's VM may not expose DMI product data through the restricted `/sys` mount. The probe is optional; raw-cgroup metrics continue without host identity metadata or Docker labels. |
| `atlas-node-exporter` | `Failed to open directory, disabling udev device properties` (path `/run/udev/data`) | node-exporter's diskstats collector wants udev metadata. macOS doesn't have udev; the collector falls back to bare diskstats without device labels. |
| `atlas-postgres-exporter` | `Error opening config file "postgres_exporter.yml": no such file or directory` | The exporter looks for an optional config file we don't mount. Without it, it uses sensible defaults and queries the database directly. Harmless. |

### 1.5. Bind-mount permission quirks (macOS Docker Desktop)

| Service | Message | Why |
|---|---|---|
| `atlas-searxng` | `WARNING: "/etc/searxng" directory is not owned by "searxng:searxng"` + same for `settings.yml` | `services/searxng/config/` is bind-mounted into `/etc/searxng`. The host user (uid 501) owns the macOS files. Docker Desktop's UID translation shows them as `root:root` in the container. A `chown` in the container cannot persist on a host bind mount. SearXNG says this "MAY cause issues"; in practice the healthcheck and searches succeed. |

### 1.6. Capability gaps (documented, not silenced)

| Service | Message | Note |
|---|---|---|
| `atlas-n8n-worker` | `Failed to start Python task runner in internal mode. because Python 3 is missing from this system.` | The n8n image is Alpine + Node, no Python. We don't bundle the Python task runner. For Python nodes, build a custom n8n image with `apk add python3 py3-pip`. Or run the runner in external mode ([n8n docs](https://docs.n8n.io/hosting/configuration/task-runners/#setting-up-external-mode)). |

### 1.7. Acceptable info messages

| Service | Message | Why |
|---|---|---|
| `atlas-weaviate` | `Multiple vector spaces are present, GraphQL Explore and REST API list objects endpoint module include params has been disabled as a result.` | We register multiple vectorizers (`text2vec-openai`, `text2vec-ollama`, `multi2vec-clip`, `generative-openai`, `generative-ollama`) so different classes can pick the right one. The trade-off is that the GraphQL `Explore` endpoint is disabled. We don't use `Explore` in any consumer (backend uses class-specific GraphQL `Get` queries instead). |
| `atlas-grafana` | `Could not register plugin" pluginId=xychart error="plugin xychart is already registered"` × 2 at boot | Grafana 11.4.3 ships `xychart` as both a core built-in and again in its plugin scanner path. The second registration attempt raises this error. The plugin works normally; this is a known upstream Grafana packaging issue in the 11.x series. |
| `atlas-tei-reranker` | `Download failed: HTTP status client error (404 Not Found)` for `1_Pooling/config.json` and `config_sentence_transformers.json`. `Could not find a Sentence Transformers config`. `Backend does not support a batch size > 8, forcing max_batch_requests=8`. | TEI probes optional Sentence Transformers sidecars that `mxbai-rerank-base-v1` does not ship (it's a cross-encoder, not a bi-encoder). The 404s are expected probes. Batch size is capped to 8 because the reranker backend is CPU-only. Service is `healthy` and reranking works. All four lines appear once at boot, then silence. |
| `atlas-tei-reranker` | `Invalid hostname, defaulting to 0.0.0.0` | TEI reads its bind address from `HOSTNAME`, which Docker sets to the container ID. The ID is not an IP address, so TEI binds `0.0.0.0:80`, as intended. No functional impact. |
| `atlas-lightrag` | `Warning: Startup directory must contain .env file for multi-instance support.` | LightRAG's server checks for `/app/.env` (its `WORKDIR`) to enable multi-instance workspace switching via `.env`. We write the resolved config to `/app/data/.env` instead (sourced via the entrypoint `sh -c 'set -a; . /app/data/.env; ...'`). Single-instance mode works correctly; this warning can be ignored until multi-instance namespacing is needed. |
| `atlas-lightrag` | `WARNING: PostgreSQL: workspace data in table 'LIGHTRAG_VDB_ENTITY/RELATION/CHUNKS_ollama_nomic_embed_text_768d' is empty.` × 3 | Fires on first boot (or after a DB wipe) because the HNSW-backed vector tables are freshly created and contain no vectors yet. LightRAG warns so that operators can distinguish a fresh setup from an accidental embedding-model change. Self-resolves as soon as documents are ingested. |
| `atlas-ray-head` / `atlas-ray-worker-1` / `atlas-ray-worker-2` | `WARNING: The object store is using /tmp/ray instead of /dev/shm because /dev/shm has only 8589934592 bytes available.` | The 8 GiB is Atlas's own `shm_size: 8gb` in `services/ray/compose.yml` (Docker's default is 64 MB). Ray recommends `>30%` of RAM for the object store; on machines with >26 GiB RAM the threshold triggers. Ray falls back to `/tmp/ray` (disk-backed), which is slower for large tensor transfers but works correctly for our use cases. To silence it, raise that `shm_size` value together with `RAY_HEAD_MEMORY_LIMIT` / the worker memory limits, which bound the usable object store. |
| `atlas-zeppelin` | `Failed to load XML configuration, proceeding with a default`. `zeppelin.config.fs.dir is not specified, fall back to local conf directory` × 2. `Credential/Interpreter/NotebookAuthorization file … is not existed` × 3. | Zeppelin starts fresh on each boot (no persistent `/opt/zeppelin/conf` bind-mount). It logs these WARNs, generates defaults in memory, and operates normally. All go silent immediately after startup. |
| `atlas-spark-master` / `atlas-spark-connect` / `atlas-spark-history` / `atlas-spark-worker-{1,2}` | `WARNING: Using incubator modules: jdk.incubator.vector` + `Unable to load native-hadoop library for your platform... using builtin-java classes where applicable` | Both are JVM-level one-shot boot warnings. The incubator vector API is used by Spark's internal optimizers (harmless on JDK 17+). The `apache/spark:4.1.2` base image does not bundle the native Hadoop library (`libhadoop.so`). Spark uses Java implementations instead, with no loss of function for our workloads. |

### 1.8. Silenced — fixed at the source (do not re-add)

Config edits silenced these messages:

| Service | Was | How silenced |
|---|---|---|
| `atlas-n8n` | `DEPRECATION: N8N_RUNNERS_ENABLED → Remove this environment variable; it is no longer needed.` | Removed `N8N_RUNNERS_ENABLED: true` from `services/n8n/compose.yml` (was set on both `n8n` and `n8n-worker`; default is now `true`). |
| `atlas-backend` | `UserWarning: Storage endpoint URL should have a trailing slash. The URL has been automatically corrected.` | Added trailing `/` in `services/backend/app/app/main.py` (`storage_url = f"{KONG_URL}/storage/v1/"`). |
| `atlas-open-web-ui` | `WARNING langchain_community.utils.user_agent: USER_AGENT environment variable not set` | Set `USER_AGENT: Atlas/Open-WebUI` in `services/open-webui/compose.yml`. |
| `atlas-weaviate` | `failed to join cluster from [<self>:8300]` + extra `heartbeat timeout` / `starting election` lines | Set `RAFT_BOOTSTRAP_EXPECT=1` + `RAFT_JOIN=weaviate` in `services/weaviate/compose.yml`. Single-node mode now starts with explicit voter config; the join-attempt warning is gone (the remaining heartbeat-timeout is intrinsic to Raft single-node election). |
| `atlas-searxng` | `ERROR:searx.botdetection: X-Forwarded-For nor X-Real-IP header is set!` (one-shot at boot) | Added `--header=X-Forwarded-For: 127.0.0.1` to the healthcheck in `services/searxng/compose.yml`. The trusted-proxies module's `log_error_only_once` would otherwise fire on the first XFF-less healthcheck after startup. |

## 2. When to update this page

Add an entry here when:

1. You investigate a warning, confirm it's library-internal / one-shot / intentional, and want future operators to skip the same investigation.
2. You silence a warning with a code or config change. Move the entry to §1.8 and say how it was silenced.

Remove an entry when:

1. The upstream library stops emitting the message in a version we've adopted.
2. We've fixed the root cause and the warning no longer appears in a fresh `./start.sh`.

If you encounter a startup warning **not** on this list, that's a real signal worth investigating before adding it here.
