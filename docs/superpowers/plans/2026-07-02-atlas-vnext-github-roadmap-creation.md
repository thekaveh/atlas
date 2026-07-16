# Atlas vNext GitHub Roadmap Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the GitHub labels, GitHub Project, epic issues, Build Now issues, backlog issues, watchlist issues, and decision issues described in the Atlas vNext GitHub Issues and Project Design.

**Architecture:** Use the committed design spec as the source of truth and the GitHub CLI as the execution layer. Create the durable GitHub objects in batches: labels first, Project and fields next, then issues by wave/type, then Project membership and verification.

**Tech Stack:** GitHub CLI (`gh`), GitHub Issues, GitHub Projects v2, Markdown issue bodies, existing Atlas docs.

## 1. Global Constraints

- Repository: `thekaveh/atlas`.
- Project owner: `thekaveh`.
- Project title: `Atlas vNext Roadmap`.
- Source spec: `docs/superpowers/specs/2026-07-02-atlas-vnext-github-issues-project-design.md`.
- Report source: `docs/strategy/atlas-vnext-strategy-report.md`.
- Expected issue count: `55` issues with label `vnext`.
- Do not create active Build Now status for Build Next, Build Later, watchlist, deferred, rejected-for-now, or already-shipped issues.
- Do not create a second Project if `Atlas vNext Roadmap` already exists; reuse it.
- Do not delete or mutate unrelated GitHub Projects, labels, or issues.

---

## 2. File Structure

No product code changes are required.

- Read: `docs/superpowers/specs/2026-07-02-atlas-vnext-github-issues-project-design.md`
  - Source of truth for issue titles, labels, Project fields, issue counts, and acceptance criteria.
- Read: `docs/strategy/atlas-vnext-strategy-report.md`
  - Source of report section links already embedded in the design spec.
- No committed script is required for the first pass.
  - If a worker wants automation, create temporary files under `/tmp/atlas-vnext-issues/` and do not commit them.

## 3. Task 1: Verify GitHub CLI Access

**Files:**
- Read: `docs/superpowers/specs/2026-07-02-atlas-vnext-github-issues-project-design.md`

**Interfaces:**
- Consumes: GitHub CLI authentication.
- Produces: Verified ability to create issues and Projects.

- [ ] **Step 1: Confirm auth scopes**

Run:

```bash
gh auth status
```

Expected:

- Logged in as `thekaveh`.
- Token scopes include `repo` and `project`.

- [ ] **Step 2: Confirm repo access**

Run:

```bash
gh repo view thekaveh/atlas --json nameWithOwner,url,defaultBranchRef
```

Expected:

- `nameWithOwner` is `thekaveh/atlas`.
- `defaultBranchRef.name` is `main`.

- [ ] **Step 3: Confirm Project access**

Run:

```bash
gh project list --owner thekaveh --format json
```

Expected:

- Command exits 0.
- Existing Projects can be listed.

## 4. Task 2: Create Missing Labels

**Files:**
- Read: `docs/superpowers/specs/2026-07-02-atlas-vnext-github-issues-project-design.md`

**Interfaces:**
- Consumes: label list from spec section 4.
- Produces: GitHub labels available for issue creation.

- [ ] **Step 1: Snapshot current labels**

Run:

```bash
gh label list --repo thekaveh/atlas --limit 200
```

Expected:

- Existing labels are printed.

- [ ] **Step 2: Create labels idempotently**

Run this exact shell loop:

```bash
while IFS='|' read -r name color description; do
  gh label create "$name" \
    --repo thekaveh/atlas \
    --color "$color" \
    --description "$description" \
  || gh label edit "$name" \
    --repo thekaveh/atlas \
    --color "$color" \
    --description "$description"
done <<'LABELS'
vnext|5319e7|Atlas vNext roadmap work
type:epic|5319e7|Roadmap epic grouping related issues
type:implementation|0e8a16|Implementation work item
type:evaluation|fbca04|Evaluation or spike before implementation
type:decision|d29922|Captured roadmap decision or deferral
type:watchlist|c5def5|Candidate to revisit later
wave:build-now|0e8a16|Build Now wave
wave:build-next|fbca04|Build Next wave
wave:build-later|d4c5f9|Build Later wave
wave:watchlist|c5def5|Watchlist item
wave:deferred|d29922|Deferred item
wave:rejected-for-now|b60205|Rejected for now
wave:already-shipped|bfd4f2|Already shipped baseline
track:platform|1d76db|Platform and product surface
track:mcp|5319e7|Model Context Protocol work
track:observability|0052cc|Observability, tracing, logs, evals
track:rag|0e8a16|RAG, GraphRAG, ingestion, retrieval
track:async-jobs|fbca04|Async jobs and background workers
track:data-eng|0e8a16|Data engineering and lakehouse work
track:data-ml|006b75|Data and ML platform work
track:identity-security|d29922|Identity, auth, secrets, security
track:creative-3d|f9d0c4|Creative and 3D track work
track:trading|b60205|Trading and financial research work
track:voice|bfdadc|Voice and realtime speech work
track:infra|5319e7|Infrastructure support work
track:decision|d29922|Decision record issue
risk:low|0e8a16|Low implementation or operating risk
risk:medium|fbca04|Medium implementation or operating risk
risk:high|d29922|High implementation or operating risk
risk:unknown|cfd3d7|Risk not yet estimated
effort:small|0e8a16|Small estimated effort
effort:medium|fbca04|Medium estimated effort
effort:large|d29922|Large estimated effort
effort:unknown|cfd3d7|Effort not yet estimated
epic:dashboard|1d76db|Belongs to Atlas Root Dashboard epic
epic:mcp|5319e7|Belongs to MCP Package epic
epic:observability|0052cc|Belongs to Observability epic
epic:ingestion|0e8a16|Belongs to Ingestion epic
epic:async-jobs|fbca04|Belongs to Async Jobs epic
epic:lakehouse|0e8a16|Belongs to Data Engineering Lakehouse epic
source:data-eng-lab|5319e7|Derived from data-eng-lab enablement handoff
LABELS
```

Expected:

- Every command exits successfully or edits an existing label.

- [ ] **Step 3: Verify labels**

Run:

```bash
gh label list --repo thekaveh/atlas --limit 200 | rg '^(vnext|type:|wave:|track:|risk:|effort:|epic:)'
```

Expected:

- All labels listed in the design spec section 4 are present.

## 5. Task 3: Create Or Reuse The Project

**Files:**
- Read: `docs/superpowers/specs/2026-07-02-atlas-vnext-github-issues-project-design.md`

**Interfaces:**
- Consumes: Project title and field list from spec section 3.
- Produces: Project number and fields.

- [ ] **Step 1: Check for existing Project**

Run:

```bash
gh project list --owner thekaveh --format json --jq '.projects[] | select(.title=="Atlas vNext Roadmap") | {number,title,url}'
```

Expected:

- If output contains a number, reuse that Project number.
- If output is empty, continue to Step 2.

- [ ] **Step 2: Create Project if missing**

Run only if Step 1 returned no Project:

```bash
gh project create --owner thekaveh --title "Atlas vNext Roadmap" --format json
```

Expected:

- JSON output contains `number`, `id`, `title`, and `url`.

- [ ] **Step 3: Export Project number**

Run this, replacing `<number>` with the created or reused Project number:

```bash
export ATLAS_VNEXT_PROJECT_NUMBER=<number>
```

Expected:

- `echo "$ATLAS_VNEXT_PROJECT_NUMBER"` prints the Project number.

- [ ] **Step 4: Create Project fields**

Run:

```bash
gh project field-create "$ATLAS_VNEXT_PROJECT_NUMBER" --owner thekaveh --name "Roadmap Status" --data-type SINGLE_SELECT --single-select-options "Build Now,Ready,Backlog,Watchlist,Deferred,Rejected For Now,Blocked,Done"
gh project field-create "$ATLAS_VNEXT_PROJECT_NUMBER" --owner thekaveh --name "Wave" --data-type SINGLE_SELECT --single-select-options "Build Now,Build Next,Build Later,Watchlist,Deferred,Rejected For Now,Already Shipped"
gh project field-create "$ATLAS_VNEXT_PROJECT_NUMBER" --owner thekaveh --name "Track" --data-type SINGLE_SELECT --single-select-options "platform,mcp,observability,rag,async-jobs,data-eng,data-ml,identity-security,creative-3d,trading,voice,infra,decision"
gh project field-create "$ATLAS_VNEXT_PROJECT_NUMBER" --owner thekaveh --name "Effort" --data-type SINGLE_SELECT --single-select-options "small,medium,large,unknown"
gh project field-create "$ATLAS_VNEXT_PROJECT_NUMBER" --owner thekaveh --name "Risk" --data-type SINGLE_SELECT --single-select-options "low,medium,high,unknown"
gh project field-create "$ATLAS_VNEXT_PROJECT_NUMBER" --owner thekaveh --name "Priority" --data-type SINGLE_SELECT --single-select-options "P0,P1,P2,P3"
gh project field-create "$ATLAS_VNEXT_PROJECT_NUMBER" --owner thekaveh --name "Type" --data-type SINGLE_SELECT --single-select-options "epic,implementation,evaluation,decision,watchlist"
gh project field-create "$ATLAS_VNEXT_PROJECT_NUMBER" --owner thekaveh --name "Source" --data-type TEXT
```

Expected:

- Each command exits 0.
- If any field already exists, skip that field and verify in Step 5.

- [ ] **Step 5: Verify Project fields**

Run:

```bash
gh project field-list "$ATLAS_VNEXT_PROJECT_NUMBER" --owner thekaveh --format json
```

Expected:

- Fields include GitHub's built-in `Status` plus custom fields `Roadmap Status`, `Wave`, `Track`, `Effort`, `Risk`, `Priority`, `Type`, and `Source`.

## 6. Task 4: Create Epic Issues

**Files:**
- Read: `docs/superpowers/specs/2026-07-02-atlas-vnext-github-issues-project-design.md` sections 6.1 through 6.6

**Interfaces:**
- Consumes: epic issue content from spec section 6.
- Produces: 6 GitHub epic issues.

- [ ] **Step 1: Create temporary body files**

Create one temporary Markdown body file under `/tmp/atlas-vnext-issues/` per epic. Each body must contain the exact source links and acceptance criteria from spec sections 6.1 through 6.6.

Run:

```bash
mkdir -p /tmp/atlas-vnext-issues
```

Expected:

- Directory exists.

- [ ] **Step 2: Create each epic issue**

Run one `gh issue create` command per epic:

```bash
gh issue create --repo thekaveh/atlas --project "Atlas vNext Roadmap" --title "Epic: Atlas Root Dashboard" --label "vnext,type:epic,track:platform,risk:low,effort:medium,epic:dashboard,wave:build-now" --body-file /tmp/atlas-vnext-issues/epic-atlas-root-dashboard.md
gh issue create --repo thekaveh/atlas --project "Atlas vNext Roadmap" --title "Epic: MCP Package" --label "vnext,type:epic,track:mcp,risk:medium,effort:medium,epic:mcp,wave:build-now" --body-file /tmp/atlas-vnext-issues/epic-mcp-package.md
gh issue create --repo thekaveh/atlas --project "Atlas vNext Roadmap" --title "Epic: Observability" --label "vnext,type:epic,track:observability,risk:medium,effort:medium,epic:observability,wave:build-now" --body-file /tmp/atlas-vnext-issues/epic-observability.md
gh issue create --repo thekaveh/atlas --project "Atlas vNext Roadmap" --title "Epic: Ingestion" --label "vnext,type:epic,track:rag,risk:medium,effort:medium,epic:ingestion,wave:build-now" --body-file /tmp/atlas-vnext-issues/epic-ingestion.md
gh issue create --repo thekaveh/atlas --project "Atlas vNext Roadmap" --title "Epic: Async Jobs" --label "vnext,type:epic,track:async-jobs,risk:medium,effort:small,epic:async-jobs,wave:build-now" --body-file /tmp/atlas-vnext-issues/epic-async-jobs.md
gh issue create --repo thekaveh/atlas --project "Atlas vNext Roadmap" --title "Epic: Data Engineering Lakehouse Enablement" --label "vnext,type:epic,track:data-eng,track:data-ml,risk:medium,effort:large,epic:lakehouse,wave:build-now,source:data-eng-lab" --body-file /tmp/atlas-vnext-issues/epic-data-engineering-lakehouse-enablement.md
```

Expected:

- Each command prints a GitHub issue URL.

- [ ] **Step 3: Verify epic count**

Run:

```bash
gh issue list --repo thekaveh/atlas --label "vnext" --label "type:epic" --state open --limit 20
```

Expected:

- Exactly 6 open epic issues are shown.

## 7. Task 5: Create Build Now Issues

**Files:**
- Read: `docs/superpowers/specs/2026-07-02-atlas-vnext-github-issues-project-design.md` sections 7.1 through 7.9

**Interfaces:**
- Consumes: Build Now issue content from spec section 7.
- Produces: 9 active implementation issues.

- [ ] **Step 1: Create temporary body files**

Create one temporary Markdown body file under `/tmp/atlas-vnext-issues/` per Build Now issue. Each body must contain summary, source links, why, scope, out of scope, dependencies, Service Admission Contract where applicable, acceptance criteria, and validation guidance drawn from spec sections 7.1 through 7.9.

- [ ] **Step 2: Create each Build Now issue**

Run:

```bash
gh issue create --repo thekaveh/atlas --project "Atlas vNext Roadmap" --title "Build Now: Atlas Root Dashboard" --label "vnext,enhancement,type:implementation,wave:build-now,track:platform,risk:low,effort:medium,epic:dashboard" --body-file /tmp/atlas-vnext-issues/build-now-atlas-root-dashboard.md
gh issue create --repo thekaveh/atlas --project "Atlas vNext Roadmap" --title "Build Now: Curated MCP Package" --label "vnext,enhancement,type:implementation,wave:build-now,track:mcp,risk:medium,effort:medium,epic:mcp" --body-file /tmp/atlas-vnext-issues/build-now-curated-mcp-package.md
gh issue create --repo thekaveh/atlas --project "Atlas vNext Roadmap" --title "Build Now: Langfuse Gateway Tracing" --label "vnext,enhancement,type:implementation,wave:build-now,track:observability,risk:medium,effort:medium,epic:observability" --body-file /tmp/atlas-vnext-issues/build-now-langfuse-gateway-tracing.md
gh issue create --repo thekaveh/atlas --project "Atlas vNext Roadmap" --title "Build Now: Crawl4AI Extraction Path" --label "vnext,enhancement,type:implementation,wave:build-now,track:rag,risk:medium,effort:small,epic:ingestion" --body-file /tmp/atlas-vnext-issues/build-now-crawl4ai-extraction-path.md
gh issue create --repo thekaveh/atlas --project "Atlas vNext Roadmap" --title "Build Now: Celery + Flower Worker Tier" --label "vnext,enhancement,type:implementation,wave:build-now,track:async-jobs,risk:medium,effort:small,epic:async-jobs" --body-file /tmp/atlas-vnext-issues/build-now-celery-flower-worker-tier.md
gh issue create --repo thekaveh/atlas --project "Atlas vNext Roadmap" --title "Build Now: Supavisor Transaction Pooler" --label "vnext,enhancement,type:implementation,wave:build-now,track:infra,risk:medium,effort:medium" --body-file /tmp/atlas-vnext-issues/build-now-supavisor-transaction-pooler.md
gh issue create --repo thekaveh/atlas --project "Atlas vNext Roadmap" --title "Build Now: Apache Tika Fallback Extractor" --label "vnext,enhancement,type:implementation,wave:build-now,track:rag,risk:medium,effort:small,epic:ingestion" --body-file /tmp/atlas-vnext-issues/build-now-apache-tika-fallback-extractor.md
gh issue create --repo thekaveh/atlas --project "Atlas vNext Roadmap" --title "Build Now: Iceberg REST Catalog And Lakehouse Buckets" --label "vnext,enhancement,type:implementation,wave:build-now,track:data-eng,track:data-ml,risk:medium,effort:medium,epic:lakehouse,source:data-eng-lab" --body-file /tmp/atlas-vnext-issues/build-now-iceberg-rest-catalog-and-lakehouse-buckets.md
gh issue create --repo thekaveh/atlas --project "Atlas vNext Roadmap" --title "Build Now: Iceberg Spark Runtime And Default Catalog Config" --label "vnext,enhancement,type:implementation,wave:build-now,track:data-eng,track:data-ml,risk:medium,effort:medium,epic:lakehouse,source:data-eng-lab" --body-file /tmp/atlas-vnext-issues/build-now-iceberg-spark-runtime-and-default-catalog-config.md
```

Expected:

- Each command prints a GitHub issue URL.

- [ ] **Step 3: Verify Build Now count**

Run:

```bash
gh issue list --repo thekaveh/atlas --label "vnext" --label "wave:build-now" --state open --limit 30
```

Expected:

- 15 open issues are shown: 6 epics plus 9 Build Now implementation issues.

## 8. Task 6: Create Backlog, Watchlist, And Decision Issues

**Files:**
- Read: `docs/superpowers/specs/2026-07-02-atlas-vnext-github-issues-project-design.md` sections 8 through 11

**Interfaces:**
- Consumes: backlog/watchlist/decision issue content from spec sections 8 through 11.
- Produces: 40 additional GitHub issues.

- [ ] **Step 1: Create Build Next issues**

Create the 12 issues from spec sections 8.1 through 8.12, using each section title as the GitHub issue title and each section's labels. Add every issue to `Atlas vNext Roadmap`.

Expected titles:

- `Build Next: SSO Pilot With Authentik First`
- `Build Next: Secrets Manager With Infisical First`
- `Build Next: OpenTelemetry Collector + Tempo + Loki`
- `Build Next: MLflow Tracking And Artifact Store`
- `Build Next: Open WebUI Pipelines`
- `Build Next: Neo4j LLM Knowledge Graph Builder`
- `Build Next: Verba RAG UI`
- `Build Next: Label Studio Review Loop`
- `Build Next: Zeppelin Spark And Iceberg Interpreter Auto-Seeding`
- `Build Next: JupyterHub Lakehouse Python Libraries`
- `Build Next: Airflow S3A SparkSubmitOperator Path`
- `Build Next: Data-Eng Track And Wizard Lakehouse Coverage`

- [ ] **Step 2: Create Build Later issues**

Create the 5 issues from spec sections 9.1 through 9.5, using each section title as the GitHub issue title and each section's labels. Add every issue to `Atlas vNext Roadmap`. Section 9.3 is the Jenkins Maven Spark app builder issue from the `data-eng-lab` handoff; the old generic Iceberg/DuckDB issue is superseded by the lakehouse epic plus A1/A2.

- [ ] **Step 3: Create Watchlist issues**

Create the 10 issues from spec sections 10.1 through 10.10, using each section title as the GitHub issue title and each section's labels. Add every issue to `Atlas vNext Roadmap`. Section 10.6 is the Trino-over-Iceberg REST watchlist issue from the `data-eng-lab` A7 request.

- [ ] **Step 4: Create decision issues**

Create the 13 issues from spec sections 11.1 through 11.13, using each section title as the GitHub issue title and each section's labels. Add every issue to `Atlas vNext Roadmap`.

- [ ] **Step 5: Verify total vNext issue count**

Run:

```bash
gh issue list --repo thekaveh/atlas --label "vnext" --state open --limit 100 --json number,title,labels
```

Expected:

- JSON contains 55 open issues if there were no pre-existing `vnext` issues.
- If pre-existing `vnext` issues exist, compare titles against the expected 55 titles from the spec and report the difference.

## 9. Task 7: Populate Project Fields

**Files:**
- Read: `docs/superpowers/specs/2026-07-02-atlas-vnext-github-issues-project-design.md`

**Interfaces:**
- Consumes: Project items created by Tasks 4 through 6.
- Produces: Project fields populated enough for prioritization.

- [ ] **Step 1: List Project fields and options**

Run:

```bash
gh project field-list "$ATLAS_VNEXT_PROJECT_NUMBER" --owner thekaveh --format json > /tmp/atlas-vnext-fields.json
```

Expected:

- `/tmp/atlas-vnext-fields.json` contains field IDs and single-select option IDs for Roadmap Status, Wave, Track, Effort, Risk, Priority, Type, and Source.

- [ ] **Step 2: List Project items**

Run:

```bash
gh project item-list "$ATLAS_VNEXT_PROJECT_NUMBER" --owner thekaveh --limit 100 --format json > /tmp/atlas-vnext-items.json
```

Expected:

- `/tmp/atlas-vnext-items.json` contains all 55 created issue items.

- [ ] **Step 3: Set fields for Build Now and epics first**

Use `gh project item-edit` with the relevant field IDs and single-select option IDs from `/tmp/atlas-vnext-fields.json`.

For every issue labeled `wave:build-now`:

- Set `Wave` to `Build Now`.
- Set `Roadmap Status` to `Build Now`.
- Set `Type` from `type:*`.
- Set `Risk` from `risk:*`.
- Set `Effort` from `effort:*`.
- Set `Track` from the primary `track:*` label.

Expected:

- All 12 Build Now items have useful Project fields.

- [ ] **Step 4: Set fields for non-active items**

For all remaining vNext issues:

- `wave:build-next` -> Wave `Build Next`, Roadmap Status `Backlog`.
- `wave:build-later` -> Wave `Build Later`, Roadmap Status `Backlog`.
- `wave:watchlist` -> Wave `Watchlist`, Roadmap Status `Watchlist`.
- `wave:deferred` -> Wave `Deferred`, Roadmap Status `Deferred`.
- `wave:rejected-for-now` -> Wave `Rejected For Now`, Roadmap Status `Rejected For Now`.
- `wave:already-shipped` -> Wave `Already Shipped`, Roadmap Status `Done`.

Expected:

- No non-Build-Now issue has Roadmap Status `Build Now`.

## 10. Task 8: Final Verification

**Files:**
- Read: `docs/superpowers/specs/2026-07-02-atlas-vnext-github-issues-project-design.md`

**Interfaces:**
- Consumes: GitHub labels, issues, and Project state.
- Produces: completion evidence.

- [ ] **Step 1: Verify label coverage**

Run:

```bash
gh label list --repo thekaveh/atlas --limit 200 | rg '^(vnext|type:|wave:|track:|risk:|effort:|epic:)'
```

Expected:

- All labels from spec section 4 are present.

- [ ] **Step 2: Verify issue count**

Run:

```bash
gh issue list --repo thekaveh/atlas --label "vnext" --state open --limit 100 --json number,title,url | jq length
```

Expected:

- `55`, unless pre-existing `vnext` issues were found and reported.

- [ ] **Step 3: Verify active wave count**

Run:

```bash
gh issue list --repo thekaveh/atlas --label "wave:build-now" --state open --limit 30 --json number,title,url | jq length
```

Expected:

- `15`.

- [ ] **Step 4: Verify Project membership**

Run:

```bash
gh project item-list "$ATLAS_VNEXT_PROJECT_NUMBER" --owner thekaveh --limit 100 --format json | jq '.items | length'
```

Expected:

- At least `55`; more is acceptable only if the Project had existing items.

- [ ] **Step 5: Commit plan file**

Run:

```bash
git add docs/superpowers/plans/2026-07-02-atlas-vnext-github-roadmap-creation.md
git commit -m "docs: plan vnext github roadmap creation"
```

Expected:

- Commit succeeds.
