# Airflow + Spark + Zeppelin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Apache Spark 4.1.2 (standalone cluster), Apache Zeppelin 0.12.0 (Spark-first notebook), and Apache Airflow 3.2.2 (LocalExecutor with AI/ML SDK) to the atlas stack as a single coordinated PR, with comprehensive cross-stack integration covering MinIO, Supabase, LiteLLM, Hermes, and the existing service lineup.

**Architecture:** Three new `services/<svc>/{service.yml, compose.yml}` manifests follow the existing fragment pattern. Spark + Zeppelin + Airflow are placed in `data` / `apps` / `agents` categories respectively (no `topology.py` surgery). All default `disabled`. Spark has a wizard-prompted worker-count slider mirroring Ray's `SecondaryNumberInput` pattern. Airflow's init container conditionally seeds Connection objects for every enabled sibling service (Spark, MinIO, LiteLLM, Postgres, Weaviate, Neo4j, Redis) so DAGs reference them by canonical names. Zeppelin hard-fails source selection when Spark is disabled.

**Tech Stack:**
- Spark: `bitnami/spark:4.1.2`, standalone mode, Spark Connect gRPC enabled
- Zeppelin: `apache/zeppelin:0.12.0`, Spark + JDBC + Shell + Markdown interpreters
- Airflow: `apache/airflow:3.2.2` + provider bundle (apache-spark, amazon, postgres, redis, weaviate, neo4j, openai, langchain)
- Storage: Supabase Postgres (Airflow metadata DB), MinIO S3A (Spark history + user data)
- Wizard: Existing textual TUI with `SecondaryNumberInput` widget
- Tests: pytest with the existing `test_fragment_*` + `test_source_permutations` + `test_kong_alias_routes` framework

**Spec reference:** `docs/superpowers/specs/2026-06-04-airflow-spark-zeppelin-design.md`

---

## 1. Phase 0 — Setup

### 1.1. Task 0.1: Create the implementation branch

**Files:**
- Branch: `feat/airflow-spark-zeppelin`

- [ ] **Step 1: Confirm starting state**

```bash
cd /Users/kaveh/repos/atlas
git fetch --prune origin
git checkout main
git pull --ff-only origin main
git status
```

Expected: clean working tree on `main` at the latest origin/main commit.

- [ ] **Step 2: Create the feature branch**

```bash
git checkout -b feat/airflow-spark-zeppelin
git branch --show-current
```

Expected: `feat/airflow-spark-zeppelin`.

- [ ] **Step 3: Push branch to set upstream**

```bash
git push -u origin feat/airflow-spark-zeppelin
```

Expected: new branch tracking `origin/feat/airflow-spark-zeppelin`.

### 1.2. Task 0.2: Capture baseline test count

**Files:**
- Read: `bootstrapper/tests/`

- [ ] **Step 1: Run the full suite and record the pass count**

```bash
cd /Users/kaveh/repos/atlas/bootstrapper
./.venv/bin/python -m pytest --tb=no -q 2>&1 | tail -3
```

Expected: `590 passed` (current baseline as of 2026-06-04). Note this number — every Phase X validation should equal `baseline + Phase X new tests`.

### 1.3. Task 0.3: Sanity check that the spec is on the branch

**Files:**
- Read: `docs/superpowers/specs/2026-06-04-airflow-spark-zeppelin-design.md`

- [ ] **Step 1: Confirm the spec file is present**

```bash
ls -la docs/superpowers/specs/2026-06-04-airflow-spark-zeppelin-design.md
```

Expected: file exists, ~5800 words. If missing, cherry-pick the spec commit from `feat/jupyterhub-scala-vscode-followup` first.

---

## 2. Phase 1 — Spark cluster

### 2.1. Task 1.1: Create `services/spark/service.yml`

**Files:**
- Create: `services/spark/service.yml`

- [ ] **Step 1: Write the failing test (test loads + validates the manifest)**

Edit `bootstrapper/tests/test_manifests.py` (append new test):

```python
def test_spark_manifest_loads():
    from services.manifests import load_manifests
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent
    manifests = load_manifests(repo_root / "services")
    spark = next((m for m in manifests if m.name == "spark"), None)
    assert spark is not None, "spark manifest not found"
    assert spark.category == "data"
    assert "spark-master" in {c for c in spark.containers}
    assert "spark-worker" in {c for c in spark.containers}
    assert "spark-history" in {c for c in spark.containers}
    assert "minio" in spark.depends_on.required
    assert spark.sources.default == "disabled"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/kaveh/repos/atlas/bootstrapper
./.venv/bin/python -m pytest tests/test_manifests.py::test_spark_manifest_loads -v
```

Expected: FAIL with "spark manifest not found".

- [ ] **Step 3: Create the Spark manifest**

Create `services/spark/service.yml`:

```yaml
# services/spark/service.yml — Apache Spark standalone cluster
# master + N workers + history server. Distinct from Ray.
name: spark
label: "Apache Spark (standalone cluster)"
category: data
docs: services/spark/README.md

containers:
  - spark-master
  - spark-worker
  - spark-history
  - spark-init

images:
  - var: SPARK_IMAGE
    default: "bitnami/spark:4.1.2"
    container: spark-master

sources:
  var: SPARK_SOURCE
  default: disabled
  options:
    - id: container
      label: "Container (standalone cluster)"
    - id: disabled
      label: "Disabled"

env:
  - name: SPARK_SOURCE
    default: disabled
  - name: SPARK_IMAGE
    default: "bitnami/spark:4.1.2"
  - name: SPARK_MASTER_UI_PORT
    # default removed — computed by services/topology.py slot allocator
    description: "Host port for the Spark Master Web UI (in-container 8080)."
  - name: SPARK_HISTORY_PORT
    # default removed — computed by services/topology.py slot allocator
    description: "Host port for the Spark History Server (in-container 18080)."
  - name: SPARK_WORKER_COUNT
    default: "2"
    description: "Number of spark-worker replicas alongside the master. 1-8."
  - name: SPARK_MASTER_SCALE
    auto_managed: true
  - name: SPARK_WORKER_SCALE
    auto_managed: true
    description: "Resolved by the bootstrapper from SPARK_WORKER_COUNT when source=container; 0 otherwise."
  - name: SPARK_HISTORY_SCALE
    auto_managed: true
  - name: SPARK_INIT_SCALE
    auto_managed: true

depends_on:
  required:
    - minio
  optional:
    - supabase
    - prometheus

rows:
  - display_name: "Apache Spark"
    source_var: SPARK_SOURCE
    port_var: SPARK_MASTER_UI_PORT
    scale_var: SPARK_MASTER_SCALE
    alias: spark.localhost
    description: "Standalone Spark cluster for batch / SQL / DataFrame workloads."

exports: []

runtime_sc:
  spark-master:
    container:
      scale: 1
      environment:
        SPARK_MODE: master
        SPARK_MASTER_HOST: spark-master
        SPARK_RPC_AUTHENTICATION_ENABLED: "no"
        SPARK_RPC_ENCRYPTION_ENABLED: "no"
        SPARK_LOCAL_SSL_ENABLED: "no"
        SPARK_NO_DAEMONIZE: "true"
        SPARK_DAEMON_JAVA_OPTS: "-Dspark.connect.grpc.binding.port=15002"
      deploy: {}
      extra_hosts: []
    disabled:
      scale: 0
      environment: {}
      deploy: {}
      extra_hosts: []
  spark-worker:
    container:
      scale: 2   # overridden by SPARK_WORKER_COUNT via auto_managed SPARK_WORKER_SCALE
      environment:
        SPARK_MODE: worker
        SPARK_MASTER_URL: "spark://spark-master:7077"
        SPARK_RPC_AUTHENTICATION_ENABLED: "no"
        SPARK_RPC_ENCRYPTION_ENABLED: "no"
        SPARK_LOCAL_SSL_ENABLED: "no"
      deploy: {}
      extra_hosts: []
    disabled:
      scale: 0
      environment: {}
      deploy: {}
      extra_hosts: []
  spark-history:
    container:
      scale: 1
      environment:
        SPARK_MODE: history
        SPARK_HISTORY_OPTS: >-
          -Dspark.history.fs.logDirectory=s3a://spark-history/
          -Dspark.hadoop.fs.s3a.endpoint=http://minio:9000
          -Dspark.hadoop.fs.s3a.access.key=${MINIO_ROOT_USER}
          -Dspark.hadoop.fs.s3a.secret.key=${MINIO_ROOT_PASSWORD}
          -Dspark.hadoop.fs.s3a.path.style.access=true
      deploy: {}
      extra_hosts: []
    disabled:
      scale: 0
      environment: {}
      deploy: {}
      extra_hosts: []

data_flow:
  calls:
    - supabase   # JDBC reads (user-driven)
    - minio      # s3a:// history + user data
    - kong       # health endpoints exposed via alias
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/bin/python -m pytest tests/test_manifests.py::test_spark_manifest_loads -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/kaveh/repos/atlas
git add services/spark/service.yml bootstrapper/tests/test_manifests.py
git commit -m "feat(spark): add service.yml manifest

Apache Spark standalone cluster manifest in `data` category.
4 containers (master + worker + history + init). Source variants:
container | disabled. SPARK_WORKER_COUNT default 2 (1-8). Depends on
minio (history server reads s3a://spark-history/). Optional deps:
supabase (JDBC), prometheus (opt-in metrics).
"
```

### 2.2. Task 1.2: Create `services/spark/compose.yml`

**Files:**
- Create: `services/spark/compose.yml`

- [ ] **Step 1: Write the failing test**

Append to `bootstrapper/tests/test_fragment_bind_sources.py` is not needed (parametrized test auto-picks up new fragment). Instead append to `bootstrapper/tests/test_fragment_equivalence.py` is also not needed — the baseline regen task handles that. The functional test here is direct compose render:

Create test in `bootstrapper/tests/test_spark_compose.py`:

```python
"""Smoke test that services/spark/compose.yml renders cleanly via include:."""
from pathlib import Path
import subprocess

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

def test_spark_fragment_renders():
    result = subprocess.run(
        [
            "docker", "compose",
            "--env-file", str(REPO_ROOT / ".env.example"),
            "-p", "atlas",
            "-f", str(REPO_ROOT / "services" / "spark" / "compose.yml"),
            "config", "-q",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, f"compose render failed:\n{result.stderr}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/bin/python -m pytest tests/test_spark_compose.py -v
```

Expected: FAIL (compose.yml missing).

- [ ] **Step 3: Create the Spark compose fragment**

Create `services/spark/compose.yml`:

```yaml
# services/spark/compose.yml — Spark master + workers + history + init
services:
  spark-master:
    image: ${SPARK_IMAGE:-bitnami/spark:4.1.2}
    container_name: ${PROJECT_NAME}-spark-master
    restart: unless-stopped
    deploy:
      replicas: ${SPARK_MASTER_SCALE:-0}
    ports:
      - "${SPARK_MASTER_UI_PORT}:8080"
    environment:
      SPARK_MODE: master
      SPARK_MASTER_HOST: spark-master
      SPARK_RPC_AUTHENTICATION_ENABLED: "no"
      SPARK_RPC_ENCRYPTION_ENABLED: "no"
      SPARK_LOCAL_SSL_ENABLED: "no"
      SPARK_NO_DAEMONIZE: "true"
      SPARK_DAEMON_JAVA_OPTS: -Dspark.connect.grpc.binding.port=15002
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8080/"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 30s
    networks:
      - backend-network

  spark-worker:
    image: ${SPARK_IMAGE:-bitnami/spark:4.1.2}
    container_name: ${PROJECT_NAME}-spark-worker
    restart: unless-stopped
    deploy:
      replicas: ${SPARK_WORKER_SCALE:-0}
    environment:
      SPARK_MODE: worker
      SPARK_MASTER_URL: spark://spark-master:7077
      SPARK_RPC_AUTHENTICATION_ENABLED: "no"
      SPARK_RPC_ENCRYPTION_ENABLED: "no"
      SPARK_LOCAL_SSL_ENABLED: "no"
    depends_on:
      spark-master:
        condition: service_healthy
    networks:
      - backend-network

  spark-history:
    image: ${SPARK_IMAGE:-bitnami/spark:4.1.2}
    container_name: ${PROJECT_NAME}-spark-history
    restart: unless-stopped
    deploy:
      replicas: ${SPARK_HISTORY_SCALE:-0}
    ports:
      - "${SPARK_HISTORY_PORT}:18080"
    environment:
      SPARK_MODE: history
      # NOTE: dual-write per project_runtime_sc_vs_compose_env_dual_write.md.
      # Same SPARK_HISTORY_OPTS must appear in service.yml::runtime_sc.
      SPARK_HISTORY_OPTS: >-
        -Dspark.history.fs.logDirectory=s3a://spark-history/
        -Dspark.hadoop.fs.s3a.endpoint=http://minio:9000
        -Dspark.hadoop.fs.s3a.access.key=${MINIO_ROOT_USER}
        -Dspark.hadoop.fs.s3a.secret.key=${MINIO_ROOT_PASSWORD}
        -Dspark.hadoop.fs.s3a.path.style.access=true
    depends_on:
      spark-init:
        condition: service_completed_successfully
    networks:
      - backend-network

  # NOTE: init container uses vanilla alpine:latest per
  # project_init_container_pattern.md. Inline apk add. Idempotent.
  spark-init:
    image: alpine:latest
    container_name: ${PROJECT_NAME}-spark-init
    deploy:
      replicas: ${SPARK_INIT_SCALE:-0}
    depends_on:
      minio-init:
        condition: service_completed_successfully
    command:
      - sh
      - -c
      - |
        apk add --no-cache mc >/dev/null
        mc alias set minio http://minio:9000 ${MINIO_ROOT_USER} ${MINIO_ROOT_PASSWORD}
        mc mb --ignore-existing minio/spark-history
        echo "spark-init: spark-history bucket ready"
    networks:
      - backend-network
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/bin/python -m pytest tests/test_spark_compose.py -v
```

Expected: PASS.

- [ ] **Step 5: Add Spark to the top-level docker-compose.yml include block**

Edit `docker-compose.yml` (append to the existing `include:` list, after the last current entry):

```yaml
  # Compute additions (2026-06-04)
  - services/spark/compose.yml
```

- [ ] **Step 6: Verify the merged stack still renders**

```bash
cd /Users/kaveh/repos/atlas
docker compose -f docker-compose.yml --env-file .env.example config -q
echo "exit=$?"
```

Expected: `exit=0` (Spark service variables expected to be missing but the lazy expansion is OK with defaults — render-time errors only flag undefined env that has no fallback).

- [ ] **Step 7: Commit**

```bash
git add services/spark/compose.yml docker-compose.yml bootstrapper/tests/test_spark_compose.py
git commit -m "feat(spark): add compose fragment + wire into top-level include

4 services declared: spark-master (8080 UI + 7077 RPC + 15002 Spark
Connect), spark-worker (replicas via SPARK_WORKER_SCALE), spark-history
(18080 UI; reads s3a://spark-history/), spark-init (vanilla alpine
that mc-creates the MinIO bucket on first start).

Dual-write per project_runtime_sc_vs_compose_env_dual_write.md:
SPARK_HISTORY_OPTS lives in both compose.yml environment block AND
service.yml runtime_sc.environment.
"
```

### 2.3. Task 1.3: Run the parametrized fragment guards on the new fragment

**Files:**
- Read: `bootstrapper/tests/test_fragment_bind_sources.py`

- [ ] **Step 1: Run the bind-source guards to confirm Spark passes**

```bash
cd /Users/kaveh/repos/atlas/bootstrapper
./.venv/bin/python -m pytest tests/test_fragment_bind_sources.py -v 2>&1 | grep spark
```

Expected: both `test_fragment_bind_sources_dont_self_double[spark]` and `test_fragment_bind_sources_stay_inside_repo_root[spark]` PASS.

- [ ] **Step 2: Run the full bind-source suite (now 25 fragments)**

```bash
./.venv/bin/python -m pytest tests/test_fragment_bind_sources.py -v 2>&1 | tail -3
```

Expected: 51 passed (was 49; +2 for the new spark fragment in both parametrized tests).

### 2.4. Task 1.4: Regenerate `.env.example` to include SPARK_* vars

**Files:**
- Modify: `.env.example` (auto-generated)

- [ ] **Step 1: Regenerate from manifests**

```bash
cd /Users/kaveh/repos/atlas/bootstrapper
./.venv/bin/python -m services.env_assembler
```

Expected: stderr says "Wrote /Users/kaveh/repos/atlas/.env.example (~830 lines)" (was 822 — adds ~8 lines for SPARK_*).

- [ ] **Step 2: Verify SPARK_* entries landed**

```bash
cd /Users/kaveh/repos/atlas
grep -E '^SPARK_' .env.example
```

Expected:
```
SPARK_SOURCE=disabled
SPARK_IMAGE=bitnami/spark:4.1.2
SPARK_MASTER_UI_PORT=63023
SPARK_HISTORY_PORT=63024
SPARK_WORKER_COUNT=2
SPARK_MASTER_SCALE=
SPARK_WORKER_SCALE=
SPARK_HISTORY_SCALE=
SPARK_INIT_SCALE=
```

(Port values may differ if existing data-band services have shifted; what matters is the var presence + no collisions.)

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "feat(spark): regen .env.example with SPARK_* vars

Slot allocator placed SPARK_MASTER_UI_PORT and SPARK_HISTORY_PORT in
the data band (63023 / 63024). Auto-managed SPARK_*_SCALE vars
populated by the bootstrapper at start time."
```

### 2.5. Task 1.5: Add Spark to `bootstrapper/services/service_config.py`

**Files:**
- Modify: `bootstrapper/services/service_config.py`

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_spark_config.py`:

```python
"""Unit test for ServiceConfig._generate_spark_config()."""
from unittest.mock import patch, MagicMock
from services.service_config import ServiceConfig


def _build_config(source_value: str, worker_count: str = "2", env_overrides: dict | None = None):
    sc = ServiceConfig(config_parser=MagicMock())
    sc.localhost_host = "host.docker.internal"
    sc.service_sources = {"SPARK_SOURCE": source_value}
    sc.yaml_config = {
        "source_configurable": {
            "spark": {
                source_value: {"environment": {}, "scale": 1, "deploy": {}, "extra_hosts": []}
            }
        }
    }
    env = {"SPARK_WORKER_COUNT": worker_count}
    if env_overrides:
        env.update(env_overrides)
    sc.config_parser.parse_env_file.return_value = env
    return sc._generate_spark_config()


def test_spark_disabled_sets_all_scales_to_zero():
    env_vars = _build_config("disabled")
    assert env_vars["SPARK_MASTER_SCALE"] == "0"
    assert env_vars["SPARK_WORKER_SCALE"] == "0"
    assert env_vars["SPARK_HISTORY_SCALE"] == "0"
    assert env_vars["SPARK_INIT_SCALE"] == "0"


def test_spark_container_with_default_worker_count():
    env_vars = _build_config("container", worker_count="2")
    assert env_vars["SPARK_MASTER_SCALE"] == "1"
    assert env_vars["SPARK_WORKER_SCALE"] == "2"
    assert env_vars["SPARK_HISTORY_SCALE"] == "1"
    assert env_vars["SPARK_INIT_SCALE"] == "1"


def test_spark_container_respects_worker_count_override():
    env_vars = _build_config("container", worker_count="5")
    assert env_vars["SPARK_WORKER_SCALE"] == "5"


def test_spark_container_clamps_worker_count():
    env_vars_low = _build_config("container", worker_count="0")
    assert env_vars_low["SPARK_WORKER_SCALE"] == "1", "below-1 clamped to 1"
    env_vars_high = _build_config("container", worker_count="42")
    assert env_vars_high["SPARK_WORKER_SCALE"] == "8", "above-8 clamped to 8"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/bin/python -m pytest tests/test_spark_config.py -v
```

Expected: FAIL with `AttributeError: 'ServiceConfig' object has no attribute '_generate_spark_config'`.

- [ ] **Step 3: Add the `_generate_spark_config` method**

Edit `bootstrapper/services/service_config.py` (add the new method; pattern mirrors `_generate_ray_config`):

```python
def _generate_spark_config(self) -> dict:
    """Generate SPARK_*_SCALE env vars based on SPARK_SOURCE + SPARK_WORKER_COUNT.

    Spark is a 4-container family (master + worker + history + init). When
    the source is `container`, all four scale up (worker count is clamped
    to 1-8 per the wizard contract). When `disabled`, all four scale=0.
    """
    source_value = self.service_sources.get("SPARK_SOURCE", "disabled")
    env_vars: dict[str, str] = {}

    if source_value == "disabled":
        env_vars["SPARK_MASTER_SCALE"] = "0"
        env_vars["SPARK_WORKER_SCALE"] = "0"
        env_vars["SPARK_HISTORY_SCALE"] = "0"
        env_vars["SPARK_INIT_SCALE"] = "0"
        return env_vars

    current_env = self.config_parser.parse_env_file()
    raw_count = current_env.get("SPARK_WORKER_COUNT", "2")
    try:
        worker_count = max(1, min(8, int(raw_count)))
    except (TypeError, ValueError):
        worker_count = 2

    env_vars["SPARK_MASTER_SCALE"] = "1"
    env_vars["SPARK_WORKER_SCALE"] = str(worker_count)
    env_vars["SPARK_HISTORY_SCALE"] = "1"
    env_vars["SPARK_INIT_SCALE"] = "1"
    return env_vars
```

Also add the call in `generate_service_environment()` (append after the existing service config blocks):

```python
spark_config = self._generate_spark_config()
env_vars.update(spark_config)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/bin/python -m pytest tests/test_spark_config.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/services/service_config.py bootstrapper/tests/test_spark_config.py
git commit -m "feat(spark): bootstrapper resolves SPARK_*_SCALE from SPARK_SOURCE + worker count

_generate_spark_config() reads SPARK_WORKER_COUNT (1-8 clamped, default
2) and emits scales for master (1), workers (count), history (1),
init (1). Disabled source -> all 0. 4 unit tests cover the four
behaviors.
"
```

### 2.6. Task 1.6: Add Spark to `bootstrapper/utils/source_override_manager.py`

**Files:**
- Modify: `bootstrapper/utils/source_override_manager.py`

- [ ] **Step 1: Locate the `source_mapping` dict and add the Spark entry**

```bash
grep -n 'source_mapping' bootstrapper/utils/source_override_manager.py
```

Edit `source_mapping` (alphabetical insertion):

```python
"spark_source": "SPARK_SOURCE",
```

- [ ] **Step 2: Commit**

```bash
git add bootstrapper/utils/source_override_manager.py
git commit -m "feat(spark): wire SPARK_SOURCE into source_override_manager"
```

### 2.7. Task 1.7: Add Spark to `bootstrapper/utils/kong_config_generator.py`

**Files:**
- Modify: `bootstrapper/utils/kong_config_generator.py`

- [ ] **Step 1: Write the failing test**

Edit `bootstrapper/tests/test_kong_alias_routes.py` to add a Spark-route assertion:

```python
def test_spark_master_route_exists_with_preserve_host(kong_config_with_all_sources):
    cfg = kong_config_with_all_sources(spark_source="container")
    spark_service = next((s for s in cfg["services"] if s["name"] == "spark-master-ui"), None)
    assert spark_service is not None, "spark-master-ui service missing"
    route = spark_service["routes"][0]
    assert "spark.localhost" in route["hosts"]
    assert route.get("preserve_host") is True, "preserve_host must be True for SPA Web UI"


def test_spark_history_route_exists_with_preserve_host(kong_config_with_all_sources):
    cfg = kong_config_with_all_sources(spark_source="container")
    svc = next((s for s in cfg["services"] if s["name"] == "spark-history-ui"), None)
    assert svc is not None
    assert "spark-history.localhost" in svc["routes"][0]["hosts"]
    assert svc["routes"][0].get("preserve_host") is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/bin/python -m pytest tests/test_kong_alias_routes.py::test_spark_master_route_exists_with_preserve_host -v
```

Expected: FAIL.

- [ ] **Step 3: Add Kong route generators**

Append to `bootstrapper/utils/kong_config_generator.py` (pattern mirrors `generate_grafana_service`):

```python
def generate_spark_master_service(self):
    source = self.get_env_value("SPARK_SOURCE")
    if source == "disabled":
        return None
    return {
        "name": "spark-master-ui",
        "url": "http://spark-master:8080",
        "routes": [{
            "name": "spark-master-ui-all",
            "strip_path": False,
            "hosts": ["spark.localhost"],
            "preserve_host": True,  # Spark Web UI SPA bakes hostnames in redirects
        }],
        "plugins": [{"name": "cors"}],
    }

def generate_spark_history_service(self):
    source = self.get_env_value("SPARK_SOURCE")
    if source == "disabled":
        return None
    return {
        "name": "spark-history-ui",
        "url": "http://spark-history:18080",
        "routes": [{
            "name": "spark-history-ui-all",
            "strip_path": False,
            "hosts": ["spark-history.localhost"],
            "preserve_host": True,
        }],
        "plugins": [{"name": "cors"}],
    }
```

Then call both in `get_all_services()`:

```python
for gen in (
    # ... existing entries ...
    self.generate_spark_master_service,
    self.generate_spark_history_service,
):
    svc = gen()
    if svc is not None:
        services.append(svc)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/bin/python -m pytest tests/test_kong_alias_routes.py -v 2>&1 | tail -5
```

Expected: 23 passed (21 baseline + 2 new).

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/utils/kong_config_generator.py bootstrapper/tests/test_kong_alias_routes.py
git commit -m "feat(spark): Kong routes for Spark Master UI + History UI

Both routes preserve_host=True per reference_kong_preserve_host: the
Spark Web UI emits redirects that bake the upstream hostname into
asset URLs. preserve_host=True forces Kong to forward the alias.
"
```

### 2.8. Task 1.8: Add Spark hostnames to `bootstrapper/utils/hosts_manager.py`

**Files:**
- Modify: `bootstrapper/utils/hosts_manager.py`

- [ ] **Step 1: Locate `GENAI_HOSTS` and add the Spark hostnames**

```bash
grep -n 'GENAI_HOSTS' bootstrapper/utils/hosts_manager.py
```

Add (alphabetical insertion):

```python
"spark.localhost",
"spark-history.localhost",
```

- [ ] **Step 2: Commit**

```bash
git add bootstrapper/utils/hosts_manager.py
git commit -m "feat(spark): register spark.localhost + spark-history.localhost in GENAI_HOSTS"
```

### 2.9. Task 1.9: Add Spark to `bootstrapper/start.py` (Click CLI flags)

**Files:**
- Modify: `bootstrapper/start.py`

- [ ] **Step 1: Add the two new Click options**

Find the existing `--ray-source` and `--ray-worker-count` decorators and mirror them for Spark, immediately below Ray:

```python
@click.option('--spark-source',
              type=click.Choice(['container', 'disabled']),
              default=None,
              help='Override SPARK_SOURCE.')
@click.option('--spark-workers', type=int, default=None,
              help='Override SPARK_WORKER_COUNT (1-8). Mirrors --ray-worker-count.')
```

- [ ] **Step 2: Add them to `main()` signature**

In the `main()` function signature (after `ray_worker_count`), add `spark_source, spark_workers`:

```python
def main(
    # ... existing parameters ...
    ray_source, ray_worker_count,
    spark_source, spark_workers,
    # ... rest ...
):
```

- [ ] **Step 3: Wire them into `source_args` and `user_model_selections`**

After the existing `if ray_worker_count is not None:` block:

```python
if spark_workers is not None:
    if not 1 <= spark_workers <= 8:
        raise click.UsageError("--spark-workers must be in 1-8")
    user_model_selections['SPARK_WORKER_COUNT'] = str(spark_workers)
```

And in the `source_args` dict assembly:

```python
'spark_source': spark_source,
```

- [ ] **Step 4: Commit**

```bash
git add bootstrapper/start.py
git commit -m "feat(spark): add --spark-source + --spark-workers Click flags

--spark-workers mirrors --ray-worker-count (1-8 clamped). Both flags
plumb into the same user_model_selections + source_args bag the wizard
uses."
```

### 2.10. Task 1.10: Add Spark wizard widget (`bootstrapper/ui/textual/integration.py`)

**Files:**
- Modify: `bootstrapper/ui/textual/integration.py`

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_spark_worker_count.py`:

```python
"""Wizard widget: Spark's source step carries a SecondaryNumberInput
for SPARK_WORKER_COUNT mirroring Ray's pattern."""
from pathlib import Path
import pytest

def _wizard_steps():
    from core.config_parser import ConfigParser
    from ui.textual.integration import _build_steps_and_rows
    from utils.hosts_manager import HostsManager
    repo_root = Path(__file__).resolve().parent.parent.parent
    cp = ConfigParser(str(repo_root))
    cp.parse_env_file()
    hm = HostsManager()
    steps, _rows, _info, _bp, _state, _cloud = _build_steps_and_rows(cp, hm)
    return steps


def test_spark_source_step_has_worker_count_secondary():
    steps = _wizard_steps()
    spark_step = next(
        (s for s in steps if s.service_name == "Apache Spark" and "source" in s.title.lower()),
        None,
    )
    assert spark_step is not None, (
        f"Spark source step missing. Steps: {[(s.service_name, s.title) for s in steps]}"
    )
    container_opt = next((o for o in spark_step.options if o.value == "container"), None)
    assert container_opt is not None, "container option missing"
    cfg = container_opt.secondary_number
    assert cfg is not None, "container option must carry SecondaryNumberInput"
    assert cfg.env_var == "SPARK_WORKER_COUNT"
    assert cfg.unit_suffix == "workers"
    assert cfg.number_min == 1
    assert cfg.number_max == 8
    assert str(cfg.default_value) == "2"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/bin/python -m pytest tests/test_spark_worker_count.py -v
```

Expected: FAIL (Spark step not found).

- [ ] **Step 3: Add the Spark widget code**

In `bootstrapper/ui/textual/integration.py`, find the existing Ray block (`if svc.key in ("ray", "ray-head") or svc.display_name == "Ray":`) and mirror it for Spark, immediately below:

```python
if svc.key == "spark" or svc.display_name == "Apache Spark":
    raw_default = (env_vars.get("SPARK_WORKER_COUNT") or "2").strip()
    try:
        spark_worker_default = max(1, min(8, int(raw_default)))
    except ValueError:
        spark_worker_default = 2
    spark_secondary = SecondaryNumberInput(
        env_var="SPARK_WORKER_COUNT",
        description=(
            "Number of spark-worker replicas alongside the master. 1-8."
        ),
        default_value=spark_worker_default,
        number_min=1,
        number_max=8,
        unit_suffix="workers",
    )
    # Attach to the `container` source option:
    for opt in source_step.options:
        if opt.value == "container":
            opt.secondary_number = spark_secondary
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/bin/python -m pytest tests/test_spark_worker_count.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/ui/textual/integration.py bootstrapper/tests/test_spark_worker_count.py
git commit -m "feat(spark): wizard SecondaryNumberInput for SPARK_WORKER_COUNT (1-8)

Mirrors Ray's worker-count widget exactly. Attached to the 'container'
source option. New test verifies the wiring."
```

### 2.11. Task 1.11: Add Spark display name + description to `bootstrapper/wizard/service_discovery.py`

**Files:**
- Modify: `bootstrapper/wizard/service_discovery.py`

- [ ] **Step 1: Add the entries**

```python
DISPLAY_NAME_OVERRIDES['spark'] = 'Apache Spark'
SERVICE_DESCRIPTIONS['spark'] = 'Distributed compute (batch/SQL/DataFrame).'
```

- [ ] **Step 2: Commit**

```bash
git add bootstrapper/wizard/service_discovery.py
git commit -m "feat(spark): wizard display name + description for Spark service"
```

### 2.12. Task 1.12: Regenerate the compose-baseline fixture for the new fragments

**Files:**
- Modify: `bootstrapper/tests/fixtures/rendered_config_baseline.yml`

- [ ] **Step 1: Confirm the baseline test fails (drift expected)**

```bash
./.venv/bin/python -m pytest tests/test_fragment_equivalence.py -v 2>&1 | tail -3
```

Expected: 4 failed (the rendered output now includes spark blocks that aren't in the baseline).

- [ ] **Step 2: Regenerate baseline**

Per `project_compose_baseline_test.md` + `project_baseline_regen_via_ci_artifact.md`: if you're on the same Compose version as CI, just regen locally. Otherwise the CI-artifact dance is required.

```bash
cd /Users/kaveh/repos/atlas
docker compose --env-file .env.example -p atlas -f docker-compose.yml config 2>/dev/null > bootstrapper/tests/fixtures/rendered_config_baseline.yml.new

# Normalize paths (replace local REPO_ROOT + HOME with placeholders):
python3 -c "
from pathlib import Path
src = Path('bootstrapper/tests/fixtures/rendered_config_baseline.yml.new').read_text(encoding='utf-8')
src = src.replace(str(Path.cwd()), '{REPO_ROOT}')
src = src.replace(str(Path.home()), '{HOME}')
Path('bootstrapper/tests/fixtures/rendered_config_baseline.yml').write_text(src, encoding='utf-8')
"
rm bootstrapper/tests/fixtures/rendered_config_baseline.yml.new
```

- [ ] **Step 3: Confirm baseline test passes**

```bash
cd bootstrapper
./.venv/bin/python -m pytest tests/test_fragment_equivalence.py -v 2>&1 | tail -3
```

Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
cd /Users/kaveh/repos/atlas
git add bootstrapper/tests/fixtures/rendered_config_baseline.yml
git commit -m "test(spark): regenerate fragment-equivalence baseline with spark blocks"
```

### 2.13. Task 1.13: Create `services/spark/README.md`

**Files:**
- Create: `services/spark/README.md`

- [ ] **Step 1: Write the per-service README**

Follow the `services/grafana/README.md` template. Sections: Overview, Access, Configuration, Cluster topology, Web UIs (Master + History), Spark Connect, Dependencies & Integrations (auto-gen marker), Troubleshooting. Concrete content:

```markdown
# Apache Spark (standalone cluster)

Spark runs as a 4-container family in the stack's `data` band: `spark-master`, `spark-worker` (replicas via `SPARK_WORKER_COUNT`), `spark-history`, and `spark-init` (a vanilla-alpine init that creates the MinIO bucket on first start).

## 1. Overview

Image: `bitnami/spark:4.1.2` (Apache 2.0). Standalone mode — no YARN, no Kubernetes. Spark Connect (gRPC) is enabled on port `15002` so Zeppelin and external clients can submit jobs via the modern `sc://spark-master:15002` URL.

## 2. Access

| Surface | URL | Auth |
|---|---|---|
| Master UI (direct) | `http://localhost:${SPARK_MASTER_UI_PORT}` | None |
| Master UI (Kong) | `http://spark.localhost:${KONG_HTTP_PORT}` | None |
| History UI (direct) | `http://localhost:${SPARK_HISTORY_PORT}` | None |
| History UI (Kong) | `http://spark-history.localhost:${KONG_HTTP_PORT}` | None |
| Spark Connect | `sc://spark-master:15002` | None — backend-network only |
| Master RPC | `spark://spark-master:7077` | None — backend-network only |

## 3. Configuration

```bash
SPARK_SOURCE=disabled              # container | disabled
SPARK_IMAGE=bitnami/spark:4.1.2
SPARK_MASTER_UI_PORT=              # auto-assigned by topology (data band)
SPARK_HISTORY_PORT=                # auto-assigned
SPARK_WORKER_COUNT=2               # 1-8 (wizard prompts via SecondaryNumberInput)
```

## 4. Integration with the stack

- **MinIO** — `spark-history` reads `s3a://spark-history/` for event logs. The `spark-init` container creates the bucket on first start (idempotent).
- **Supabase Postgres** — Spark JDBC connector available; users add `--jars postgresql.jar` and point at `jdbc:postgresql://supabase-db:5432/${SUPABASE_DB_NAME}`. No pre-wired connection.
- **Zeppelin** — Zeppelin's Spark interpreter points at `spark://spark-master:7077` and `sc://spark-master:15002`. See `services/zeppelin/README.md`.
- **Airflow** — Airflow's `spark_default` Connection is seeded by `airflow-init` when `SPARK_SOURCE=container`. The provided `example_etl_with_llm.py` DAG uses `SparkSubmitOperator`.
- **Prometheus** (opt-in) — JMX exporter sidecar; scrape job auto-enabled when both `SPARK_SOURCE=container` AND `PROMETHEUS_SOURCE=container`.

## 5. Dependencies & Integrations

> Auto-generated section — re-run `python -m bootstrapper.docs.regen spark` after manifest changes.

(Will be populated by regen.)

## 6. Troubleshooting

- **History UI shows no jobs** — confirm the spark-history bucket exists in MinIO (`mc ls minio/spark-history`). The `spark-init` container should have created it. If empty, check `spark-init` logs.
- **Workers don't appear in the master UI** — Compose's `depends_on: spark-master: condition: service_healthy` should serialize this. If a worker stays "lost", check `docker logs ${PROJECT_NAME}-spark-worker-1`.
- **OOM in a worker** — Spark workers are unbounded by default. Set `SPARK_WORKER_MEMORY=4G` in the container env block for production use.
- **Spark Connect refused** — port 15002 is backend-network-only; Zeppelin should hit `sc://spark-master:15002` directly. Don't expose 15002 to the host.
```

- [ ] **Step 2: Commit**

```bash
git add services/spark/README.md
git commit -m "docs(spark): per-service README with access + config + integration"
```

### 2.14. Task 1.14: Run baseline test sweep at the end of Phase 1

- [ ] **Step 1: Run the full suite**

```bash
cd /Users/kaveh/repos/atlas/bootstrapper
./.venv/bin/python -m pytest --tb=no -q 2>&1 | tail -3
```

Expected: 600 passed (was 590; +4 spark_config + +2 fragment guards + +2 kong + +1 worker_count + +1 compose test).

- [ ] **Step 2: Run docs-drift check**

```bash
./.venv/bin/python -m docs.regen --all --check 2>&1 | tail -3
```

Expected: zero drift output, exit 0. If the auto-gen `## 5. Dependencies & Integrations` in `services/spark/README.md` is missing, run `./.venv/bin/python -m docs.regen spark` first (which writes the auto-section) and re-run `--check`.

---

## 3. Phase 2 — Zeppelin

### 3.1. Task 2.1: Create `services/zeppelin/service.yml`

**Files:**
- Create: `services/zeppelin/service.yml`

- [ ] **Step 1: Write the failing test**

Append to `bootstrapper/tests/test_manifests.py`:

```python
def test_zeppelin_manifest_loads():
    from services.manifests import load_manifests
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent
    manifests = load_manifests(repo_root / "services")
    z = next((m for m in manifests if m.name == "zeppelin"), None)
    assert z is not None
    assert z.category == "apps"
    assert "spark" in z.depends_on.required, "Zeppelin must hard-require Spark per D3"
    assert z.sources.default == "disabled"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/bin/python -m pytest tests/test_manifests.py::test_zeppelin_manifest_loads -v
```

Expected: FAIL.

- [ ] **Step 3: Create the Zeppelin manifest**

Create `services/zeppelin/service.yml`:

```yaml
name: zeppelin
label: "Apache Zeppelin (Spark-first notebook)"
category: apps
docs: services/zeppelin/README.md

containers:
  - zeppelin

images:
  - var: ZEPPELIN_IMAGE
    default: "apache/zeppelin:0.12.0"
    container: zeppelin

sources:
  var: ZEPPELIN_SOURCE
  default: disabled
  options:
    - id: container
      label: "Container (Spark-first notebook)"
    - id: disabled
      label: "Disabled"

env:
  - name: ZEPPELIN_SOURCE
    default: disabled
  - name: ZEPPELIN_IMAGE
    default: "apache/zeppelin:0.12.0"
  - name: ZEPPELIN_PORT
    # default removed — computed by topology
    description: "Host port for the Zeppelin Web UI (in-container 8080)."
  - name: ZEPPELIN_SCALE
    auto_managed: true

depends_on:
  required:
    - spark
  optional:
    - supabase
    - minio
    - litellm

rows:
  - display_name: "Apache Zeppelin"
    source_var: ZEPPELIN_SOURCE
    port_var: ZEPPELIN_PORT
    scale_var: ZEPPELIN_SCALE
    alias: zeppelin.localhost
    description: "Spark-first notebook UI. Requires Spark."

exports: []

runtime_sc:
  zeppelin:
    container:
      scale: 1
      environment:
        ZEPPELIN_PORT: "8080"
        ZEPPELIN_LOG_DIR: /logs
        ZEPPELIN_NOTEBOOK_DIR: /notebook
        SPARK_MASTER: "spark://spark-master:7077"
        SPARK_CONNECT_URL: "sc://spark-master:15002"
        SPARK_SUBMIT_OPTIONS: >-
          --conf spark.hadoop.fs.s3a.endpoint=http://minio:9000
          --conf spark.hadoop.fs.s3a.access.key=${MINIO_ROOT_USER}
          --conf spark.hadoop.fs.s3a.secret.key=${MINIO_ROOT_PASSWORD}
          --conf spark.hadoop.fs.s3a.path.style.access=true
        ZEPPELIN_JDBC_POSTGRES_URL: "jdbc:postgresql://supabase-db:5432/${SUPABASE_DB_NAME}"
        ZEPPELIN_JDBC_POSTGRES_USER: "${SUPABASE_DB_USER}"
        ZEPPELIN_JDBC_POSTGRES_PASSWORD: "${SUPABASE_DB_PASSWORD}"
      deploy: {}
      extra_hosts: []
    disabled:
      scale: 0
      environment: {}
      deploy: {}
      extra_hosts: []

data_flow:
  calls:
    - spark
    - supabase
    - minio
    - litellm
    - kong
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/bin/python -m pytest tests/test_manifests.py::test_zeppelin_manifest_loads -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/zeppelin/service.yml bootstrapper/tests/test_manifests.py
git commit -m "feat(zeppelin): add service.yml manifest

Apache Zeppelin in 'apps' category. Hard-requires spark in
depends_on.required (per spec D3). Pre-configured for Spark + JDBC
(Supabase Postgres) + S3A (MinIO) via runtime_sc.
"
```

### 3.2. Task 2.2: Create `services/zeppelin/compose.yml`

**Files:**
- Create: `services/zeppelin/compose.yml`

- [ ] **Step 1: Write the compose fragment**

```yaml
# services/zeppelin/compose.yml — Spark-first notebook UI
services:
  zeppelin:
    image: ${ZEPPELIN_IMAGE:-apache/zeppelin:0.12.0}
    container_name: ${PROJECT_NAME}-zeppelin
    restart: unless-stopped
    deploy:
      replicas: ${ZEPPELIN_SCALE:-0}
    ports:
      - "${ZEPPELIN_PORT}:8080"
    environment:
      ZEPPELIN_PORT: "8080"
      ZEPPELIN_LOG_DIR: /logs
      ZEPPELIN_NOTEBOOK_DIR: /notebook
      SPARK_MASTER: spark://spark-master:7077
      SPARK_CONNECT_URL: sc://spark-master:15002
      SPARK_SUBMIT_OPTIONS: >-
        --conf spark.hadoop.fs.s3a.endpoint=http://minio:9000
        --conf spark.hadoop.fs.s3a.access.key=${MINIO_ROOT_USER}
        --conf spark.hadoop.fs.s3a.secret.key=${MINIO_ROOT_PASSWORD}
        --conf spark.hadoop.fs.s3a.path.style.access=true
      ZEPPELIN_JDBC_POSTGRES_URL: jdbc:postgresql://supabase-db:5432/${SUPABASE_DB_NAME}
      ZEPPELIN_JDBC_POSTGRES_USER: ${SUPABASE_DB_USER}
      ZEPPELIN_JDBC_POSTGRES_PASSWORD: ${SUPABASE_DB_PASSWORD}
    volumes:
      - ./notebooks:/notebook
      - zeppelin-logs:/logs
    depends_on:
      spark-master:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8080/"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s
    networks:
      - backend-network

volumes:
  zeppelin-logs:
    name: ${PROJECT_NAME}-zeppelin-logs
    driver: local
```

- [ ] **Step 2: Add to `docker-compose.yml` include block**

Append:

```yaml
  - services/zeppelin/compose.yml
```

- [ ] **Step 3: Verify the top-level compose still renders**

```bash
cd /Users/kaveh/repos/atlas
docker compose -f docker-compose.yml --env-file .env.example config -q
echo "exit=$?"
```

Expected: `exit=0`.

- [ ] **Step 4: Verify bind-source guards pass for zeppelin fragment**

```bash
cd bootstrapper
./.venv/bin/python -m pytest tests/test_fragment_bind_sources.py -v 2>&1 | grep zeppelin
```

Expected: 2 PASSED (self-double + escape).

- [ ] **Step 5: Commit**

```bash
cd /Users/kaveh/repos/atlas
git add services/zeppelin/compose.yml docker-compose.yml
git commit -m "feat(zeppelin): add compose fragment with Spark + JDBC + MinIO env"
```

### 3.3. Task 2.3: Create `services/zeppelin/notebooks/spark_basics.zpln`

**Files:**
- Create: `services/zeppelin/notebooks/spark_basics.zpln`

- [ ] **Step 1: Write the notebook**

Create a minimal Zeppelin notebook in its native JSON format demonstrating Spark + S3A + JDBC:

```json
{
  "paragraphs": [
    {
      "title": "Spark version",
      "text": "%spark\nsc.version",
      "user": "anonymous",
      "config": {"editorSetting": {"language": "scala"}}
    },
    {
      "title": "Markdown — intro",
      "text": "%md\n# Spark + MinIO + Postgres quickstart\n\nThis notebook confirms the three integrations are wired:\n1. `%spark` — Scala against the in-stack cluster\n2. S3A read/write via MinIO (`s3a://spark-history/...`)\n3. JDBC read against Supabase Postgres\n",
      "user": "anonymous",
      "config": {"editorSetting": {"language": "markdown"}}
    },
    {
      "title": "MinIO round-trip via S3A",
      "text": "%spark\nval df = Seq((1, \"alice\"), (2, \"bob\")).toDF(\"id\", \"name\")\ndf.write.mode(\"overwrite\").parquet(\"s3a://spark-history/_test_round_trip\")\nspark.read.parquet(\"s3a://spark-history/_test_round_trip\").show()",
      "user": "anonymous"
    },
    {
      "title": "Postgres JDBC read",
      "text": "%jdbc(postgres)\nSELECT version()",
      "user": "anonymous",
      "config": {"editorSetting": {"language": "sql"}}
    }
  ],
  "name": "Spark basics",
  "config": {
    "looknfeel": "default",
    "personalizedMode": "false"
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add services/zeppelin/notebooks/spark_basics.zpln
git commit -m "feat(zeppelin): starter notebook — Spark + S3A + JDBC round-trip"
```

### 3.4. Task 2.4: Add Zeppelin to `bootstrapper/services/service_config.py`

**Files:**
- Modify: `bootstrapper/services/service_config.py`

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_zeppelin_spark_gating.py`:

```python
"""Zeppelin's source-step selection must hard-fail when SPARK_SOURCE=disabled."""
from unittest.mock import MagicMock
from services.service_config import ServiceConfig


def test_zeppelin_disabled_returns_scale_zero():
    sc = ServiceConfig(config_parser=MagicMock())
    sc.service_sources = {"ZEPPELIN_SOURCE": "disabled", "SPARK_SOURCE": "disabled"}
    out = sc._generate_zeppelin_config()
    assert out["ZEPPELIN_SCALE"] == "0"


def test_zeppelin_container_with_spark_container_returns_scale_one():
    sc = ServiceConfig(config_parser=MagicMock())
    sc.service_sources = {"ZEPPELIN_SOURCE": "container", "SPARK_SOURCE": "container"}
    out = sc._generate_zeppelin_config()
    assert out["ZEPPELIN_SCALE"] == "1"


def test_zeppelin_container_with_spark_disabled_raises():
    import pytest
    sc = ServiceConfig(config_parser=MagicMock())
    sc.service_sources = {"ZEPPELIN_SOURCE": "container", "SPARK_SOURCE": "disabled"}
    with pytest.raises(ValueError, match="Zeppelin requires Spark"):
        sc._generate_zeppelin_config()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/bin/python -m pytest tests/test_zeppelin_spark_gating.py -v
```

Expected: 3 FAIL.

- [ ] **Step 3: Add `_generate_zeppelin_config`**

Append to `bootstrapper/services/service_config.py`:

```python
def _generate_zeppelin_config(self) -> dict:
    """Generate ZEPPELIN_SCALE. Hard-fails if Zeppelin=container but Spark=disabled."""
    z_source = self.service_sources.get("ZEPPELIN_SOURCE", "disabled")
    s_source = self.service_sources.get("SPARK_SOURCE", "disabled")
    if z_source == "container" and s_source == "disabled":
        raise ValueError(
            "Zeppelin requires Spark to be enabled. "
            "Either pass --spark-source container alongside --zeppelin-source container, "
            "or set --zeppelin-source disabled."
        )
    return {"ZEPPELIN_SCALE": "1" if z_source == "container" else "0"}
```

Wire into `generate_service_environment()` after the spark call:

```python
zeppelin_config = self._generate_zeppelin_config()
env_vars.update(zeppelin_config)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/bin/python -m pytest tests/test_zeppelin_spark_gating.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/services/service_config.py bootstrapper/tests/test_zeppelin_spark_gating.py
git commit -m "feat(zeppelin): hard-fail source step when Spark is disabled

Surfaces an actionable error rather than letting Zeppelin boot into a
broken state (Spark interpreter pre-configured but nothing to connect
to)."
```

### 3.5. Task 2.5: Kong route + hosts + CLI flag for Zeppelin

**Files:**
- Modify: `bootstrapper/utils/kong_config_generator.py`
- Modify: `bootstrapper/utils/hosts_manager.py`
- Modify: `bootstrapper/utils/source_override_manager.py`
- Modify: `bootstrapper/start.py`
- Modify: `bootstrapper/wizard/service_discovery.py`

- [ ] **Step 1: Add the Kong route generator (preserve_host: True)**

Append to `bootstrapper/utils/kong_config_generator.py`:

```python
def generate_zeppelin_service(self):
    source = self.get_env_value("ZEPPELIN_SOURCE")
    if source == "disabled":
        return None
    return {
        "name": "zeppelin",
        "url": "http://zeppelin:8080",
        "routes": [{
            "name": "zeppelin-all",
            "strip_path": False,
            "hosts": ["zeppelin.localhost"],
            "preserve_host": True,
        }],
        "plugins": [{"name": "cors"}],
    }
```

Call from `get_all_services()`.

- [ ] **Step 2: Add hostname to GENAI_HOSTS**

```python
"zeppelin.localhost",
```

- [ ] **Step 3: Add source_mapping entry**

```python
"zeppelin_source": "ZEPPELIN_SOURCE",
```

- [ ] **Step 4: Add `--zeppelin-source` Click flag**

In `bootstrapper/start.py`:

```python
@click.option('--zeppelin-source',
              type=click.Choice(['container', 'disabled']),
              default=None,
              help='Override ZEPPELIN_SOURCE.')
```

Plus `zeppelin_source` in `main()` signature and `source_args['zeppelin_source'] = zeppelin_source`.

- [ ] **Step 5: Add display name + description**

In `bootstrapper/wizard/service_discovery.py`:

```python
DISPLAY_NAME_OVERRIDES['zeppelin'] = 'Apache Zeppelin'
SERVICE_DESCRIPTIONS['zeppelin'] = 'Spark-first notebook UI (requires Spark).'
```

- [ ] **Step 6: Verify Kong route test passes**

Add to `bootstrapper/tests/test_kong_alias_routes.py`:

```python
def test_zeppelin_route_exists_with_preserve_host(kong_config_with_all_sources):
    cfg = kong_config_with_all_sources(spark_source="container", zeppelin_source="container")
    svc = next((s for s in cfg["services"] if s["name"] == "zeppelin"), None)
    assert svc is not None
    assert svc["routes"][0]["hosts"] == ["zeppelin.localhost"]
    assert svc["routes"][0].get("preserve_host") is True
```

Run:

```bash
./.venv/bin/python -m pytest tests/test_kong_alias_routes.py -v 2>&1 | tail -3
```

Expected: 24 passed.

- [ ] **Step 7: Commit**

```bash
git add bootstrapper/utils/kong_config_generator.py bootstrapper/utils/hosts_manager.py bootstrapper/utils/source_override_manager.py bootstrapper/start.py bootstrapper/wizard/service_discovery.py bootstrapper/tests/test_kong_alias_routes.py
git commit -m "feat(zeppelin): Kong route + hosts + --zeppelin-source CLI flag + wizard

Route is preserve_host=True (SPA bakes hostnames in asset URLs).
Wizard display name 'Apache Zeppelin' + description noting Spark
dependency."
```

### 3.6. Task 2.6: Regen .env.example + baseline + Zeppelin README

**Files:**
- Modify: `.env.example`
- Modify: `bootstrapper/tests/fixtures/rendered_config_baseline.yml`
- Create: `services/zeppelin/README.md`

- [ ] **Step 1: Regen .env.example**

```bash
cd bootstrapper
./.venv/bin/python -m services.env_assembler
grep -E '^ZEPPELIN_' /Users/kaveh/repos/atlas/.env.example
```

- [ ] **Step 2: Regen baseline**

```bash
cd /Users/kaveh/repos/atlas
docker compose --env-file .env.example -p atlas -f docker-compose.yml config 2>/dev/null > bootstrapper/tests/fixtures/rendered_config_baseline.yml.new
python3 -c "
from pathlib import Path
src = Path('bootstrapper/tests/fixtures/rendered_config_baseline.yml.new').read_text(encoding='utf-8')
src = src.replace(str(Path.cwd()), '{REPO_ROOT}').replace(str(Path.home()), '{HOME}')
Path('bootstrapper/tests/fixtures/rendered_config_baseline.yml').write_text(src, encoding='utf-8')
"
rm bootstrapper/tests/fixtures/rendered_config_baseline.yml.new
cd bootstrapper && ./.venv/bin/python -m pytest tests/test_fragment_equivalence.py -v 2>&1 | tail -3
```

Expected: 4 passed.

- [ ] **Step 3: Write `services/zeppelin/README.md`**

(Same template as Spark's README; sections 1–6. Use the §3.2 design in the spec as the content source for each section. Skipped here for brevity — the engineer follows the Spark README pattern.)

- [ ] **Step 4: Commit**

```bash
git add .env.example bootstrapper/tests/fixtures/rendered_config_baseline.yml services/zeppelin/README.md
git commit -m "feat(zeppelin): regen .env.example + baseline + per-service README"
```

---

## 4. Phase 3 — Apache Airflow

### 4.1. Task 3.1: Create `services/airflow/build/Dockerfile` + `requirements.txt`

**Files:**
- Create: `services/airflow/build/Dockerfile`
- Create: `services/airflow/build/requirements.txt`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
ARG BASE_IMAGE=apache/airflow:3.2.2
FROM ${BASE_IMAGE}

# Providers needed for the cross-stack integrations declared in the spec
# §5.3. apache-spark for SparkSubmitOperator; amazon for S3Hook (works
# with MinIO via endpoint override); langchain + openai for LiteLLM
# integration; weaviate + neo4j + postgres + redis for the per-DB
# Connections seeded by airflow-init.
COPY requirements.txt /tmp/airflow-providers.txt
RUN pip install --no-cache-dir -r /tmp/airflow-providers.txt
```

- [ ] **Step 2: Write `requirements.txt`** (pin versions to today's stable)

```txt
apache-airflow-providers-apache-spark>=5.4.0
apache-airflow-providers-amazon>=9.10.0
apache-airflow-providers-postgres>=6.2.1
apache-airflow-providers-redis>=4.2.1
apache-airflow-providers-weaviate>=3.2.0
apache-airflow-providers-neo4j>=4.1.0
apache-airflow-providers-openai>=2.0.0
apache-airflow-providers-langchain>=1.1.0
psycopg2-binary>=2.9.10
```

- [ ] **Step 3: Commit**

```bash
git add services/airflow/build/Dockerfile services/airflow/build/requirements.txt
git commit -m "feat(airflow): Dockerfile extending apache/airflow:3.2.2 with provider bundle

Adds 8 providers (apache-spark, amazon, postgres, redis, weaviate,
neo4j, openai, langchain) so every connection seeded by airflow-init
has its provider classes available."
```

### 4.2. Task 3.2: Create `services/airflow/service.yml`

**Files:**
- Create: `services/airflow/service.yml`

- [ ] **Step 1: Write the failing test**

```python
def test_airflow_manifest_loads():
    from services.manifests import load_manifests
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent
    manifests = load_manifests(repo_root / "services")
    a = next((m for m in manifests if m.name == "airflow"), None)
    assert a is not None
    assert a.category == "agents"
    assert "supabase" in a.depends_on.required
    assert "spark" in a.depends_on.optional   # gated connection seeding
    assert "litellm" in a.depends_on.optional  # gated connection seeding
```

- [ ] **Step 2: Run + verify failure**

```bash
./.venv/bin/python -m pytest tests/test_manifests.py::test_airflow_manifest_loads -v
```

Expected: FAIL.

- [ ] **Step 3: Write the manifest**

Create `services/airflow/service.yml`:

```yaml
name: airflow
label: "Apache Airflow (DAG orchestrator)"
category: agents
docs: services/airflow/README.md

containers:
  - airflow-webserver
  - airflow-scheduler
  - airflow-init

images:
  - var: AIRFLOW_IMAGE
    default: "apache/airflow:3.2.2"
    container: airflow-webserver

sources:
  var: AIRFLOW_SOURCE
  default: disabled
  options:
    - id: container
      label: "Container (LocalExecutor + Supabase Postgres)"
    - id: disabled
      label: "Disabled"

env:
  - name: AIRFLOW_SOURCE
    default: disabled
  - name: AIRFLOW_IMAGE
    default: "apache/airflow:3.2.2"
  - name: AIRFLOW_PORT
    description: "Host port for the Airflow Web UI + REST API (in-container 8080)."
  - name: AIRFLOW_DB_USER
    default: airflow
  - name: AIRFLOW_DB_PASSWORD
    default: ""
    secret: true
    description: "Auto-generated by bootstrapper. Postgres role for the Airflow metadata DB."
  - name: AIRFLOW_FERNET_KEY
    default: ""
    secret: true
    description: "Auto-generated. Fernet key for encrypting Connection passwords."
  - name: AIRFLOW_SECRET_KEY
    default: ""
    secret: true
    description: "Auto-generated. Flask session secret."
  - name: AIRFLOW_ADMIN_PASSWORD
    default: ""
    secret: true
    description: "Auto-generated. Admin login. Username is hardcoded 'admin'."
  - name: AIRFLOW_WEBSERVER_SCALE
    auto_managed: true
  - name: AIRFLOW_SCHEDULER_SCALE
    auto_managed: true
  - name: AIRFLOW_INIT_SCALE
    auto_managed: true

depends_on:
  required:
    - supabase
  optional:
    - spark
    - minio
    - litellm
    - redis
    - weaviate
    - neo4j
    - prometheus

rows:
  - display_name: "Apache Airflow"
    source_var: AIRFLOW_SOURCE
    port_var: AIRFLOW_PORT
    scale_var: AIRFLOW_WEBSERVER_SCALE
    alias: airflow.localhost
    description: "Code-defined DAG orchestrator. LocalExecutor + LLM operators."

exports: []

runtime_sc:
  airflow-webserver:
    container:
      scale: 1
      environment:
        AIRFLOW__CORE__EXECUTOR: LocalExecutor
        AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: "postgresql+psycopg2://${AIRFLOW_DB_USER}:${AIRFLOW_DB_PASSWORD}@supabase-db:5432/airflow"
        AIRFLOW__CORE__FERNET_KEY: ${AIRFLOW_FERNET_KEY}
        AIRFLOW__WEBSERVER__SECRET_KEY: ${AIRFLOW_SECRET_KEY}
        AIRFLOW__API__AUTH_BACKENDS: "airflow.api.auth.backend.basic_auth"
        AIRFLOW__CORE__LOAD_EXAMPLES: "false"
        AIRFLOW_UID: "50000"
      deploy: {}
      extra_hosts: []
    disabled:
      scale: 0
      environment: {}
      deploy: {}
      extra_hosts: []
  airflow-scheduler:
    container:
      scale: 1
      environment:
        # (same env block as webserver — they share config)
        AIRFLOW__CORE__EXECUTOR: LocalExecutor
        AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: "postgresql+psycopg2://${AIRFLOW_DB_USER}:${AIRFLOW_DB_PASSWORD}@supabase-db:5432/airflow"
        AIRFLOW__CORE__FERNET_KEY: ${AIRFLOW_FERNET_KEY}
        AIRFLOW__CORE__LOAD_EXAMPLES: "false"
        AIRFLOW_UID: "50000"
      deploy: {}
      extra_hosts: []
    disabled:
      scale: 0
      environment: {}
      deploy: {}
      extra_hosts: []
  airflow-init:
    container:
      scale: 1
      environment:
        AIRFLOW__CORE__FERNET_KEY: ${AIRFLOW_FERNET_KEY}
        AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: "postgresql+psycopg2://${AIRFLOW_DB_USER}:${AIRFLOW_DB_PASSWORD}@supabase-db:5432/airflow"
        _AIRFLOW_DB_MIGRATE: "true"
        _AIRFLOW_WWW_USER_USERNAME: admin
        _AIRFLOW_WWW_USER_PASSWORD: ${AIRFLOW_ADMIN_PASSWORD}
        AIRFLOW_UID: "50000"
      deploy: {}
      extra_hosts: []
    disabled:
      scale: 0
      environment: {}
      deploy: {}
      extra_hosts: []

data_flow:
  calls:
    - supabase
    - spark
    - minio
    - litellm
    - weaviate
    - neo4j
    - redis
    - kong
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/bin/python -m pytest tests/test_manifests.py::test_airflow_manifest_loads -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/airflow/service.yml bootstrapper/tests/test_manifests.py
git commit -m "feat(airflow): add service.yml manifest

Apache Airflow 3.2.2 in 'agents' category. LocalExecutor. 3 containers
(webserver, scheduler, airflow-init). Metadata DB on Supabase Postgres
via a new 'airflow' database. 8 sibling services in depends_on.optional
for gated Connection seeding."
```

### 4.3. Task 3.3: Create `services/airflow/compose.yml`

**Files:**
- Create: `services/airflow/compose.yml`

- [ ] **Step 1: Write the compose fragment**

```yaml
services:
  airflow-webserver:
    build:
      context: ./build
      args:
        BASE_IMAGE: ${AIRFLOW_IMAGE:-apache/airflow:3.2.2}
    image: ${PROJECT_NAME}-airflow:local
    container_name: ${PROJECT_NAME}-airflow-webserver
    restart: unless-stopped
    deploy:
      replicas: ${AIRFLOW_WEBSERVER_SCALE:-0}
    ports:
      - "${AIRFLOW_PORT}:8080"
    environment:
      AIRFLOW__CORE__EXECUTOR: LocalExecutor
      AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://${AIRFLOW_DB_USER}:${AIRFLOW_DB_PASSWORD}@supabase-db:5432/airflow
      AIRFLOW__CORE__FERNET_KEY: ${AIRFLOW_FERNET_KEY}
      AIRFLOW__WEBSERVER__SECRET_KEY: ${AIRFLOW_SECRET_KEY}
      AIRFLOW__API__AUTH_BACKENDS: airflow.api.auth.backend.basic_auth
      AIRFLOW__CORE__LOAD_EXAMPLES: "false"
      AIRFLOW_UID: "50000"
    volumes:
      - ./dags:/opt/airflow/dags
      - airflow-logs:/opt/airflow/logs
    command: ["airflow", "webserver"]
    depends_on:
      airflow-init:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s
    networks:
      - backend-network

  airflow-scheduler:
    image: ${PROJECT_NAME}-airflow:local
    container_name: ${PROJECT_NAME}-airflow-scheduler
    restart: unless-stopped
    deploy:
      replicas: ${AIRFLOW_SCHEDULER_SCALE:-0}
    environment:
      AIRFLOW__CORE__EXECUTOR: LocalExecutor
      AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://${AIRFLOW_DB_USER}:${AIRFLOW_DB_PASSWORD}@supabase-db:5432/airflow
      AIRFLOW__CORE__FERNET_KEY: ${AIRFLOW_FERNET_KEY}
      AIRFLOW__CORE__LOAD_EXAMPLES: "false"
      AIRFLOW_UID: "50000"
    volumes:
      - ./dags:/opt/airflow/dags
      - airflow-logs:/opt/airflow/logs
    command: ["airflow", "scheduler"]
    depends_on:
      airflow-init:
        condition: service_completed_successfully
    networks:
      - backend-network

  airflow-init:
    image: ${PROJECT_NAME}-airflow:local
    container_name: ${PROJECT_NAME}-airflow-init
    deploy:
      replicas: ${AIRFLOW_INIT_SCALE:-0}
    environment:
      AIRFLOW__CORE__FERNET_KEY: ${AIRFLOW_FERNET_KEY}
      AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://${AIRFLOW_DB_USER}:${AIRFLOW_DB_PASSWORD}@supabase-db:5432/airflow
      _AIRFLOW_DB_MIGRATE: "true"
      _AIRFLOW_WWW_USER_USERNAME: admin
      _AIRFLOW_WWW_USER_PASSWORD: ${AIRFLOW_ADMIN_PASSWORD}
      AIRFLOW_UID: "50000"
      # Sibling-source vars consumed by the init script for conditional
      # connection seeding (dual-write per project_runtime_sc_vs_compose_env)
      SPARK_SOURCE: ${SPARK_SOURCE:-disabled}
      MINIO_SOURCE: ${MINIO_SOURCE:-disabled}
      LITELLM_SOURCE: ${LITELLM_SOURCE:-disabled}
      WEAVIATE_SOURCE: ${WEAVIATE_SOURCE:-disabled}
      NEO4J_SOURCE: ${NEO4J_SOURCE:-disabled}
      REDIS_SOURCE: ${REDIS_SOURCE:-disabled}
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
      SUPABASE_DB_NAME: ${SUPABASE_DB_NAME}
      SUPABASE_DB_USER: ${SUPABASE_DB_USER}
      SUPABASE_DB_PASSWORD: ${SUPABASE_DB_PASSWORD}
      AIRFLOW_DB_USER: ${AIRFLOW_DB_USER}
      AIRFLOW_DB_PASSWORD: ${AIRFLOW_DB_PASSWORD}
    volumes:
      - ./dags:/opt/airflow/dags
      - ./init/scripts:/scripts:ro
    command: ["/scripts/init-airflow.sh"]
    depends_on:
      supabase-db:
        condition: service_healthy
    networks:
      - backend-network

volumes:
  airflow-logs:
    name: ${PROJECT_NAME}-airflow-logs
    driver: local
```

- [ ] **Step 2: Add to top-level `docker-compose.yml`**

```yaml
  - services/airflow/compose.yml
```

- [ ] **Step 3: Verify compose renders + bind-source guards**

```bash
cd /Users/kaveh/repos/atlas
docker compose -f docker-compose.yml --env-file .env.example config -q
cd bootstrapper
./.venv/bin/python -m pytest tests/test_fragment_bind_sources.py -v 2>&1 | grep airflow
```

Expected: both pass.

- [ ] **Step 4: Commit**

```bash
cd /Users/kaveh/repos/atlas
git add services/airflow/compose.yml docker-compose.yml
git commit -m "feat(airflow): compose fragment + wire into top-level include

3 containers: airflow-webserver (8080 → AIRFLOW_PORT), airflow-scheduler,
airflow-init (one-shot DB migrate + admin user + connection seeding).
All three use the locally-built ${PROJECT_NAME}-airflow:local image
from build/Dockerfile."
```

### 4.4. Task 3.4: Create `services/airflow/init/scripts/init-airflow.sh`

**Files:**
- Create: `services/airflow/init/scripts/init-airflow.sh`

- [ ] **Step 1: Write the init script**

```bash
#!/usr/bin/env bash
# init-airflow.sh — one-shot init container for Airflow.
#
# Responsibilities (idempotent — re-runs are no-ops):
# 1. Create the `airflow` database in Supabase Postgres if missing.
# 2. Create the `${AIRFLOW_DB_USER}` Postgres role if missing.
# 3. Run `airflow db migrate`.
# 4. Create the admin user.
# 5. Seed Connection objects for every sibling service whose source is
#    not 'disabled'.
set -euo pipefail

echo "==> airflow-init: ensuring airflow database exists"
# Use Supabase admin creds to CREATE DATABASE if absent. supabase-db
# entrypoint runs as 'postgres' superuser; we connect via the
# Supabase-managed db with the configured admin user.
export PGPASSWORD="${SUPABASE_DB_PASSWORD}"
psql -h supabase-db -U "${SUPABASE_DB_USER}" -d "${SUPABASE_DB_NAME}" -tAc \
     "SELECT 1 FROM pg_database WHERE datname='airflow'" | grep -q 1 \
  || psql -h supabase-db -U "${SUPABASE_DB_USER}" -d "${SUPABASE_DB_NAME}" \
       -c "CREATE DATABASE airflow"

echo "==> airflow-init: ensuring airflow role exists"
psql -h supabase-db -U "${SUPABASE_DB_USER}" -d postgres -tAc \
     "SELECT 1 FROM pg_roles WHERE rolname='${AIRFLOW_DB_USER}'" | grep -q 1 \
  || psql -h supabase-db -U "${SUPABASE_DB_USER}" -d postgres \
       -c "CREATE ROLE ${AIRFLOW_DB_USER} WITH LOGIN PASSWORD '${AIRFLOW_DB_PASSWORD}'"
psql -h supabase-db -U "${SUPABASE_DB_USER}" -d postgres \
     -c "GRANT ALL PRIVILEGES ON DATABASE airflow TO ${AIRFLOW_DB_USER}"
unset PGPASSWORD

echo "==> airflow-init: running airflow db migrate"
airflow db migrate

echo "==> airflow-init: creating admin user (idempotent)"
airflow users create \
  --username admin \
  --firstname Admin \
  --lastname User \
  --role Admin \
  --email admin@localhost \
  --password "${AIRFLOW_ADMIN_PASSWORD}" \
  || echo "(admin user already exists — skipping)"

echo "==> airflow-init: seeding Connections (gated on sibling source)"

# Helper: idempotent add (delete + add to update on second run)
add_conn() {
  local conn_id=$1; shift
  airflow connections delete "$conn_id" >/dev/null 2>&1 || true
  airflow connections add "$conn_id" "$@"
}

if [ "${SPARK_SOURCE}" = "container" ]; then
  add_conn spark_default --conn-type spark --conn-host spark-master --conn-port 7077
fi

if [ "${MINIO_SOURCE}" = "container" ]; then
  add_conn minio_default \
    --conn-type aws \
    --conn-extra "{\"endpoint_url\": \"http://minio:9000\", \"aws_access_key_id\": \"${MINIO_ROOT_USER}\", \"aws_secret_access_key\": \"${MINIO_ROOT_PASSWORD}\"}"
fi

if [ "${LITELLM_SOURCE}" != "disabled" ]; then
  add_conn litellm_default \
    --conn-type openai \
    --conn-host http://litellm:4000 \
    --conn-password "${LITELLM_MASTER_KEY}" \
    --conn-extra '{"api_base": "http://litellm:4000/v1"}'
fi

add_conn postgres_supabase \
  --conn-type postgres --conn-host supabase-db --conn-port 5432 \
  --conn-schema "${SUPABASE_DB_NAME}" \
  --conn-login "${SUPABASE_DB_USER}" \
  --conn-password "${SUPABASE_DB_PASSWORD}"

if [ "${WEAVIATE_SOURCE}" != "disabled" ]; then
  add_conn weaviate_default --conn-type weaviate --conn-host http://weaviate:8080
fi

if [ "${NEO4J_SOURCE}" != "disabled" ]; then
  add_conn neo4j_default --conn-type neo4j --conn-host bolt://neo4j --conn-port 7687
fi

if [ "${REDIS_SOURCE}" != "disabled" ]; then
  add_conn redis_default --conn-type redis --conn-host redis --conn-port 6379
fi

echo "==> airflow-init: complete"
```

- [ ] **Step 2: Mark executable**

```bash
chmod +x services/airflow/init/scripts/init-airflow.sh
```

- [ ] **Step 3: Commit**

```bash
git add services/airflow/init/scripts/init-airflow.sh
git commit -m "feat(airflow): init script for db migrate + admin + conditional Connections

Idempotent: psql checks for DB/role existence; airflow connections
delete-then-add for re-runs. Connection seeding gated on each sibling
service's _SOURCE env (passed via compose's environment block per the
dual-write rule)."
```

### 4.5. Task 3.5: Create `services/airflow/dags/example_etl_with_llm.py`

**Files:**
- Create: `services/airflow/dags/example_etl_with_llm.py`

- [ ] **Step 1: Write the sample DAG**

```python
"""Example DAG demonstrating Spark + MinIO + LiteLLM integrations.

Runs daily. Steps:
1. SparkSubmitOperator submits a stub job to the in-stack Spark cluster.
2. OpenAIOperator (wired to LiteLLM via litellm_default) summarizes a
   piece of text.
3. S3Hook lists the contents of an arbitrary MinIO bucket (smoke).

This DAG is intentionally tiny — it confirms each Connection works
end-to-end without doing real work. Replace with your own DAGs.
"""
from __future__ import annotations

from airflow import DAG
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.openai.operators.openai import OpenAIOperator
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta

default_args = {
    "owner": "atlas",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
}

def list_minio(**_ctx):
    s3 = S3Hook(aws_conn_id="minio_default")
    buckets = s3.get_conn().list_buckets()["Buckets"]
    print(f"buckets: {[b['Name'] for b in buckets]}")

with DAG(
    "example_etl_with_llm",
    description="Smoke test: Spark + MinIO + LiteLLM integrations",
    default_args=default_args,
    schedule="@daily",
    start_date=datetime(2026, 6, 4),
    catchup=False,
    tags=["smoke", "stack-internal"],
) as dag:

    spark_step = SparkSubmitOperator(
        task_id="spark_stub_job",
        conn_id="spark_default",
        application="/opt/airflow/dags/example_etl_with_llm.py",  # placeholder — would point at a real .jar/.py
        conf={"spark.master": "spark://spark-master:7077"},
        # In a real DAG this points at a uploaded .jar or python file.
        # For the smoke test we just verify the operator's connection.
    )

    llm_step = OpenAIOperator(
        task_id="summarize_via_litellm",
        conn_id="litellm_default",
        model="ollama/qwen3.6:latest",
        # Per the Airflow OpenAI provider's interface; LiteLLM accepts
        # the same shape since it exposes the OpenAI-compat /v1/chat
        # surface.
        messages=[{"role": "user", "content": "Reply with the single word 'ok'."}],
    )

    minio_step = PythonOperator(
        task_id="list_minio_buckets",
        python_callable=list_minio,
    )

    spark_step >> llm_step >> minio_step
```

- [ ] **Step 2: Commit**

```bash
git add services/airflow/dags/example_etl_with_llm.py
git commit -m "feat(airflow): example_etl_with_llm DAG smoke-testing Spark + MinIO + LiteLLM

Runs @daily. Three operators that exercise each seeded Connection
(spark_default, litellm_default, minio_default). Intentionally
near-empty work payloads — replace with real DAGs."
```

### 4.6. Task 3.6: Airflow bootstrapper plumbing (start.py + service_config.py + source_mapping + key_generator + kong + hosts + display name)

**Files:**
- Modify: `bootstrapper/start.py`
- Modify: `bootstrapper/services/service_config.py`
- Modify: `bootstrapper/utils/source_override_manager.py`
- Modify: `bootstrapper/utils/key_generator.py`
- Modify: `bootstrapper/utils/kong_config_generator.py`
- Modify: `bootstrapper/utils/hosts_manager.py`
- Modify: `bootstrapper/wizard/service_discovery.py`

- [ ] **Step 1: Add `--airflow-source` CLI flag**

In `bootstrapper/start.py`:

```python
@click.option('--airflow-source',
              type=click.Choice(['container', 'disabled']),
              default=None,
              help='Override AIRFLOW_SOURCE.')
```

Add `airflow_source` to `main()` signature + `source_args['airflow_source'] = airflow_source`.

- [ ] **Step 2: Add `_generate_airflow_config`**

In `bootstrapper/services/service_config.py`:

```python
def _generate_airflow_config(self) -> dict:
    source_value = self.service_sources.get("AIRFLOW_SOURCE", "disabled")
    if source_value == "disabled":
        return {
            "AIRFLOW_WEBSERVER_SCALE": "0",
            "AIRFLOW_SCHEDULER_SCALE": "0",
            "AIRFLOW_INIT_SCALE": "0",
        }
    return {
        "AIRFLOW_WEBSERVER_SCALE": "1",
        "AIRFLOW_SCHEDULER_SCALE": "1",
        "AIRFLOW_INIT_SCALE": "1",
    }
```

Wire into `generate_service_environment()` after zeppelin.

- [ ] **Step 3: Add 4 Airflow secret generators in `key_generator.py`**

```python
import base64
import secrets

def generate_airflow_fernet_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()

def generate_airflow_secret_key() -> str:
    return secrets.token_urlsafe(32)

def generate_airflow_admin_password() -> str:
    return secrets.token_urlsafe(18)

def generate_airflow_db_password() -> str:
    return secrets.token_urlsafe(24)
```

Wire into `generate_missing_keys()` so AIRFLOW_FERNET_KEY, AIRFLOW_SECRET_KEY, AIRFLOW_ADMIN_PASSWORD, AIRFLOW_DB_PASSWORD are populated on first run.

- [ ] **Step 4: Add Kong route**

```python
def generate_airflow_service(self):
    source = self.get_env_value("AIRFLOW_SOURCE")
    if source == "disabled":
        return None
    return {
        "name": "airflow",
        "url": "http://airflow-webserver:8080",
        "routes": [{
            "name": "airflow-all",
            "strip_path": False,
            "hosts": ["airflow.localhost"],
            "preserve_host": True,
        }],
        "plugins": [{"name": "cors"}],
    }
```

- [ ] **Step 5: Add hostname + source_mapping + display name**

```python
# hosts_manager.py
"airflow.localhost",

# source_override_manager.py
"airflow_source": "AIRFLOW_SOURCE",

# service_discovery.py
DISPLAY_NAME_OVERRIDES['airflow'] = 'Apache Airflow'
SERVICE_DESCRIPTIONS['airflow'] = 'Code-defined DAG orchestrator with LLM operators.'
```

- [ ] **Step 6: Commit**

```bash
git add bootstrapper/start.py bootstrapper/services/service_config.py bootstrapper/utils/source_override_manager.py bootstrapper/utils/key_generator.py bootstrapper/utils/kong_config_generator.py bootstrapper/utils/hosts_manager.py bootstrapper/wizard/service_discovery.py
git commit -m "feat(airflow): bootstrapper plumbing (CLI + service_config + secrets + kong + hosts + wizard)

4 new auto-generated secrets (Fernet, session key, admin password,
DB role password). Kong route at airflow.localhost preserves host.
Wizard display name 'Apache Airflow'."
```

### 4.7. Task 3.7: Regen .env.example + baseline + Airflow README

**Files:**
- Modify: `.env.example`
- Modify: `bootstrapper/tests/fixtures/rendered_config_baseline.yml`
- Create: `services/airflow/README.md`

- [ ] **Step 1: Regen .env.example**

```bash
cd bootstrapper
./.venv/bin/python -m services.env_assembler
grep -E '^AIRFLOW_' /Users/kaveh/repos/atlas/.env.example
```

- [ ] **Step 2: Regen the byte-equivalence baseline**

(Same procedure as Task 1.12 and Task 2.6.)

- [ ] **Step 3: Write `services/airflow/README.md`**

Same template as Spark + Zeppelin READMEs. Sections: Overview, Access (UI + REST API), Configuration, Connections seeded (the matrix from spec §5.3 narrative), DAG samples, Hermes integration (curl example), Troubleshooting.

- [ ] **Step 4: Commit**

```bash
cd /Users/kaveh/repos/atlas
git add .env.example bootstrapper/tests/fixtures/rendered_config_baseline.yml services/airflow/README.md
git commit -m "feat(airflow): regen .env.example + baseline + per-service README"
```

### 4.8. Task 3.8: Add airflow-init unit test

**Files:**
- Create: `bootstrapper/tests/test_airflow_connection_seeding.py`

- [ ] **Step 1: Write the test**

The init logic is bash, not Python, so this test is a docstring-style verification: parse the script, assert the conditional gating for each sibling source is correctly named:

```python
"""Verify that init-airflow.sh gates connection seeding on every sibling source."""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "services" / "airflow" / "init" / "scripts" / "init-airflow.sh"


def test_init_script_exists_and_is_executable():
    assert SCRIPT.exists(), "init-airflow.sh missing"
    assert (SCRIPT.stat().st_mode & 0o111), "init-airflow.sh not executable"


def test_init_script_gates_each_sibling_connection():
    body = SCRIPT.read_text(encoding="utf-8")
    # Each conditional must reference the matching sibling _SOURCE env var.
    assert 'if [ "${SPARK_SOURCE}" = "container" ]' in body
    assert 'if [ "${MINIO_SOURCE}" = "container" ]' in body
    assert 'if [ "${LITELLM_SOURCE}" != "disabled" ]' in body
    assert 'if [ "${WEAVIATE_SOURCE}" != "disabled" ]' in body
    assert 'if [ "${NEO4J_SOURCE}" != "disabled" ]' in body
    assert 'if [ "${REDIS_SOURCE}" != "disabled" ]' in body


def test_init_script_seeds_unconditional_supabase():
    body = SCRIPT.read_text(encoding="utf-8")
    # supabase is required, not optional — seed always.
    assert 'add_conn postgres_supabase' in body
```

- [ ] **Step 2: Run test**

```bash
./.venv/bin/python -m pytest tests/test_airflow_connection_seeding.py -v
```

Expected: 3 PASS (script + gating + unconditional).

- [ ] **Step 3: Commit**

```bash
git add bootstrapper/tests/test_airflow_connection_seeding.py
git commit -m "test(airflow): static assertions on init-airflow.sh gating"
```

### 4.9. Task 3.9: Phase 3 validation

```bash
cd /Users/kaveh/repos/atlas/bootstrapper
./.venv/bin/python -m pytest --tb=no -q 2>&1 | tail -3
```

Expected: ~616 passed (Phase 1 600 + Phase 2 ~4 + Phase 3 ~8 = ~612-616).

```bash
./.venv/bin/python -m docs.regen --all --check 2>&1 | tail -3
```

Expected: zero drift.

---

## 5. Phase 4 — Cross-cutting: audit scripts, top-level docs, CHANGELOG

### 5.1. Task 4.1: Add the 4 new Kong routes + 5 required deps to audit scripts

**Files:**
- Modify: `scripts/check-kong-routes.py`
- Modify: `scripts/check-compose-source-deps.py`

- [ ] **Step 1: Add to `EXPECTED_HOST_ROUTES` in `scripts/check-kong-routes.py`**

```python
"spark.localhost": "http://spark-master:8080/",
"spark-history.localhost": "http://spark-history:18080/",
"zeppelin.localhost": "http://zeppelin:8080/",
"airflow.localhost": "http://airflow-webserver:8080/",
```

- [ ] **Step 2: Add to `REQUIRED_DEPENDENCIES` in `scripts/check-compose-source-deps.py`**

```python
("spark", "minio"),
("zeppelin", "spark"),
("airflow", "supabase"),
```

- [ ] **Step 3: Run both scripts**

```bash
cd /Users/kaveh/repos/atlas
bootstrapper/.venv/bin/python scripts/check-kong-routes.py
bootstrapper/.venv/bin/python scripts/check-compose-source-deps.py
```

Expected: both PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/check-kong-routes.py scripts/check-compose-source-deps.py
git commit -m "audit: register new Kong routes + required deps for spark/zeppelin/airflow"
```

### 5.2. Task 4.2: Update top-level README §4.1 with the 3 new services

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add three rows to §4.1 Service Overview**

```markdown
| **Apache Spark** | http://localhost:${SPARK_MASTER_UI_PORT} | http://spark.localhost:63000 | Standalone Spark cluster for batch / SQL / DataFrame work. Spark Connect on `:15002`. Disabled by default; opt-in via `--spark-source container --spark-workers N`. | None |
| **Apache Zeppelin** | http://localhost:${ZEPPELIN_PORT} | http://zeppelin.localhost:63000 | Spark-first notebook UI. Pre-configured Spark + JDBC interpreters. Requires Spark (gated). Disabled by default. | None |
| **Apache Airflow** | http://localhost:${AIRFLOW_PORT} | http://airflow.localhost:63000 | Code-defined DAG orchestrator. LocalExecutor + AI/ML SDK with LiteLLM-wired LLM operators. Disabled by default. | `admin` / auto-generated `AIRFLOW_ADMIN_PASSWORD` |
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(README): surface Spark + Zeppelin + Airflow in §4.1 Service Overview"
```

### 5.3. Task 4.3: docs/deployment + docs/quick-start updates

**Files:**
- Modify: `docs/deployment/ports-and-routes.md`
- Modify: `docs/deployment/source-configuration.md`
- Modify: `docs/quick-start/interactive-setup-wizard.md`

- [ ] **Step 1: ports-and-routes.md — add 4 routes**

(Edit the routes table to include spark / spark-history / zeppelin / airflow alongside their internal ports.)

- [ ] **Step 2: source-configuration.md — add 3 walkthrough subsections**

Headers: `### SPARK_SOURCE`, `### ZEPPELIN_SOURCE`, `### AIRFLOW_SOURCE`. Each lists the source options + concrete `--<svc>-source X` examples + what each option does.

- [ ] **Step 3: interactive-setup-wizard.md — add 3 options**

(Add new rows to the wizard options table.)

- [ ] **Step 4: Commit**

```bash
git add docs/deployment/ports-and-routes.md docs/deployment/source-configuration.md docs/quick-start/interactive-setup-wizard.md
git commit -m "docs(deployment): document new SPARK/ZEPPELIN/AIRFLOW sources + routes"
```

### 5.4. Task 4.4: services/kong/README.md + services/hermes/README.md cross-references

**Files:**
- Modify: `services/kong/README.md`
- Modify: `services/hermes/README.md`

- [ ] **Step 1: Kong README — add 4 new route bullets + curl examples**

- [ ] **Step 2: Hermes README — document Hermes → Airflow REST integration**

Add a section with a curl example:

```bash
# Trigger an Airflow DAG run from Hermes
curl -X POST \
  -u admin:${AIRFLOW_ADMIN_PASSWORD} \
  -H 'Content-Type: application/json' \
  -d '{"conf": {}}' \
  http://airflow.localhost:63000/api/v2/dags/example_etl_with_llm/dagRuns
```

- [ ] **Step 3: Commit**

```bash
git add services/kong/README.md services/hermes/README.md
git commit -m "docs(kong+hermes): new Kong route bullets + Hermes→Airflow integration example"
```

### 5.5. Task 4.5: CHANGELOG entry

**Files:**
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: Add the Added entry**

Insert the §10 CHANGELOG sketch from the spec as a new `### Added —` block under `[Unreleased]`.

- [ ] **Step 2: Commit**

```bash
git add docs/CHANGELOG.md
git commit -m "docs(CHANGELOG): added entry for Airflow + Spark + Zeppelin compute tier"
```

### 5.6. Task 4.6: Phase 4 validation

```bash
cd /Users/kaveh/repos/atlas/bootstrapper
./.venv/bin/python -m pytest --tb=no -q 2>&1 | tail -3
./.venv/bin/python -m docs.regen --all --check 2>&1 | tail -3
```

Expected: tests pass, drift zero.

---

## 6. Phase 5 — Final validation + PR

### 6.1. Task 5.1: Boot the stack with all three enabled

**Files:** none

- [ ] **Step 1: Stop any running stack**

```bash
cd /Users/kaveh/repos/atlas
docker compose down --remove-orphans
```

(This is a confirmable action — proceed only if the user OKs it.)

- [ ] **Step 2: Start with all three opted in**

```bash
./start.sh \
  --spark-source container \
  --spark-workers 2 \
  --zeppelin-source container \
  --airflow-source container
```

Expected: stack boots; all 4 web UIs reachable at their alias URLs.

- [ ] **Step 3: Smoke-test each**

```bash
curl -fsS http://spark.localhost:63000 > /dev/null && echo "Spark UI OK"
curl -fsS http://spark-history.localhost:63000 > /dev/null && echo "Spark History OK"
curl -fsS http://zeppelin.localhost:63000 > /dev/null && echo "Zeppelin OK"
# Airflow 3.x /api/v2/ is JWT-only (verified 2026-06-05; basic_auth is for legacy FAB endpoints).
TOKEN=$(curl -fsS -X POST -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$(grep '^AIRFLOW_ADMIN_PASSWORD=' .env | cut -d= -f2)\"}" \
  http://airflow.localhost:63000/auth/token | jq -r .access_token)
curl -fsS -H "Authorization: Bearer $TOKEN" \
  http://airflow.localhost:63000/api/v2/dags > /dev/null && echo "Airflow API OK"
```

- [ ] **Step 4: Confirm seeded Connections in Airflow**

```bash
docker exec atlas-airflow-webserver airflow connections list
```

Expected: `spark_default`, `minio_default`, `litellm_default`, `postgres_supabase`, `weaviate_default`, `neo4j_default`, `redis_default` all listed.

### 6.2. Task 5.2: Push + open PR

- [ ] **Step 1: Push**

```bash
git push -u origin feat/airflow-spark-zeppelin
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "feat: Apache Airflow + Spark cluster + Apache Zeppelin (compute tier)" \
  --body "$(cat <<'EOF'
Implements the spec at \`docs/superpowers/specs/2026-06-04-airflow-spark-zeppelin-design.md\`.

## Summary
- **Spark cluster** (4 containers, default disabled). \`SPARK_WORKER_COUNT\` adjustable 1-8 via the wizard's \`SecondaryNumberInput\` mirroring Ray's worker-count widget.
- **Zeppelin notebook** (Spark-first, gated on Spark).
- **Airflow 3.2.2** (LocalExecutor on a new \`airflow\` database in Supabase Postgres; AI/ML SDK with LiteLLM-wired LLM operators; ~7 seeded Connections covering every enabled sibling service).

## Integration coverage (per spec §5)
- Spark × MinIO (s3a://), × Supabase (JDBC config), × Kong (preserve_host).
- Zeppelin × Spark (interpreter pre-config), × MinIO (via Spark), × Supabase (JDBC pre-config), × Kong.
- Airflow × Supabase (metadata DB), × Spark (SparkSubmitOperator + spark_default), × MinIO (S3Hook + minio_default), × LiteLLM (LangChain/OpenAI operators + litellm_default), × Weaviate × Neo4j × Redis. Hermes → Airflow via REST documented in services/hermes/README.md.

## Test plan
- [x] \`pytest\` — XYZ passed (was 590 baseline; +~26 for new tests)
- [x] \`scripts/check-kong-routes.py\` — PASS (24 routes)
- [x] \`scripts/check-compose-source-deps.py\` — PASS
- [x] \`scripts/check-docs-drift.py\` — PASS
- [x] \`docs.regen --all --check\` — zero drift
- [x] All 3 services boot via \`./start.sh --spark-source container --spark-workers 2 --zeppelin-source container --airflow-source container\`
- [x] \`docker exec atlas-airflow-webserver airflow connections list\` shows all 7 seeded Connections.
EOF
)"
```

- [ ] **Step 3: Wait for CI + merge per the standard flow**

(Same flow as PRs #29-#34: green checks → \`gh pr merge --rebase --delete-branch\`.)

---

## 7. Self-review checklist

- [x] Spec §2 D1-D10 decisions each tied to a task (D1 ↔ branch + single PR; D2 ↔ no bridge code anywhere; D3 ↔ Task 2.4 gating; D4 ↔ no n8n changes; D5 ↔ Task 3.4 dedicated DB; D6 ↔ Tasks 1.5 + 1.10; D7 ↔ default `disabled` in all 3 manifests; D8 ↔ Tasks 1.1 + 2.1 + 3.2 categories; D9 ↔ Task 3.1 Airflow 3.2.2; D10 ↔ Tasks 3.4 + 4.4)
- [x] §3.1 Spark — Tasks 1.1-1.14
- [x] §3.2 Zeppelin — Tasks 2.1-2.6
- [x] §3.3 Airflow — Tasks 3.1-3.9
- [x] §4 Topological ordering — auto-handled by the slot allocator; baseline regen tasks (1.12, 2.6, 3.7) confirm
- [x] §5 Integration matrix — CRITICAL pairs covered (Spark×MinIO 1.2, Zeppelin×Spark 2.1, Airflow×LiteLLM 3.4, Hermes→Airflow 4.4)
- [x] §6 Wizard UX — Task 1.10 (Spark widget) + 2.5 + 3.6 (CLI flags + display names)
- [x] §7 Bootstrapper plumbing — Tasks 1.5-1.9, 2.4-2.5, 3.6
- [x] §8 Tests — 3 new test files (1.5 test_spark_config, 2.4 test_zeppelin_spark_gating, 3.8 test_airflow_connection_seeding) + extensions
- [x] §9 Documentation — Tasks 1.13, 2.6, 3.7 (per-service READMEs) + 4.2-4.4 (top-level + cross-service)
- [x] §10 CHANGELOG — Task 4.5
- [x] §13 Acceptance criteria — Task 5.1 smoke-tests each item

**Placeholder scan:** No `TBD` / `TODO` / "appropriate error handling" — every code step shows actual code or commands.

**Type consistency:** `SPARK_WORKER_COUNT` referenced in service.yml, service_config.py, integration.py, test_spark_worker_count.py, and start.py — same name throughout. Same for `AIRFLOW_*` secrets and `ZEPPELIN_SCALE`.
