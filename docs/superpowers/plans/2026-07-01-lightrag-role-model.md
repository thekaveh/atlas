# LightRAG Role Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose LightRAG v1.5.4 role-specific LLM settings in Atlas so extraction, keyword extraction, and query answering can use independently selected models without changing current defaults.

**Architecture:** Keep `lightrag-init` responsible only for resolving the base `LLM_MODEL`, `EMBEDDING_MODEL`, and `EMBEDDING_DIM` into `/app/data/.env`. Add Atlas-owned `LIGHTRAG_*` role inputs to the LightRAG manifest and statically map them to LightRAG's native `EXTRACT_*`, `KEYWORD_*`, and `QUERY_*` environment names in the runtime `lightrag` container. Empty role values remain blank so LightRAG falls back to the base `LLM_*` configuration.

**Tech Stack:** Docker Compose fragments, Atlas service manifests, Python pytest, PyYAML, LightRAG v1.5.4 environment contract, generated `.env.example`, generated compose baseline.

**Supersession note (2026-07-01):** The plan's corrective TEI note is now implemented in current runtime docs. Atlas emits `LIGHTRAG_RERANK_BINDING=null` and leaves `LIGHTRAG_RERANK_BINDING_HOST` empty by default because direct stock LightRAG-to-TEI rerank payloads are incompatible without an adapter.

## 1. Global Constraints

- Preserve existing Atlas behavior when every role-specific variable is unset.
- Do not hard-code `mistral-small3.2:24b` as an Atlas default.
- Do not set `LLM_MODEL`, `EMBEDDING_MODEL`, or `EMBEDDING_DIM` directly in `services/lightrag/compose.yml`; `lightrag-init` resolves and writes them to `/app/data/.env`.
- Use LightRAG native runtime names in the `lightrag` container: `EXTRACT_LLM_MODEL`, `KEYWORD_LLM_MODEL`, `QUERY_LLM_MODEL`, and corresponding binding, host, API key, concurrency, and timeout variables.
- Keep role API keys secret-marked in the manifest.
- Keep generated artifacts in sync: `.env.example` and `bootstrapper/tests/fixtures/rendered_config_baseline.yml`.
- Do not change Docker images, base storage architecture, or LightRAG's init model resolution flow.
- Out of scope for this PR: VLM role variables. The spec mentions VLM as a supported LightRAG role, but its requested Atlas inputs and acceptance criteria cover only `EXTRACT`, `KEYWORD`, and `QUERY`.

---

## 2. Validity Review

- Valid: Atlas currently exposes only `LIGHTRAG_LLM_MODEL` as the base chat model in `services/lightrag/service.yml`.
- Valid: `lightrag-init` currently writes only `LLM_MODEL`, `EMBEDDING_MODEL`, and `EMBEDDING_DIM` to `/app/data/.env` via `services/lightrag/init/scripts/resolve-models.py`.
- Valid: `services/lightrag/compose.yml` sources `/app/data/.env` before running `python -m lightrag.api.lightrag_server`.
- Valid: Atlas does not currently declare or map `LIGHTRAG_EXTRACT_*`, `LIGHTRAG_KEYWORD_*`, or `LIGHTRAG_QUERY_*` variables.
- Valid: LightRAG v1.5.4 reads role-specific env vars from the process environment and falls back to base `LLM_*` settings when role values are absent or falsey.
- Nuance: Static Compose mappings like `EXTRACT_LLM_MODEL: ${LIGHTRAG_EXTRACT_LLM_MODEL:-}` render empty variables when unset. In LightRAG v1.5.4, empty strings are safe for string role settings because runtime resolution uses `or` fallback; empty integers are safe because `get_env_value(..., int)` catches `ValueError` and returns the default.
- Scope decision: Do not forward provider-specific role options such as `EXTRACT_OLLAMA_LLM_NUM_CTX` in this PR. The spec's Atlas change list does not request them; the rag-showcase overlay can continue to set native provider-specific variables directly.

## 3. File Structure

- Modify `services/lightrag/service.yml`: declare Atlas-owned role env vars so `.env.example`, validation, and ownership checks know about them.
- Modify `services/lightrag/compose.yml`: map Atlas-owned variables to LightRAG native runtime env names on the `lightrag` service.
- Create `bootstrapper/tests/test_lightrag_role_models.py`: focused tests for manifest ownership, compose mapping, and rendered runtime environment.
- Modify `.env.example`: generated from manifests; should gain the new blank role variables under the LightRAG section.
- Modify `bootstrapper/tests/fixtures/rendered_config_baseline.yml`: generated compose baseline; should gain native role env entries in the rendered `lightrag.environment` block and Atlas-prefixed inputs in `lightrag-init.environment` only if deliberately added there.
- Modify `services/lightrag/README.md`: document role model variables and local Ollama recommendation.
- Modify `docs/deployment/source-configuration.md`: add a concise role-specific LightRAG subsection and replace the obsolete disabled-rerank wording with the current `RERANK_BINDING=null` behavior.

---

### 3.1. Task 1: Add Failing Role-Model Contract Tests

**Files:**
- Create: `bootstrapper/tests/test_lightrag_role_models.py`

**Interfaces:**
- Consumes: `services/lightrag/service.yml`, `services/lightrag/compose.yml`, root `.env.example`, root `docker-compose.yml`.
- Produces: regression coverage proving Atlas declares role knobs, maps them to native LightRAG names, and renders configured role values into the container environment.

- [ ] **Step 1: Write the failing tests**

Create `bootstrapper/tests/test_lightrag_role_models.py`:

```python
"""LightRAG role-specific LLM model configuration tests."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
LIGHTRAG_MANIFEST = REPO_ROOT / "services" / "lightrag" / "service.yml"
LIGHTRAG_COMPOSE = REPO_ROOT / "services" / "lightrag" / "compose.yml"
COMPOSE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

ROLE_INPUTS = {
    "LIGHTRAG_EXTRACT_LLM_MODEL": {"native": "EXTRACT_LLM_MODEL", "secret": False},
    "LIGHTRAG_KEYWORD_LLM_MODEL": {"native": "KEYWORD_LLM_MODEL", "secret": False},
    "LIGHTRAG_QUERY_LLM_MODEL": {"native": "QUERY_LLM_MODEL", "secret": False},
    "LIGHTRAG_EXTRACT_LLM_BINDING": {"native": "EXTRACT_LLM_BINDING", "secret": False},
    "LIGHTRAG_KEYWORD_LLM_BINDING": {"native": "KEYWORD_LLM_BINDING", "secret": False},
    "LIGHTRAG_QUERY_LLM_BINDING": {"native": "QUERY_LLM_BINDING", "secret": False},
    "LIGHTRAG_EXTRACT_LLM_BINDING_HOST": {"native": "EXTRACT_LLM_BINDING_HOST", "secret": False},
    "LIGHTRAG_KEYWORD_LLM_BINDING_HOST": {"native": "KEYWORD_LLM_BINDING_HOST", "secret": False},
    "LIGHTRAG_QUERY_LLM_BINDING_HOST": {"native": "QUERY_LLM_BINDING_HOST", "secret": False},
    "LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY": {"native": "EXTRACT_LLM_BINDING_API_KEY", "secret": True},
    "LIGHTRAG_KEYWORD_LLM_BINDING_API_KEY": {"native": "KEYWORD_LLM_BINDING_API_KEY", "secret": True},
    "LIGHTRAG_QUERY_LLM_BINDING_API_KEY": {"native": "QUERY_LLM_BINDING_API_KEY", "secret": True},
    "LIGHTRAG_EXTRACT_MAX_ASYNC_LLM": {"native": "EXTRACT_MAX_ASYNC_LLM", "secret": False},
    "LIGHTRAG_KEYWORD_MAX_ASYNC_LLM": {"native": "KEYWORD_MAX_ASYNC_LLM", "secret": False},
    "LIGHTRAG_QUERY_MAX_ASYNC_LLM": {"native": "QUERY_MAX_ASYNC_LLM", "secret": False},
    "LIGHTRAG_EXTRACT_LLM_TIMEOUT": {"native": "EXTRACT_LLM_TIMEOUT", "secret": False},
    "LIGHTRAG_KEYWORD_LLM_TIMEOUT": {"native": "KEYWORD_LLM_TIMEOUT", "secret": False},
    "LIGHTRAG_QUERY_LLM_TIMEOUT": {"native": "QUERY_LLM_TIMEOUT", "secret": False},
}


def _manifest_env_by_name() -> dict[str, dict]:
    data = yaml.safe_load(LIGHTRAG_MANIFEST.read_text(encoding="utf-8"))
    return {entry["name"]: entry for entry in data["env"]}


def _compose_lightrag_environment() -> dict[str, str]:
    data = yaml.safe_load(LIGHTRAG_COMPOSE.read_text(encoding="utf-8"))
    return data["services"]["lightrag"]["environment"]


def test_lightrag_manifest_declares_role_llm_inputs():
    env_by_name = _manifest_env_by_name()

    for atlas_name, meta in ROLE_INPUTS.items():
        assert atlas_name in env_by_name
        assert env_by_name[atlas_name].get("default", "") == ""
        if meta["secret"]:
            assert env_by_name[atlas_name].get("secret") is True


def test_lightrag_compose_maps_role_inputs_to_native_env_names():
    env = _compose_lightrag_environment()

    for atlas_name, meta in ROLE_INPUTS.items():
        native_name = meta["native"]
        assert native_name in env
        assert env[native_name] == f"${{{atlas_name}:-}}"


def test_lightrag_compose_keeps_base_models_init_resolved():
    env = _compose_lightrag_environment()

    assert "LLM_MODEL" not in env
    assert "EMBEDDING_MODEL" not in env
    assert "EMBEDDING_DIM" not in env


def _docker_available() -> bool:
    return shutil.which("docker") is not None


@pytest.mark.skipif(
    not _docker_available() or not ENV_EXAMPLE.is_file(),
    reason="docker not on PATH or .env.example missing",
)
def test_lightrag_role_models_render_into_container_environment(tmp_path: Path):
    env_file = tmp_path / ".env"
    overrides = {
        "PROJECT_NAME": "atlas",
        "LIGHTRAG_SOURCE": "container",
        "LIGHTRAG_SCALE": "1",
        "LIGHTRAG_INIT_SCALE": "1",
        "LIGHTRAG_EXTRACT_LLM_MODEL": "mistral-small3.2:24b",
        "LIGHTRAG_KEYWORD_LLM_MODEL": "mistral-small3.2:24b",
        "LIGHTRAG_QUERY_LLM_MODEL": "qwen3.6:latest",
        "LIGHTRAG_EXTRACT_MAX_ASYNC_LLM": "1",
        "LIGHTRAG_QUERY_LLM_TIMEOUT": "900",
    }

    out_lines = []
    seen = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            out_lines.append(line)
            continue
        key = line.split("=", 1)[0]
        if key in overrides:
            out_lines.append(f"{key}={overrides[key]}")
            seen.add(key)
        else:
            out_lines.append(line)
    for key, value in overrides.items():
        if key not in seen:
            out_lines.append(f"{key}={value}")
    env_file.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-p",
            "atlas",
            "-f",
            str(COMPOSE),
            "config",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    env = rendered["services"]["lightrag"]["environment"]

    assert env["EXTRACT_LLM_MODEL"] == "mistral-small3.2:24b"
    assert env["KEYWORD_LLM_MODEL"] == "mistral-small3.2:24b"
    assert env["QUERY_LLM_MODEL"] == "qwen3.6:latest"
    assert env["EXTRACT_MAX_ASYNC_LLM"] == "1"
    assert env["QUERY_LLM_TIMEOUT"] == "900"
    assert env["KEYWORD_LLM_TIMEOUT"] == ""
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
cd bootstrapper && uv run pytest tests/test_lightrag_role_models.py -q
```

Expected: FAIL. The manifest declaration test and compose mapping test should report missing role variables.

- [ ] **Step 3: Commit the failing tests**

```bash
git add bootstrapper/tests/test_lightrag_role_models.py
git commit -m "test: capture LightRAG role model contract"
```

---

### 3.2. Task 2: Add Atlas Role Inputs and Native Compose Mapping

**Files:**
- Modify: `services/lightrag/service.yml`
- Modify: `services/lightrag/compose.yml`

**Interfaces:**
- Consumes: the test contract from Task 1.
- Produces: Atlas-owned `LIGHTRAG_*` role inputs and runtime `EXTRACT_*`, `KEYWORD_*`, `QUERY_*` variables read by LightRAG.

- [ ] **Step 1: Add role input declarations to the LightRAG manifest**

In `services/lightrag/service.yml`, insert this block after the existing `LIGHTRAG_LLM_MODEL` entry:

```yaml
  - name: LIGHTRAG_EXTRACT_LLM_MODEL
    default: ""
    description: "Optional LightRAG EXTRACT role model. Empty = inherit LLM_MODEL."
  - name: LIGHTRAG_KEYWORD_LLM_MODEL
    default: ""
    description: "Optional LightRAG KEYWORD role model. Empty = inherit LLM_MODEL."
  - name: LIGHTRAG_QUERY_LLM_MODEL
    default: ""
    description: "Optional LightRAG QUERY role model. Empty = inherit LLM_MODEL."
  - name: LIGHTRAG_EXTRACT_LLM_BINDING
    default: ""
    description: "Optional EXTRACT role provider binding. Empty = inherit LLM_BINDING."
  - name: LIGHTRAG_KEYWORD_LLM_BINDING
    default: ""
    description: "Optional KEYWORD role provider binding. Empty = inherit LLM_BINDING."
  - name: LIGHTRAG_QUERY_LLM_BINDING
    default: ""
    description: "Optional QUERY role provider binding. Empty = inherit LLM_BINDING."
  - name: LIGHTRAG_EXTRACT_LLM_BINDING_HOST
    default: ""
    description: "Optional EXTRACT role provider endpoint. Empty = inherit LLM_BINDING_HOST."
  - name: LIGHTRAG_KEYWORD_LLM_BINDING_HOST
    default: ""
    description: "Optional KEYWORD role provider endpoint. Empty = inherit LLM_BINDING_HOST."
  - name: LIGHTRAG_QUERY_LLM_BINDING_HOST
    default: ""
    description: "Optional QUERY role provider endpoint. Empty = inherit LLM_BINDING_HOST."
  - name: LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY
    default: ""
    secret: true
    description: "Optional EXTRACT role provider API key. Empty = inherit LLM_BINDING_API_KEY for same-provider roles."
  - name: LIGHTRAG_KEYWORD_LLM_BINDING_API_KEY
    default: ""
    secret: true
    description: "Optional KEYWORD role provider API key. Empty = inherit LLM_BINDING_API_KEY for same-provider roles."
  - name: LIGHTRAG_QUERY_LLM_BINDING_API_KEY
    default: ""
    secret: true
    description: "Optional QUERY role provider API key. Empty = inherit LLM_BINDING_API_KEY for same-provider roles."
  - name: LIGHTRAG_EXTRACT_MAX_ASYNC_LLM
    default: ""
    description: "Optional EXTRACT role LLM concurrency. Empty = inherit MAX_ASYNC_LLM."
  - name: LIGHTRAG_KEYWORD_MAX_ASYNC_LLM
    default: ""
    description: "Optional KEYWORD role LLM concurrency. Empty = inherit MAX_ASYNC_LLM."
  - name: LIGHTRAG_QUERY_MAX_ASYNC_LLM
    default: ""
    description: "Optional QUERY role LLM concurrency. Empty = inherit MAX_ASYNC_LLM."
  - name: LIGHTRAG_EXTRACT_LLM_TIMEOUT
    default: ""
    description: "Optional EXTRACT role LLM timeout in seconds. Empty = inherit LLM_TIMEOUT."
  - name: LIGHTRAG_KEYWORD_LLM_TIMEOUT
    default: ""
    description: "Optional KEYWORD role LLM timeout in seconds. Empty = inherit LLM_TIMEOUT."
  - name: LIGHTRAG_QUERY_LLM_TIMEOUT
    default: ""
    description: "Optional QUERY role LLM timeout in seconds. Empty = inherit LLM_TIMEOUT."
```

- [ ] **Step 2: Map Atlas inputs to LightRAG native runtime names**

In `services/lightrag/compose.yml`, insert this block immediately after `LLM_BINDING_API_KEY: ${LITELLM_MASTER_KEY}`:

```yaml
      # Role-specific LightRAG v1.5+ LLM overrides. Empty values are
      # intentional: LightRAG treats them as unset and falls back to the base
      # LLM_* settings resolved by lightrag-init.
      EXTRACT_LLM_MODEL: ${LIGHTRAG_EXTRACT_LLM_MODEL:-}
      KEYWORD_LLM_MODEL: ${LIGHTRAG_KEYWORD_LLM_MODEL:-}
      QUERY_LLM_MODEL: ${LIGHTRAG_QUERY_LLM_MODEL:-}
      EXTRACT_LLM_BINDING: ${LIGHTRAG_EXTRACT_LLM_BINDING:-}
      KEYWORD_LLM_BINDING: ${LIGHTRAG_KEYWORD_LLM_BINDING:-}
      QUERY_LLM_BINDING: ${LIGHTRAG_QUERY_LLM_BINDING:-}
      EXTRACT_LLM_BINDING_HOST: ${LIGHTRAG_EXTRACT_LLM_BINDING_HOST:-}
      KEYWORD_LLM_BINDING_HOST: ${LIGHTRAG_KEYWORD_LLM_BINDING_HOST:-}
      QUERY_LLM_BINDING_HOST: ${LIGHTRAG_QUERY_LLM_BINDING_HOST:-}
      EXTRACT_LLM_BINDING_API_KEY: ${LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY:-}
      KEYWORD_LLM_BINDING_API_KEY: ${LIGHTRAG_KEYWORD_LLM_BINDING_API_KEY:-}
      QUERY_LLM_BINDING_API_KEY: ${LIGHTRAG_QUERY_LLM_BINDING_API_KEY:-}
      EXTRACT_MAX_ASYNC_LLM: ${LIGHTRAG_EXTRACT_MAX_ASYNC_LLM:-}
      KEYWORD_MAX_ASYNC_LLM: ${LIGHTRAG_KEYWORD_MAX_ASYNC_LLM:-}
      QUERY_MAX_ASYNC_LLM: ${LIGHTRAG_QUERY_MAX_ASYNC_LLM:-}
      EXTRACT_LLM_TIMEOUT: ${LIGHTRAG_EXTRACT_LLM_TIMEOUT:-}
      KEYWORD_LLM_TIMEOUT: ${LIGHTRAG_KEYWORD_LLM_TIMEOUT:-}
      QUERY_LLM_TIMEOUT: ${LIGHTRAG_QUERY_LLM_TIMEOUT:-}
```

- [ ] **Step 3: Do not add these role vars to `lightrag-init`**

Check that `services/lightrag/compose.yml::lightrag-init.environment` still only receives base resolution inputs:

```yaml
      LIGHTRAG_LLM_MODEL: ${LIGHTRAG_LLM_MODEL:-}
      LIGHTRAG_EMBEDDING_MODEL: ${LIGHTRAG_EMBEDDING_MODEL:-}
      LIGHTRAG_EMBEDDING_DIM: ${LIGHTRAG_EMBEDDING_DIM:-}
```

Reason: role-specific values are runtime server config, not init-time model discovery inputs.

- [ ] **Step 4: Run the Task 1 tests and verify they pass**

Run:

```bash
cd bootstrapper && uv run pytest tests/test_lightrag_role_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the manifest and compose changes**

```bash
git add services/lightrag/service.yml services/lightrag/compose.yml
git commit -m "feat: expose LightRAG role model settings"
```

---

### 3.3. Task 3: Regenerate Environment and Compose Artifacts

**Files:**
- Modify: `.env.example`
- Modify: `bootstrapper/tests/fixtures/rendered_config_baseline.yml`

**Interfaces:**
- Consumes: manifest env declarations and compose environment mappings.
- Produces: committed generated artifacts expected by CI.

- [ ] **Step 1: Regenerate `.env.example`**

Run:

```bash
cd bootstrapper && uv run python -m services.env_assembler
```

Expected: `.env.example` changes and includes all `LIGHTRAG_EXTRACT_*`, `LIGHTRAG_KEYWORD_*`, and `LIGHTRAG_QUERY_*` entries.

- [ ] **Step 2: Verify the new keys are present**

Run:

```bash
rg -n "LIGHTRAG_(EXTRACT|KEYWORD|QUERY)_(LLM_MODEL|LLM_BINDING|LLM_BINDING_HOST|LLM_BINDING_API_KEY|MAX_ASYNC_LLM|LLM_TIMEOUT)" .env.example
```

Expected: 18 matching active `KEY=` lines.

- [ ] **Step 3: Regenerate the compose baseline from the normalized test renderer**

Run:

```bash
cd bootstrapper && uv run python - <<'PY'
from pathlib import Path
import yaml

from tests.test_fragment_equivalence import COMPOSE, _render

baseline = Path("tests/fixtures/rendered_config_baseline.yml")
baseline.write_text(yaml.safe_dump(_render(COMPOSE), sort_keys=False), encoding="utf-8")
PY
```

Expected: `bootstrapper/tests/fixtures/rendered_config_baseline.yml` changes only in the rendered LightRAG environment area and any formatting emitted by `yaml.safe_dump`.

- [ ] **Step 4: Run generated-artifact tests**

Run:

```bash
cd bootstrapper && uv run pytest \
  tests/test_env_assembler.py::test_committed_env_example_matches_assembler_output \
  tests/test_env_example_consistency.py \
  tests/test_fragment_equivalence.py \
  -q
```

Expected: PASS.

- [ ] **Step 5: Commit generated artifacts**

```bash
git add .env.example bootstrapper/tests/fixtures/rendered_config_baseline.yml
git commit -m "chore: refresh LightRAG role model generated artifacts"
```

---

### 3.4. Task 4: Document Role-Specific LightRAG Configuration

**Files:**
- Modify: `services/lightrag/README.md`
- Modify: `docs/deployment/source-configuration.md`

**Interfaces:**
- Consumes: new env vars from Task 2.
- Produces: user-facing docs that explain fallback behavior and local Ollama recommendations without changing defaults.

- [ ] **Step 1: Update the LightRAG service README config block**

In `services/lightrag/README.md`, extend the existing env example in section `## 3. Configuration` after `LIGHTRAG_LLM_MODEL=`:

```env
LIGHTRAG_EXTRACT_LLM_MODEL=                         # empty = inherit LLM_MODEL
LIGHTRAG_KEYWORD_LLM_MODEL=                         # empty = inherit LLM_MODEL
LIGHTRAG_QUERY_LLM_MODEL=                           # empty = inherit LLM_MODEL
LIGHTRAG_EXTRACT_MAX_ASYNC_LLM=                     # empty = inherit MAX_ASYNC_LLM
LIGHTRAG_QUERY_LLM_TIMEOUT=                         # empty = inherit LLM_TIMEOUT
```

- [ ] **Step 2: Add role-specific prose to the LightRAG service README**

Insert this paragraph after the env block in section `## 3. Configuration`:

````markdown
LightRAG v1.5 supports role-specific LLM settings for extraction, keyword extraction, and final query answering. Atlas exposes those as `LIGHTRAG_EXTRACT_*`, `LIGHTRAG_KEYWORD_*`, and `LIGHTRAG_QUERY_*` inputs, then maps them to LightRAG's native `EXTRACT_*`, `KEYWORD_*`, and `QUERY_*` runtime environment names. Leave a role value empty to inherit the base `LLM_*` configuration resolved by `lightrag-init`.

For local Ollama graph RAG, use a fast non-reasoning model for `EXTRACT` and `KEYWORD`, and reserve the stronger answer model for `QUERY`:

```env
LIGHTRAG_LLM_MODEL=qwen3.6:latest
LIGHTRAG_EXTRACT_LLM_MODEL=mistral-small3.2:24b
LIGHTRAG_KEYWORD_LLM_MODEL=mistral-small3.2:24b
LIGHTRAG_QUERY_LLM_MODEL=qwen3.6:latest
```

Atlas intentionally does not ship those model names as defaults; deployments that do not set role variables keep the existing single-model behavior.
````

- [ ] **Step 3: Update the init container wording**

In `services/lightrag/README.md` section `## 7. Init container`, replace step 2:

```markdown
2. Resolves `LIGHTRAG_LLM_MODEL` / `LIGHTRAG_EMBEDDING_MODEL` / `LIGHTRAG_EMBEDDING_DIM` from LiteLLM.
```

with:

```markdown
2. Resolves the base `LIGHTRAG_LLM_MODEL` / `LIGHTRAG_EMBEDDING_MODEL` / `LIGHTRAG_EMBEDDING_DIM` from LiteLLM and writes LightRAG's native `LLM_MODEL` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` to `/app/data/.env`. Role-specific `LIGHTRAG_EXTRACT_*`, `LIGHTRAG_KEYWORD_*`, and `LIGHTRAG_QUERY_*` variables are passed directly to the runtime container.
```

- [ ] **Step 4: Update source-configuration LightRAG section**

In `docs/deployment/source-configuration.md`, add this after the `LIGHTRAG_SOURCE` bullets in section `### 4.7 LIGHTRAG_SOURCE`:

````markdown
Role-specific LLM overrides are optional and preserve the single-model fallback when left empty:

```bash
LIGHTRAG_LLM_MODEL=qwen3.6:latest
LIGHTRAG_EXTRACT_LLM_MODEL=mistral-small3.2:24b
LIGHTRAG_KEYWORD_LLM_MODEL=mistral-small3.2:24b
LIGHTRAG_QUERY_LLM_MODEL=qwen3.6:latest
```

Use `EXTRACT` and `KEYWORD` for high-volume structured extraction work and `QUERY` for final answer generation. For local Ollama deployments, a cheaper non-reasoning extraction model usually keeps indexing responsive while allowing query answering to use the project-selected stronger model. Empty role-specific values inherit the base `LLM_MODEL`, so existing deployments do not need to set these variables.
````

- [ ] **Step 5: Keep the TEI reranker note aligned while editing the same doc**

In `docs/deployment/source-configuration.md`, ensure the disabled-source note reads:

```markdown
- **`disabled`** - `TEI_RERANKER_ENDPOINT` empties; LightRAG's `RERANK_BINDING` is emitted as `null` so LightRAG disables reranking instead of crashing on an empty binding.
```

- [ ] **Step 6: Run docs checks**

Run:

```bash
PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all --check
python scripts/check_doc_links.py
```

Expected: both commands pass. The docs regen check should not rewrite LightRAG generated dependency sections because `data_flow.calls` did not change.

- [ ] **Step 7: Commit docs**

```bash
git add services/lightrag/README.md docs/deployment/source-configuration.md
git commit -m "docs: document LightRAG role model tuning"
```

---

### 3.5. Task 5: Add an Opt-In Routing Smoke Script

**Files:**
- Create: `scripts/smoke-lightrag-role-models.sh`

**Interfaces:**
- Consumes: an operator's local Atlas stack, configured `.env`, Docker Compose, LightRAG API, and LiteLLM logs.
- Produces: a repeatable manual smoke that confirms configured role values reach runtime and gives the operator the request-level log lines needed to verify model routing.

- [ ] **Step 1: Create the smoke script**

Create `scripts/smoke-lightrag-role-models.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

project="${PROJECT_NAME:-atlas}"
lightrag_url="${LIGHTRAG_URL:-http://localhost:${LIGHTRAG_API_PORT:-63074}}"
api_key="${LIGHTRAG_API_KEY:-}"
extract_model="${LIGHTRAG_EXTRACT_LLM_MODEL:-}"
query_model="${LIGHTRAG_QUERY_LLM_MODEL:-}"

if [ -z "$api_key" ]; then
  echo "LIGHTRAG_API_KEY must be exported from .env before running this smoke." >&2
  echo "Example: set -a; . ./.env; set +a; scripts/smoke-lightrag-role-models.sh" >&2
  exit 2
fi

if [ -z "$extract_model" ] || [ -z "$query_model" ]; then
  echo "Set LIGHTRAG_EXTRACT_LLM_MODEL and LIGHTRAG_QUERY_LLM_MODEL before running this smoke." >&2
  exit 2
fi

echo "[smoke] LightRAG URL: $lightrag_url"
echo "[smoke] expected EXTRACT model: $extract_model"
echo "[smoke] expected QUERY model: $query_model"

echo "[smoke] runtime role environment:"
docker compose -p "$project" exec -T lightrag sh -lc \
  'env | sort | grep -E "^(LLM_MODEL|EXTRACT_LLM_MODEL|KEYWORD_LLM_MODEL|QUERY_LLM_MODEL|EXTRACT_MAX_ASYNC_LLM|QUERY_LLM_TIMEOUT)="'

tmp_doc="$(mktemp)"
cat > "$tmp_doc" <<'DOC'
Atlas is a self-hosted engineering platform. LightRAG is the graph-augmented RAG service. Role-specific LLM configuration lets extraction use a fast model while answers use a stronger model.
DOC

echo "[smoke] uploading one small document"
curl -fsS -X POST "$lightrag_url/documents/upload" \
  -H "Authorization: Bearer $api_key" \
  -F "file=@${tmp_doc};filename=atlas-lightrag-role-smoke.txt" >/tmp/lightrag-upload-response.json

echo "[smoke] waiting 30 seconds for extraction calls to reach LiteLLM"
sleep 30

echo "[smoke] querying LightRAG"
curl -fsS -X POST "$lightrag_url/query" \
  -H "Authorization: Bearer $api_key" \
  -H "Content-Type: application/json" \
  -d '{"query": "/hybrid What does role-specific LightRAG configuration allow Atlas to do?"}' \
  >/tmp/lightrag-query-response.json

echo "[smoke] recent LiteLLM log lines mentioning expected models:"
docker compose -p "$project" logs --since=10m litellm \
  | grep -E "$extract_model|$query_model" \
  | tail -40 || true

echo "[smoke] upload response: /tmp/lightrag-upload-response.json"
echo "[smoke] query response: /tmp/lightrag-query-response.json"
echo "[smoke] pass criteria: the runtime env shows EXTRACT/QUERY values, and LiteLLM logs show requests for both expected models."
```

- [ ] **Step 2: Make the script executable**

Run:

```bash
chmod +x scripts/smoke-lightrag-role-models.sh
```

- [ ] **Step 3: Document the smoke in the script header if reviewers want stricter preconditions**

If the reviewer asks for clearer preconditions, add these comments below the shebang:

```bash
# Precondition: run against a disposable or development Atlas stack.
# Precondition: .env has LIGHTRAG_SOURCE=container and role model variables set.
# Precondition: the configured models are available through LiteLLM.
```

- [ ] **Step 4: Commit the smoke script**

```bash
git add scripts/smoke-lightrag-role-models.sh
git commit -m "test: add LightRAG role routing smoke"
```

---

### 3.6. Task 6: Final Verification

**Files:**
- No new files.

**Interfaces:**
- Consumes: all changes from Tasks 1-5.
- Produces: verified branch ready for PR.

- [ ] **Step 1: Run focused LightRAG tests**

Run:

```bash
cd bootstrapper && uv run pytest \
  tests/test_lightrag_role_models.py \
  tests/test_lightrag_config.py \
  tests/test_lightrag_tei_source_permutations.py \
  tests/test_lightrag_manifest_imperative_parity.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run generated artifact and compose tests**

Run:

```bash
cd bootstrapper && uv run pytest \
  tests/test_env_assembler.py \
  tests/test_env_example_consistency.py \
  tests/test_fragment_equivalence.py \
  tests/test_source_permutations.py \
  -q
```

Expected: PASS, with Docker-dependent tests skipped only if Docker is unavailable.

- [ ] **Step 3: Run docs and audit scripts**

Run:

```bash
PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all --check
python scripts/check_doc_links.py
python scripts/check-docs-drift.py
python scripts/check-compose-source-deps.py
python scripts/check-kong-routes.py
```

Expected: all commands exit 0.

- [ ] **Step 4: Run the full bootstrapper suite**

Run:

```bash
cd bootstrapper && uv run pytest -q
```

Expected: all tests pass. Baseline before implementation on this branch was `1383 passed, 3 skipped`.

- [ ] **Step 5: Run Compose render smoke**

Run:

```bash
tmp_env="$(mktemp)"
cp .env.example "$tmp_env"
{
  echo "LIGHTRAG_SOURCE=container"
  echo "LIGHTRAG_SCALE=1"
  echo "LIGHTRAG_INIT_SCALE=1"
  echo "LIGHTRAG_EXTRACT_LLM_MODEL=mistral-small3.2:24b"
  echo "LIGHTRAG_KEYWORD_LLM_MODEL=mistral-small3.2:24b"
  echo "LIGHTRAG_QUERY_LLM_MODEL=qwen3.6:latest"
} >> "$tmp_env"
docker compose --env-file "$tmp_env" -p atlas -f docker-compose.yml config -q
docker compose --env-file "$tmp_env" -p atlas -f docker-compose.yml config \
  | grep -E "EXTRACT_LLM_MODEL:|KEYWORD_LLM_MODEL:|QUERY_LLM_MODEL:"
rm "$tmp_env"
```

Expected: config succeeds and prints the three native role model variables.

- [ ] **Step 6: Inspect the diff**

Run:

```bash
git status --short
git diff --stat main...HEAD
git diff main...HEAD -- services/lightrag/service.yml services/lightrag/compose.yml
```

Expected: only scoped LightRAG role-model, generated artifact, docs, tests, and smoke-script changes appear.

- [ ] **Step 7: Commit any remaining verification fixes**

If any command required a small fix, commit it with the narrowest relevant message:

```bash
git add <fixed-files>
git commit -m "fix: align LightRAG role model verification"
```

---

## 4. Implementation Notes

- `LIGHTRAG_LLM_MODEL` remains the base fallback. Setting only `LIGHTRAG_EXTRACT_LLM_MODEL` is enough for same-provider role tuning because LightRAG inherits the base binding, host, API key, timeout, and concurrency.
- Cross-provider role tuning requires setting the role binding, model, and API key. LightRAG v1.5.4 validates that and exits when required cross-provider fields are missing.
- Do not add `LIGHTRAG_EXTRACT_*` to `resolve-models.py`; those are not resolved from LiteLLM defaults and should remain explicit operator inputs.
- Do not make the wizard prompt for these values in this PR. They are advanced `.env` knobs surfaced in `.env.example` and docs.
- Do not regenerate `services/lightrag/architecture.svg` or `.html`; service graph dependencies do not change.

## 5. Self-Review Checklist

- Spec coverage: role model, binding, host, API key, max async, and timeout variables are covered for `EXTRACT`, `KEYWORD`, and `QUERY`.
- Fallback behavior: covered by blank defaults, static compose `:-` mappings, and tests that keep base `LLM_MODEL` out of compose.
- Validation: covered by unit/compose tests plus an opt-in smoke script for request-level observation.
- Docs: service README and deployment source guide explain local Ollama recommendation and no-defaults policy.
- Generated artifacts: `.env.example` and rendered compose baseline are explicitly regenerated and tested.
- Known gap: VLM role support is intentionally deferred because it is not in the spec's requested Atlas input list or acceptance criteria.
