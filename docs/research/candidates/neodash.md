---
category-fit: apps
generated: 2026-07-04
license: Apache-2.0
name: NeoDash
referenced-by: [neo4j]
slug: neodash
type: external-service
upstream: https://github.com/neo4j-labs/neodash
---

# NeoDash

## Headline
Low-code dashboard builder for Neo4j — design Cypher-powered dashboards (charts, tables, graphs, maps) without writing a custom UI.

## Watchlist decision (2026-07-04)

Keep NeoDash on the watchlist for now: Atlas **must not add `services/neodash/service.yml` yet** because the stack does not yet have richer graph-native application data or a starter dashboard worth presenting. The Atlas root dashboard is already the service entrypoint; NeoDash only makes sense as a data dashboard for Neo4j content, not as another landing page.

The upstream maintenance signal also raises the admission bar. The Neo4j Labs GitHub repository now says the project is no longer maintained, and the Neo4j Labs docs describe the Labs version as unsupported. That does not make NeoDash useless, but a future Atlas PR must explicitly choose between the unsupported Labs Docker image, NeoDash commercial, or a different graph-dashboard path before adding a service.

Root dashboard remains the product/system dashboard for service status, tracks, links, warnings, and docs. NeoDash would complement it only by visualizing namespaced graph data: backend Graphiti projections, Neo4j LLM Graph Builder outputs, GraphRAG evidence, n8n graph automations, or local-deep-researcher traces.

Future service shape, if a later graph-dashboard ticket promotes this:

- Track membership: `rag` by default; optionally `agents` when the selected dashboards use agent/workflow graph data. Do not add it to non-graph tracks by default.
- Service category: `apps`, because it is a user-facing browser UI.
- Source values/default: `NEODASH_SOURCE=disabled|container`, disabled by default.
- Wizard placement: RAG/graph visualization section after Neo4j and at least one graph-producing service, with prompt copy warning that it is only useful after graph data exists.
- Topology and port strategy: allocate one `apps` topology slot for the web UI only when a service manifest is added.
- Kong alias and route behavior: use an explicit `neodash.localhost` alias, not generic `dash.localhost`; route must not imply this is the Atlas root dashboard.
- Direct URL expectations: direct host port for local development; Kong URL for browser use.
- Required dependency: Neo4j. The future manifest's required edge is `neodash -> neo4j`.
- Optional graph producers/consumers: `backend -> neo4j` for Graphiti/readiness projections, `llm-graph-builder -> neo4j` for document graphs, n8n/local-deep-researcher graph writes, and Open WebUI/backend GraphRAG reads.
- Dashboard data gate: include at least one committed starter dashboard or documented import path that queries Atlas-owned labels/properties, not a blank dashboard shell.
- Namespace and safety gate: dashboard queries must be scoped to Atlas-owned label/property/database conventions such as backend Graphiti `group_id` prefixes or LLM Graph Builder labels. Avoid unbounded scans of unrelated Neo4j data.
- Auth posture: prefer read-only Neo4j credentials for dashboard access. If Atlas still exposes only a single admin Neo4j user, do not expose NeoDash broadly.
- Standalone/read-only mode: prefer Standalone/read-only mode for a first slice so users can view curated dashboards without editing queries or dashboard definitions.
- Init companion: maybe. Only add one if Atlas seeds a starter dashboard, read-only Neo4j role, or dashboard metadata; otherwise the service should remain a plain web UI.
- Tests required for a future service PR: manifest validation, source validation, env assembly, topology/category, track membership, Kong route/auth, disabled Neo4j behavior, no default enablement, compose source-permutation coverage, custom `BASE_PORT`, docs drift, and dashboard query fixtures that prove namespaced/read-only Cypher.
- Edge cases: empty Neo4j database, disabled Neo4j, disabled graph producers, unavailable read-only role, stale `.env`, custom `BASE_PORT`, route exposure with admin credentials, unsupported upstream image drift, and generated-doc drift.

## Problem it solves
Today the only way to look at graph data is the raw Neo4j Browser at `graph.localhost`, which targets developers, not analysts. NeoDash could add a shareable dashboard layer so non-Cypher users can explore data the stack writes into Neo4j, but only after Atlas has meaningful graph data and scoped dashboard queries.

## Stack wiring sketch
- Browser → `kong` (route `neodash.localhost`) → neodash container
- neodash → `neo4j` via `bolt://neo4j-graph-db:7687`
- (optional) neodash dashboard definitions stored in `neo4j` itself, persisted across restarts

## Effort
small-to-medium — the container is simple, but a useful Atlas integration needs read-only credentials, a concrete starter dashboard, route/auth decisions, and a clear answer to the unsupported Labs image question.

## Risks & open questions
- Upstream maintenance: the Labs repository is no longer maintained; the Docker image may remain usable but should not be treated as a strategic default without review.
- Auth: NeoDash can reuse Neo4j creds; Atlas should avoid broad exposure unless a read-only Neo4j role or standalone/read-only deployment is available.
- Dashboards live per-user in browser localStorage unless explicitly saved to the DB.
- Root-dashboard confusion: a generic `dash.localhost` alias would be ambiguous now that Atlas has its own root dashboard.
- Namespace risk: dashboards can run arbitrary Cypher against the connected database unless queries and credentials are constrained.

## Why now (and why not sooner)
Not now. Revisit once multiple services write useful, namespaced graph data into Neo4j and Atlas can ship a read-only starter dashboard that clearly complements the root dashboard.

## Upstream evidence
- https://github.com/neo4j-labs/neodash
- https://neo4j.com/labs/neodash/
- https://neo4j.com/labs/neodash/2.4/developer-guide/configuration/
- https://neo4j.com/labs/neodash/2.4/developer-guide/build-and-run/
- https://neo4j.com/labs/neodash/2.4/user-guide/dashboards/
- https://neo4j.com/labs/neodash/2.1/user-guide/faq/
- https://hub.docker.com/r/neo4jlabs/neodash
