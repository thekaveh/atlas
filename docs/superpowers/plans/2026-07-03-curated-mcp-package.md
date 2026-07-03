# Curated MCP Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a disabled-by-default Atlas MCP service family exposing a curated Postgres, Neo4j, and SearXNG tool surface.

**Architecture:** Ship one `mcp-servers` service family in the `agents` category. The first slice is direct Streamable HTTP MCP over `/mcp`, routed by Kong at `mcp.localhost`, with read/search-only tools and documentation for Open WebUI, Hermes, and conditional LiteLLM consumption.

**Tech Stack:** Docker Compose, Atlas manifests/topology, Python 3.12, stable `mcp>=1,<2`, psycopg, Neo4j Python driver, requests, pytest.

## Global Constraints

- `MCP_SERVERS_SOURCE=disabled` by default.
- Do not add a generic one-MCP-server-per-service pattern.
- Do not default to MetaMCP, Docker MCP Gateway, or `mcpo`.
- Do not auto-register MCP servers into Open WebUI, Hermes, or LiteLLM in v1.
- Postgres and Neo4j tools are read-only by default.
- `mcp-servers` belongs to `gen-ai-rag`, `gen-ai-eng`, and `all`; it is not part of `data-eng` in this slice.
- Port assignment must come from `bootstrapper/services/topology.py`.
- Kong alias is `mcp.localhost`; direct endpoint is `/mcp`.

---

### Task 1: Admission Contract And Wiring

**Files:**
- Create: `bootstrapper/tests/test_mcp_servers_service.py`
- Modify: `bootstrapper/tracks.yml`
- Modify: `bootstrapper/utils/source_override_manager.py`
- Modify: `bootstrapper/start.py`
- Modify: `bootstrapper/services/service_config.py`
- Modify: `bootstrapper/utils/kong_config_generator.py`

**Interfaces:**
- Consumes: Atlas manifest loader, source override mapping, service config generation.
- Produces: `MCP_SERVERS_SOURCE`, `MCP_SERVERS_PORT`, `MCP_SERVERS_SCALE`, `--mcp-servers-source`, `mcp.localhost`.

- [ ] **Step 1: Write failing admission tests**

```python
def test_mcp_servers_manifest_admission_contract() -> None:
    manifest = _manifest()
    assert manifest["name"] == "mcp-servers"
    assert manifest["category"] == "agents"
    assert manifest["sources"]["var"] == "MCP_SERVERS_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert manifest["depends_on"]["required"] == ["supabase", "neo4j", "searxng"]
    assert manifest["data_flow"]["calls"] == ["supabase", "neo4j", "searxng"]
```

- [ ] **Step 2: Verify tests fail**

Run: `cd bootstrapper && uv run pytest tests/test_mcp_servers_service.py -q`

Expected: fail because `services/mcp-servers/service.yml` does not exist.

- [ ] **Step 3: Implement manifest, CLI, scale, and route seams**

Add `services/mcp-servers/service.yml`, include it in `docker-compose.yml`, add source mapping, click option, track membership, scale generation with Neo4j/SearXNG gates, and a Kong service route for `mcp.localhost`.

- [ ] **Step 4: Verify focused tests pass**

Run: `cd bootstrapper && uv run pytest tests/test_mcp_servers_service.py tests/test_kong_alias_routes.py tests/test_wizard_app_discovery.py -q`

Expected: all selected tests pass.

### Task 2: MCP Runtime

**Files:**
- Create: `services/mcp-servers/build/Dockerfile`
- Create: `services/mcp-servers/runtime/requirements.txt`
- Create: `services/mcp-servers/runtime/atlas_mcp_server.py`
- Create: `services/mcp-servers/runtime/tests/test_runtime_guards.py`
- Create: `services/mcp-servers/compose.yml`

**Interfaces:**
- Consumes: `SUPABASE_DB_*`, `NEO4J_URI`, `GRAPH_DB_*`, `SEARXNG_URL`.
- Produces: HTTP MCP endpoint at `/mcp` and lightweight health endpoint at `/health`.

- [ ] **Step 1: Write failing runtime guard tests**

```python
def test_postgres_guard_rejects_mutation_and_multiple_statements():
    assert is_safe_postgres_read("select 1")
    assert not is_safe_postgres_read("select 1; drop table users")
    assert not is_safe_postgres_read("delete from users")
```

- [ ] **Step 2: Verify runtime tests fail**

Run: `cd services/mcp-servers/runtime && uv run --with pytest pytest tests/test_runtime_guards.py -q`

Expected: fail because runtime module does not exist.

- [ ] **Step 3: Implement minimal stable FastMCP runtime**

Implement bounded tools: `postgres_query`, `neo4j_schema`, `neo4j_read_cypher`, `searxng_web_search`. Reject writes, multiple statements, and oversized limits before calling upstream services.

- [ ] **Step 4: Verify runtime tests pass**

Run: `cd services/mcp-servers/runtime && uv run --with pytest --with mcp --with psycopg[binary] --with neo4j --with requests pytest tests/test_runtime_guards.py -q`

Expected: all runtime tests pass.

### Task 3: Docs And Generated Artifacts

**Files:**
- Create: `services/mcp-servers/README.md`
- Generate: `services/mcp-servers/architecture.svg`
- Generate: `services/mcp-servers/architecture.html`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/deployment/ports-and-routes.md`

**Interfaces:**
- Consumes: docs regen and env assembler.
- Produces: user-facing service docs and drift-clean generated files.

- [ ] **Step 1: Write docs assertions**

```python
def test_mcp_docs_describe_consumers_and_guardrails() -> None:
    readme = README.read_text()
    assert "Open WebUI" in readme
    assert "Hermes" in readme
    assert "LiteLLM" in readme
    assert "MetaMCP" in readme
    assert "Docling MCP" in readme
    assert "read-only" in readme
```

- [ ] **Step 2: Verify docs assertions fail**

Run: `cd bootstrapper && uv run pytest tests/test_mcp_servers_service.py -q`

Expected: fail because README is missing.

- [ ] **Step 3: Add README and regenerate docs**

Run: `cd bootstrapper && uv run python -m services.env_assembler`

Run: `cd bootstrapper && uv run python -m bootstrapper.docs.regen mcp-servers`

Run: `cd bootstrapper && uv run python -m tools.generate_readme_topology`

- [ ] **Step 4: Verify docs drift locally**

Run: `PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all --check`

Expected: no drift.

### Task 4: Full Verification And PR

**Files:**
- All changed files.

**Interfaces:**
- Consumes: complete branch diff.
- Produces: commit, PR, green CI, merge, cleanup.

- [ ] **Step 1: Run full verification**

Run:

```bash
cd bootstrapper && uv run pytest -q
PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all --check
python scripts/check_doc_links.py
python scripts/check-docs-drift.py
python scripts/check-compose-source-deps.py
python scripts/check-kong-routes.py
python scripts/validate_research_schema.py --all
cd bootstrapper && uv run python -m tools.validate_fragments
docker compose --env-file .env.example -p atlas -f docker-compose.yml config -q
git diff --check
```

- [ ] **Step 2: Request code review**

Dispatch a reviewer against `origin/main...HEAD` with the issue expansion and this plan as requirements.

- [ ] **Step 3: Fix required review findings**

Use TDD for any behavioral fix, then rerun focused and full verification.

- [ ] **Step 4: Commit, push, PR, CI, merge, cleanup**

Commit with message `Add curated MCP server package`, push, create PR closing `#195`, wait for required checks, squash merge, delete branches, move issue/project item to Done, and return main to `origin/main`.
