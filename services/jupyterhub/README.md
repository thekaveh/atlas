# JupyterHub - Data Science IDE

**Port:** 63094
**Category:** Application Tier
**Dependencies:** PostgreSQL, Redis, LiteLLM (gateway to Ollama / cloud LLMs), Weaviate, Neo4j, MinIO, Iceberg REST, Spark

---

## 1. Overview

JupyterHub provides an interactive Jupyter Lab environment pre-configured with access to all Atlas services. It's designed for data scientists and AI engineers to experiment, prototype, and develop AI applications.

## 2. Quick Start

### 2.1 Access JupyterHub

```bash
# Start the stack (JupyterHub enabled by default)
./start.sh

# Access at: http://localhost:63094
```

### 2.2 Disable JupyterHub

```bash
# Temporarily disable
./start.sh --jupyterhub-source disabled

# Permanently disable (edit .env)
JUPYTERHUB_SOURCE=disabled
```

## 3. Features

- **Pre-installed AI Libraries**: OpenAI SDK (pointed at LiteLLM), LangChain, LlamaIndex, Transformers
- **Database Clients**: Weaviate, Neo4j, PostgreSQL, Redis, Supabase
- **Lakehouse Clients**: PySpark Connect, `boto3`, `s3fs`, `pyiceberg`, `pyarrow`, and `duckdb` for MinIO + Iceberg REST workflows
- **Financial Research Kit**: OpenBB + CCXT libraries and a guarded paper-portfolio notebook for read-only market research
- **Sample Notebooks**: 13 ready-to-use notebooks (00-12) demonstrating service integration
- **Persistent Storage**: All notebooks saved in Docker volumes
- **Environment Variables**: Auto-configured connections to all services
- **Multi-kernel runtime**: Python 3 (default) plus **Scala 2.13** and **Scala 3** kernels via Almond. Pick one from JupyterLab's launcher or VS Code's kernel picker. See §11.
- **VS Code-ready**: configured for remote-Jupyter access out of the box. Open local `.ipynb` files in VS Code and run them on this container as the kernel. See §10.

## 4. Configuration

### 4.1 Environment Variables (`.env`)

```bash
JUPYTERHUB_SOURCE=container     # Options: container, disabled
# Base image is the maintained quay.io home of the Jupyter Docker Stacks.
# The old Docker Hub jupyter/* images were frozen in 2023, so we migrated to
# quay.io and pinned the python-3.11 line (patch 3.11.10) — same Python, just
# the maintained registry. A moving :latest tag would force a full rebuild
# (8-10 min) every start; a pinned patch keeps Docker-layer caching.
JUPYTERHUB_IMAGE=quay.io/jupyter/datascience-notebook:python-3.11.10
JUPYTERHUB_PORT=63094
JUPYTERHUB_TOKEN=               # Optional: authentication token
```

> **Performance Tip**: The `python-3.11` tag provides stable Docker layer caching, reducing rebuild times from 8-10 minutes to 5-10 seconds on subsequent starts. Using `:latest` forces Docker to check for updates and rebuild layers every time.

### 4.2 Authentication

- **No token set**: Auto-generated token shown in logs
- **Custom token**: Set `JUPYTERHUB_TOKEN` in `.env`
- **View token**: `docker logs ${PROJECT_NAME}-jupyterhub | grep token`

## 5. Sample Notebooks

| Notebook | Description |
|----------|-------------|
| `00_environment_check.ipynb` | Verify all service connections |
| `01_litellm_basics.ipynb` | LLM inference via the LiteLLM gateway (Ollama upstream) |
| `02_langchain_rag.ipynb` | RAG pipeline with Weaviate |
| `03_neo4j_graphs.ipynb` | Knowledge graph queries |
| `04_supabase_data.ipynb` | Database and storage operations |
| `05_comfyui_images.ipynb` | AI image generation |
| `06_n8n_workflows.ipynb` | Workflow automation |
| `07_ray_cluster.ipynb` | Distributed compute on the Ray cluster |
| `08_scala_basics.ipynb` | Scala 3 syntax, `import $ivy` dependency loading, calling LiteLLM from Scala, Scala-3 enums + extension methods. Opens on the `scala3` kernel. |
| `09_spark_connect.ipynb` | Distributed Spark via the `spark-connect` sidecar (DataFrame/SQL + an s3a MinIO round-trip). Requires `SPARK_SOURCE != disabled`. |
| `10_spark_scala.ipynb` | The Scala counterpart to 09 — Spark Connect from the **Scala 2.13** kernel via `import $ivy.\`org.apache.spark::spark-connect-client-jvm:4.1.2\``. |
| `11_financial_research_kit.ipynb` | Read-only OpenBB + CCXT market research, paper portfolio analytics, optional MinIO datasets, MLflow paper-run metrics, and LiteLLM summaries. No live trading. |
| `12_iceberg_advanced_sql.ipynb` | Spark Connect advanced Iceberg smoke: `MERGE INTO`, `VERSION AS OF`, branch/WAP, schema evolution, nested JSON, Structured Streaming, and table maintenance. |

## 6. Service Integration Examples

### 6.1 Connect to the LLM gateway (LiteLLM)

Every notebook talks to LiteLLM via the OpenAI-compatible API — never to Ollama directly. The container has `OPENAI_API_BASE` and `OPENAI_API_KEY` pre-set from `LITELLM_BASE_URL` and `LITELLM_API_KEY` (which equals `LITELLM_MASTER_KEY`).

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url=os.getenv("OPENAI_API_BASE"),  # e.g. http://litellm:4000/v1
    api_key=os.getenv("OPENAI_API_KEY"),    # equals $LITELLM_API_KEY
)

response = client.chat.completions.create(
    model="ollama/qwen3.6:latest",  # or "gpt-4o", "claude-sonnet-4-6", etc.
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

LangChain users should reach for `ChatOpenAI` / `OpenAIEmbeddings` against the same env vars:

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="ollama/qwen3.6:latest")  # picks up OPENAI_API_BASE / OPENAI_API_KEY
```

### 6.2 Connect to Weaviate (Vector DB)

```python
import os
import weaviate

client = weaviate.connect_to_custom(
    http_host=os.getenv("WEAVIATE_URL").replace("http://", "").split(":")[0],
    http_port=8080
)
```

### 6.3 Connect to Neo4j (Graph DB)

```python
import os
from neo4j import GraphDatabase

driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD"))
)
```

### 6.4 Run Spark (Spark Connect)

The image carries `pyspark-client` — a thin Spark Connect client, no JVM. The
driver runs on the `spark-connect` sidecar (requires `SPARK_SOURCE != disabled`);
`s3a://` and Spark History work via the server's own conf, so no storage keys in
the notebook. `SPARK_REMOTE` (compose-injected, default `sc://spark-connect:15002`)
can be overridden to target a remote/managed endpoint (e.g. EMR Serverless). See
`09_spark_connect.ipynb`.

```python
import os
from pyspark.sql import SparkSession

spark = SparkSession.builder.remote(
    os.getenv("SPARK_REMOTE", "sc://spark-connect:15002")
).getOrCreate()
spark.range(5).show()
```

The advanced lakehouse validation notebook lives at
`12_iceberg_advanced_sql.ipynb` and can also be run from the repository root:

```bash
scripts/smoke-iceberg-advanced-sql.sh spark-connect
```

This is an opt-in `data-eng` / `all` track smoke. It adds No new service, no new
SOURCE, and no new port; it requires `SPARK_SOURCE=container`,
`ICEBERG_REST_SOURCE=container`, and `MINIO_SOURCE=container`. It covers
`MERGE INTO`, `VERSION AS OF`, Structured Streaming from `s3a://landing/` into
Iceberg with `s3a://checkpoints/`, and maintenance procedures. See
[`docs/deployment/iceberg-advanced-smoke.md`](../../docs/deployment/iceberg-advanced-smoke.md).

### 6.5 Query the lakehouse from Python

The image includes the Python lakehouse clients used by Atlas data tracks:
`boto3`, `s3fs`, `pyiceberg[s3fs]`, `pyarrow`, and `duckdb`. Compose injects
scoped Jupyter MinIO credentials, `AWS_ENDPOINT_URL_S3=http://minio:9000`,
`ICEBERG_REST_URI=http://iceberg-rest:8181`, and
`ICEBERG_WAREHOUSE=s3://lakehouse/` by default. The MinIO calls work whenever
MinIO is enabled; the Iceberg catalog smoke requires `ICEBERG_REST_SOURCE=container`.

```python
import os
import boto3
import duckdb
from pyiceberg.catalog import load_catalog

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["AWS_ENDPOINT_URL_S3"],
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
)
print([bucket["Name"] for bucket in s3.list_buckets()["Buckets"]])

catalog = load_catalog(
    "rest",
    uri=os.environ["ICEBERG_REST_URI"],
    warehouse=os.environ["ICEBERG_WAREHOUSE"],
)
print(catalog.list_namespaces())

arrow_table = catalog.load_table(("default", "example")).scan().to_arrow()
print(duckdb.sql("select count(*) from arrow_table").fetchone())
```

Import smoke:

```bash
docker exec ${PROJECT_NAME}-jupyterhub python - <<'PY'
import boto3, s3fs, pyarrow, duckdb
from pyiceberg.catalog import load_catalog
print("lakehouse imports ok")
PY
```

Catalog smoke:

```bash
docker exec ${PROJECT_NAME}-jupyterhub python - <<'PY'
import os
from pyiceberg.catalog import load_catalog
catalog = load_catalog(
    "rest",
    uri=os.environ["ICEBERG_REST_URI"],
    warehouse=os.environ["ICEBERG_WAREHOUSE"],
)
print(catalog.list_namespaces())
PY
```

## 7. Data Persistence

- **Work Directory**: `/home/jovyan/work` - Persisted in `jupyterhub-data` volume
- **Sample Notebooks**: `/home/jovyan/notebooks` - Read-only, copy to `work/` to modify
- **Shared Config**: `/shared` - Weaviate configuration (read-only)

## 8. Custom Packages

### 8.1 Temporary Installation

```bash
!pip install package-name
```

### 8.2 Permanent Installation

1. Edit `services/jupyterhub/build/requirements.txt`
2. Rebuild: `docker compose build jupyterhub`
3. Restart: `./stop.sh && ./start.sh`

## 9. Advanced Configuration

### 9.1 GPU-aware workflows

JupyterHub itself is configured through `.env` and the stack startup flow. Prefer enabling GPU-backed upstream services through their SOURCE variables, for example `LLM_PROVIDER_SOURCE=ollama-container-gpu`, `COMFYUI_SOURCE=container-gpu`, or `MULTI2VEC_CLIP_SOURCE=container-gpu`.

Avoid direct `docker-compose.yml` edits for normal operation; local compose edits are unsupported experiments and can be overwritten or invalidated by future stack changes.

### 9.2 Multi-user Setup

For authentication, create `jupyterhub_config.py`:

```python
c.JupyterHub.authenticator_class = 'firstuseauthenticator.FirstUseAuthenticator'
```

## 10. Connecting from VS Code (run local notebooks on this container)

VS Code's Jupyter extension can use this container as the **remote kernel** for any `.ipynb` you open on your laptop. Notebook cells execute inside the container — with the full ML toolchain — while editor, history, and source control stay on your local machine.

### 10.1 One-time setup

1. **Install Microsoft's Jupyter extension** in VS Code (`ms-toolsai.jupyter`).
2. **Start the stack** so this container is running:
   ```bash
   ./start.sh
   ```
3. **Grab the token.** `JUPYTERHUB_TOKEN` in `.env` is optional — `service.yml` defaults it to empty, in which case Jupyter Server auto-generates one and prints it to the container's stdout on every restart. Pick whichever applies:
   ```bash
   # If you set JUPYTERHUB_TOKEN in .env manually:
   grep '^JUPYTERHUB_TOKEN=' .env

   # Otherwise (default), grep the auto-generated value out of the logs:
   docker logs ${PROJECT_NAME}-jupyterhub 2>&1 | grep -oE 'token=[a-f0-9]+' | tail -1
   ```
   Treat the token like a password. It changes every restart unless you pin it in `.env`.

### 10.2 Connect

1. Open any local `.ipynb` in VS Code.
2. Click the **kernel selector** in the top-right of the notebook → **"Select Another Kernel"** → **"Existing Jupyter Server"** → **"Enter the URL of the running Jupyter server"**.
3. Paste one of these URLs, substituting the actual token **and your actual ports**. The ports below are the defaults for `BASE_PORT=63000`; if you launched with `--base-port`, use your stack's real `JUPYTERHUB_PORT` (direct) and Kong HTTP port (aliased) from `.env` — e.g. on `--base-port 64000` the direct port is `64094`. `grep -E '^(JUPYTERHUB_PORT|KONG_HTTP_PORT)=' .env` prints both.
   - Direct port: `http://localhost:${JUPYTERHUB_PORT}/?token=<JUPYTERHUB_TOKEN>` (default `63094`)
   - Kong-aliased (after `./start.sh --setup-hosts`): `http://jupyter.localhost:${KONG_HTTP_PORT}/?token=<JUPYTERHUB_TOKEN>` (default `63000`)
4. When VS Code prompts to **remember the server**, give it a name (e.g. `atlas`). The server now appears in every future kernel-picker.
5. VS Code then asks which **kernel** to use on that server. Pick **Python 3 (ipykernel)**, **Scala 2.13**, or **Scala 3** depending on the notebook.

### 10.3 What's pre-configured on the stack side

#### 10.3.1 The container's startup chain

The container launches via a 4-step chain that ultimately invokes `jupyter lab` with the right flags. Knowing this chain matters because the compose-level `command:` override sits at exactly one of the steps, and editing it incorrectly silently breaks user-id setup or argv forwarding.

```
docker run
  → ENTRYPOINT (Dockerfile):     /usr/local/bin/startup.sh
  → CMD (compose `command:`):    start-notebook.sh \
                                   --ServerApp.allow_origin=${JUPYTER_ALLOW_ORIGIN:-*} \
                                   --ServerApp.allow_remote_access=True \
                                   --ServerApp.disable_check_xsrf=False
```

Docker then `exec`s `ENTRYPOINT + CMD` as a single argv:

```
/usr/local/bin/startup.sh start-notebook.sh --ServerApp.allow_origin=${JUPYTER_ALLOW_ORIGIN:-*} \
    --ServerApp.allow_remote_access=True --ServerApp.disable_check_xsrf=False
```

The four pieces of the chain:

| Step | Lives in | Role |
|---|---|---|
| 1. `ENTRYPOINT` → `/usr/local/bin/startup.sh` | `services/jupyterhub/build/Dockerfile` (`ENTRYPOINT` directive near the end) | Our wrapper. Prints the env summary, materialises `/home/jovyan/work/.env` from the resolved environment, prints the welcome banner, then ends with `exec "$@"` — which **forwards every CMD token unchanged** to the next stage. |
| 2. `CMD` → `start-notebook.sh ...` | `services/jupyterhub/compose.yml` `command:` block | This **replaces** the Dockerfile's `CMD ["start-notebook.sh"]` with our explicit list (the script name + three flags). Compose's `command:` always replaces the Dockerfile's CMD, never extends it — so the script name **must stay as element 0** of the list. Lose it and `startup.sh` ends up `exec`ing whatever flag is first, fails to find an executable, and the container restart-loops with `exec: --ServerApp.allow_origin=...: not found`. |
| 3. `start-notebook.sh` | upstream `jupyter/docker-stacks` image | The official Jupyter docker-stacks boot script. Switches UIDs/GIDs based on `NB_UID`/`NB_GID`/`GRANT_SUDO`, sources hooks under `/usr/local/bin/before-notebook.d/`, then `exec`s `jupyter lab` with **all of its own `$@` forwarded through**. Contract documented at [jupyter/docker-stacks](https://github.com/jupyter/docker-stacks/blob/main/images/docker-stacks-foundation/start.sh). |
| 4. `jupyter lab --ServerApp.allow_origin=<JUPYTER_ALLOW_ORIGIN> ...` | runtime | Jupyter Server (the underlying app) parses each `--ServerApp.<name>=<value>` as a Traitlet config set on the `ServerApp` class — equivalent to writing the same value in `jupyter_server_config.py`. |

Net effect: the three flags reach `jupyter lab` exactly as if they were in a config file, without us needing to mount one.

#### 10.3.2 What each flag actually changes

| Flag | Mechanism it disables / opens | Why VS Code needs it |
|---|---|---|
| `--ServerApp.allow_origin=${JUPYTER_ALLOW_ORIGIN:-*}` | Two things at once: (a) the `Access-Control-Allow-Origin` response header for CORS preflights, and (b) the WebSocket-upgrade `Origin` header check. By default, Jupyter Server rejects any non-empty origin that doesn't match the bound interface. | VS Code's webview frame sends `Origin: vscode-webview://...` on the kernel-WebSocket handshake. HTTP requests succeed (token auth handles those) but the upgrade is rejected with `403 Forbidden` and cell-execute hangs. The default `*` accepts any origin; **the token gate still applies on every request.** |
| `--ServerApp.allow_remote_access=True` | The "is the source IP local?" pre-check that runs *before* token auth. By default, Jupyter only accepts connections whose source IP matches the bound interface (loopback). | When VS Code on your host talks to the container, the request crosses Docker's network namespace — from the container's POV the source IP is the Docker bridge, not loopback. Without this flag, Jupyter returns `403 Forbidden — Disallowed origin` and the token check never runs. |
| `--ServerApp.disable_check_xsrf=False` | Cross-Site Request Forgery protection on POST/PUT/DELETE. `False` is **already** Jupyter's default. | Listed explicitly NOT to change behaviour but as a visible knob for a future "POST returns 403" debug session. VS Code's Jupyter extension sends the XSRF token in headers, so XSRF stays on safely. |

`JUPYTER_ALLOW_ORIGIN=*` is exported in the container environment and interpolated into the CLI flag above, making `.env` the single origin allowlist knob.

> **Tightening the origin allowlist:** `allow_origin='*'` is acceptable here because **the token is the auth gate** — without `JUPYTERHUB_TOKEN`, no request reaches the kernel. If you want a tighter list (e.g., only `vscode-webview://*` plus your dev origin), set `JUPYTER_ALLOW_ORIGIN` to a comma-separated allowlist in `.env` and restart JupyterHub.

> **Why not a config file?** A `jupyter_server_config.py` would work equivalently, but it requires either baking it into the image (rebuild on every config tweak) or bind-mounting it (one more volume to track). CLI flags on `command:` are the lowest-friction knob: edit one line in compose, restart, done.

### 10.4 Notebook layout: where files live

- The notebook file lives **on your laptop** (wherever you opened it in VS Code).
- The kernel runs **in the container**. Anything `os.getcwd()` returns is the container's filesystem, not your laptop's.
- The `/home/jovyan/work` directory is the persistent volume (`jupyterhub-data`). Use this if you need files (datasets, models) to survive container restarts.
- To open a notebook that ALREADY lives in the container (e.g., a sample), use VS Code's "Open Folder over SSH" workflow or browse to `http://localhost:63094` for the native JupyterLab UI. The VS Code remote-kernel flow above is for the inverse case: local file, remote kernel.

### 10.5 Troubleshooting

- **Token rejected.** Re-read `.env`; check the variable hasn't been hand-rotated. `docker logs ${PROJECT_NAME}-jupyterhub | grep -i token` shows the value the container actually started with.
- **Kernel starts but cells hang.** WebSocket upgrade failure — confirm the three `--ServerApp.*` flags are present in `docker inspect ${PROJECT_NAME}-jupyterhub --format='{{json .Config.Cmd}}'`. If the compose file was edited but the container wasn't rebuilt, run `./stop.sh && ./start.sh`.
- **CORS error in VS Code's developer console.** `JUPYTER_ALLOW_ORIGIN` was tightened past what VS Code uses. Set it to `*` temporarily; the Jupyter token is still required for any kernel operation.
- **"Address already in use" on 63094.** `./start.sh --base-port 64000` to relocate the whole stack.
- **Scala 2.13 / Scala 3 missing from the kernel picker.** The running image predates the Almond layer in `services/jupyterhub/build/Dockerfile`. Rebuild with `docker compose up jupyterhub --build --no-deps -d` (no full-stack restart needed). Confirm via `docker exec ${PROJECT_NAME}-jupyterhub jupyter kernelspec list` — both `scala213` and `scala3` should appear alongside `python3`. See §11 for full kernel-install details.
- **Server connects but no kernels listed.** Look at the URL VS Code stored — it must include `/?token=<value>`. If you pasted the URL without the token, VS Code thinks it's connected but every kernel request 403s. `Jupyter: Specify Jupyter Server for Connections` → re-enter the URL with the token suffix.
- **Cell output appears in the wrong notebook.** VS Code occasionally caches a stale kernel binding when you switch between two notebooks on the same server. Right-click the notebook tab → `Restart Kernel` resets the binding.

### 10.6 Verification screenshots

Reference screenshots for each step of the connect flow live under `services/jupyterhub/docs/screenshots/`:

- `01-vscode-select-existing-server.png` — VS Code's `Select Another Kernel` → `Existing Jupyter Server` dialog
- `02-vscode-enter-server-url.png` — the URL-entry prompt with the token suffix highlighted
- `03-vscode-server-name-prompt.png` — the friendly-name prompt (`atlas` is a good default)
- `04-vscode-kernel-picker.png` — the kernel-selection list showing `Python 3 (ipykernel)`, `Scala 2.13`, `Scala 3`

If you're following the docs to set up VS Code for the first time and one of these screenshots no longer matches your VS Code version, open an issue — the Jupyter extension's dialog text changes occasionally. _Screenshots are stored as PNGs in the repo so they survive offline reads of this README; capture them on your own machine if the directory is empty after a fresh clone (see §10.7)._

### 10.7 Capturing the screenshots yourself

If `docs/screenshots/` is empty (early-stage repo state), reproduce the four PNGs above by walking §10.1–§10.2 in your VS Code session and `Cmd+Shift+5` (macOS) / `Win+Shift+S` (Windows) at each dialog. Save under the same filenames so the references above resolve.

## 11. Multi-kernel runtime (Python + Scala)

This container ships **three kernels**:

| Kernel ID | Display name | Versions | Source |
|---|---|---|---|
| `python3` | Python 3 (ipykernel) | matches the `JUPYTERHUB_IMAGE` (currently 3.11) | upstream `jupyter/datascience-notebook` |
| `scala213` | Scala 2.13 | Scala `2.13.16`, Almond `0.14.5` | installed at image build time via Coursier |
| `scala3` | Scala 3 | Scala `3.4.3`, Almond `0.14.5` | installed at image build time via Coursier |

**To pick a Scala kernel:**

- **In JupyterLab:** open the launcher (`+` button) and click the Scala tile.
- **In VS Code:** kernel-picker → "Scala 2.13" or "Scala 3".

**To verify the kernels are actually installed in the running container:**

```bash
docker exec ${PROJECT_NAME}-jupyterhub jupyter kernelspec list
```

You should see `scala213` and `scala3` alongside `python3` (and `ir`, `julia-1.9` from the upstream image). If only `ir` / `julia-1.9` / `python3` appear, the container was built before the Scala layer was added — rebuild with the args at the top of the Dockerfile baked in:

```bash
docker compose up jupyterhub --build --no-deps -d
```

`--no-deps` skips restarting the entire stack; only the jupyterhub container is replaced. The image gains ~600 MB of toolchain on this build, mostly cached after the first run.

**Smoke-test a Scala cell** without opening JupyterLab — useful in CI / cold-start verification:

```bash
docker exec ${PROJECT_NAME}-jupyterhub bash -lc \
  "echo 'val x = (1 to 5).map(_ * 2).sum; println(s\"sum=\$x\")' | jupyter run --kernel=scala3 /dev/stdin"
```

The expected last line is `sum=30`. The first run for each Scala kernel resolves Almond's classpath and can take 30-60 s; subsequent runs are sub-second.

**To pin different Scala / Almond versions** edit `services/jupyterhub/build/Dockerfile` and bump the `ALMOND_VERSION` / `ALMOND_SCALA_2_VERSION` / `ALMOND_SCALA_3_VERSION` build args at the top, then rebuild:

```bash
docker compose build jupyterhub && ./stop.sh && ./start.sh
```

The Scala toolchain (JDK 17 + Coursier + both Almond kernels) adds **~600 MB** to the container image. If you don't need Scala, drop the `apt-get` `openjdk-17-jdk-headless` line plus the two `cs launch` blocks from the Dockerfile and rebuild.

## 12. Architecture

JupyterHub runs inside the Docker Compose network and receives environment variables for the services that are enabled. It reaches LLMs through the always-on LiteLLM gateway (`LITELLM_BASE_URL` / `LITELLM_API_KEY`, also exported as `OPENAI_API_BASE` / `OPENAI_API_KEY`) and connects directly to Weaviate, Neo4j, PostgreSQL/Supabase, Redis, MinIO, Iceberg REST, Spark Connect, ComfyUI, n8n, STT/TTS, and document-processing services when those services are available.

For the current high-level stack diagram, see [Architecture Diagram](../../docs/diagrams/architecture.svg).

## 13. Resources

- [Jupyter Lab Documentation](https://jupyterlab.readthedocs.io/)
- [JupyterHub Documentation](https://jupyterhub.readthedocs.io/)
- [Almond — Scala kernel for Jupyter](https://almond.sh/)
- [VS Code Jupyter extension](https://marketplace.visualstudio.com/items?itemName=ms-toolsai.jupyter)
- [Sample Notebooks](./build/notebooks/)
- [Atlas Docs](../../README.md)

## 14. Support

- **Logs**: `docker logs ${PROJECT_NAME}-jupyterhub`
- **Issues**: [GitHub Issues](https://github.com/thekaveh/atlas/issues)
- **Docs**: [Full Documentation](../../README.md)

## 15. Dependencies & Integrations

> Auto-generated section — the **Current** subsections are derived from `services/jupyterhub/service.yml`'s `data_flow.calls` field (and inverse passes). Re-run `python -m bootstrapper.docs.regen jupyterhub` after manifest changes.

### 15.1 Current — Upstream (this service calls)

| Service | Category |
|---|---|
| ray | infra |
| iceberg-rest | data |
| minio | data |
| neo4j | data |
| redpanda | data |
| spark | data |
| supabase | data |
| weaviate | data |
| litellm | llm |
| comfyui | media |
| searxng | media |
| hermes | agents |
| n8n | agents |
| backend | apps |
| label-studio | apps |
| mlflow | apps |

### 15.2 Current — Downstream (services that call this)

| Service | Category |
|---|---|
| kong | infra |

### 15.3 Architecture diagram

![jupyterhub architecture](./architecture.svg)

[Open the interactive HTML diagram](./architecture.html) for a full-screen view.

### 15.4 Future — Missing pair integrations

- **jupyterhub ↔ backend** — *Why:* the FastAPI backend already aggregates LiteLLM, Weaviate, Neo4j, ComfyUI, and Hermes so notebooks should reuse it instead of hand-rolling per-upstream clients. *Mechanism:* adaptive env `BACKEND_BASE_URL=http://backend:8000` consumed via `httpx` against `/v1/...` routes. *Effort:* small. *Confidence:* high.
- **jupyterhub ↔ hermes** — *Why:* researchers want to drive the tool-using agent runtime from notebooks (chain prompts, inspect intermediate tool calls) without going through Open WebUI. *Mechanism:* `HERMES_AGENT_MODEL=hermes-agent` env hint plus a sample notebook calling the existing `OPENAI_API_BASE` LiteLLM alias. *Effort:* small. *Confidence:* high.
- **jupyterhub ↔ local-deep-researcher** — *Why:* long LangGraph deep-research runs should be launchable from a notebook and streamable into a dataframe. *Mechanism:* `DEEP_RESEARCHER_BASE_URL=http://local-deep-researcher:2024` plus an SSE client snippet against LangGraph's `/runs/stream`. *Effort:* medium. *Confidence:* medium.
- **jupyterhub ↔ openclaw** — *Why:* unattended notebook jobs (training, sweeps, embeddings) should ping Slack/Discord when they finish. *Mechanism:* inject `OPENCLAW_WEBHOOK_URL=http://openclaw-gateway:<port>/webhook/notify` and post JSON from a util helper. *Effort:* small. *Confidence:* medium.

### 15.5 Future — Candidate new services

- **MLflow** ([details](../../docs/research/candidates/mlflow.md)) — *Headline:* self-hosted experiment-tracking, run-history, and model-registry server backed by Supabase Postgres + MinIO artifacts. *Wires into:* jupyterhub, backend, supabase, minio, n8n.
- **Label Studio** ([details](../../docs/research/candidates/label-studio.md)) — *Headline:* multi-user annotation studio for text, image, audio, and document labeling that produces supervised datasets for downstream ingestion. *Wires into:* jupyterhub, backend, weaviate, minio, supabase.

### 15.6 Future — Unused features in this service

- **Real multi-user JupyterHub (DockerSpawner + Authenticator)** — *Why pursue:* today the container is single-user `jupyter/datascience-notebook` despite the service name, so a proper Hub with `DockerSpawner` and `NativeAuthenticator`/OAuth would let multiple humans share the stack. *Effort:* large.
- **Jupyter AI extension wired to LiteLLM** — *Why pursue:* `jupyter-ai` accepts any OpenAI-compatible base URL, so pointing it at `LITELLM_BASE_URL` exposes every gateway model as a first-class `%ai` magic. *Effort:* small.
- **GPU enablement for the notebook container** — *Why pursue:* the image already ships PyTorch + PyG but the manifest exposes no `container-gpu` source, so heavy training falls back to CPU even when the host has a GPU. *Effort:* medium.
- **jupyter-server-proxy for ComfyUI/n8n** — *Why pursue:* the proxy is already in `requirements.txt` but unused; mounting ComfyUI and n8n behind `/proxy/<service>/` would embed those UIs in iframes without leaving the lab. *Effort:* small.
- **Persistent kernel state via ipyparallel** — *Why pursue:* long-running RAG/agent loops lose state on kernel restart; an `ipyparallel` cluster (workers as sidecars) would survive restarts. *Effort:* medium.

## 16. Troubleshooting

### 16.1 Cannot Access JupyterHub

**Check if running:**
```bash
docker ps | grep jupyterhub
```

**View logs:**
```bash
docker logs ${PROJECT_NAME}-jupyterhub
```

### 16.2 Token Not Working

**Get current token:**
```bash
docker logs ${PROJECT_NAME}-jupyterhub | grep "token="
```

**Set permanent token:**
```bash
# In .env
JUPYTERHUB_TOKEN=my-secret-token
```

### 16.3 Port Already in Use

```bash
# In .env
JUPYTERHUB_PORT=64048  # Use different port
```

### 16.4 Out of Memory

Increase Docker memory:
- Docker Desktop → Settings → Resources → Memory
- Recommended: 8GB+ for data science workloads
