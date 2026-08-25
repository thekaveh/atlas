# Plugin Kong Timeouts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow backend plugins to declare Kong upstream timeouts without changing the behavior of plugins that omit them.

**Architecture:** The shared plugin schema feeds matching host-time and container-time manifest models. Timeout-bearing plugins receive dedicated Kong services because Kong timeouts are service-scoped; all other backend routes remain on the shared service, with route auth composed exactly as today.

**Tech Stack:** Python 3.10+, Pydantic v2, JSON Schema 2020-12, PyYAML, Kong DB-less declarative YAML, pytest.

## Global Constraints

- Timeout values are strict integers in milliseconds from 1 through 2,147,483,646 inclusive.
- Omitted timeout fields must remain omitted from generated Kong services.
- Plugins without timeout overrides must retain the existing shared `backend-api` service shape.
- Timed plugins must preserve `inherit`, `open`, and `key-auth` behavior.
- No Docker image, Kong version, or live-stack dependency may change.

---

### Task 1: Extend and synchronize the plugin manifest contract

**Files:**
- Modify: `bootstrapper/schemas/plugin.schema.json`
- Modify: `bootstrapper/core/plugin_manifest.py`
- Modify: `services/backend/app/app/plugin_manifest.py`
- Test: `bootstrapper/tests/test_plugin_manifest.py`
- Test: `services/backend/app/app/tests/test_plugin_manifest.py`

**Interfaces:**
- Consumes: version-1 `plugin.yml` mappings.
- Produces: `PluginManifest.connect_timeout`, `.write_timeout`, and `.read_timeout` as `int | None`, plus host-side timeout-policy derivation.

- [ ] **Step 1: Add failing validator and derivation tests**

Add parameterized cases for each field, partial declarations, `1`, `2147483646`, zero, negatives, overflow, booleans, floats, and numeric strings. Assert the host derivation contains only manifests with at least one declared timeout and only their explicit keys.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_plugin_manifest.py -q
PYTHONPATH=services/backend/app/app uv run --project bootstrapper pytest services/backend/app/app/tests/test_plugin_manifest.py -q
```

Expected: new cases fail because the three fields are forbidden or absent.

- [ ] **Step 3: Implement the shared schema and model fields**

Define each schema property as:

```json
{"type": "integer", "minimum": 1, "maximum": 2147483646}
```

Add matching optional strict integer fields to both models. Load the values into the host dataclass and derive ordered `(name, route_prefix, timeout_mapping)` policies containing explicit fields only.

- [ ] **Step 4: Run tests and verify GREEN**

Run both commands from Step 2. Expected: all cases pass with no validator drift.

- [ ] **Step 5: Commit the manifest contract**

```bash
git add bootstrapper/schemas/plugin.schema.json bootstrapper/core/plugin_manifest.py bootstrapper/tests/test_plugin_manifest.py services/backend/app/app/plugin_manifest.py services/backend/app/app/tests/test_plugin_manifest.py
git commit -m "feat: validate plugin Kong timeouts"
```

### Task 2: Generate dedicated Kong services for timed plugins

**Files:**
- Modify: `bootstrapper/utils/kong_config_generator.py`
- Test: `bootstrapper/tests/test_kong_alias_routes.py`

**Interfaces:**
- Consumes: ordered `plugin_route_auth` and `plugin_route_timeouts` policy lists.
- Produces: the historical shared backend service plus zero or more dedicated timed-plugin services.

- [ ] **Step 1: Add failing Kong configuration tests**

Cover no overrides, each individual timeout, all three fields, multiple plugins, `inherit`/`open`/`key-auth`, timeout-plus-auth de-duplication, and route-name collisions across shared/dedicated services.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_kong_alias_routes.py -q
```

Expected: new tests fail because the generator has no timeout policy or dedicated services.

- [ ] **Step 3: Implement minimal service splitting**

Add a default-empty `plugin_route_timeouts` collection, allocate route names across all plugin policies, omit timed prefixes from shared auth-specific routes, and append one dedicated service per timed plugin. Copy only explicit timeout keys and resolve `inherit` through `_backend_kong_auth_mode()`.

- [ ] **Step 4: Run tests and verify GREEN**

Run the command from Step 2. Expected: all old and new Kong routing/auth tests pass.

- [ ] **Step 5: Commit Kong generation**

```bash
git add bootstrapper/utils/kong_config_generator.py bootstrapper/tests/test_kong_alias_routes.py
git commit -m "feat: generate plugin-specific Kong timeout services"
```

### Task 3: Wire startup policy and expose inventory

**Files:**
- Modify: `bootstrapper/start.py`
- Modify: `services/backend/app/app/plugin_seam.py`
- Test: `bootstrapper/tests/test_plugin_manifest.py`
- Test: `bootstrapper/tests/test_consumer_doctor.py`
- Test: `services/backend/app/app/tests/test_plugin_seam.py`

**Interfaces:**
- Consumes: one plugin discovery result.
- Produces: generator auth and timeout policy collections; inventory `timeouts` mappings with declared values only.

- [ ] **Step 1: Add failing wiring and inventory tests**

Assert a single discovery result feeds both policy lists, malformed manifests remain excluded, and inventory rows show only explicitly declared timeout fields.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_consumer_doctor.py -q
PYTHONPATH=services/backend/app/app uv run --project bootstrapper pytest services/backend/app/app/tests/test_plugin_seam.py -q
```

Expected: new assertions fail because startup and inventory do not transfer timeout data.

- [ ] **Step 3: Implement policy wiring and inventory output**

Discover manifests once in the startup derivation helper, return both policies, assign both to the generator, and serialize only non-`None` timeout fields under inventory `timeouts`.

- [ ] **Step 4: Run tests and verify GREEN**

Run the commands from Step 2. Expected: all focused integration cases pass.

- [ ] **Step 5: Commit integration wiring**

```bash
git add bootstrapper/start.py bootstrapper/tests/test_consumer_doctor.py services/backend/app/app/plugin_seam.py services/backend/app/app/tests/test_plugin_seam.py
git commit -m "feat: wire plugin timeout policy into Kong"
```

### Task 4: Document and verify the complete contract

**Files:**
- Modify: `docs/deployment/reusing-atlas.md`
- Modify: `docs/CHANGELOG.md`
- Modify if generated: documentation outputs identified by the drift checker.

**Interfaces:**
- Consumes: the implemented manifest and generator behavior.
- Produces: consumer-facing examples, range/units guidance, and verified repository artifacts.

- [ ] **Step 1: Update consumer documentation and changelog**

Document a long-running route example, all three optional fields, milliseconds, valid bounds, per-field omission behavior, and service-level isolation.

- [ ] **Step 2: Run focused and drift verification**

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_plugin_manifest.py bootstrapper/tests/test_kong_alias_routes.py bootstrapper/tests/test_consumer_doctor.py -q
PYTHONPATH=services/backend/app/app uv run --project bootstrapper pytest services/backend/app/app/tests/test_plugin_manifest.py services/backend/app/app/tests/test_plugin_seam.py -q
make docs-check
git diff --check
```

Expected: zero failures and no documentation drift.

- [ ] **Step 3: Run broad verification**

```bash
uv run --project bootstrapper pytest bootstrapper/tests -q
```

Run the backend suite in its declared Python 3.12 dependency environment. Expected: all non-pre-existing tests pass.

- [ ] **Step 4: Review requirements and diff**

Compare the implementation line-by-line with the design and issue #974, confirm no live-stack dependency or unrelated refactor, and resolve all critical/important review findings.

- [ ] **Step 5: Commit documentation and final fixes**

```bash
git add docs/deployment/reusing-atlas.md docs/CHANGELOG.md
git commit -m "docs: explain plugin Kong timeout overrides"
```

### Task 5: Integrate through protected Gitflow branches

**Files:**
- No source-file changes expected.

**Interfaces:**
- Consumes: verified feature branch.
- Produces: merged PR to `develop`, then promotion PR from `develop` to `main`.

- [ ] **Step 1: Push and create the feature PR**

```bash
git push -u origin codex/issue-974-plugin-timeouts
gh pr create --base develop --head codex/issue-974-plugin-timeouts
```

- [ ] **Step 2: Wait for required checks and merge to develop**

Confirm the four live gitflow checks pass, the branch is current, and conversations are resolved; then squash-merge.

- [ ] **Step 3: Synchronize ancestry if strict mode requires it**

If `develop` and `main` have equal trees but divergent squash history, merge current `main` into a temporary branch from `develop`, prove its tree is content-neutral, and land that sync through its own checked PR to `develop`.

- [ ] **Step 4: Promote develop to main**

Create or refresh the `develop` to `main` PR, wait for all required checks, and squash-merge only when mergeability is clean.

- [ ] **Step 5: Audit remote state**

Fetch remote refs, confirm issue #974 is closed, confirm `origin/main` and `origin/develop` have identical trees, and record PR/commit/test evidence before declaring completion.
