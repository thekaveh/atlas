# JupyterHub - Atlas

Interactive data science IDE with pre-configured access to all Atlas services.

## Overview

JupyterHub provides a Jupyter Lab environment with pre-installed libraries for:
- LLM integration via the always-on LiteLLM gateway (OpenAI SDK, LangChain, LlamaIndex pointed at `LITELLM_BASE_URL`)
- Vector databases (Weaviate)
- Graph databases (Neo4j)
- Relational databases (PostgreSQL via Supabase)
- Lakehouse storage and catalogs (MinIO, Iceberg REST, DuckDB)
- Financial research (OpenBB, CCXT, paper portfolios; no live trading)
- Image generation (ComfyUI)
- Workflow automation (n8n)

## Quick Start

### Access JupyterHub

```bash
# Start the stack (JupyterHub enabled by default)
./start.sh

# Access at: http://localhost:63081
```

### Disable JupyterHub

```bash
./start.sh --jupyterhub-source disabled
```

## Configuration

### Environment Variables

Set in `.env` file:

```bash
JUPYTERHUB_SOURCE=container     # Options: container, disabled
JUPYTERHUB_IMAGE=quay.io/jupyter/datascience-notebook:python-3.11.10
JUPYTERHUB_PORT=63081
JUPYTERHUB_TOKEN=               # Optional: authentication token
```

### Authentication

- If `JUPYTERHUB_TOKEN` is not set, a token will be auto-generated on startup
- Check Docker logs to see the token: `docker logs ${PROJECT_NAME}-jupyterhub`
- Set a permanent token in `.env` to avoid regeneration

## Pre-installed Packages

### AI/ML Libraries
- `openai` - OpenAI SDK (used against the LiteLLM gateway via `OPENAI_API_BASE` / `OPENAI_API_KEY`)
- `langchain` / `langchain-openai` - LLM application framework (use `ChatOpenAI` / `OpenAIEmbeddings` against the same env vars)
- `llama-index` - Data framework for LLM applications
- `transformers` - Hugging Face transformers
- `sentence-transformers` - Sentence embeddings

### Database Clients
- `weaviate-client` - Weaviate vector database
- `neo4j` - Neo4j graph database
- `psycopg2-binary` - PostgreSQL
- `redis` - Redis client
- `supabase` - Supabase client
- `sqlalchemy` - SQL toolkit

### Data Science
- `pandas` - Data manipulation
- `numpy` - Numerical computing
- `boto3` / `s3fs` - S3-compatible MinIO access with scoped Jupyter credentials
- `pyiceberg` / `pyarrow` - Iceberg REST catalog and Arrow table access
- `duckdb` - In-process SQL over Arrow, Parquet, and local dataframes
- `openbb` / `openbb-yfinance` - Read-only financial data research packages for notebooks
- `ccxt` - Unified public exchange-data client; private/trading methods are blocked by Atlas helpers
- `matplotlib` - Plotting
- `seaborn` - Statistical visualization
- `plotly` - Interactive visualizations
- `networkx` - Network analysis

## Sample Notebooks

| Notebook | Description |
|----------|-------------|
| `00_environment_check.ipynb` | Verify all service connections |
| `01_litellm_basics.ipynb` | LLM integration via the LiteLLM gateway (Ollama upstream) |
| `02_langchain_rag.ipynb` | RAG pipeline with Weaviate |
| `03_neo4j_graphs.ipynb` | Knowledge graph queries |
| `04_supabase_data.ipynb` | Database and storage operations |
| `05_comfyui_images.ipynb` | Image generation workflows |
| `06_n8n_workflows.ipynb` | Workflow automation |
| `07_ray_cluster.ipynb` | Distributed compute on the Ray cluster |
| `08_scala_basics.ipynb` | Scala basics on the scala3 kernel |
| `09_spark_connect.ipynb` | Spark Connect (gRPC) from Python |
| `10_spark_scala.ipynb` | Spark with the Scala kernel |
| `11_financial_research_kit.ipynb` | Read-only OpenBB/CCXT research and paper portfolios |
| `12_iceberg_advanced_sql.ipynb` | Advanced Iceberg SQL smoke through Spark Connect |

## Service Integration

### LLM access (LiteLLM gateway)

Every notebook reaches LLMs through LiteLLM via the OpenAI-compatible API. `OPENAI_API_BASE` and `OPENAI_API_KEY` are pre-set inside the container from `LITELLM_BASE_URL` and `LITELLM_API_KEY` (which equals `LITELLM_MASTER_KEY`).

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url=os.getenv("OPENAI_API_BASE"),  # e.g. http://litellm:4000/v1
    api_key=os.getenv("OPENAI_API_KEY"),    # equals $LITELLM_API_KEY
)

response = client.chat.completions.create(
    model="ollama/qwen3.6:latest",  # also: gpt-4o, claude-sonnet-4-6, openrouter/auto, ...
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

LangChain users:

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="ollama/qwen3.6:latest")  # picks up OPENAI_API_BASE / OPENAI_API_KEY
print(llm.invoke("Hello!").content)
```

### Weaviate (Vector DB)

```python
import os
import weaviate

client = weaviate.connect_to_custom(
    http_host=os.getenv("WEAVIATE_URL").replace("http://", "").split(":")[0],
    http_port=8080,
    http_secure=False
)
print(client.is_ready())
```

### Neo4j (Graph DB)

```python
import os
from neo4j import GraphDatabase

driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD"))
)

with driver.session() as session:
    result = session.run("MATCH (n) RETURN count(n) as count")
    print(result.single()["count"])
```

### PostgreSQL (Supabase)

```python
import os
from sqlalchemy import create_engine, text

engine = create_engine(os.getenv("DATABASE_URL"))
with engine.connect() as conn:
    result = conn.execute(text("SELECT version()"))
    print(result.fetchone())
```

### Lakehouse (MinIO + Iceberg REST + DuckDB)

JupyterHub includes `boto3`, `s3fs`, `pyiceberg[s3fs]`, `pyarrow`, and `duckdb`.
The container receives scoped MinIO credentials (`MINIO_JUPYTER_*` in `.env`,
exported as `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`) plus the Iceberg REST
catalog URL. The PyIceberg catalog smoke requires `ICEBERG_REST_SOURCE=container`;
MinIO SDK calls only require MinIO.

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

Validation commands:

```bash
docker exec ${PROJECT_NAME}-jupyterhub python - <<'PY'
import boto3, s3fs, pyarrow, duckdb
from pyiceberg.catalog import load_catalog
print("lakehouse imports ok")
PY

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

## Data Persistence

### Work Directory
- Location: `/home/jovyan/work`
- Persisted in: `atlas-jupyterhub-data` Docker volume
- All notebooks and files in this directory persist across container restarts

### Sample Notebooks
- Location: `/home/jovyan/notebooks`
- Read-only mount from host: `./services/jupyterhub/build/notebooks`
- Copy to `work/` directory to modify

## Custom Package Installation

### Temporary (current session only)

```bash
!pip install package-name
```

### Permanent

1. Add package to `services/jupyterhub/build/requirements.txt`
2. Rebuild the container: `docker compose build jupyterhub`
3. Restart: `./stop.sh && ./start.sh`

## Troubleshooting

### Cannot connect to services

Check environment variables in a notebook:

```python
import os
print("LiteLLM:", os.getenv("LITELLM_BASE_URL"), "/", os.getenv("OPENAI_API_BASE"))
print("Weaviate:", os.getenv("WEAVIATE_URL"))
print("Neo4j:", os.getenv("NEO4J_URI"))
print("MinIO:", os.getenv("AWS_ENDPOINT_URL_S3"))
print("Iceberg REST:", os.getenv("ICEBERG_REST_URI"))
```

### Token not working

1. Check logs: `docker logs ${PROJECT_NAME}-jupyterhub`
2. Find the token URL in logs
3. Or set `JUPYTERHUB_TOKEN` in `.env` and restart

### Port already in use

Change port in `.env`:

```bash
JUPYTERHUB_PORT=64081  # Use different port
```

### Out of memory

Increase Docker memory allocation:
- Docker Desktop → Settings → Resources → Memory
- Recommended: 8GB+ for data science workloads

## Advanced Configuration

### Multi-user Setup

For multi-user JupyterHub with authentication, create `jupyterhub_config.py`:

```python
# See: https://jupyterhub.readthedocs.io/
c.JupyterHub.authenticator_class = 'firstuseauthenticator.FirstUseAuthenticator'
```

### Custom Jupyter Extensions

Add to Dockerfile:

```dockerfile
RUN jupyter labextension install @jupyter-widgets/jupyterlab-manager
```

### GPU-aware workflows

JupyterHub itself is configured through `.env` and the stack startup flow. Prefer enabling GPU-backed upstream services through SOURCE variables such as `LLM_PROVIDER_SOURCE=ollama-container-gpu`, `COMFYUI_SOURCE=container-gpu`, or `MULTI2VEC_CLIP_SOURCE=container-gpu`.

Avoid direct `docker-compose.yml` edits for normal operation; local compose edits are unsupported experiments and can be invalidated by future stack changes.

## Resources

- [Jupyter Lab Documentation](https://jupyterlab.readthedocs.io/)
- [JupyterHub Documentation](https://jupyterhub.readthedocs.io/)
- [Atlas Docs](../../../docs/README.md)

## Support

- GitHub Issues: [Report bugs](https://github.com/thekaveh/atlas/issues)
- Documentation: [Full docs](../../../docs/README.md)
- Logs: `docker logs ${PROJECT_NAME}-jupyterhub`
