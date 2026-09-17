# Atlas vNext Strategy Report Design

> **Status: archived spec** — dated 2026-07-02; a point-in-time record kept for archaeology, not current guidance. The landed outcome is recorded in the [CHANGELOG](../../CHANGELOG.md).

## 1. Purpose

Prepare a decision-ready strategy report for Atlas that evaluates the project as a self-hosted AI, data, ML, automation, and vertical-application platform. The report must be candid about strengths and weaknesses, compare Atlas with adjacent competitors, reconcile existing candidate-service research with current upstream reality, and recommend a ranked top 20 list of vNext candidates.

The report is a strategy artifact, not an implementation plan. It should help decide what to build next and what to defer.

## 2. Deliverables

The work has two deliverables:

1. This design spec, committed before the research/report pass.
2. A full report at `docs/strategy/atlas-vnext-strategy-report.md`.

The full report should include source links for external claims and should summarize the repo evidence it uses. After the report is written, the report summary must be shown to the user.

## 3. Scope

The report will cover:

- Atlas' current strengths and weaknesses.
- Where Atlas delivers today: SOURCE-based configuration, tracks, service manifests, Kong routing, docs, tests, observability, and service breadth.
- Where Atlas falls short: auth and SSO, unified dashboard, MCP/tooling layer, traceability, production operations, multi-user readiness, onboarding complexity, and vertical-track maturity.
- Competitor categories rather than a single direct peer.
- Existing candidate-service research in `docs/research/` and `docs/ROADMAP.md`.
- Fresh external research for competitors, MCP options, candidate service maturity, latest-image or latest-feature claims, licensing, and deployment posture.
- A ranked top 20 vNext candidate list.
- Dedicated recommendations for a Kong-root Atlas dashboard, MCP architecture, 3D/game-generation track, trading/financial-AI track, stronger RAG/content ingestion, observability, security, and platform polish.

Out of scope:

- Implementing new services.
- Editing service manifests or compose files.
- Making roadmap changes before the report is approved.
- Treating marketing claims as fact without checking primary sources when current-state accuracy matters.

## 4. Evidence Sources

Internal evidence should include:

- `README.md`
- `docs/ROADMAP.md`
- `docs/research/README.md`
- `docs/research/integration-matrix.md`
- `docs/research/rows/*.md`
- `docs/research/candidates/*.md`
- `docs/deployment/ports-and-routes.md`
- `docs/CONTRIBUTING-services.md`
- `bootstrapper/tracks.yml`
- `services/*/service.yml`
- relevant service READMEs where needed
- recent git history

External evidence should prioritize primary sources:

- official project docs
- official GitHub repositories and releases
- official container-image registries or image docs
- protocol specifications
- license files
- vendor documentation for integration claims

Secondary sources may be used only for market positioning when primary sources are unavailable, and the report should label them as such.

## 5. Methodology

The report will be built in four layers:

1. Internal repo review.
   Extract Atlas' current architecture, service inventory, track model, documentation maturity, test posture, route model, and existing candidate-service research.

2. External current-state research.
   Verify competitors and candidate services with web research, especially where the answer depends on current versions, current MCP support, recent Docker images, licensing, or project activity.

3. Evaluation rubric.
   Score candidate services and strategic improvements against the same criteria:
   - strategic fit
   - reuse of existing Atlas primitives
   - user value
   - implementation effort
   - operational cost
   - security risk
   - license fit
   - maintenance burden
   - dependency blast radius
   - maturity and upstream health

4. Roadmap synthesis.
   Convert findings into a ranked top 20 and group them into implementation waves: near-term platform polish, MCP/tooling, observability/security, RAG/content ingestion, 3D/game generation, trading/financial AI, and data/ML expansion.

## 6. Competitor Model

Atlas should be compared against categories, because no single platform is an exact peer:

- local AI workbenches and model stacks
- agent and RAG application platforms
- workflow and automation platforms
- self-host data and ML platforms
- observability, tracing, and evaluation platforms
- vertical AI stacks for 3D/game and trading workflows
- vendor ecosystem platforms that overlap pieces of Atlas but are not self-host-first

The report should make clear where Atlas competes directly, where it complements those systems, and where it should avoid competing.

## 7. Report Outline

The full report should use this structure:

1. Executive Summary
2. Current-State Assessment
3. Competitor Landscape
4. Strategic Gaps
5. MCP Recommendation
6. Kong Root Dashboard Recommendation
7. vNext Top 20
8. Track Expansion
9. Implementation Waves
10. Appendices

The appendices should include the scoring rubric, source notes, rejected candidates, and watchlist services.

## 8. Expected Recommendations

The report must answer these questions explicitly:

- Should Atlas add a web interface at the main Kong alias as the entrypoint?
- If yes, what should the minimum viable dashboard include?
- Should Atlas adopt MCP through a single aggregator, service-by-service MCP sidecars, a curated package of ready-made servers, or a phased hybrid?
- Which current services are good MCP targets, which are consumers, and which should not be MCP-wrapped?
- Which candidate services belong in the top 20 vNext list?
- Which candidates should be deferred or rejected for now?
- Does a 3D/game-generation track make sense, and what should its first implementation wave be?
- Does a trading/financial-AI track make sense, and what safety boundaries are required before live trading?
- What platform improvements are more important than adding more services?

## 9. Acceptance Criteria

The report is successful if it provides:

- a clear read on Atlas' strategic position
- a candid strengths and weaknesses assessment
- a current competitor comparison with source links
- a practical MCP recommendation
- a yes/no recommendation for the Kong-root dashboard
- a ranked top 20 vNext candidate list
- concrete suggestions for 3D/game, trading, RAG, data/ML, security, observability, and platform polish
- build-now, build-later, and reject-for-now guidance
- enough source attribution that a reviewer can verify the major claims

## 10. Review Gate

After this spec is committed, the user should review it before the full report is prepared. If the user requests changes, update this spec and rerun the self-review. Only after approval should the full report research and writing pass begin.
