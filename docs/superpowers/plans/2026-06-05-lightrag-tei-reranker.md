# LightRAG + TEI Reranker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new services (`lightrag` in `agents` tier, `tei-reranker` in `llm` tier) to the atlas stack. Both default-disabled. Wire LightRAG into existing Supabase pgvector + Neo4j + Redis (with adaptive in-process fallback), register LightRAG with LiteLLM as a callable model, extend `hermes`/`n8n`/`backend` `runtime_adaptive` to consume LightRAG, and add a standalone reranker service reusable by compatible callers.

**Architecture:** Two new manifests follow the standard service-addition pattern (~25-file checklist). Slot allocator (`topology.py`) assigns host ports automatically — no hand-coded literals. Storage backends are resolved via `runtime_adaptive`, with in-process fallback when the corresponding source is `disabled`. LiteLLM gets a hand-coded `lightrag_model_entry()` (mirrors `hermes_model_entry()`). RAG-Anything is intentionally not added (subsumed by LightRAG v1.5.0).

**Tech Stack:** Python 3.12 (bootstrapper), Docker Compose v2.26+, Textual TUI, pytest, click, PyYAML, LightRAG v1.5.0, TEI 1.9, alpine init containers.

**Spec:** `docs/superpowers/specs/2026-06-05-lightrag-tei-reranker-design.md` (commit `dbe1b70`).

**Memory cross-refs:** `project_service_addition_checklist`, `project_init_container_pattern`, `project_runtime_sc_vs_compose_env_dual_write`, `project_cli_source_flag_three_seams`, `project_compose_include_path_resolution`, `feedback_localhost_url_override_symmetry`, `feedback_commits` (commits use terse third-person verb; no Claude Co-Authored-By trailer).

**Supersession note (2026-07-01):** This implementation plan is historical. Current Atlas keeps TEI Reranker as a standalone reusable service, but stock LightRAG is not wired directly to TEI because LightRAG sends rerank requests as `query` plus `documents` and TEI expects `query` plus `texts`. Current runtime behavior emits `LIGHTRAG_RERANK_BINDING=null` and clears `LIGHTRAG_RERANK_BINDING_HOST`; direct reranking requires a compatible adapter.

---

## 1. Phase 1 — TEI Reranker (leaf service, no deps)

Ship TEI Reranker first since it has no dependencies and LightRAG will optionally consume it.

### 1.1. Task 1: TEI Reranker `service.yml`

**Files:**
- Create: `services/tei-reranker/service.yml`

- [ ] **Step 1: Create the manifest**

```bash
mkdir -p services/tei-reranker
```

```yaml
# services/tei-reranker/service.yml
# TEI Reranker — BGE-reranker-v2-m3 inference for RAG quality lift.
# Published image, no build context. Image var resolved per source variant
# in service_config._generate_tei_reranker_config (mirrors DOCLING_GPU_IMAGE).
name: tei-reranker
label: "TEI Reranker (BGE-reranker-v2-m3)"
category: llm
docs: services/tei-reranker/README.md

containers:
  - tei-reranker

images:
  - var: TEI_RERANKER_CPU_IMAGE
    default: "ghcr.io/huggingface/text-embeddings-inference:cpu-1.9"
    container: tei-reranker
    notes: "Used when TEI_RERANKER_SOURCE=container-cpu."
  - var: TEI_RERANKER_GPU_IMAGE
    default: "ghcr.io/huggingface/text-embeddings-inference:1.9"
    container: tei-reranker
    notes: "Used when TEI_RERANKER_SOURCE=container-gpu. CUDA host required."

sources:
  var: TEI_RERANKER_SOURCE
  default: disabled
  options:
    - id: container-cpu
      label: "Container (CPU)"
    - id: container-gpu
      label: "Container (GPU, NVIDIA)"
    - id: localhost
      label: "Host (existing TEI rerank on this machine)"
    - id: disabled
      label: "Disabled"

env:
  - name: TEI_RERANKER_SOURCE
    default: disabled
  - name: TEI_RERANKER_PORT
    # default computed by services/topology.py slot allocator
    description: "Host port for the TEI rerank API (in-container listen port is 80)."
  - name: TEI_RERANKER_LOCALHOST_PORT
    default: "63031"
    description: "Host port for the host-installed TEI rerank source variant."
  - name: TEI_RERANKER_MODEL_ID
    default: "BAAI/bge-reranker-v2-m3"
  - name: TEI_RERANKER_REVISION
    default: "main"
  - name: TEI_RERANKER_MAX_CLIENT_BATCH_SIZE
    default: 32
  - name: TEI_RERANKER_MEMORY_LIMIT
    default: 4g
  - name: TEI_RERANKER_CPU_LIMIT
    default: "2.0"
  - name: TEI_RERANKER_HF_CACHE_DIR
    default: "/data"
  - name: TEI_RERANKER_SCALE
    auto_managed: true
  - name: TEI_RERANKER_ENDPOINT
    auto_managed: true
    description: "Resolved per TEI_RERANKER_SOURCE. Consumed by LightRAG runtime_adaptive."
  - name: TEI_RERANKER_IMAGE_RESOLVED
    auto_managed: true
    description: "Image picked per source variant (CPU vs GPU)."

depends_on:
  required: []
  optional: []

rows:
  - display_name: "TEI Reranker"
    source_var: TEI_RERANKER_SOURCE
    port_var: TEI_RERANKER_PORT
    scale_var: TEI_RERANKER_SCALE
    alias: rerank.localhost
    description: "BGE-reranker-v2-m3 inference for RAG quality lift."
    localhost_endpoint_var: TEI_RERANKER_ENDPOINT
    localhost_port_var: TEI_RERANKER_LOCALHOST_PORT

exports: []

runtime_sc:
  tei-reranker:
    container-cpu:
      scale: 1
      environment:
        TEI_RERANKER_ENDPOINT: "http://tei-reranker:80"
      deploy: {}
      extra_hosts: []
    container-gpu:
      scale: 1
      environment:
        TEI_RERANKER_ENDPOINT: "http://tei-reranker:80"
      deploy:
        resources:
          reservations:
            devices:
              - driver: nvidia
                count: 1
                capabilities: [gpu]
      extra_hosts: []
    localhost:
      scale: 0
      environment:
        TEI_RERANKER_ENDPOINT: "http://host.docker.internal:${TEI_RERANKER_LOCALHOST_PORT:-63031}"
      deploy: {}
      extra_hosts:
        - "host.docker.internal:host-gateway"
    disabled:
      scale: 0
      environment:
        TEI_RERANKER_ENDPOINT: ""
      deploy: {}
      extra_hosts: []

data_flow:
  calls: []
```

- [ ] **Step 2: Validate manifest against schema**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_manifests.py -v -k tei_reranker 2>&1 | tail -20`

If no tei_reranker-specific test exists yet, run the generic manifest validator:

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_manifest_validator.py -v 2>&1 | tail -20`

Expected: PASS (or at minimum: no schema errors for `services/tei-reranker/service.yml`).

- [ ] **Step 3: Commit**

```bash
git add services/tei-reranker/service.yml
git commit -m "feat(tei-reranker): add service.yml manifest"
```

---

### 1.2. Task 2: TEI Reranker `compose.yml`

**Files:**
- Create: `services/tei-reranker/compose.yml`

- [ ] **Step 1: Create the fragment**

```yaml
# services/tei-reranker/compose.yml
# TEI Reranker fragment. Image resolved per source variant at .env-render time
# via TEI_RERANKER_IMAGE_RESOLVED (set by _generate_tei_reranker_config).
services:
  tei-reranker:
    image: ${TEI_RERANKER_IMAGE_RESOLVED:-ghcr.io/huggingface/text-embeddings-inference:cpu-1.9}
    container_name: ${PROJECT_NAME}-tei-reranker
    deploy:
      replicas: ${TEI_RERANKER_SCALE:-0}
      resources:
        limits:
          memory: ${TEI_RERANKER_MEMORY_LIMIT:-4g}
          cpus: '${TEI_RERANKER_CPU_LIMIT:-2.0}'
    command:
      - "--model-id=${TEI_RERANKER_MODEL_ID:-BAAI/bge-reranker-v2-m3}"
      - "--revision=${TEI_RERANKER_REVISION:-main}"
      - "--max-client-batch-size=${TEI_RERANKER_MAX_CLIENT_BATCH_SIZE:-32}"
      - "--port=80"
    ports:
      - "${TEI_RERANKER_PORT}:80"
    environment:
      # Every var listed in runtime_sc.tei-reranker.<source>.environment MUST
      # also appear here (memory: project_runtime_sc_vs_compose_env_dual_write).
      TEI_RERANKER_ENDPOINT: ${TEI_RERANKER_ENDPOINT}
      HF_HOME: ${TEI_RERANKER_HF_CACHE_DIR:-/data}
    volumes:
      - tei-reranker-data:/data
    networks:
      - backend-network
    extra_hosts: []
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost/health"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 120s

volumes:
  tei-reranker-data:
    name: ${PROJECT_NAME}-tei-reranker-data
```

- [ ] **Step 2: Verify no self-doubling path**

Run: `grep -E '\./services/tei-reranker' services/tei-reranker/compose.yml`

Expected: no matches (self-double bug guard per memory `project_compose_include_path_resolution`).

- [ ] **Step 3: Commit**

```bash
git add services/tei-reranker/compose.yml
git commit -m "feat(tei-reranker): add compose.yml fragment"
```

---

### 1.3. Task 3: `_generate_tei_reranker_config()` handler + tests

**Files:**
- Create: `bootstrapper/tests/test_tei_reranker_config.py`
- Modify: `bootstrapper/services/service_config.py`

- [ ] **Step 1: Write failing tests**

```python
# bootstrapper/tests/test_tei_reranker_config.py
"""Tests for _generate_tei_reranker_config()."""
from __future__ import annotations

import pytest
from bootstrapper.services.service_config import ServiceConfig


@pytest.fixture
def base_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "PROJECT_NAME=atlas\n"
        "TEI_RERANKER_LOCALHOST_PORT=63031\n"
        "TEI_RERANKER_CPU_IMAGE=ghcr.io/huggingface/text-embeddings-inference:cpu-1.9\n"
        "TEI_RERANKER_GPU_IMAGE=ghcr.io/huggingface/text-embeddings-inference:1.9\n",
        encoding="utf-8",
    )
    return env


def _make(env_path, source):
    sc = ServiceConfig(env_file=env_path, localhost_host="localhost")
    sc.service_sources = {"TEI_RERANKER_SOURCE": source}
    return sc


def test_disabled_clears_endpoint_and_scale(base_env):
    sc = _make(base_env, "disabled")
    env = sc._generate_tei_reranker_config()
    assert env["TEI_RERANKER_ENDPOINT"] == ""
    assert env["TEI_RERANKER_SCALE"] == "0"


def test_container_cpu_resolves_cpu_image(base_env):
    sc = _make(base_env, "container-cpu")
    env = sc._generate_tei_reranker_config()
    assert env["TEI_RERANKER_ENDPOINT"] == "http://tei-reranker:80"
    assert env["TEI_RERANKER_SCALE"] == "1"
    assert env["TEI_RERANKER_IMAGE_RESOLVED"].endswith(":cpu-1.9")


def test_container_gpu_resolves_gpu_image(base_env):
    sc = _make(base_env, "container-gpu")
    env = sc._generate_tei_reranker_config()
    assert env["TEI_RERANKER_ENDPOINT"] == "http://tei-reranker:80"
    assert env["TEI_RERANKER_SCALE"] == "1"
    assert env["TEI_RERANKER_IMAGE_RESOLVED"] == \
        "ghcr.io/huggingface/text-embeddings-inference:1.9"


def test_localhost_uses_localhost_port(base_env):
    sc = _make(base_env, "localhost")
    env = sc._generate_tei_reranker_config()
    assert env["TEI_RERANKER_ENDPOINT"] == "http://localhost:63031"
    assert env["TEI_RERANKER_SCALE"] == "0"
```

- [ ] **Step 2: Run tests — verify fail**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_tei_reranker_config.py -v 2>&1 | tail -30`

Expected: FAIL with `AttributeError: 'ServiceConfig' object has no attribute '_generate_tei_reranker_config'` or `ImportError`.

- [ ] **Step 3: Implement the handler**

Open `bootstrapper/services/service_config.py`. Locate `_generate_hermes_config(self)` (currently near line 572). Add this method immediately after it:

```python
    def _generate_tei_reranker_config(self) -> Dict[str, str]:
        """Resolve TEI Reranker endpoint, scale, and per-source image."""
        source_value = self.service_sources.get('TEI_RERANKER_SOURCE', 'disabled')
        env_vars: Dict[str, str] = {}
        current_env = self.config_parser.parse_env_file()
        cpu_image = current_env.get(
            'TEI_RERANKER_CPU_IMAGE',
            'ghcr.io/huggingface/text-embeddings-inference:cpu-1.9',
        )
        gpu_image = current_env.get(
            'TEI_RERANKER_GPU_IMAGE',
            'ghcr.io/huggingface/text-embeddings-inference:1.9',
        )

        if source_value == 'disabled':
            env_vars['TEI_RERANKER_ENDPOINT'] = ''
            env_vars['TEI_RERANKER_SCALE'] = '0'
            env_vars['TEI_RERANKER_IMAGE_RESOLVED'] = cpu_image
        elif source_value == 'localhost':
            port = current_env.get('TEI_RERANKER_LOCALHOST_PORT', '63031')
            env_vars['TEI_RERANKER_ENDPOINT'] = f'http://{self.localhost_host}:{port}'
            env_vars['TEI_RERANKER_SCALE'] = '0'
            env_vars['TEI_RERANKER_IMAGE_RESOLVED'] = cpu_image
        else:  # container-cpu | container-gpu
            env_vars['TEI_RERANKER_ENDPOINT'] = 'http://tei-reranker:80'
            env_vars['TEI_RERANKER_SCALE'] = '1'
            env_vars['TEI_RERANKER_IMAGE_RESOLVED'] = (
                gpu_image if source_value == 'container-gpu' else cpu_image
            )
        return env_vars
```

Wire the handler into `generate_service_environment()`. Find the call to `_generate_hermes_config()` (search for it) and add this line immediately after (TEI Reranker must run **before** `_generate_lightrag_config()` so LightRAG's adaptive can read `TEI_RERANKER_ENDPOINT`):

```python
        env_vars.update(self._generate_tei_reranker_config())
```

- [ ] **Step 4: Run tests — verify pass**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_tei_reranker_config.py -v 2>&1 | tail -20`

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/services/service_config.py bootstrapper/tests/test_tei_reranker_config.py
git commit -m "feat(tei-reranker): add service_config handler + tests"
```

---

### 1.4. Task 4: CLI source flag (4 seams) for TEI Reranker

**Files:**
- Modify: `bootstrapper/start.py` (3 edits — Click decorator, main() signature, source_args dict, port-clear list)
- Modify: `bootstrapper/utils/source_override_manager.py` (source_mapping)
- Modify: `bootstrapper/tests/test_wizard_app_discovery.py` (extend existing test)

- [ ] **Step 1: Extend existing wizard-discovery test**

Open `bootstrapper/tests/test_wizard_app_discovery.py`. Find the test that checks `source_mapping` contents (search for `source_mapping_includes_app_service_flags` or similar). Add this test below it:

```python
def test_source_mapping_includes_tei_reranker():
    from bootstrapper.utils.source_override_manager import SourceOverrideManager
    mgr = SourceOverrideManager()
    assert 'tei_reranker_source' in mgr.source_mapping
    assert mgr.source_mapping['tei_reranker_source'] == 'TEI_RERANKER_SOURCE'
```

- [ ] **Step 2: Run — verify fail**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_wizard_app_discovery.py::test_source_mapping_includes_tei_reranker -v 2>&1 | tail -10`

Expected: FAIL — `'tei_reranker_source' in mgr.source_mapping` is False.

- [ ] **Step 3: Wire seam 4 — source_mapping**

Open `bootstrapper/utils/source_override_manager.py`. In the `source_mapping = {...}` dict (currently around line 21), add the entry near the alphabetically-correct position (after `'spark_master_source'` block, before `'doc_processor_source'` if alphabetical, or just append):

```python
            'tei_reranker_source': 'TEI_RERANKER_SOURCE',
```

- [ ] **Step 4: Wire seams 1+2+3 — Click decorator, main() signature, source_args dict, port-clear list**

Open `bootstrapper/start.py`. Find the `--hermes-source` Click option block (around line 1733). Add this directly after:

```python
@click.option('--tei-reranker-source',
              type=click.Choice(['container-cpu', 'container-gpu',
                                 'localhost', 'disabled'],
                                case_sensitive=False),
              help='Override TEI_RERANKER_SOURCE')
```

Find the `main()` function signature (around line 1792). Add `tei_reranker_source,` to the parameter list near the other `*_source` params.

Find the `source_args = {...}` dict inside `main()` (around line 1942). Add:

```python
        'tei_reranker_source': tei_reranker_source,
```

Find the port-clear list (search for `'HERMES_API_PORT'` — it lives in a list of ports cleared on `--base-port` change). Add:

```python
        'TEI_RERANKER_PORT',
```

- [ ] **Step 5: Run tests — verify pass**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_wizard_app_discovery.py -v 2>&1 | tail -20`

Expected: all PASS including the new test.

Run also: `PYTHONPATH=bootstrapper python -c "from bootstrapper.start import main; print('main loads ok')"`

Expected: `main loads ok` (no SyntaxError from broken signature).

- [ ] **Step 6: Commit**

```bash
git add bootstrapper/start.py bootstrapper/utils/source_override_manager.py bootstrapper/tests/test_wizard_app_discovery.py
git commit -m "feat(tei-reranker): wire --tei-reranker-source CLI flag (4 seams)"
```

---

### 1.5. Task 5: Endpoint/localhost-validator/hosts entries for TEI Reranker

**Files:**
- Modify: `bootstrapper/utils/endpoint_vars.py`
- Modify: `bootstrapper/utils/localhost_validator.py`
- Modify: `bootstrapper/utils/hosts_manager.py`

- [ ] **Step 1: Add endpoint var mapping**

Open `bootstrapper/utils/endpoint_vars.py`. Find the `LOCALHOST_ENDPOINT_VARS` dict. Add:

```python
LOCALHOST_ENDPOINT_VARS["tei-reranker"] = "TEI_RERANKER_ENDPOINT"
```

- [ ] **Step 2: Add localhost validator check**

Open `bootstrapper/utils/localhost_validator.py`. Find `SERVICE_CHECKS` dict. Add the block (matching the hermes block's shape):

```python
SERVICE_CHECKS['TEI_RERANKER_SOURCE'] = {
    'localhost': {
        'port_var': 'TEI_RERANKER_LOCALHOST_PORT',
        'default': 63031,
        'probe': '/health',
    },
}
```

- [ ] **Step 3: Add hosts manager entry**

Open `bootstrapper/utils/hosts_manager.py`. Find the `GENAI_HOSTS` list. Add:

```python
GENAI_HOSTS.append("rerank.localhost")
```

- [ ] **Step 4: Run all relevant tests**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_localhost_port_override.py bootstrapper/tests/test_kong_and_hosts_wiring.py -v 2>&1 | tail -20`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/utils/endpoint_vars.py bootstrapper/utils/localhost_validator.py bootstrapper/utils/hosts_manager.py
git commit -m "feat(tei-reranker): wire endpoint/localhost/hosts entries"
```

---

### 1.6. Task 6: Kong route for TEI Reranker

**Files:**
- Modify: `bootstrapper/utils/kong_config_generator.py`
- Modify: `scripts/check-kong-routes.py`
- Modify: `bootstrapper/tests/test_kong_alias_routes.py`

- [ ] **Step 1: Write failing test extension**

Open `bootstrapper/tests/test_kong_alias_routes.py`. Find the existing test for hermes routes (search for `hermes`). Add:

```python
def test_tei_reranker_route_generated_when_enabled(monkeypatch):
    from bootstrapper.utils.kong_config_generator import KongConfigGenerator
    monkeypatch.setenv("TEI_RERANKER_SOURCE", "container-cpu")
    gen = KongConfigGenerator()
    services = gen.get_all_services()
    matches = [s for s in services if s.get("host") == "rerank.localhost"]
    assert matches, "Expected rerank.localhost route"
    assert matches[0]["url"] == "http://tei-reranker:80/"
    # No preserve_host (pure REST inference; no SPA)
    assert matches[0].get("preserve_host") is not True


def test_tei_reranker_route_omitted_when_disabled(monkeypatch):
    from bootstrapper.utils.kong_config_generator import KongConfigGenerator
    monkeypatch.setenv("TEI_RERANKER_SOURCE", "disabled")
    gen = KongConfigGenerator()
    services = gen.get_all_services()
    assert not any(s.get("host") == "rerank.localhost" for s in services)
```

- [ ] **Step 2: Run — verify fail**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_kong_alias_routes.py -v -k tei_reranker 2>&1 | tail -15`

Expected: FAIL.

- [ ] **Step 3: Add Kong service generator**

Open `bootstrapper/utils/kong_config_generator.py`. Find `generate_hermes_service()`. Add this method after it:

```python
    def generate_tei_reranker_service(self) -> dict | None:
        """Kong route for TEI Reranker — pure REST inference, no SPA."""
        import os
        source = os.environ.get("TEI_RERANKER_SOURCE", "disabled")
        if source == "disabled":
            return None
        if source == "localhost":
            url = self._localhost_url("TEI_RERANKER_LOCALHOST_PORT", "63031")
        else:  # container-cpu | container-gpu
            url = "http://tei-reranker:80/"
        return {
            "name": "tei-reranker",
            "host": "rerank.localhost",
            "url": url,
            "preserve_host": False,
            "strip_path": False,
        }
```

Now find `get_all_services()`. Add the call (matching the alphabetical-block convention for the file):

```python
        services.append(self.generate_tei_reranker_service())
```

Filter out `None` entries at the end of `get_all_services()` if the method doesn't already (check the existing pattern; most likely you append into a list filtered by `[s for s in services if s is not None]`).

- [ ] **Step 4: Add audit script entry**

Open `scripts/check-kong-routes.py`. Find `EXPECTED_HOST_ROUTES` dict. Add:

```python
    "rerank.localhost": "http://tei-reranker:80/",
```

- [ ] **Step 5: Run tests + audit**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_kong_alias_routes.py -v -k tei_reranker 2>&1 | tail -15`

Expected: 2 passed.

Run: `python3 scripts/check-kong-routes.py`

Expected: exit 0 (route is gated on TEI_RERANKER_SOURCE != disabled, so a freshly-cloned .env should pass).

- [ ] **Step 6: Commit**

```bash
git add bootstrapper/utils/kong_config_generator.py scripts/check-kong-routes.py bootstrapper/tests/test_kong_alias_routes.py
git commit -m "feat(tei-reranker): add Kong rerank.localhost route + audit"
```

---

### 1.7. Task 7: Add TEI Reranker to compose include + regen `.env.example`

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example` (regenerated)

- [ ] **Step 1: Add include line**

Open `docker-compose.yml`. In the `# LLM tier` block, after `- services/ollama/compose.yml`, add:

```yaml
  - services/tei-reranker/compose.yml
```

- [ ] **Step 2: Validate compose**

Run: `docker compose --env-file .env.example -f docker-compose.yml config 2>&1 | grep -iE 'warning|error' | head -10`

Expected: zero output (no warnings, no errors).

- [ ] **Step 3: Regenerate `.env.example`**

Run: `PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all 2>&1 | tail -10`

This may also regen architecture diagrams; that's expected.

- [ ] **Step 4: Verify `.env.example` contains new vars**

Run: `grep '^TEI_RERANKER_' .env.example | sort`

Expected: 10 lines (SOURCE, PORT, LOCALHOST_PORT, MODEL_ID, REVISION, MAX_CLIENT_BATCH_SIZE, MEMORY_LIMIT, CPU_LIMIT, HF_CACHE_DIR, SCALE / ENDPOINT / IMAGE_RESOLVED — auto-managed ones may be blank).

If section banner is missing or vars landed in `(unsectioned)`, check that the section banner in env_assembler uses `─` (U+2500), not `=` — see memory `project_env_backfill_unicode_bar_bug`.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat(tei-reranker): include fragment in top-level compose + regen .env.example"
```

---

### 1.8. Task 8: TEI Reranker README

**Files:**
- Create: `services/tei-reranker/README.md`

- [ ] **Step 1: Create README**

```markdown
# TEI Reranker

> **Image:** `ghcr.io/huggingface/text-embeddings-inference:cpu-1.9` (CPU) / `:1.9` (GPU)
> **Container port:** 80  · **Default host port:** allocated by `topology.py` slot allocator (LLM band 63030–63039)
> **Default:** disabled

## 1. Overview

HuggingFace `text-embeddings-inference` running BAAI/bge-reranker-v2-m3 — a cross-encoder reranker that scores `(query, passage)` pairs. Use it as a quality lift on top of any first-stage retriever (vector search, BM25, hybrid). The image exposes a stable `/rerank` HTTP endpoint and a `/health` probe.

The service is reusable by consumers that send TEI's request body shape (`query` plus `texts`). Atlas does not directly wire stock LightRAG to TEI today because LightRAG's built-in Jina/Cohere rerank clients send `query` plus `documents`, which TEI rejects without an adapter.

## 2. Source variants

| Source | Container scale | Endpoint | Notes |
|---|---|---|---|
| `container-cpu` | 1 | `http://tei-reranker:80` | Default CPU image; runs on any host |
| `container-gpu` | 1 | `http://tei-reranker:80` | CUDA image; needs NVIDIA |
| `localhost` | 0 | `http://host.docker.internal:${TEI_RERANKER_LOCALHOST_PORT}` | Host-installed TEI |
| `disabled` | 0 | `""` | Reranker service off |

## 3. Configuration

```env
TEI_RERANKER_SOURCE=disabled                       # default
TEI_RERANKER_PORT=...                              # slot-allocated
TEI_RERANKER_LOCALHOST_PORT=63031                  # mirror
TEI_RERANKER_MODEL_ID=BAAI/bge-reranker-v2-m3
TEI_RERANKER_REVISION=main
TEI_RERANKER_MAX_CLIENT_BATCH_SIZE=32
TEI_RERANKER_MEMORY_LIMIT=4g
TEI_RERANKER_CPU_LIMIT=2.0
TEI_RERANKER_HF_CACHE_DIR=/data
```

## 4. Usage

```bash
# Rerank passages
curl -s http://localhost:${TEI_RERANKER_PORT}/rerank \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "What is graph-augmented RAG?",
    "texts": [
      "LightRAG combines knowledge graphs with dense vector retrieval.",
      "GraphQL is a query language.",
      "Reranking improves RAG quality by ordering retrieved passages."
    ]
  }'
# → [{"index": 0, "score": ...}, ...]
```

## 5. Dependencies & Integrations

<!-- Auto-regenerated by `python -m bootstrapper.docs.regen tei-reranker`. -->
<!-- Section content populated on next regen pass; do not hand-edit. -->

## 6. Health checks

```bash
curl -fs http://localhost:${TEI_RERANKER_PORT}/health   # 200 OK when up
```

Container `start_period` is 120 s (first run downloads the model).

## 7. Troubleshooting

- **Out of memory on CPU variant** — bump `TEI_RERANKER_MEMORY_LIMIT`. BGE-reranker-v2-m3 needs ~3 GB on CPU under typical load.
- **Slow inference** — switch to `container-gpu` if NVIDIA is available; CPU latency is ~150 ms per pair vs ~15 ms on GPU.
- **Model not found** — verify `TEI_RERANKER_MODEL_ID` matches a public HF repo. Private repos need an `HF_TOKEN` env var (not wired by default; hand-add to the compose env block).
```

- [ ] **Step 2: Regenerate the `## 5. Dependencies & Integrations` section**

Run: `PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen tei-reranker 2>&1 | tail -10`

Expected: section 5 populated.

- [ ] **Step 3: Verify docs-drift check**

Run: `python3 scripts/check-docs-drift.py 2>&1 | tail -15`

Expected: PASS for tei-reranker (other drift may exist from other services; new service should not produce drift).

- [ ] **Step 4: Commit**

```bash
git add services/tei-reranker/README.md
git commit -m "docs(tei-reranker): add service README"
```

---

## 2. Phase 2 — LightRAG manifest + storage

### 2.1. Task 9: LightRAG `service.yml`

**Files:**
- Create: `services/lightrag/service.yml`

- [ ] **Step 1: Create the manifest**

```bash
mkdir -p services/lightrag
```

```yaml
# services/lightrag/service.yml
# LightRAG — graph-augmented RAG server.
# Storage backends adapt to Supabase/Neo4j/Redis when enabled, fall back to
# in-process file backends when those sources are disabled. LLM + embedding
# bindings inherit from LiteLLM defaults (resolved at startup by lightrag-init).
name: lightrag
label: "LightRAG (graph-augmented RAG server)"
category: agents
docs: services/lightrag/README.md

containers:
  - lightrag
  - lightrag-init

images:
  - var: LIGHTRAG_IMAGE
    default: "ghcr.io/hkuds/lightrag:1.5.0"
    container: lightrag
  - var: LIGHTRAG_INIT_IMAGE
    default: "alpine:latest"
    container: lightrag-init

sources:
  var: LIGHTRAG_SOURCE
  default: disabled
  options:
    - id: container
      label: "Container (in-stack LightRAG)"
    - id: localhost
      label: "Host (existing LightRAG on this machine)"
    - id: disabled
      label: "Disabled"

env:
  - name: LIGHTRAG_SOURCE
    default: disabled
  - name: LIGHTRAG_API_PORT
    # default computed by services/topology.py slot allocator
    description: "Host port for LightRAG (in-container 9621)."
  - name: LIGHTRAG_LOCALHOST_PORT
    default: "63068"
    description: "Host port for the localhost source variant."
  - name: LIGHTRAG_API_KEY
    default: ""
    secret: true
    description: "Auto-generated bearer key. Forwarded to LiteLLM as the model api_key."
  - name: LIGHTRAG_WORKERS
    default: 2
  - name: LIGHTRAG_MEMORY_LIMIT
    default: 6g
  - name: LIGHTRAG_CPU_LIMIT
    default: "2.0"
  - name: LIGHTRAG_LLM_BINDING
    default: openai
  - name: LIGHTRAG_LLM_BINDING_HOST
    default: "http://litellm:4000/v1"
  - name: LIGHTRAG_LLM_MODEL
    default: ""
    description: "Empty = lightrag-init resolves from LITELLM_DEFAULT_MODEL."
  - name: LIGHTRAG_EMBEDDING_BINDING
    default: openai
  - name: LIGHTRAG_EMBEDDING_BINDING_HOST
    default: "http://litellm:4000/v1"
  - name: LIGHTRAG_EMBEDDING_MODEL
    default: ""
    description: "Empty = lightrag-init resolves from LITELLM_EMBEDDING_MODEL."
  - name: LIGHTRAG_EMBEDDING_DIM
    default: 768
  - name: LIGHTRAG_VLM_PROCESS_ENABLE
    default: "true"
  - name: LIGHTRAG_KV_STORAGE
    default: RedisKVStorage
  - name: LIGHTRAG_VECTOR_STORAGE
    default: PGVectorStorage
  - name: LIGHTRAG_GRAPH_STORAGE
    default: Neo4JStorage
  - name: LIGHTRAG_DOC_STATUS_STORAGE
    default: RedisKVStorage
  - name: LIGHTRAG_SCALE
    auto_managed: true
  - name: LIGHTRAG_INIT_SCALE
    auto_managed: true
  - name: LIGHTRAG_ENDPOINT
    auto_managed: true
    description: "Consumed by hermes/n8n/backend runtime_adaptive and LiteLLM model_list."
  - name: LIGHTRAG_RERANK_BINDING_HOST
    auto_managed: true
  - name: LIGHTRAG_DOCLING_ENDPOINT
    auto_managed: true
  - name: LIGHTRAG_PG_URI
    auto_managed: true
  - name: LIGHTRAG_NEO4J_URI
    auto_managed: true
  - name: LIGHTRAG_NEO4J_USERNAME
    auto_managed: true
  - name: LIGHTRAG_NEO4J_PASSWORD
    auto_managed: true
  - name: LIGHTRAG_REDIS_URI
    auto_managed: true

depends_on:
  required:
    - litellm
  optional:
    - supabase
    - neo4j
    - redis
    - docling
    - tei-reranker

rows:
  - display_name: "LightRAG"
    source_var: LIGHTRAG_SOURCE
    port_var: LIGHTRAG_API_PORT
    scale_var: LIGHTRAG_SCALE
    alias: lightrag.localhost
    description: "Graph-augmented RAG server with WebUI, KG extractor, multimodal ingestion."
    localhost_endpoint_var: LIGHTRAG_ENDPOINT
    localhost_port_var: LIGHTRAG_LOCALHOST_PORT

exports: []

runtime_sc:
  lightrag:
    container:
      scale: 1
      environment:
        LIGHTRAG_ENDPOINT: "http://lightrag:9621"
      deploy: {}
      extra_hosts: []
    localhost:
      scale: 0
      environment:
        LIGHTRAG_ENDPOINT: "http://host.docker.internal:${LIGHTRAG_LOCALHOST_PORT:-63068}"
      deploy: {}
      extra_hosts:
        - "host.docker.internal:host-gateway"
    disabled:
      scale: 0
      environment:
        LIGHTRAG_ENDPOINT: ""
      deploy: {}
      extra_hosts: []
  lightrag-init:
    container:
      scale: 1
      environment: {}
      deploy: {}
      extra_hosts: []
    disabled:
      scale: 0
      environment: {}
      deploy: {}
      extra_hosts: []

runtime_adaptive:
  lightrag:
    adapts_to:
      - doc_processor
      - tei_reranker
      - supabase
      - neo4j
      - redis
    environment_adaptation:
      LIGHTRAG_DOCLING_ENDPOINT: ${DOCLING_ENDPOINT}
      LIGHTRAG_RERANK_BINDING_HOST: ""
      LIGHTRAG_PG_URI: "postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@supabase-db:5432/${POSTGRES_DB}"
      LIGHTRAG_NEO4J_URI: "bolt://neo4j:7687"
      LIGHTRAG_NEO4J_USERNAME: neo4j
      LIGHTRAG_NEO4J_PASSWORD: ${NEO4J_PASSWORD}
      LIGHTRAG_REDIS_URI: "redis://:${REDIS_PASSWORD}@redis:6379/2"
    extra_hosts_adaptation: none
    failure_mode: "Storage falls back to NanoVectorDB / NetworkX / JsonKV when supabase/neo4j/redis source is disabled. Reranker omitted when disabled. Docling skipped → multimodal images become text-only."
  lightrag-init:
    adapts_to:
      - llm_provider
    environment_adaptation:
      LITELLM_DEFAULT_MODEL: ${LITELLM_DEFAULT_MODEL}
      LITELLM_EMBEDDING_MODEL: ${LITELLM_EMBEDDING_MODEL}
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
    extra_hosts_adaptation: inherit from llm_provider
    failure_mode: "lightrag-init exits non-zero; LightRAG container does not start. Required because lightrag-init resolves model names from LiteLLM's /v1/models."

runtime_deps:
  lightrag:
    optional:
      - supabase
      - neo4j
      - redis
      - docling
      - tei-reranker
    info_message: "LightRAG enabled — will wire optional storage and capability services when available."

data_flow:
  calls:
    - litellm
    - supabase
    - neo4j
    - redis
    - docling
    - tei-reranker
```

- [ ] **Step 2: Validate manifest**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_manifest_validator.py bootstrapper/tests/test_manifests.py -v 2>&1 | tail -20`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add services/lightrag/service.yml
git commit -m "feat(lightrag): add service.yml manifest"
```

---

### 2.2. Task 10: LightRAG `compose.yml`

**Files:**
- Create: `services/lightrag/compose.yml`

- [ ] **Step 1: Create the fragment**

```yaml
# services/lightrag/compose.yml
# LightRAG fragment. Every env var declared in runtime_sc.lightrag.<src>.environment
# AND every adaptive var (LIGHTRAG_DOCLING_ENDPOINT, etc.) MUST also appear in
# the environment block below — memory: project_runtime_sc_vs_compose_env_dual_write.
services:
  lightrag:
    image: ${LIGHTRAG_IMAGE:-ghcr.io/hkuds/lightrag:1.5.0}
    container_name: ${PROJECT_NAME}-lightrag
    deploy:
      replicas: ${LIGHTRAG_SCALE:-0}
      resources:
        limits:
          memory: ${LIGHTRAG_MEMORY_LIMIT:-6g}
          cpus: '${LIGHTRAG_CPU_LIMIT:-2.0}'
    ports:
      - "${LIGHTRAG_API_PORT}:9621"
    environment:
      LIGHTRAG_ENDPOINT: ${LIGHTRAG_ENDPOINT}
      HOST: 0.0.0.0
      PORT: 9621
      WORKERS: ${LIGHTRAG_WORKERS:-2}
      LIGHTRAG_API_KEY: ${LIGHTRAG_API_KEY}
      LLM_BINDING: ${LIGHTRAG_LLM_BINDING:-openai}
      LLM_BINDING_HOST: ${LIGHTRAG_LLM_BINDING_HOST:-http://litellm:4000/v1}
      LLM_BINDING_API_KEY: ${LITELLM_MASTER_KEY}
      LLM_MODEL: ${LIGHTRAG_LLM_MODEL}
      EMBEDDING_BINDING: ${LIGHTRAG_EMBEDDING_BINDING:-openai}
      EMBEDDING_BINDING_HOST: ${LIGHTRAG_EMBEDDING_BINDING_HOST:-http://litellm:4000/v1}
      EMBEDDING_BINDING_API_KEY: ${LITELLM_MASTER_KEY}
      EMBEDDING_MODEL: ${LIGHTRAG_EMBEDDING_MODEL}
      EMBEDDING_DIM: ${LIGHTRAG_EMBEDDING_DIM:-768}
      VLM_PROCESS_ENABLE: ${LIGHTRAG_VLM_PROCESS_ENABLE:-true}
      LIGHTRAG_KV_STORAGE: ${LIGHTRAG_KV_STORAGE:-RedisKVStorage}
      LIGHTRAG_VECTOR_STORAGE: ${LIGHTRAG_VECTOR_STORAGE:-PGVectorStorage}
      LIGHTRAG_GRAPH_STORAGE: ${LIGHTRAG_GRAPH_STORAGE:-Neo4JStorage}
      LIGHTRAG_DOC_STATUS_STORAGE: ${LIGHTRAG_DOC_STATUS_STORAGE:-RedisKVStorage}
      POSTGRES_URI: ${LIGHTRAG_PG_URI}
      NEO4J_URI: ${LIGHTRAG_NEO4J_URI}
      NEO4J_USERNAME: ${LIGHTRAG_NEO4J_USERNAME}
      NEO4J_PASSWORD: ${LIGHTRAG_NEO4J_PASSWORD}
      REDIS_URI: ${LIGHTRAG_REDIS_URI}
      LIGHTRAG_PARSER: "*:native-teP,*:legacy-R"
      DOCLING_ENDPOINT: ${LIGHTRAG_DOCLING_ENDPOINT}
      RERANK_BINDING: ${LIGHTRAG_RERANK_BINDING:-null}
      RERANK_BINDING_HOST: ${LIGHTRAG_RERANK_BINDING_HOST}
      RERANK_MODEL: BAAI/bge-reranker-v2-m3
    volumes:
      - lightrag-data:/app/data
    networks:
      - backend-network
    depends_on:
      lightrag-init:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "curl", "-fs", "http://localhost:9621/health"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 300s
    restart: unless-stopped

  lightrag-init:
    image: ${LIGHTRAG_INIT_IMAGE:-alpine:latest}
    container_name: ${PROJECT_NAME}-lightrag-init
    deploy:
      replicas: ${LIGHTRAG_INIT_SCALE:-0}
    entrypoint: ["sh", "-c", "/scripts/init-lightrag.sh"]
    environment:
      LIGHTRAG_SOURCE: ${LIGHTRAG_SOURCE}
      LITELLM_DEFAULT_MODEL: ${LITELLM_DEFAULT_MODEL}
      LITELLM_EMBEDDING_MODEL: ${LITELLM_EMBEDDING_MODEL}
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
      LIGHTRAG_PG_URI: ${LIGHTRAG_PG_URI}
      LIGHTRAG_NEO4J_URI: ${LIGHTRAG_NEO4J_URI}
      LIGHTRAG_NEO4J_USERNAME: ${LIGHTRAG_NEO4J_USERNAME}
      LIGHTRAG_NEO4J_PASSWORD: ${LIGHTRAG_NEO4J_PASSWORD}
      LIGHTRAG_REDIS_URI: ${LIGHTRAG_REDIS_URI}
    volumes:
      - ./init/scripts:/scripts:ro
      - lightrag-data:/app/data
    networks:
      - backend-network
    restart: "no"

volumes:
  lightrag-data:
    name: ${PROJECT_NAME}-lightrag-data
```

- [ ] **Step 2: Verify no self-doubling path**

Run: `grep -E '\./services/lightrag' services/lightrag/compose.yml`

Expected: no matches.

- [ ] **Step 3: Commit**

```bash
git add services/lightrag/compose.yml
git commit -m "feat(lightrag): add compose.yml fragment"
```

---

### 2.3. Task 11: LightRAG init scripts

**Files:**
- Create: `services/lightrag/init/scripts/init-lightrag.sh`
- Create: `services/lightrag/init/scripts/resolve-models.py`
- Create: `services/lightrag/init/scripts/migrate-pgvector.sql`
- Create: `services/lightrag/init/scripts/migrate-neo4j.cypher`

- [ ] **Step 1: Create init shell wrapper**

```bash
mkdir -p services/lightrag/init/scripts
```

```bash
# services/lightrag/init/scripts/init-lightrag.sh
#!/bin/sh
# LightRAG init container.
# Pattern: alpine + inline apk add (memory: project_init_container_pattern).
# Bash re-exec with sentinel to avoid loop.
set -e

if [ "${INIT_BOOTSTRAPPED:-}" != "1" ]; then
  apk add --no-cache bash curl jq postgresql-client ca-certificates >/dev/null
  export INIT_BOOTSTRAPPED=1
  exec bash -- "$0" "$@"
fi

# ── bash body ─────────────────────────────────────────────────────────────
set -euo pipefail

if [ "${LIGHTRAG_SOURCE:-disabled}" = "disabled" ]; then
  echo "[lightrag-init] LIGHTRAG_SOURCE=disabled, nothing to do"
  exit 0
fi

echo "[lightrag-init] waiting for LiteLLM /v1/models..."
deadline=$((SECONDS + 60))
until curl -fs -H "Authorization: Bearer ${LITELLM_MASTER_KEY:-}" \
            http://litellm:4000/v1/models >/dev/null 2>&1; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "[lightrag-init] FAIL: LiteLLM not reachable after 60s" >&2
    exit 1
  fi
  sleep 2
done

echo "[lightrag-init] resolving model bindings..."
python3 /scripts/resolve-models.py > /app/data/.lightrag-resolved.env

if [ -n "${LIGHTRAG_PG_URI:-}" ]; then
  echo "[lightrag-init] running pgvector migrations..."
  psql "${LIGHTRAG_PG_URI}" -v ON_ERROR_STOP=1 -f /scripts/migrate-pgvector.sql
fi

if [ -n "${LIGHTRAG_NEO4J_URI:-}" ]; then
  echo "[lightrag-init] running Neo4j migrations..."
  # Use cypher-shell from apk (not installed by default — install on demand).
  if ! command -v cypher-shell >/dev/null 2>&1; then
    apk add --no-cache cypher-shell >/dev/null 2>&1 || {
      # cypher-shell not in alpine main; fall back to a curl-based HTTP submission.
      curl -fs -u "${LIGHTRAG_NEO4J_USERNAME}:${LIGHTRAG_NEO4J_PASSWORD}" \
        -H 'Content-Type: application/json' \
        --data "$(jq -Rn --rawfile q /scripts/migrate-neo4j.cypher \
                  '{statements: [{statement: $q}]}')" \
        "http://neo4j:7474/db/neo4j/tx/commit" >/dev/null
    }
  fi
fi

echo "[lightrag-init] done"
```

- [ ] **Step 2: Create model resolver**

```python
# services/lightrag/init/scripts/resolve-models.py
"""Resolve LightRAG's LLM/embedding model names + embedding dim from LiteLLM.

Reads LiteLLM's /v1/models, picks LITELLM_DEFAULT_MODEL for chat/VLM and
LITELLM_EMBEDDING_MODEL for embedding, computes the embedding dimension from
a known lookup table (or by issuing a probe embedding), and emits
KEY=VALUE lines on stdout for the calling shell to consume.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

LITELLM_URL = "http://litellm:4000/v1/models"
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")

# Known embedding dims for the commonly-used models in this stack. If the
# resolved model isn't here, fall back to a probe embedding.
KNOWN_DIMS = {
    "nomic-embed-text": 768,
    "ollama/nomic-embed-text": 768,
    "bge-m3": 1024,
    "BAAI/bge-m3": 1024,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


def fetch_models() -> list[str]:
    req = urllib.request.Request(
        LITELLM_URL,
        headers={"Authorization": f"Bearer {MASTER_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = json.loads(r.read().decode("utf-8"))
        return [m["id"] for m in payload.get("data", [])]
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"# WARN could not fetch /v1/models: {e}", file=sys.stderr)
        return []


def resolve_dim(model: str) -> int:
    # Direct hit
    if model in KNOWN_DIMS:
        return KNOWN_DIMS[model]
    # Substring match (e.g. "ollama/bge-m3" matches "bge-m3")
    for key, dim in KNOWN_DIMS.items():
        if key in model:
            return dim
    # Probe embedding fallback
    try:
        req = urllib.request.Request(
            "http://litellm:4000/v1/embeddings",
            data=json.dumps({"input": "probe", "model": model}).encode(),
            headers={
                "Authorization": f"Bearer {MASTER_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read().decode("utf-8"))
        return len(payload["data"][0]["embedding"])
    except Exception as e:
        print(f"# WARN dim probe failed for {model}: {e}", file=sys.stderr)
        return 768  # safe fallback for nomic-embed-text


def main() -> None:
    available = fetch_models()
    chat = os.environ.get("LITELLM_DEFAULT_MODEL", "").strip()
    embed = os.environ.get("LITELLM_EMBEDDING_MODEL", "").strip()
    if not chat and available:
        chat = available[0]
    if not embed:
        # Prefer anything with "embed" in the name
        embed_candidates = [m for m in available if "embed" in m.lower()]
        embed = embed_candidates[0] if embed_candidates else "ollama/nomic-embed-text"
    dim = resolve_dim(embed)
    print(f"LIGHTRAG_LLM_MODEL={chat}")
    print(f"LIGHTRAG_EMBEDDING_MODEL={embed}")
    print(f"LIGHTRAG_EMBEDDING_DIM={dim}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create pgvector migration**

```sql
-- services/lightrag/init/scripts/migrate-pgvector.sql
-- Idempotent pgvector schema setup for LightRAG.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS lightrag;

-- Chunks vector table. The actual dimension is configured by LightRAG at
-- runtime via PGVectorStorage; we provision a generic placeholder that the
-- LightRAG storage layer will manage. If LightRAG creates tables on first
-- run, this no-ops harmlessly.
CREATE TABLE IF NOT EXISTS lightrag.vectors_meta (
    schema_version int NOT NULL DEFAULT 1,
    applied_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO lightrag.vectors_meta (schema_version) VALUES (1)
ON CONFLICT DO NOTHING;
```

- [ ] **Step 4: Create Neo4j migration**

```cypher
// services/lightrag/init/scripts/migrate-neo4j.cypher
// Idempotent Neo4j constraints + indexes for LightRAG's graph store.

CREATE CONSTRAINT lightrag_entity_id IF NOT EXISTS
FOR (n:Entity) REQUIRE n.id IS UNIQUE;

CREATE INDEX lightrag_entity_name IF NOT EXISTS
FOR (n:Entity) ON (n.name);

CREATE INDEX lightrag_relation_predicate IF NOT EXISTS
FOR ()-[r:RELATION]-() ON (r.predicate);
```

- [ ] **Step 5: Make shell script executable + smoke-test**

Run: `chmod +x services/lightrag/init/scripts/init-lightrag.sh`

Smoke-test the apk-installable path (per memory `project_init_container_pattern`):

```bash
docker run --rm \
  -v "$(pwd)/services/lightrag/init/scripts:/scripts:ro" \
  -e LIGHTRAG_SOURCE=disabled \
  alpine:latest /scripts/init-lightrag.sh
```

Expected: exits 0, prints `LIGHTRAG_SOURCE=disabled, nothing to do`.

- [ ] **Step 6: Commit**

```bash
git add services/lightrag/init/
git commit -m "feat(lightrag): add init scripts (alpine + apk pattern)"
```

---

### 2.4. Task 12: `_generate_lightrag_config()` handler + tests

**Files:**
- Create: `bootstrapper/tests/test_lightrag_config.py`
- Modify: `bootstrapper/services/service_config.py`

- [ ] **Step 1: Write failing tests**

```python
# bootstrapper/tests/test_lightrag_config.py
"""Tests for _generate_lightrag_config()."""
from __future__ import annotations

import pytest
from bootstrapper.services.service_config import ServiceConfig


@pytest.fixture
def base_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "PROJECT_NAME=atlas\n"
        "LIGHTRAG_LOCALHOST_PORT=63068\n",
        encoding="utf-8",
    )
    return env


def _make(env_path, source):
    sc = ServiceConfig(env_file=env_path, localhost_host="localhost")
    sc.service_sources = {"LIGHTRAG_SOURCE": source}
    return sc


def test_disabled_clears_endpoint_and_scales(base_env):
    sc = _make(base_env, "disabled")
    env = sc._generate_lightrag_config()
    assert env["LIGHTRAG_ENDPOINT"] == ""
    assert env["LIGHTRAG_SCALE"] == "0"
    assert env["LIGHTRAG_INIT_SCALE"] == "0"


def test_container_endpoint_and_scales(base_env):
    sc = _make(base_env, "container")
    env = sc._generate_lightrag_config()
    assert env["LIGHTRAG_ENDPOINT"] == "http://lightrag:9621"
    assert env["LIGHTRAG_SCALE"] == "1"
    assert env["LIGHTRAG_INIT_SCALE"] == "1"


def test_localhost_uses_LIGHTRAG_LOCALHOST_PORT(base_env):
    sc = _make(base_env, "localhost")
    env = sc._generate_lightrag_config()
    assert env["LIGHTRAG_ENDPOINT"] == "http://localhost:63068"
    assert env["LIGHTRAG_SCALE"] == "0"
    assert env["LIGHTRAG_INIT_SCALE"] == "0"
```

- [ ] **Step 2: Run — verify fail**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_lightrag_config.py -v 2>&1 | tail -15`

Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement the handler**

Open `bootstrapper/services/service_config.py`. Add this method immediately after `_generate_tei_reranker_config` (from Task 3):

```python
    def _generate_lightrag_config(self) -> Dict[str, str]:
        """Resolve LightRAG endpoint and init scale per source.

        Storage URI adaptation (PG/Neo4j/Redis) happens in
        _generate_adaptive_services_config since those are listed in
        service.yml::runtime_adaptive.environment_adaptation.
        """
        source_value = self.service_sources.get('LIGHTRAG_SOURCE', 'disabled')
        env_vars: Dict[str, str] = {}
        if source_value == 'disabled':
            env_vars['LIGHTRAG_ENDPOINT'] = ''
            env_vars['LIGHTRAG_SCALE'] = '0'
            env_vars['LIGHTRAG_INIT_SCALE'] = '0'
        elif source_value == 'localhost':
            current_env = self.config_parser.parse_env_file()
            port = current_env.get('LIGHTRAG_LOCALHOST_PORT', '63068')
            env_vars['LIGHTRAG_ENDPOINT'] = f'http://{self.localhost_host}:{port}'
            env_vars['LIGHTRAG_SCALE'] = '0'
            env_vars['LIGHTRAG_INIT_SCALE'] = '0'
        else:  # container
            cfg = self.get_service_config('lightrag', source_value)
            endpoint = cfg.get('environment', {}).get(
                'LIGHTRAG_ENDPOINT', 'http://lightrag:9621'
            )
            env_vars['LIGHTRAG_ENDPOINT'] = endpoint.replace(
                'host.docker.internal', self.localhost_host
            )
            env_vars['LIGHTRAG_SCALE'] = '1'
            env_vars['LIGHTRAG_INIT_SCALE'] = '1'
        return env_vars
```

Wire into `generate_service_environment()`. Add this call **after** `_generate_tei_reranker_config()` (so TEI_RERANKER_ENDPOINT is resolved before any future adapter-owned LightRAG rerank substitution):

```python
        env_vars.update(self._generate_lightrag_config())
```

- [ ] **Step 4: Run tests — verify pass**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_lightrag_config.py -v 2>&1 | tail -15`

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/services/service_config.py bootstrapper/tests/test_lightrag_config.py
git commit -m "feat(lightrag): add service_config handler + tests"
```

---

### 2.5. Task 13: CLI source flag (4 seams) for LightRAG

**Files:**
- Modify: `bootstrapper/start.py`
- Modify: `bootstrapper/utils/source_override_manager.py`
- Modify: `bootstrapper/tests/test_wizard_app_discovery.py`

- [ ] **Step 1: Write failing test**

Add to `bootstrapper/tests/test_wizard_app_discovery.py`:

```python
def test_source_mapping_includes_lightrag():
    from bootstrapper.utils.source_override_manager import SourceOverrideManager
    mgr = SourceOverrideManager()
    assert 'lightrag_source' in mgr.source_mapping
    assert mgr.source_mapping['lightrag_source'] == 'LIGHTRAG_SOURCE'
```

- [ ] **Step 2: Run — verify fail**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_wizard_app_discovery.py::test_source_mapping_includes_lightrag -v 2>&1 | tail -10`

Expected: FAIL.

- [ ] **Step 3: Wire seam 4**

Open `bootstrapper/utils/source_override_manager.py`. Add to `source_mapping`:

```python
            'lightrag_source': 'LIGHTRAG_SOURCE',
```

- [ ] **Step 4: Wire seams 1+2+3 in start.py**

Open `bootstrapper/start.py`. Add Click decorator near `--hermes-source`:

```python
@click.option('--lightrag-source',
              type=click.Choice(['container', 'localhost', 'disabled'],
                                case_sensitive=False),
              help='Override LIGHTRAG_SOURCE')
```

Add `lightrag_source` to the `main()` signature parameter list.

Add to `source_args` dict:

```python
        'lightrag_source': lightrag_source,
```

Add to the port-clear list:

```python
        'LIGHTRAG_API_PORT',
```

- [ ] **Step 5: Run tests — verify pass**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_wizard_app_discovery.py -v 2>&1 | tail -15`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bootstrapper/start.py bootstrapper/utils/source_override_manager.py bootstrapper/tests/test_wizard_app_discovery.py
git commit -m "feat(lightrag): wire --lightrag-source CLI flag (4 seams)"
```

---

### 2.6. Task 14: Endpoint/validator/hosts entries + Kong route for LightRAG

**Files:**
- Modify: `bootstrapper/utils/endpoint_vars.py`
- Modify: `bootstrapper/utils/localhost_validator.py`
- Modify: `bootstrapper/utils/hosts_manager.py`
- Modify: `bootstrapper/utils/kong_config_generator.py`
- Modify: `scripts/check-kong-routes.py`
- Modify: `bootstrapper/tests/test_kong_alias_routes.py`

- [ ] **Step 1: Write failing Kong test**

Add to `bootstrapper/tests/test_kong_alias_routes.py`:

```python
def test_lightrag_route_generated_with_preserve_host(monkeypatch):
    from bootstrapper.utils.kong_config_generator import KongConfigGenerator
    monkeypatch.setenv("LIGHTRAG_SOURCE", "container")
    monkeypatch.setenv("LIGHTRAG_LOCALHOST_PORT", "63068")
    gen = KongConfigGenerator()
    services = gen.get_all_services()
    matches = [s for s in services if s.get("host") == "lightrag.localhost"]
    assert matches, "Expected lightrag.localhost route"
    assert matches[0]["url"] == "http://lightrag:9621/"
    # SPA at /webui — preserve_host required
    assert matches[0]["preserve_host"] is True


def test_lightrag_route_omitted_when_disabled(monkeypatch):
    from bootstrapper.utils.kong_config_generator import KongConfigGenerator
    monkeypatch.setenv("LIGHTRAG_SOURCE", "disabled")
    gen = KongConfigGenerator()
    services = gen.get_all_services()
    assert not any(s.get("host") == "lightrag.localhost" for s in services)
```

- [ ] **Step 2: Run — verify fail**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_kong_alias_routes.py -v -k lightrag 2>&1 | tail -15`

Expected: FAIL.

- [ ] **Step 3: Add endpoint var mapping + validator + hosts**

In `bootstrapper/utils/endpoint_vars.py`:

```python
LOCALHOST_ENDPOINT_VARS["lightrag"] = "LIGHTRAG_ENDPOINT"
```

In `bootstrapper/utils/localhost_validator.py`:

```python
SERVICE_CHECKS['LIGHTRAG_SOURCE'] = {
    'localhost': {
        'port_var': 'LIGHTRAG_LOCALHOST_PORT',
        'default': 63068,
        'probe': '/health',
    },
}
```

In `bootstrapper/utils/hosts_manager.py`:

```python
GENAI_HOSTS.append("lightrag.localhost")
```

- [ ] **Step 4: Add Kong route generator**

In `bootstrapper/utils/kong_config_generator.py`, add method after `generate_hermes_service()`:

```python
    def generate_lightrag_service(self) -> dict | None:
        """Kong route for LightRAG — WebUI SPA at /webui, preserve_host required."""
        import os
        source = os.environ.get("LIGHTRAG_SOURCE", "disabled")
        if source == "disabled":
            return None
        if source == "localhost":
            url = self._localhost_url("LIGHTRAG_LOCALHOST_PORT", "63068")
        else:  # container
            url = "http://lightrag:9621/"
        return {
            "name": "lightrag",
            "host": "lightrag.localhost",
            "url": url,
            "preserve_host": True,
            "strip_path": False,
        }
```

Call from `get_all_services()`:

```python
        services.append(self.generate_lightrag_service())
```

- [ ] **Step 5: Audit script entry**

In `scripts/check-kong-routes.py`, add to `EXPECTED_HOST_ROUTES`:

```python
    "lightrag.localhost": "http://lightrag:9621/",
```

- [ ] **Step 6: Run all relevant tests + audit**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_kong_alias_routes.py bootstrapper/tests/test_kong_and_hosts_wiring.py -v 2>&1 | tail -15`

Expected: PASS.

Run: `python3 scripts/check-kong-routes.py`

Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add bootstrapper/utils/endpoint_vars.py bootstrapper/utils/localhost_validator.py bootstrapper/utils/hosts_manager.py bootstrapper/utils/kong_config_generator.py scripts/check-kong-routes.py bootstrapper/tests/test_kong_alias_routes.py
git commit -m "feat(lightrag): add Kong lightrag.localhost route + endpoint/host wiring"
```

---

### 2.7. Task 15: LightRAG API key generator

**Files:**
- Modify: `bootstrapper/utils/key_generator.py`
- Modify: `bootstrapper/tests/test_cli_safe_secret_generation.py` (or create dedicated test)

- [ ] **Step 1: Write failing test**

Append to `bootstrapper/tests/test_cli_safe_secret_generation.py`:

```python
def test_generate_lightrag_api_key_returns_prefixed_secret():
    from bootstrapper.utils.key_generator import generate_lightrag_api_key
    key = generate_lightrag_api_key()
    assert key.startswith("sk-lightrag-")
    assert len(key) > len("sk-lightrag-") + 20  # token_urlsafe entropy floor
```

- [ ] **Step 2: Run — verify fail**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_cli_safe_secret_generation.py -v -k lightrag 2>&1 | tail -10`

Expected: FAIL.

- [ ] **Step 3: Add generator + updater**

In `bootstrapper/utils/key_generator.py`, find `generate_hermes_api_key`. Add immediately after:

```python
def generate_lightrag_api_key() -> str:
    """Bearer key for LightRAG /api endpoints. Forwarded to LiteLLM."""
    return f"sk-lightrag-{secrets.token_urlsafe(32)}"


def generate_and_update_lightrag_api_key(env_path: Path) -> None:
    """Generate LIGHTRAG_API_KEY and write to env_path if missing."""
    current = _parse_env(env_path)
    if current.get("LIGHTRAG_API_KEY"):
        return
    key = generate_lightrag_api_key()
    _update_env(env_path, "LIGHTRAG_API_KEY", key)
```

Find `generate_missing_keys()`. Add the gated call:

```python
    if current.get("LIGHTRAG_SOURCE", "disabled") != "disabled" and \
            not current.get("LIGHTRAG_API_KEY"):
        generate_and_update_lightrag_api_key(env_path)
```

- [ ] **Step 4: Run test — verify pass**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_cli_safe_secret_generation.py -v -k lightrag 2>&1 | tail -10`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/utils/key_generator.py bootstrapper/tests/test_cli_safe_secret_generation.py
git commit -m "feat(lightrag): add API key generator"
```

---

### 2.8. Task 16: Add LightRAG to compose include + regen `.env.example`

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example` (regenerated)

- [ ] **Step 1: Add include line**

Open `docker-compose.yml`. In the App tier / agents block (where hermes lives), add (alphabetical: between hermes and n8n):

```yaml
  - services/lightrag/compose.yml
```

- [ ] **Step 2: Validate compose**

Run: `docker compose --env-file .env.example -f docker-compose.yml config 2>&1 | grep -iE 'warning|error' | head -10`

Expected: zero output.

- [ ] **Step 3: Regenerate `.env.example`**

Run: `PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all 2>&1 | tail -10`

- [ ] **Step 4: Verify .env.example contents**

Run: `grep '^LIGHTRAG_' .env.example | sort | wc -l`

Expected: ≥20 lines (every env var declared in service.yml::env).

Run: `grep 'LIGHTRAG_' .env.example | head -5`

Expected: section banner uses `─` (U+2500), not `=`.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat(lightrag): include fragment in top-level compose + regen .env.example"
```

---

### 2.9. Task 17: LightRAG README

**Files:**
- Create: `services/lightrag/README.md`

- [ ] **Step 1: Create README**

```markdown
# LightRAG

> **Image:** `ghcr.io/hkuds/lightrag:1.5.0`
> **Container port:** 9621 (API + WebUI)  · **Default host port:** allocated by `topology.py` (agents band 63060–63079)
> **Default:** disabled

## 1. Overview

[LightRAG](https://github.com/HKUDS/LightRAG) is a graph-augmented RAG server. It ingests documents (PDF, Office, images, tables, equations — multimodal pipeline absorbed from RAG-Anything in v1.5.0), extracts a knowledge graph via LLM-driven entity/relation extraction, embeds chunks and entities into a vector store, and exposes a unified query API that combines graph traversal with vector search.

In this stack, LightRAG reuses existing infrastructure:

- **LLM + embeddings** routed through LiteLLM (`LLM_BINDING_HOST=http://litellm:4000/v1`).
- **Vector store** → Supabase pgvector (`PGVectorStorage`).
- **Graph store** → Neo4j (`Neo4JStorage`).
- **KV + doc-status** → Redis (`RedisKVStorage`).
- **Document parsing** → Docling (when `DOC_PROCESSOR_SOURCE != disabled`).
- **Reranker** → TEI Reranker (when `TEI_RERANKER_SOURCE != disabled`).

When any of these backends is disabled, LightRAG transparently falls back to in-process file backends (`NanoVectorDBStorage` / `NetworkXStorage` / `JsonKVStorage`). Multimodal images become text-only when docling is disabled.

## 2. Source variants

| Source | Scale | Endpoint | Notes |
|---|---|---|---|
| `container` | 1 | `http://lightrag:9621` | In-stack LightRAG |
| `localhost` | 0 | `http://host.docker.internal:${LIGHTRAG_LOCALHOST_PORT}` | Host-installed LightRAG |
| `disabled` | 0 | `""` | LightRAG off; consumers see empty endpoint |

## 3. Configuration

Storage selectors and model bindings can be overridden via `.env`:

```env
LIGHTRAG_SOURCE=disabled                            # default
LIGHTRAG_KV_STORAGE=RedisKVStorage                  # alt: JsonKVStorage
LIGHTRAG_VECTOR_STORAGE=PGVectorStorage             # alt: NanoVectorDBStorage, QdrantVectorDBStorage, ...
LIGHTRAG_GRAPH_STORAGE=Neo4JStorage                 # alt: NetworkXStorage, MemgraphStorage, AGEStorage
LIGHTRAG_DOC_STATUS_STORAGE=RedisKVStorage          # alt: JsonKVStorage
LIGHTRAG_LLM_MODEL=                                 # empty = inherit LITELLM_DEFAULT_MODEL
LIGHTRAG_EMBEDDING_MODEL=                           # empty = inherit LITELLM_EMBEDDING_MODEL
LIGHTRAG_VLM_PROCESS_ENABLE=true                    # vision LLM for images/figures
```

## 4. Usage

### 4a. Web UI

Browse `http://lightrag.localhost:${KONG_HTTP_PORT}` (after `--setup-hosts`) or `http://localhost:${LIGHTRAG_API_PORT}/webui`. Upload documents, view the KG, run queries.

### 4b. Native API

```bash
# Insert a document
curl -sX POST http://localhost:${LIGHTRAG_API_PORT}/documents/upload \
  -H "Authorization: Bearer ${LIGHTRAG_API_KEY}" \
  -F "file=@my-paper.pdf"

# Query
curl -sX POST http://localhost:${LIGHTRAG_API_PORT}/query \
  -H "Authorization: Bearer ${LIGHTRAG_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"query": "/hybrid What is graph-augmented RAG?"}'
```

Query mode prefixes: `/hybrid`, `/local`, `/global`, `/naive`, `/mix`. Default is `/hybrid`.

### 4c. Via LiteLLM (recommended for other stack services)

LightRAG is registered with LiteLLM as the `lightrag` model when enabled. Any LiteLLM consumer (open-webui, openclaw, n8n, hermes, backend, local-deep-researcher, jupyterhub) can invoke it:

```bash
curl -sX POST http://localhost:${LITELLM_PORT}/v1/chat/completions \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "lightrag",
    "messages": [
      {"role": "user", "content": "/hybrid What is graph-augmented RAG?"}
    ]
  }'
```

## 5. Dependencies & Integrations

<!-- Auto-regenerated by `python -m bootstrapper.docs.regen lightrag`. -->
<!-- Section content populated on next regen pass; do not hand-edit. -->

## 6. Storage backend matrix

| Storage role | Default backend | In-process fallback (when source disabled) |
|---|---|---|
| KV | Redis `db=2` | JsonKVStorage (`/app/data/kv/*.json`) |
| Vector | Supabase pgvector | NanoVectorDBStorage (`/app/data/vectors/*.json`) |
| Graph | Neo4j | NetworkXStorage (`/app/data/graph/*.graphml`) |
| Doc-status | Redis `db=2` | JsonKVStorage |

## 7. Init container

`lightrag-init` runs once per `docker compose up`. It:

1. Waits for LiteLLM `/v1/models` (60 s timeout).
2. Resolves `LIGHTRAG_LLM_MODEL` / `LIGHTRAG_EMBEDDING_MODEL` / `LIGHTRAG_EMBEDDING_DIM` from LiteLLM.
3. Runs idempotent pgvector + Neo4j migrations.

## 8. Troubleshooting

- **First boot exceeds health-check timeout** — `start_period` is 300 s. Initial model downloads (tokenizer + embedding model + reranker) can take up to 5 min.
- **`OPENAI_API_KEY` warning at startup** — LightRAG checks env even when using `openai`-compatible Ollama. Harmless; the actual key is the `LITELLM_MASTER_KEY` forwarded as `LLM_BINDING_API_KEY`.
- **Empty KG after ingestion** — verify `LIGHTRAG_LLM_MODEL` actually points at a chat-capable model. Some embedding-only Ollama tags will silently produce empty triples.
- **`pgvector` dim mismatch** — drop and rerun the migration when changing `LIGHTRAG_EMBEDDING_DIM`: `psql ... -c "DROP SCHEMA lightrag CASCADE"` then restart.
```

- [ ] **Step 2: Regenerate `## 5` section + diagrams**

Run: `PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen lightrag 2>&1 | tail -10`

- [ ] **Step 3: Run docs-drift check**

Run: `python3 scripts/check-docs-drift.py 2>&1 | tail -15`

Expected: no drift for lightrag.

- [ ] **Step 4: Commit**

```bash
git add services/lightrag/README.md services/lightrag/architecture.html services/lightrag/architecture.svg
git commit -m "docs(lightrag): add service README + regen diagrams"
```

---

### 2.10. Task 18: End-to-end env generation across all source values

**Files:**
- Modify: `bootstrapper/tests/test_source_permutations.py` (extend) OR
- Create: `bootstrapper/tests/test_lightrag_tei_source_permutations.py`

- [ ] **Step 1: Write the multi-source matrix test**

```python
# bootstrapper/tests/test_lightrag_tei_source_permutations.py
"""End-to-end env generation across LightRAG + TEI Reranker source values."""
from __future__ import annotations

import shutil
import pytest
from pathlib import Path
from bootstrapper.services.service_config import ServiceConfig


REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"


@pytest.fixture
def env_copy(tmp_path):
    env = tmp_path / ".env"
    shutil.copy(ENV_EXAMPLE, env)
    return env


@pytest.mark.parametrize("lightrag_source", ["disabled", "container", "localhost"])
def test_lightrag_env_generation_each_source(env_copy, lightrag_source):
    sc = ServiceConfig(env_file=env_copy, localhost_host="localhost")
    sc.service_sources = {
        "LIGHTRAG_SOURCE": lightrag_source,
        "TEI_RERANKER_SOURCE": "disabled",
    }
    env = sc.generate_service_environment()
    assert "LIGHTRAG_ENDPOINT" in env
    assert "LIGHTRAG_SCALE" in env
    if lightrag_source == "disabled":
        assert env["LIGHTRAG_ENDPOINT"] == ""
        assert env["LIGHTRAG_SCALE"] == "0"
    elif lightrag_source == "container":
        assert "lightrag" in env["LIGHTRAG_ENDPOINT"]
        assert env["LIGHTRAG_SCALE"] == "1"
    elif lightrag_source == "localhost":
        assert "localhost:63068" in env["LIGHTRAG_ENDPOINT"]
        assert env["LIGHTRAG_SCALE"] == "0"


@pytest.mark.parametrize("tei_source", [
    "disabled", "container-cpu", "container-gpu", "localhost"
])
def test_tei_reranker_env_generation_each_source(env_copy, tei_source):
    sc = ServiceConfig(env_file=env_copy, localhost_host="localhost")
    sc.service_sources = {
        "LIGHTRAG_SOURCE": "disabled",
        "TEI_RERANKER_SOURCE": tei_source,
    }
    env = sc.generate_service_environment()
    assert "TEI_RERANKER_ENDPOINT" in env
    assert "TEI_RERANKER_SCALE" in env
    if tei_source == "disabled":
        assert env["TEI_RERANKER_ENDPOINT"] == ""
        assert env["TEI_RERANKER_SCALE"] == "0"
    elif tei_source == "localhost":
        assert "localhost:63031" in env["TEI_RERANKER_ENDPOINT"]
    else:  # container variants
        assert env["TEI_RERANKER_ENDPOINT"] == "http://tei-reranker:80"
        assert env["TEI_RERANKER_SCALE"] == "1"


def test_lightrag_adaptive_disables_direct_tei_rerank(env_copy):
    sc = ServiceConfig(env_file=env_copy, localhost_host="localhost")
    sc.service_sources = {
        "LIGHTRAG_SOURCE": "container",
        "TEI_RERANKER_SOURCE": "container-cpu",
    }
    env = sc.generate_service_environment()
    # Direct LightRAG->TEI rerank is disabled unless a compatible adapter is introduced.
    assert env.get("LIGHTRAG_RERANK_BINDING_HOST", "") == ""
    assert env.get("LIGHTRAG_RERANK_BINDING") == "null"


def test_lightrag_adaptive_blanks_rerank_when_tei_disabled(env_copy):
    sc = ServiceConfig(env_file=env_copy, localhost_host="localhost")
    sc.service_sources = {
        "LIGHTRAG_SOURCE": "container",
        "TEI_RERANKER_SOURCE": "disabled",
    }
    env = sc.generate_service_environment()
    assert env.get("LIGHTRAG_RERANK_BINDING_HOST", "") == ""
```

- [ ] **Step 2: Run — verify pass**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_lightrag_tei_source_permutations.py -v 2>&1 | tail -25`

Expected: 9 passed.

- [ ] **Step 3: Run `--base-port` test**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_port_migration.py bootstrapper/tests/test_blank_base_port.py -v 2>&1 | tail -15`

Expected: PASS (both LightRAG + TEI Reranker ports relocate under `--base-port`).

- [ ] **Step 4: Commit**

```bash
git add bootstrapper/tests/test_lightrag_tei_source_permutations.py
git commit -m "test(lightrag,tei-reranker): add source-permutation matrix tests"
```

---

## 3. Phase 3 — Cross-service adaptive wiring

### 3.1. Task 19: Add `lightrag` to hermes/n8n/backend `runtime_adaptive.adapts_to`

**Files:**
- Modify: `services/hermes/service.yml`
- Modify: `services/n8n/service.yml`
- Modify: `services/backend/service.yml`
- Create: `bootstrapper/tests/test_hermes_n8n_backend_adapts_to_lightrag.py`

- [ ] **Step 1: Write failing test**

```python
# bootstrapper/tests/test_hermes_n8n_backend_adapts_to_lightrag.py
"""Assert hermes, n8n, backend declare lightrag in runtime_adaptive.adapts_to."""
from __future__ import annotations

import yaml
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> dict:
    text = (REPO_ROOT / "services" / name / "service.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


@pytest.mark.parametrize("svc,container,expected_env_var", [
    ("hermes", "hermes-init", "LIGHTRAG_INTERNAL_URL"),
    ("n8n", "n8n", "LIGHTRAG_ENDPOINT"),
    ("backend", "backend", "LIGHTRAG_ENDPOINT"),
])
def test_adapts_to_includes_lightrag(svc, container, expected_env_var):
    data = _load(svc)
    block = data["runtime_adaptive"][container]
    assert "lightrag" in block["adapts_to"], \
        f"{svc}.{container}.runtime_adaptive.adapts_to missing 'lightrag'"
    assert expected_env_var in block["environment_adaptation"], \
        f"{svc}.{container} missing env var {expected_env_var}"
```

- [ ] **Step 2: Run — verify fail**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_hermes_n8n_backend_adapts_to_lightrag.py -v 2>&1 | tail -15`

Expected: 3 failures.

- [ ] **Step 3: Patch hermes service.yml**

In `services/hermes/service.yml`, find `runtime_adaptive.hermes-init.adapts_to`. Add `- lightrag` to the list. Add to the `environment_adaptation` dict:

```yaml
      LIGHTRAG_INTERNAL_URL: ${LIGHTRAG_ENDPOINT}
```

In `services/hermes/service.yml::env`, add:

```yaml
  - name: LIGHTRAG_INTERNAL_URL
    auto_managed: true
```

Update `services/hermes/init/templates/config.yaml.tmpl` (or the rendering script that consumes it) to conditionally emit a `rag_query` tool block when `LIGHTRAG_INTERNAL_URL` is non-empty.

First inspect how `COMFYUI_INTERNAL_URL` is gated to pick up the exact syntax:

```bash
grep -nE 'COMFYUI_INTERNAL_URL|comfyui' services/hermes/init/templates/config.yaml.tmpl services/hermes/init/scripts/init-hermes.sh
```

Then replicate the exact gating pattern for `LIGHTRAG_INTERNAL_URL`. The block emitted when non-empty should be:

```yaml
  - name: rag_query
    endpoint: ${LIGHTRAG_INTERNAL_URL}/query
    api_key: ${LIGHTRAG_API_KEY}
    description: "Graph-augmented RAG query (LightRAG)."
```

If `services/hermes/init/templates/config.yaml.tmpl` is a Go-template-style file (the existing pattern uses `{{- if env "..." }}` blocks), use:

```yaml
{{- if env "LIGHTRAG_INTERNAL_URL" }}
  - name: rag_query
    endpoint: ${LIGHTRAG_INTERNAL_URL}/query
    api_key: ${LIGHTRAG_API_KEY}
    description: "Graph-augmented RAG query (LightRAG)."
{{- end }}
```

If the template is a Python-rendered file using a shell-style conditional in `init-hermes.sh`, replicate the corresponding shell logic (`if [ -n "${LIGHTRAG_INTERNAL_URL}" ]; then cat <<EOF >> "$out" ... EOF; fi`).

Also extend `services/hermes/service.yml::env` with:

```yaml
  - name: LIGHTRAG_API_KEY
    auto_managed: true
```

so the key flows through env_assembler into hermes-init's env.

- [ ] **Step 4: Patch n8n service.yml**

In `services/n8n/service.yml`, add to `runtime_adaptive.n8n.adapts_to`:

```yaml
    - lightrag
```

Add to `environment_adaptation`:

```yaml
      LIGHTRAG_ENDPOINT: ${LIGHTRAG_ENDPOINT}
      LIGHTRAG_API_KEY: ${LIGHTRAG_API_KEY}
```

In `services/n8n/compose.yml`, find the n8n service's `environment:` block and add (per dual-write rule):

```yaml
      LIGHTRAG_ENDPOINT: ${LIGHTRAG_ENDPOINT}
      LIGHTRAG_API_KEY: ${LIGHTRAG_API_KEY}
```

- [ ] **Step 5: Patch backend service.yml**

In `services/backend/service.yml`, add to `runtime_adaptive.backend.adapts_to`:

```yaml
    - lightrag
```

Add to `environment_adaptation`:

```yaml
      LIGHTRAG_ENDPOINT: ${LIGHTRAG_ENDPOINT}
      LIGHTRAG_API_KEY: ${LIGHTRAG_API_KEY}
```

In `services/backend/compose.yml`, add to the backend service's `environment:` block:

```yaml
      LIGHTRAG_ENDPOINT: ${LIGHTRAG_ENDPOINT}
      LIGHTRAG_API_KEY: ${LIGHTRAG_API_KEY}
```

- [ ] **Step 6: Regenerate diagrams + run tests**

Run: `PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen hermes n8n backend 2>&1 | tail -10`

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_hermes_n8n_backend_adapts_to_lightrag.py -v 2>&1 | tail -15`

Expected: 3 passed.

Run: `docker compose --env-file .env.example -f docker-compose.yml config 2>&1 | grep -iE 'warning|error' | head -5`

Expected: zero output.

- [ ] **Step 7: Commit**

```bash
git add services/hermes/ services/n8n/ services/backend/ bootstrapper/tests/test_hermes_n8n_backend_adapts_to_lightrag.py
git commit -m "feat(lightrag): wire hermes/n8n/backend runtime_adaptive consumers"
```

---

## 4. Phase 4 — LiteLLM model registration

### 4.1. Task 20: `lightrag_model_entry()` in LiteLLM init

**Files:**
- Modify: `services/litellm/init/scripts/init.py`
- Create: `bootstrapper/tests/test_lightrag_litellm_registration.py`

- [ ] **Step 1: Write failing tests**

```python
# bootstrapper/tests/test_lightrag_litellm_registration.py
"""Tests for lightrag_model_entry()."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_PY = REPO_ROOT / "services/litellm/init/scripts/init.py"


def _load_init_module():
    spec = importlib.util.spec_from_file_location("litellm_init", INIT_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["litellm_init"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("LIGHTRAG_SOURCE", "disabled")
    mod = _load_init_module()
    assert mod.lightrag_model_entry() is None


def test_returns_entry_when_enabled(monkeypatch):
    monkeypatch.setenv("LIGHTRAG_SOURCE", "container")
    monkeypatch.setenv("LIGHTRAG_ENDPOINT", "http://lightrag:9621")
    monkeypatch.setenv("LIGHTRAG_API_KEY", "sk-lightrag-test")
    mod = _load_init_module()
    entry = mod.lightrag_model_entry()
    assert entry is not None
    assert entry["model_name"] == "lightrag"
    assert entry["litellm_params"]["api_base"] == "http://lightrag:9621/api"
    assert entry["litellm_params"]["api_key"] == "sk-lightrag-test"


def test_adapter_is_openai_not_ollama_chat(monkeypatch):
    monkeypatch.setenv("LIGHTRAG_SOURCE", "container")
    monkeypatch.setenv("LIGHTRAG_ENDPOINT", "http://lightrag:9621")
    monkeypatch.setenv("LIGHTRAG_API_KEY", "sk-lightrag-test")
    mod = _load_init_module()
    entry = mod.lightrag_model_entry()
    assert entry["litellm_params"]["model"].startswith("openai/")
    assert "ollama_chat" not in entry["litellm_params"]["model"]
```

- [ ] **Step 2: Run — verify fail**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_lightrag_litellm_registration.py -v 2>&1 | tail -15`

Expected: 3 failures.

- [ ] **Step 3: Implement `lightrag_model_entry()`**

Open `services/litellm/init/scripts/init.py`. Find `hermes_model_entry()` (around line 275). Add this function immediately after it:

```python
def lightrag_model_entry() -> dict[str, Any] | None:
    """Return a model_list entry for `lightrag` when LightRAG is enabled.

    Adapter is openai/ (not ollama_chat/) because LightRAG's Ollama-shim
    implements /api/chat with OpenAI-style messages and the ollama_chat
    adapter expects the full Ollama tag-listing protocol.
    """
    if os.environ.get("LIGHTRAG_SOURCE", "disabled") == "disabled":
        return None
    endpoint = os.environ.get("LIGHTRAG_ENDPOINT", "")
    if not endpoint:
        return None
    return {
        "model_name": "lightrag",
        "litellm_params": {
            "model": "openai/lightrag",
            "api_base": f"{endpoint.rstrip('/')}/api",
            "api_key": os.environ.get("LIGHTRAG_API_KEY", "sk-no-auth"),
        },
        "model_info": {
            "mode": "chat",
            "description": (
                "LightRAG graph-augmented RAG. Encode query mode as "
                "system prompt prefix /hybrid|/local|/global|/naive|/mix."
            ),
        },
    }
```

Now find `build_config()` (around line 304). Find the line that adds the hermes entry to `model_list`. Add the lightrag entry similarly:

```python
    lightrag_entry = lightrag_model_entry()
    if lightrag_entry is not None:
        model_list.append(lightrag_entry)
```

- [ ] **Step 4: Run tests — verify pass**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_lightrag_litellm_registration.py -v 2>&1 | tail -15`

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add services/litellm/init/scripts/init.py bootstrapper/tests/test_lightrag_litellm_registration.py
git commit -m "feat(lightrag): register as 'lightrag' model in LiteLLM model_list"
```

---

## 5. Phase 5 — Audits + tests

### 5.1. Task 21: Update `check-compose-source-deps.py`

**Files:**
- Modify: `scripts/check-compose-source-deps.py`

- [ ] **Step 1: Inspect existing structure**

Run: `head -80 scripts/check-compose-source-deps.py | tail -40`

You'll see two sets: `FORBIDDEN_OPTIONAL_DEPENDS_ON` (edges that must NOT exist as hard compose `depends_on` since the target is source-replaceable) and `REQUIRED_DEPENDS_ON` (edges that MUST exist for boot ordering).

- [ ] **Step 2: Add entries**

Open `scripts/check-compose-source-deps.py`. Add to `FORBIDDEN_OPTIONAL_DEPENDS_ON` (LightRAG's compose intentionally does NOT hard-depend on litellm/supabase/neo4j/redis/docling/tei-reranker since each can be source=localhost/disabled):

```python
    ("lightrag", "litellm"),
    ("lightrag", "supabase"),
    ("lightrag", "neo4j"),
    ("lightrag", "redis"),
    ("lightrag", "docling"),
    ("lightrag", "tei-reranker"),
    ("lightrag-init", "litellm"),
```

Add to `REQUIRED_DEPENDS_ON` (LightRAG container MUST wait for its init to complete):

```python
    ("lightrag", "lightrag-init"),
```

- [ ] **Step 3: Run audit**

Run: `python3 scripts/check-compose-source-deps.py`

Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add scripts/check-compose-source-deps.py
git commit -m "chore(audit): add LightRAG source-deps audit pairs"
```

---

### 5.2. Task 22: Extend `test_localhost_port_consumer_symmetry.py`

**Files:**
- Modify: `bootstrapper/tests/test_localhost_port_consumer_symmetry.py`

- [ ] **Step 1: Add new ports to the canonical list**

Open the file. Find the list of `*_LOCALHOST_PORT` variables (or the assertion that lists them). Add:

```python
    "LIGHTRAG_LOCALHOST_PORT",
    "TEI_RERANKER_LOCALHOST_PORT",
```

- [ ] **Step 2: Run**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_localhost_port_consumer_symmetry.py -v 2>&1 | tail -20`

Expected: PASS for both new ports. If the test fails on a missing consumer site, verify all four sites are wired:
- `runtime_sc.<svc>.localhost.environment` (service.yml)
- Kong `_localhost_url()` call (kong_config_generator.py)
- `_generate_<svc>_config()` localhost branch (service_config.py)
- `SERVICE_CHECKS[*]` (localhost_validator.py)

- [ ] **Step 3: Commit**

```bash
git add bootstrapper/tests/test_localhost_port_consumer_symmetry.py
git commit -m "test: extend localhost-port-consumer-symmetry for LightRAG + TEI Reranker"
```

---

### 5.3. Task 23: Document the no-model-picker decision

**Files:**
- Modify: `bootstrapper/tests/test_user_model_selections_seam_parity.py`

- [ ] **Step 1: Add explanatory comment**

Open the file. At the top of the test class or module, add:

```python
# LightRAG and TEI Reranker (added 2026-06-05) intentionally have NO model
# picker step in the wizard. LightRAG inherits LITELLM_DEFAULT_MODEL /
# LITELLM_EMBEDDING_MODEL via lightrag-init at startup; TEI Reranker uses a
# static TEI_RERANKER_MODEL_ID default. Neither needs the --<svc>-models
# four-seam pattern guarded by this file's tests. Do not add them here.
```

- [ ] **Step 2: Commit**

```bash
git add bootstrapper/tests/test_user_model_selections_seam_parity.py
git commit -m "test: document no-picker exclusion for LightRAG + TEI Reranker"
```

---

### 5.4. Task 24: Refresh `test_fragment_equivalence` baseline (CI-artifact dance)

This is the two-cycle dance from memory `project_baseline_regen_via_ci_artifact`. **Skip this task if running locally on Docker Compose v2.x matching CI**; only required when local Docker version diverges.

**Files:**
- Modify: `.github/workflows/services-lint.yml` (temp artifact upload step)
- Modify: `bootstrapper/tests/fixtures/rendered_config_baseline.yml`

- [ ] **Step 1: Check the local-vs-CI baseline drift**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_fragment_equivalence.py -v 2>&1 | tail -30`

Expected if local matches CI: PASS.
Expected if local diverges: FAIL with line-count diff in the hundreds. If so, continue with the CI-artifact dance below; otherwise commit the locally-regenerated baseline (regen via `python -m bootstrapper.tools.validate_fragments --regen-baseline`).

- [ ] **Step 2: CI-artifact dance (only if local diverges)**

Add temporary step to `.github/workflows/services-lint.yml` under the relevant job:

```yaml
      - name: Upload rendered baseline (temporary)
        uses: actions/upload-artifact@v4
        with:
          name: rendered_config_baseline
          path: bootstrapper/tests/fixtures/rendered_config_baseline.yml.regen
```

Add a step that runs the regen target:

```bash
PYTHONPATH=bootstrapper python -m bootstrapper.tools.validate_fragments --emit-baseline > bootstrapper/tests/fixtures/rendered_config_baseline.yml.regen
```

Commit + push the workflow-temp change. Wait for the workflow run. Download the artifact, copy to `bootstrapper/tests/fixtures/rendered_config_baseline.yml`. Revert the temp workflow step.

- [ ] **Step 3: Run + commit**

Run: `PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/test_fragment_equivalence.py -v 2>&1 | tail -10`

Expected: PASS.

```bash
git add bootstrapper/tests/fixtures/rendered_config_baseline.yml
git commit -m "test: refresh fragment-equivalence baseline for LightRAG + TEI Reranker"
```

---

## 6. Phase 6 — Documentation surface

### 6.1. Task 25: Update top-level docs

**Files:**
- Modify: `docs/README.md`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/deployment/ports-and-routes.md`
- Modify: `docs/deployment/source-configuration.md`
- Modify: `docs/quick-start/interactive-setup-wizard.md`

- [ ] **Step 1: `docs/README.md` services index**

Add two rows in the services index table (alphabetical):

```markdown
| [LightRAG](../services/lightrag/README.md) | agents | Graph-augmented RAG server with WebUI + multimodal ingestion |
| [TEI Reranker](../services/tei-reranker/README.md) | llm | BGE-reranker-v2-m3 inference for RAG quality lift |
```

- [ ] **Step 2: `docs/CHANGELOG.md`**

Under `[Unreleased]`, add:

```markdown
### Added (LightRAG service)
- New `services/lightrag/` manifest: graph-augmented RAG server pinned to `ghcr.io/hkuds/lightrag:1.5.0`. Default `disabled`.
- Storage adapts to Supabase pgvector, Neo4j, Redis with in-process fallback when any backend source is `disabled`.
- Registered with LiteLLM as the `lightrag` model (Ollama-shim served via OpenAI adapter); reachable transitively by open-webui, openclaw, n8n, hermes, backend, local-deep-researcher, jupyterhub.
- Wired into `runtime_adaptive` of hermes/n8n/backend for direct calls.
- Kong route `lightrag.localhost` with `preserve_host: True` (WebUI SPA).
- Init container resolves LLM/embedding model + dim from LiteLLM `/v1/models` at boot.
- RAG-Anything is NOT added — subsumed by LightRAG v1.5.0's multimodal pipeline.

### Added (TEI Reranker service)
- New `services/tei-reranker/` manifest: HF text-embeddings-inference running BAAI/bge-reranker-v2-m3. Default `disabled`. Four source variants: `container-cpu`, `container-gpu`, `localhost`, `disabled`.
- Consumed optionally by LightRAG (`RERANK_BINDING_HOST`); reusable by any future service.
- Kong route `rerank.localhost`.
```

- [ ] **Step 3: `docs/deployment/ports-and-routes.md`**

Add four rows (and the two Kong hostnames):

```markdown
| `LIGHTRAG_API_PORT` | agents | `lightrag.localhost` | LightRAG API + WebUI |
| `LIGHTRAG_LOCALHOST_PORT` | agents | — | LightRAG host-installed port mirror |
| `TEI_RERANKER_PORT` | llm | `rerank.localhost` | TEI rerank API |
| `TEI_RERANKER_LOCALHOST_PORT` | llm | — | TEI rerank host-installed port mirror |
```

- [ ] **Step 4: `docs/deployment/source-configuration.md`**

Add two matrix rows (alphabetical) and two dedicated subsections.

Matrix rows:

```markdown
| `LIGHTRAG_SOURCE` | `container` / `localhost` / `disabled` | `disabled` |
| `TEI_RERANKER_SOURCE` | `container-cpu` / `container-gpu` / `localhost` / `disabled` | `disabled` |
```

Dedicated subsections:

```markdown
### `LIGHTRAG_SOURCE`

LightRAG runs out-of-process as either an in-stack container or a host-installed process.

- **`container`** — Pulls `ghcr.io/hkuds/lightrag:1.5.0` and runs it on `backend-network`. Storage backends are adapted from existing services (Supabase pgvector, Neo4j, Redis); when any of those is `disabled`, LightRAG falls back to in-process file backends.
- **`localhost`** — Expects an existing LightRAG running on the host at `LIGHTRAG_LOCALHOST_PORT` (default 63068). Backend-network consumers reach it via `host.docker.internal`.
- **`disabled`** — `LIGHTRAG_ENDPOINT` empties; hermes/n8n/backend skip the LightRAG capability; LiteLLM's `model_list` omits the `lightrag` entry.

### `TEI_RERANKER_SOURCE`

Inference server for BGE-reranker-v2-m3. Reusable by compatible consumers that send TEI's `query` plus `texts` request body.

- **`container-cpu`** — `ghcr.io/huggingface/text-embeddings-inference:cpu-1.9`. Runs anywhere; ~150 ms per pair latency.
- **`container-gpu`** — `:1.9` image with NVIDIA reservation. ~15 ms per pair on RTX-class GPU.
- **`localhost`** — Existing TEI process on host at `TEI_RERANKER_LOCALHOST_PORT` (default 63031).
- **`disabled`** — `TEI_RERANKER_ENDPOINT` empties; LightRAG's `RERANK_BINDING` is emitted as `null` so LightRAG disables reranking instead of crashing on an empty binding.
```

- [ ] **Step 5: `docs/quick-start/interactive-setup-wizard.md`**

Add two rows to the wizard options table:

```markdown
| LightRAG | `container` / `localhost` / `disabled` | `disabled` | Graph-augmented RAG server |
| TEI Reranker | `container-cpu` / `container-gpu` / `localhost` / `disabled` | `disabled` | BGE-reranker-v2-m3 inference |
```

- [ ] **Step 6: Verify docs-drift**

Run: `python3 scripts/check-docs-drift.py 2>&1 | tail -10`

Expected: PASS for the new entries.

- [ ] **Step 7: Commit**

```bash
git add docs/README.md docs/CHANGELOG.md docs/deployment/ports-and-routes.md docs/deployment/source-configuration.md docs/quick-start/interactive-setup-wizard.md
git commit -m "docs: index LightRAG + TEI Reranker; ports/routes/wizard tables"
```

---

### 6.2. Task 26: Update root README.md (5 places)

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Find and update the 5 places**

Run: `grep -nE 'hermes|openclaw|airflow' README.md | head -20`

This identifies the 5 places where service lists appear:

1. **Architecture diagram caption** — add "LightRAG + TEI Reranker" alongside other 2026-06-05 additions.
2. **Localhost source list** — add `LIGHTRAG_LOCALHOST_PORT=63068`, `TEI_RERANKER_LOCALHOST_PORT=63031` to the localhost-default list.
3. **Service URL table** — add rows:

```markdown
| LightRAG | `http://lightrag.localhost:${KONG_HTTP_PORT}` (WebUI), `http://localhost:${LIGHTRAG_API_PORT}/webui` |
| TEI Reranker | `http://localhost:${TEI_RERANKER_PORT}/rerank` (API only) |
```

4. **Service descriptions** — short bullets:

```markdown
- **LightRAG** — graph-augmented RAG server. KG + vector + multimodal ingestion. Default disabled.
- **TEI Reranker** — BGE-reranker-v2-m3 inference for RAG quality lift. Default disabled.
```

5. **CLI examples** — append to source-flag examples:

```bash
# Enable LightRAG with default backends (uses Supabase + Neo4j + Redis)
./start.sh --lightrag-source=container --tei-reranker-source=container-cpu
```

- [ ] **Step 2: Run docs-drift**

Run: `python3 scripts/check-docs-drift.py 2>&1 | tail -10`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): add LightRAG + TEI Reranker to root README (5 places)"
```

---

### 6.3. Task 27: Cross-reference READMEs

**Files:**
- Modify: `services/kong/README.md`
- Modify: `services/hermes/README.md`
- Modify: `services/n8n/README.md`
- Modify: `services/backend/README.md`
- Modify: `services/litellm/README.md`
- Modify: `services/supabase/README.md`
- Modify: `services/neo4j/README.md`
- Modify: `services/redis/README.md`

- [ ] **Step 1: Per-service additions**

For each file, add a short cross-reference subsection. Concrete additions:

**`services/kong/README.md`** — add to the route list:

```markdown
- `lightrag.localhost` → http://lightrag:9621/ (LightRAG WebUI + API; `preserve_host` enabled)
- `rerank.localhost` → http://tei-reranker:80/ (TEI rerank API)

Example: `curl http://lightrag.localhost:${KONG_HTTP_PORT}/health`
```

**`services/hermes/README.md`** — add under §8:

```markdown
### RAG capability via LightRAG

When `LIGHTRAG_SOURCE != disabled`, hermes-init injects `LIGHTRAG_INTERNAL_URL` into `/opt/data/config.yaml` as a `rag_query` tool. Hermes can call LightRAG's `/query` endpoint with the configured `LIGHTRAG_API_KEY`. Disabled when LightRAG is off.
```

**`services/n8n/README.md`** — add a subsection:

```markdown
### Calling LightRAG from n8n

When `LIGHTRAG_SOURCE != disabled`, the env vars `LIGHTRAG_ENDPOINT` and `LIGHTRAG_API_KEY` are injected into n8n containers. Use the HTTP Request node:

- URL: `={{$env.LIGHTRAG_ENDPOINT}}/query`
- Auth: Bearer token from `={{$env.LIGHTRAG_API_KEY}}`
- Body (JSON): `{"query": "/hybrid Your question"}`
```

**`services/backend/README.md`** — add:

```markdown
### LightRAG integration

When `LIGHTRAG_SOURCE != disabled`, the backend receives `LIGHTRAG_ENDPOINT` and `LIGHTRAG_API_KEY` env vars. No `/rag` route is currently implemented; future PRs can add one without manifest changes.
```

**`services/litellm/README.md`** — add a subsection:

```markdown
### Built-in `lightrag` model

When `LIGHTRAG_SOURCE != disabled`, `litellm-init` registers a `lightrag` model that proxies to LightRAG's Ollama-shim (`{LIGHTRAG_ENDPOINT}/api`). Encode the query mode in the user message prefix: `/hybrid`, `/local`, `/global`, `/naive`, `/mix`. Default mode is `/hybrid`.

```bash
curl -sX POST http://localhost:${LITELLM_PORT}/v1/chat/completions \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
  -d '{"model":"lightrag","messages":[{"role":"user","content":"/hybrid What is RAG?"}]}'
```
```

**`services/supabase/README.md`** — add:

```markdown
### LightRAG schema

When `LIGHTRAG_SOURCE != disabled` AND `SUPABASE_SOURCE != disabled`, `lightrag-init` runs `migrate-pgvector.sql` which provisions `CREATE EXTENSION IF NOT EXISTS vector` and a `lightrag` schema. LightRAG's `PGVectorStorage` manages tables under that schema at runtime.
```

**`services/neo4j/README.md`** — add:

```markdown
### LightRAG graph store

When `LIGHTRAG_SOURCE != disabled` AND `NEO4J_GRAPH_DB_SOURCE != disabled`, `lightrag-init` provisions `Entity` constraints and indexes. LightRAG writes the extracted KG (entities + relations) to Neo4j. Browse at `neo4j.localhost:${KONG_HTTP_PORT}`.
```

**`services/redis/README.md`** — add:

```markdown
### LightRAG KV store

When `LIGHTRAG_SOURCE != disabled` AND `REDIS_SOURCE != disabled`, LightRAG uses Redis `db=2` as its KV and doc-status backend (via `RedisKVStorage`). Use `redis-cli -n 2 KEYS '*'` to inspect.
```

- [ ] **Step 2: Run docs-drift + regen**

Run: `PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all 2>&1 | tail -10`

Run: `python3 scripts/check-docs-drift.py 2>&1 | tail -10`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add services/kong/README.md services/hermes/README.md services/n8n/README.md services/backend/README.md services/litellm/README.md services/supabase/README.md services/neo4j/README.md services/redis/README.md
git commit -m "docs: cross-reference LightRAG/TEI Reranker in consumer READMEs"
```

---

## 7. Phase 7 — Verification matrix + live smoke

### 7.1. Task 28: Run the full verification matrix

**Files:** (verification only — no edits)

- [ ] **Step 1: Compose config**

```bash
docker compose --env-file .env.example -f docker-compose.yml config 2>&1 | grep -i warning
```

Expected: zero output.

- [ ] **Step 2: Source-deps audit**

```bash
python3 scripts/check-compose-source-deps.py
```

Expected: exit 0.

- [ ] **Step 3: Kong routes audit**

```bash
python3 scripts/check-kong-routes.py
```

Expected: exit 0.

- [ ] **Step 4: Docs-drift audit**

```bash
python3 scripts/check-docs-drift.py
```

Expected: PASS.

- [ ] **Step 5: Regen drift gate**

```bash
PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all --check
echo "exit=$?"
```

Expected: `exit=0`.

- [ ] **Step 6: Full pytest run**

```bash
PYTHONPATH=bootstrapper python -m pytest bootstrapper/tests/ -v 2>&1 | tail -40
```

Expected: all PASS.

- [ ] **Step 7: `--base-port 64000` test**

```bash
cp .env.example /tmp/.env.base64000
PYTHONPATH=bootstrapper python -c "
from bootstrapper.core.port_manager import PortManager
pm = PortManager()
ports = pm.port_defaults_for(64000)
assert ports['LIGHTRAG_API_PORT'] >= 64060, ports
assert ports['LIGHTRAG_API_PORT'] < 64080, ports
assert ports['TEI_RERANKER_PORT'] >= 64030, ports
assert ports['TEI_RERANKER_PORT'] < 64040, ports
print('OK', ports['LIGHTRAG_API_PORT'], ports['TEI_RERANKER_PORT'])
"
```

Expected: `OK <lightrag-port> <tei-port>` with both ports in the expected band.

- [ ] **Step 8: Init smoke test (alpine pattern)**

```bash
docker run --rm \
  -v "$(pwd)/services/lightrag/init/scripts:/scripts:ro" \
  -e LIGHTRAG_SOURCE=disabled \
  alpine:latest /scripts/init-lightrag.sh
```

Expected: prints "nothing to do" and exits 0.

- [ ] **Step 9: Commit any audit-driven fixes**

If steps 1–8 surface drift or audit failures, fix them in-place and commit. If all pass, no commit needed.

```bash
# only if fixes needed:
git add -A
git commit -m "chore: address verification-matrix findings"
```

---

### 7.2. Task 29: Live smoke test (manual)

**Files:** (manual verification only — no edits)

- [ ] **Step 1: Enable both services in .env**

```bash
cp .env.example .env
sed -i.bak 's/^LIGHTRAG_SOURCE=.*/LIGHTRAG_SOURCE=container/' .env
sed -i.bak 's/^TEI_RERANKER_SOURCE=.*/TEI_RERANKER_SOURCE=container-cpu/' .env
rm .env.bak
```

- [ ] **Step 2: Launch the stack**

```bash
./start.sh
```

Expected: stack comes up; LightRAG's first health check is satisfied within `start_period` (300 s — initial weight downloads).

- [ ] **Step 3: Health checks**

```bash
LIGHTRAG_API_PORT=$(grep '^LIGHTRAG_API_PORT=' .env | cut -d= -f2)
LIGHTRAG_API_KEY=$(grep '^LIGHTRAG_API_KEY=' .env | cut -d= -f2)
LITELLM_PORT=$(grep '^LITELLM_PORT=' .env | cut -d= -f2)
LITELLM_MASTER_KEY=$(grep '^LITELLM_MASTER_KEY=' .env | cut -d= -f2)

curl -sH "Authorization: Bearer $LIGHTRAG_API_KEY" \
  "http://localhost:$LIGHTRAG_API_PORT/health"
```

Expected: `200 OK` with a JSON status body.

- [ ] **Step 4: LiteLLM model registration check**

```bash
curl -sH "Authorization: Bearer $LITELLM_MASTER_KEY" \
  "http://localhost:$LITELLM_PORT/v1/models" | jq -r '.data[].id' | grep -q lightrag
echo "lightrag-in-litellm=$?"
```

Expected: `lightrag-in-litellm=0` (model is registered).

- [ ] **Step 5: End-to-end RAG query via LiteLLM**

```bash
curl -sX POST "http://localhost:$LITELLM_PORT/v1/chat/completions" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "lightrag",
    "messages": [{"role": "user", "content": "/naive hello"}]
  }' | jq -r '.choices[0].message.content'
```

Expected: non-empty response (LightRAG returns a message even before any documents are ingested).

- [ ] **Step 6: Ingest + query (optional, requires a test document)**

Browse to `http://lightrag.localhost:${KONG_HTTP_PORT}/webui` (or `http://localhost:${LIGHTRAG_API_PORT}/webui`), upload a small PDF, wait for ingestion (5–10 min for first-time embedding), then query via either the WebUI or the API.

Verify in Neo4j Browser (`neo4j.localhost:${KONG_HTTP_PORT}`) that `MATCH (n:Entity) RETURN n LIMIT 10` returns entities extracted from the document.

Verify in Supabase Studio (`supabase.localhost:${KONG_HTTP_PORT}`) that the `lightrag` schema contains populated vector tables.

- [ ] **Step 7: Document any issues**

If anything fails:
- Check container logs: `docker logs ${PROJECT_NAME}-lightrag-init` and `docker logs ${PROJECT_NAME}-lightrag`.
- Check that LiteLLM resolved a chat-capable model: `curl -sH "Authorization: Bearer $LITELLM_MASTER_KEY" "http://localhost:$LITELLM_PORT/v1/models" | jq`.
- If the first run times out, the model download may be ongoing. Increase `start_period` in `services/lightrag/compose.yml` and re-up.

- [ ] **Step 8: Stop the stack**

```bash
./stop.sh
```

(Do not run `docker compose down` per memory `feedback_dont_tear_down_running_stack`.)

---

## 8. Plan summary

29 tasks across 7 phases. Roughly:

- **Phase 1 (Tasks 1–8)**: TEI Reranker manifest, handler, CLI, validators, Kong, README — leaf service first.
- **Phase 2 (Tasks 9–18)**: LightRAG manifest, init scripts, handler, CLI, validators, Kong, key generator, README, permutation tests.
- **Phase 3 (Task 19)**: Cross-service `runtime_adaptive` wiring into hermes/n8n/backend.
- **Phase 4 (Task 20)**: LiteLLM `lightrag_model_entry()` registration.
- **Phase 5 (Tasks 21–24)**: Audit-script entries, port-symmetry test, no-picker documentation, baseline refresh.
- **Phase 6 (Tasks 25–27)**: Documentation surface (root index, ports table, source-config matrix, wizard table, root README, cross-references).
- **Phase 7 (Tasks 28–29)**: Verification matrix + live smoke test.

After completion, the user gets:
1. Two new wizard tiles (`LightRAG · source`, `TEI Reranker · source`).
2. Two new Kong aliases (`lightrag.localhost`, `rerank.localhost`).
3. One new LiteLLM model (`lightrag`).
4. Hermes/n8n/backend with LightRAG capability auto-wired.
5. Storage-backend adaptive fallback (graceful degradation when Supabase/Neo4j/Redis disabled).
6. RAG-Anything's multimodal capabilities (via LightRAG v1.5+ absorption) — no separate service needed.
