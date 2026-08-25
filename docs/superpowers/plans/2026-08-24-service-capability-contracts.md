# Service Capability Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Each implementation task requires a fresh implementer, a specification review, and a code-quality review before the next task begins.

**Goal:** Add a validated capability contract to every Atlas service manifest and render it into deterministic, drift-checked service documentation without requiring a live stack.

**Architecture:** Typed manifest metadata is the single source of truth. A dedicated docs resolver and writer render singleton and aggregate capability tables into numbered README sections; existing schema and docs-drift gates enforce completeness and synchronization.

**Tech Stack:** YAML, JSON Schema 2020-12, Python 3.12 dataclasses, pytest, Markdown, existing Atlas documentation regeneration tooling.

## 1. Global Constraints

- Implement only issue #967.
- Do not require a running Atlas stack, GPU, external API, or model download.
- Do not change service runtime behavior, images, sources, compose wiring, or
  architecture diagrams.
- Ground every capability claim in committed code, configuration, tests, or
  existing documentation; do not copy generic boilerplate across services.
- Treat runtime status and verification confidence as separate dimensions.
- Preserve all existing README text and section numbers. Append the generated
  section on first insertion and replace it in place thereafter.
- Keep the three-surface docs pipeline and existing dependency-section
  generator intact.
- Use strict RED → GREEN tests for every tooling change and commit each task
  only after specification and code-quality reviews pass.
- Do not push until all implementation tasks and final local verification pass.

---

### 1.1. Task 1: Typed schema and four hand-authored pilot contracts

**Files:**
- Modify: `bootstrapper/schemas/service.schema.json`
- Modify: `bootstrapper/services/manifests.py`
- Modify: `bootstrapper/services/manifest_validator.py`
- Modify: `bootstrapper/tests/test_manifests.py`
- Modify: `bootstrapper/tests/test_manifest_validator.py`
- Modify: `services/blender-mcp/service.yml`
- Modify: `services/speaches/service.yml`
- Modify: `services/comfyui/service.yml`
- Modify: `services/lightrag/service.yml`

**Interfaces:**
- Produces: `Capability(name, status, verification, note)`
- Produces: `Manifest.capabilities: list[Capability]`

- [ ] **Step 1: Write failing schema and loader tests**

Cover valid entries, missing fields, extra fields, empty/multiline strings,
invalid runtime status, invalid verification, loader order preservation, and
duplicate names. Keep the top-level field optional during this pilot task so
untouched manifests still load.

- [ ] **Step 2: Run tests and verify RED**

```bash
uv run --project bootstrapper pytest \
  bootstrapper/tests/test_manifests.py \
  bootstrapper/tests/test_manifest_validator.py -q
```

Expected: failures identify the missing schema, dataclass, loader projection,
and duplicate-name validation.

- [ ] **Step 3: Implement the minimal typed contract**

Add the optional schema property, immutable dataclass, loader projection, and
duplicate-name validation. Do not add rendering or a global completeness rule.

- [ ] **Step 4: Hand-author and validate the four pilots**

Research each claim against its manifest, compose fragment, implementation,
tests, and README. Add concise, distinct entries for the known limits listed in
the design. Do not generate these blocks with a script.

- [ ] **Step 5: Run tests and verify GREEN**

Run the focused tests plus manifest validation for the real repository.

- [ ] **Step 6: Review and commit**

After specification and code-quality reviews pass:

```bash
git add bootstrapper/schemas/service.schema.json \
  bootstrapper/services/manifests.py \
  bootstrapper/services/manifest_validator.py \
  bootstrapper/tests/test_manifests.py \
  bootstrapper/tests/test_manifest_validator.py \
  services/blender-mcp/service.yml services/speaches/service.yml \
  services/comfyui/service.yml services/lightrag/service.yml
git commit -m "feat(docs): define service capability contracts"
```

### 1.2. Task 2: Deterministic capability section generation

**Files:**
- Create: `bootstrapper/docs/capabilities_resolver.py`
- Create: `bootstrapper/docs/capabilities_section_writer.py`
- Create: `bootstrapper/tests/test_capabilities_section_writer.py`
- Modify: `bootstrapper/docs/regen.py`
- Modify: `bootstrapper/tests/test_docs_regen_fences.py`

**Interfaces:**
- Produces: `resolve_capability_rows(doc_name, manifests) -> tuple[CapabilityRow, ...]`
- Produces: `render_capabilities_section(...) -> str`
- Produces: fence-aware, dynamically numbered `upsert_capabilities_section(...)`

- [ ] **Step 1: Write failing resolver, renderer, and upsert tests**

Cover singleton pages, aggregate pages, duplicate-member removal, declaration
order, Markdown table escaping, empty-contract behavior, first insertion after
the highest top-level numbered heading, in-place replacement, fenced lookalike
headings, and byte-identical second-pass output.

- [ ] **Step 2: Run tests and verify RED**

```bash
uv run --project bootstrapper pytest \
  bootstrapper/tests/test_capabilities_section_writer.py \
  bootstrapper/tests/test_docs_regen_fences.py -q
```

Expected: collection or assertion failures name the missing resolver/writer and
regen integration.

- [ ] **Step 3: Implement the isolated docs components**

Consume typed `Manifest.capabilities`, preserve declared order, escape table
cells, show a `Service` column only for aggregate pages, and reuse the existing
README aggregate membership. Keep capability logic separate from dependency
graph objects.

- [ ] **Step 4: Integrate with regeneration**

Regenerate capability sections for manifest-backed and supported aggregate
folders. Explicitly skip only the documented `multi2vec-clip` pointer folder.

- [ ] **Step 5: Run tests and verify GREEN**

Run the focused tests and a temporary-repository regeneration idempotence test.

- [ ] **Step 6: Review and commit**

After specification and code-quality reviews pass:

```bash
git add bootstrapper/docs/capabilities_resolver.py \
  bootstrapper/docs/capabilities_section_writer.py \
  bootstrapper/docs/regen.py \
  bootstrapper/tests/test_capabilities_section_writer.py \
  bootstrapper/tests/test_docs_regen_fences.py
git commit -m "feat(docs): render service capability sections"
```

### 1.3. Task 3: Infra and data capability rollout

**Files:**
- Modify: every `services/*/service.yml` whose category is `infra` or `data`
  and does not already contain a pilot contract
- Modify: focused manifest contract inventory tests as needed

- [ ] **Step 1: Write a failing category-completeness test**

Assert every real infra/data manifest has a non-empty capability list with
unique, meaningful names and notes.

- [ ] **Step 2: Run the test and verify RED**

Expected: the failure lists the exact unpopulated infra/data manifests.

- [ ] **Step 3: Research and hand-author the category contracts**

Use each service's manifest, compose fragment, implementation, tests, and
README. Record primary functions and material limits; use `untested` honestly
where static repository evidence cannot certify runtime behavior.

- [ ] **Step 4: Run tests and verify GREEN**

Validate schema, duplicate names, category completeness, and manifest loading.

- [ ] **Step 5: Review and commit**

After claim-by-claim specification and code-quality reviews pass:

```bash
git add services/*/service.yml bootstrapper/tests
git commit -m "docs(services): contract infra and data capabilities"
```

### 1.4. Task 4: LLM and media capability rollout

**Files:**
- Modify: every `services/*/service.yml` whose category is `llm` or `media`
  and does not already contain a pilot contract
- Modify: focused manifest contract inventory tests as needed

- [ ] **Step 1: Extend the completeness test and verify RED**

Expected: the failure lists the exact unpopulated LLM/media manifests.

- [ ] **Step 2: Research and hand-author the category contracts**

Pay particular attention to provider passthroughs, source-specific support,
model provisioning, architecture-specific variants, inert knobs, and runtime
paths that have only static coverage.

- [ ] **Step 3: Run tests and verify GREEN**

Validate schema, duplicate names, category completeness, and manifest loading.

- [ ] **Step 4: Review and commit**

After claim-by-claim specification and code-quality reviews pass:

```bash
git add services/*/service.yml bootstrapper/tests
git commit -m "docs(services): contract llm and media capabilities"
```

### 1.5. Task 5: Agents, apps, and virtual capability rollout

**Files:**
- Modify: every remaining `services/*/service.yml`
- Modify: focused manifest contract inventory tests as needed

- [ ] **Step 1: Extend repository completeness to all manifests and verify RED**

Expected: the failure lists every remaining unpopulated manifest, including
virtual logical families.

- [ ] **Step 2: Research and hand-author the remaining contracts**

For virtual manifests, describe the configuration/provider contract rather
than implying a container exists. For app/agent services, distinguish UI or
workflow exposure from Atlas-specific integrations that lack live validation.

- [ ] **Step 3: Run tests and verify GREEN**

Validate all 57 manifests and the exact README-only aggregate/pointer set.

- [ ] **Step 4: Review and commit**

After claim-by-claim specification and code-quality reviews pass:

```bash
git add services/*/service.yml bootstrapper/tests
git commit -m "docs(services): contract agent and app capabilities"
```

### 1.6. Task 6: Enforce completeness and regenerate canonical READMEs

**Files:**
- Modify: `bootstrapper/schemas/service.schema.json`
- Modify: `bootstrapper/tests/test_manifests.py`
- Modify: `bootstrapper/tests/test_docs_drift.py`
- Modify: generated `services/*/README.md` capability sections

- [ ] **Step 1: Write failing global gate tests**

Assert `capabilities` is a top-level required field, deleting it from any
synthetic manifest fails schema validation, the README-only exception set is
closed, and generated output changes when a contract changes.

- [ ] **Step 2: Run tests and verify RED**

Expected: top-level omission remains accepted and real READMEs lack generated
sections.

- [ ] **Step 3: Require the field and regenerate**

Make `capabilities` globally required, run the generator for all docs, inspect
every changed section, and run generation a second time to prove byte
idempotence.

```bash
PYTHONPATH=bootstrapper uv run --project bootstrapper \
  python -m bootstrapper.docs.regen --all
PYTHONPATH=bootstrapper uv run --project bootstrapper \
  python -m bootstrapper.docs.regen --all --check
```

- [ ] **Step 4: Run tests and verify GREEN**

Run manifest, writer, regeneration, drift, heading, and internal-link tests.

- [ ] **Step 5: Review generated output and commit**

After specification and code-quality reviews pass:

```bash
git add bootstrapper/schemas/service.schema.json bootstrapper/tests \
  services/*/README.md
git commit -m "docs(services): publish capability contracts"
```

### 1.7. Task 7: Verification, review, and gitflow delivery

**Files:**
- Verify all changed and generated files; no new runtime files.

- [ ] **Step 1: Run focused verification**

```bash
uv run --project bootstrapper pytest \
  bootstrapper/tests/test_manifests.py \
  bootstrapper/tests/test_manifest_validator.py \
  bootstrapper/tests/test_capabilities_section_writer.py \
  bootstrapper/tests/test_docs_regen_fences.py \
  bootstrapper/tests/test_docs_drift.py -q
```

- [ ] **Step 2: Run required repository verification**

```bash
uv run --project bootstrapper pytest bootstrapper/tests -q
PYTHONPATH=bootstrapper uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check
make docs-check
uv run --project bootstrapper python scripts/check_doc_links.py
uv run --project bootstrapper python scripts/check-compose-source-deps.py
uv run --project bootstrapper python scripts/check-kong-routes.py
uv run --project bootstrapper python scripts/validate_research_schema.py --all
uv run --project bootstrapper python scripts/check-track-membership.py
```

Expected: every command exits `0`; regeneration reports no drift.

- [ ] **Step 3: Review the complete diff**

Inspect `git diff origin/develop...HEAD`, validate all 57 manifest contracts,
sample every status/verification combination, inspect all aggregate sections,
and request an independent final review. Resolve every Critical or Important
finding before pushing.

- [ ] **Step 4: Push and merge through gitflow**

Push `codex/967-service-capability-contracts`, create a PR to `develop`, wait
for every required check and conversation to resolve, and squash-merge. Create
a clean promotion branch from current `main`, cherry-pick only the resulting
`develop` squash commit, prove its tree matches `origin/develop`, open a PR to
`main`, wait for all required checks, and squash-merge. Never merge red checks
and never push directly to a protected branch.

- [ ] **Step 5: Verify and clean up**

Confirm #967 is closed/completed, refresh local protected branches, remove the
temporary worktree, prune worktree metadata, and delete the local and remote
feature/promotion branches. Mark the sequential goal complete only after both
protected branches contain #967.
