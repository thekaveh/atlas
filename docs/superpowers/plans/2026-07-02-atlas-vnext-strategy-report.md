# Atlas vNext Strategy Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a decision-ready Atlas vNext strategy report that assesses the project, compares competitors, evaluates MCP and dashboard options, and ranks the top 20 next candidates.

**Architecture:** This is a documentation/research deliverable, not a code feature. The work creates one strategy report from internal repo evidence plus current external primary-source research, then validates the report against the approved design spec and repository documentation conventions.

**Tech Stack:** Markdown, Atlas docs/research corpus, web research with primary sources, Git.

## 1. Global Constraints

- Do not implement new services.
- Do not edit service manifests or compose files.
- Do not make roadmap changes before the report is approved.
- Use current external research for competitors, MCP options, latest-image or latest-feature claims, licensing, and deployment posture.
- Prefer official project docs, GitHub repositories, releases, image registries, protocol specifications, license files, and vendor integration docs.
- Label secondary sources when primary sources are unavailable.
- The final report path is `docs/strategy/atlas-vnext-strategy-report.md`.
- After generating the report, display a summary to the user.

## 2. Acceptance Criteria

- The report states Atlas' strategic position clearly.
- The report contains a candid strengths and weaknesses assessment.
- The report compares current competitors with source links.
- The report gives a practical MCP recommendation.
- The report gives a yes/no recommendation for the Kong-root dashboard.
- The report ranks a top 20 vNext candidate list.
- The report covers 3D/game, trading, RAG, data/ML, security, observability, and platform polish.
- The report separates build-now, build-later, and reject-for-now guidance.
- Major claims can be verified from repo evidence or linked primary sources.

---

## 3. File Structure

- Create: `docs/strategy/atlas-vnext-strategy-report.md`
  - Owns the final analysis, competitor comparison, MCP recommendation, dashboard recommendation, ranked top 20, and implementation waves.
- Read: `docs/superpowers/specs/2026-07-02-atlas-vnext-strategy-report-design.md`
  - Approved scope, methodology, outline, and acceptance criteria.
- Read: `README.md`, `docs/ROADMAP.md`, `docs/research/README.md`, `docs/research/integration-matrix.md`, `docs/research/rows/*.md`, `docs/research/candidates/*.md`, `docs/deployment/ports-and-routes.md`, `docs/CONTRIBUTING-services.md`, `bootstrapper/tracks.yml`, `services/*/service.yml`
  - Internal evidence corpus.

---

### 3.1. Task 1: Create Report Skeleton And Internal Evidence Inventory

**Files:**
- Create: `docs/strategy/atlas-vnext-strategy-report.md`
- Read: `docs/superpowers/specs/2026-07-02-atlas-vnext-strategy-report-design.md`
- Read: `docs/research/integration-matrix.md`
- Read: `bootstrapper/tracks.yml`

**Interfaces:**
- Consumes: approved design spec and internal research index.
- Produces: report file with final headings and an internal-evidence notes section that later tasks replace with polished analysis.

- [ ] **Step 1: Create the strategy docs directory**

Run:

```bash
mkdir -p docs/strategy
```

Expected: `docs/strategy` exists.

- [ ] **Step 2: Add the final report skeleton**

Create `docs/strategy/atlas-vnext-strategy-report.md` with these headings:

```markdown
# Atlas vNext Strategy Report

## 1. Executive Summary

## 2. Current-State Assessment

## 3. Competitor Landscape

## 4. Strategic Gaps

## 5. MCP Recommendation

## 6. Kong Root Dashboard Recommendation

## 7. vNext Top 20

## 8. Track Expansion

## 9. Implementation Waves

## 10. Appendices
```

- [ ] **Step 3: Inventory internal source counts**

Run:

```bash
find services -maxdepth 2 -name service.yml | sort | wc -l
find docs/research/candidates -maxdepth 1 -name '*.md' | sort | wc -l
find docs/research/rows -maxdepth 1 -name '*.md' | sort | wc -l
```

Expected: counts are captured in notes for the report's current-state section.

- [ ] **Step 4: Extract current tracks**

Run:

```bash
sed -n '1,220p' bootstrapper/tracks.yml
```

Expected: track names and service membership are reflected in the report's assessment.

- [ ] **Step 5: Commit the skeleton only if it is useful as an independent checkpoint**

Run:

```bash
git add docs/strategy/atlas-vnext-strategy-report.md
git commit -m "docs: scaffold Atlas vNext strategy report"
```

Expected: commit succeeds, or this step is skipped if Task 1 and Task 2 are completed in the same checkpoint.

---

### 3.2. Task 2: Write Current-State Assessment From Repo Evidence

**Files:**
- Modify: `docs/strategy/atlas-vnext-strategy-report.md`
- Read: `README.md`
- Read: `docs/ROADMAP.md`
- Read: `docs/deployment/ports-and-routes.md`
- Read: `docs/CONTRIBUTING-services.md`
- Read: `services/*/service.yml`

**Interfaces:**
- Consumes: report skeleton.
- Produces: sections 2 and relevant appendix notes covering strengths, weaknesses, and project posture.

- [ ] **Step 1: Read the internal architecture sources**

Run:

```bash
sed -n '1,260p' README.md
sed -n '1,260p' docs/ROADMAP.md
sed -n '1,220p' docs/deployment/ports-and-routes.md
sed -n '1,260p' docs/CONTRIBUTING-services.md
```

Expected: notes cover Atlas' service model, Kong routing, docs/test discipline, and service-addition mechanics.

- [ ] **Step 2: Summarize service manifest inventory**

Run:

```bash
python - <<'PY'
from pathlib import Path
import yaml
rows = []
for path in sorted(Path('services').glob('*/service.yml')):
    data = yaml.safe_load(path.read_text())
    rows.append((path.parent.name, data.get('category'), bool(data.get('virtual')), list((data.get('sources') or {}).keys())))
for name, category, virtual, sources in rows:
    print(f"{name}\t{category}\tvirtual={virtual}\tsources={','.join(sources)}")
PY
```

Expected: manifest categories, virtual services, and source variants inform strengths and operational-cost analysis.

- [ ] **Step 3: Write strengths**

Update section `## 2. Current-State Assessment` with concrete strengths:

```markdown
### 2.1 Strengths

- Source-configurable service model with consistent `container` / `localhost` / `disabled` patterns.
- Kong-centered routing with predictable `*.localhost` aliases.
- Mature per-service manifest and docs generation discipline.
- Tracks model that makes a broad stack selectable by use case.
- Strong self-host primitives: Supabase, Redis, LiteLLM, MinIO, Weaviate, Neo4j, n8n, Open WebUI, JupyterHub, Ray, Spark, Airflow, Prometheus, and Grafana.
- Better-than-usual documentation and drift checks for a compose-native AI stack.
```

Then expand each bullet into concise report prose with repo-specific evidence.

- [ ] **Step 4: Write weaknesses**

Update section `## 2. Current-State Assessment` with concrete weaknesses:

```markdown
### 2.2 Weaknesses

- The root Kong entrypoint is still infrastructure-first rather than product-first.
- Auth and SSO are fragmented across services.
- MCP is documented but not yet implemented as a shared stack capability.
- LLM observability, trace correlation, and eval loops lag behind the service breadth.
- The stack's breadth risks onboarding overload without a dashboard and stronger guided paths.
- Vertical tracks are well imagined but not yet first-class wizard tracks.
```

Then expand each bullet into concise report prose with repo-specific evidence.

- [ ] **Step 5: Validate section 2 against the design spec**

Run:

```bash
rg -n "strength|weakness|SOURCE|Kong|MCP|dashboard|auth|track" docs/strategy/atlas-vnext-strategy-report.md
```

Expected: all approved current-state themes appear in the report.

---

### 3.3. Task 3: Research Competitors And Market Position

**Files:**
- Modify: `docs/strategy/atlas-vnext-strategy-report.md`

**Interfaces:**
- Consumes: current-state assessment.
- Produces: section 3 with source-linked competitor categories and a positioning summary.

- [ ] **Step 1: Research local AI workbench competitors**

Use web research against primary sources for:

```text
Open WebUI
Ollama
LM Studio
AnythingLLM
Jan
Pinokio
```

Expected: section 3 explains what each category offers that Atlas does not, especially polish, app-store-like installation, model UX, and single-purpose simplicity.

- [ ] **Step 2: Research agent and RAG platform competitors**

Use web research against primary sources for:

```text
Dify
RAGFlow
Flowise
LangFlow
LlamaIndex
Haystack
OpenHands
```

Expected: section 3 explains how these tools compare on app-building, RAG pipelines, workflow UX, hosted options, and plugin ecosystems.

- [ ] **Step 3: Research data, ML, and observability competitors**

Use web research against primary sources for:

```text
MLflow
Langfuse
OpenMetadata
Dagster
Superset
Kubernetes-based AI platform stacks
```

Expected: section 3 identifies where Atlas should integrate rather than compete.

- [ ] **Step 4: Write competitor matrix**

Add a table with columns:

```markdown
| Category | Examples | What they do better | What Atlas does better | Strategic implication |
|---|---|---|---|---|
```

Expected: every row has a practical implication, not just comparison prose.

- [ ] **Step 5: Validate sources**

Run:

```bash
rg -n "https?://" docs/strategy/atlas-vnext-strategy-report.md
```

Expected: competitor claims have source links.

---

### 3.4. Task 4: Write MCP And Kong Dashboard Recommendations

**Files:**
- Modify: `docs/strategy/atlas-vnext-strategy-report.md`
- Read: `docs/ROADMAP.md`
- Read: `docs/research/candidates/mcp-gateway.md`
- Read: `docs/research/candidates/mcpo.md`
- Read: `docs/research/candidates/docling-mcp.md`
- Read: `docs/research/candidates/voicebox.md`
- Read: `docs/deployment/ports-and-routes.md`

**Interfaces:**
- Consumes: internal MCP and route research plus current external MCP checks.
- Produces: sections 5 and 6.

- [ ] **Step 1: Verify current MCP options**

Use web research against primary sources for:

```text
Model Context Protocol specification
MetaMCP
Docker MCP Gateway
mcpo
Docling MCP
Open WebUI MCP support
LiteLLM MCP support
Hermes MCP support
```

Expected: section 5 distinguishes verified current support from roadmap or inferred support.

- [ ] **Step 2: Write MCP target/consumer matrix**

Add a table with columns:

```markdown
| Atlas service | MCP role | Recommendation | Rationale |
|---|---|---|---|
```

Expected: Supabase/Postgres, Neo4j, Weaviate, SearXNG, MinIO, n8n, Backend, Docling, Open WebUI, Hermes, OpenClaw, LiteLLM, Ollama, Kong, STT/TTS providers, and virtual services are classified.

- [ ] **Step 3: Write MCP architecture recommendation**

Recommended conclusion to test against evidence:

```markdown
Atlas should adopt a phased hybrid: start with a small curated MCP surface for the highest-value internal tools, expose it through an aggregator when there are enough servers to justify namespacing and policy, and avoid one-MCP-server-per-service cargo culting.
```

Expected: report explains aggregator versus sidecars, curated package versus service-by-service, and security boundaries.

- [ ] **Step 4: Write Kong root dashboard recommendation**

Recommended conclusion to test against evidence:

```markdown
Yes: Atlas should use the Kong root alias as a product entrypoint, but the first version should be a lightweight service directory and health dashboard, not a replacement for Grafana, Supabase Studio, or the setup wizard.
```

Expected: section 6 defines minimum viable dashboard contents: active services, direct and Kong URLs, auth notes, health status, track context, common actions, docs links, and warnings for disabled dependencies.

- [ ] **Step 5: Validate explicit answers**

Run:

```bash
rg -n "phased hybrid|service directory|health dashboard|MCP role|Kong root" docs/strategy/atlas-vnext-strategy-report.md
```

Expected: MCP and dashboard recommendations are easy to find.

---

### 3.5. Task 5: Rank vNext Top 20 And Track Expansions

**Files:**
- Modify: `docs/strategy/atlas-vnext-strategy-report.md`
- Read: `docs/research/candidates/*.md`
- Read: `docs/ROADMAP.md`

**Interfaces:**
- Consumes: repo candidate corpus, external verification, and rubric.
- Produces: sections 7, 8, and 9.

- [ ] **Step 1: Build candidate longlist**

Run:

```bash
python - <<'PY'
from pathlib import Path
for path in sorted(Path('docs/research/candidates').glob('*.md')):
    text = path.read_text()
    name = next((line.split(':', 1)[1].strip() for line in text.splitlines() if line.startswith('name:')), path.stem)
    category = next((line.split(':', 1)[1].strip() for line in text.splitlines() if line.startswith('category-fit:')), 'unknown')
    print(f"{path.stem}\t{name}\t{category}")
PY
```

Expected: the report considers all existing candidate one-pagers before adding outside suggestions.

- [ ] **Step 2: Add outside suggestions to the longlist**

Use web research for services not already covered if they materially improve Atlas:

```text
3D/game: Blender MCP, Unreal Engine MCP, Godot, Hunyuan3D, TRELLIS, NerfStudio, glTF-Transform, LiveKit.
Trading: OpenBB, Hummingbot, NautilusTrader, Freqtrade, TimescaleDB, Redpanda, OpenBao, CCXT, FinRL, FinGPT.
Platform: Authentik, Infisical, OpenTelemetry Collector, Loki, Tempo, Dagster, Lakekeeper, Trino, Superset.
```

Expected: outside additions are either ranked, watchlisted, or rejected with rationale.

- [ ] **Step 3: Apply scoring rubric**

For each finalist, evaluate:

```text
strategic fit
reuse of Atlas primitives
user value
implementation effort
operational cost
security risk
license fit
maintenance burden
dependency blast radius
maturity and upstream health
```

Expected: section 7 top 20 rankings include short rationale, effort, risk, dependencies, and first slice.

- [ ] **Step 4: Write track-expansion recommendations**

Section 8 must include:

```markdown
### 8.1 3D / Game-Generation Track
### 8.2 Trading / Financial-AI Track
### 8.3 RAG And Content-Ingestion Track
### 8.4 Data / ML Platform Track
```

Expected: each track has a first wave, later wave, and safety/defer notes.

- [ ] **Step 5: Write implementation waves**

Section 9 must include:

```markdown
### 9.1 Build Now
### 9.2 Build Next
### 9.3 Build Later
### 9.4 Reject Or Defer For Now
```

Expected: the report gives decision-ready sequencing.

---

### 3.6. Task 6: Validate, Self-Review, And Commit Report

**Files:**
- Modify: `docs/strategy/atlas-vnext-strategy-report.md`

**Interfaces:**
- Consumes: completed report.
- Produces: committed report ready for user review.

- [ ] **Step 1: Check design-spec coverage**

Run:

```bash
for term in "strategic position" "strength" "weakness" "competitor" "MCP" "Kong" "Top 20" "3D" "trading" "RAG" "security" "observability" "build now" "reject"; do
  rg -n "$term" docs/strategy/atlas-vnext-strategy-report.md >/dev/null || echo "missing: $term"
done
```

Expected: no `missing:` lines.

- [ ] **Step 2: Check for incomplete markers**

Run:

```bash
pattern='TO''DO|TB''D|FIX''ME|place''holder|fill'' in|later'' maybe|[?][?]'
rg -n "$pattern" docs/strategy/atlas-vnext-strategy-report.md
```

Expected: no output.

- [ ] **Step 3: Check links are present**

Run:

```bash
rg -n "https?://" docs/strategy/atlas-vnext-strategy-report.md | wc -l
```

Expected: a nonzero count high enough to support competitor and current-state claims.

- [ ] **Step 4: Run markdown link validation**

Run:

```bash
python scripts/check_doc_links.py
```

Expected: command exits 0.

- [ ] **Step 5: Review git diff**

Run:

```bash
git diff -- docs/strategy/atlas-vnext-strategy-report.md
```

Expected: diff contains only the intended report.

- [ ] **Step 6: Commit the report**

Run:

```bash
git add docs/strategy/atlas-vnext-strategy-report.md
git commit -m "docs: add Atlas vNext strategy report"
```

Expected: commit succeeds.

- [ ] **Step 7: Summarize the report to the user**

In the final response, include:

```text
Report path
Commit hash
Top recommendation
Top 5 vNext candidates
Validation commands run
Any verification that could not be completed
```

Expected: user can review the report and decide whether to request changes.
