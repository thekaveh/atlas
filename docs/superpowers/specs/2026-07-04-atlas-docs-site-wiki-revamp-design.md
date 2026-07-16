# Atlas Docs Site And Wiki Revamp Design

## 1. Overview

Atlas needs a documentation system that feels as intentional as the platform it describes. The current repository already has useful machinery: a generated MkDocs site, a GitHub Pages workflow, a GitHub Wiki exporter, service READMEs, manifest-driven service metadata, topology data, generated service diagrams, and CI drift checks. The problem is not absence of infrastructure. The problem is that the published surfaces still read and look like thin build artifacts: shallow service pages, a small wiki, a chaotic navigation tree, stock-theme ergonomics, and too little editorial structure.

This design replaces the current generated documentation layer with a polished, synchronized, comprehensive documentation product:

- A Material for MkDocs `.io` site with Atlas-branded dark mode by default and a light-mode toggle.
- A full generated GitHub Wiki companion, not a small stub.
- A stronger information architecture that starts broad and progressively narrows into tracks, services, architecture, configuration, operations, and references.
- Service pages generated from canonical repository sources so new services and service changes update the site and wiki automatically.
- Diagram coverage that uses the required `architecture-diagram` design system for main stack and service dependency depictions.
- CI checks that prevent the published docs, wiki export, navigation, diagrams, and service metadata from drifting away from the repo.

## 2. Goals

### 2.1. Product Goals

- Make the public documentation site feel finished, elegant, technical, and trustworthy.
- Make the GitHub Wiki useful enough for readers who discover Atlas through GitHub's wiki surface.
- Preserve a single source-of-truth model so maintainers do not have to hand-edit README, service docs, wiki pages, and `.io` pages separately.
- Ensure every service addition or service settings change updates the public docs through generator and CI contracts.
- Use the current Atlas hero art and wizard screenshot across the public documentation surfaces.
- Keep all generated documentation hierarchically numbered, from generic overview sections to specific reference details.

### 2.2. Engineering Goals

- Reuse existing doc infrastructure where it is valuable: `scripts/generate-docs-site.py`, `scripts/export-docs-wiki.py`, `scripts/check-docs-site.py`, `docs-pages.yml`, service manifests, topology, tracks, service READMEs, and generated architecture assets.
- Replace weak generated content with richer generated pages rather than creating parallel hand-maintained copies.
- Make the generator modular enough that future workers can improve one area without editing a monolithic script.
- Fail CI when service docs, service manifests, topology, routes, ports, tracks, generated diagrams, wiki pages, or MkDocs navigation drift.
- Keep the implementation compatible with GitHub Pages deployment through Actions.

## 3. Non-Goals

- Do not replace GitHub Pages with a separate hosting stack.
- Do not make the wiki the canonical source of truth.
- Do not hand-copy service READMEs into static MkDocs pages.
- Do not remove service-local documentation ownership under `services/<name>/README.md`.
- Do not perform broad architectural changes to Atlas services as part of the docs revamp.
- Do not use decorative prose that states the obvious about images, diagrams, or code snippets.

## 4. Current-State Findings

### 4.1. Existing Strengths

- `mkdocs.yml` already publishes `docs/` to `site/` with `site_url: https://thekaveh.github.io/atlas/`.
- `.github/workflows/docs-pages.yml` already builds the site, uploads the Pages artifact, deploys to GitHub Pages, and pushes the generated wiki.
- `scripts/check-docs-site.py` already runs the generator, `mkdocs build --strict`, and a built-site link validator.
- `scripts/export-docs-wiki.py` already pushes `docs/wiki/*.md` to `https://github.com/thekaveh/atlas.wiki.git`.
- `bootstrapper/tests/test_docs_site_platform.py` already asserts key docs-site and wiki contracts.
- Service metadata already exists in `services/<name>/service.yml`, `bootstrapper/tracks.yml`, `services/topology.py`, and service READMEs.
- Per-service architecture assets already exist for most services under `services/<name>/architecture.{svg,html}`.

### 4.2. Current Weaknesses

- The current MkDocs theme is the stock `mkdocs` theme with custom CSS. It lacks the navigation ergonomics, search polish, palette toggles, and docs-product feel expected from a serious public documentation site.
- The current generated service pages mostly say "Source README remains the source of truth." They index services but do not explain them.
- The wiki currently contains only a small overview, quick start, service list, architecture pointer, and reference pointer. It is not a useful GitHub-native documentation surface.
- The navigation is mechanically numbered but hard to scan. Important concepts are split across shallow generated pages, root docs, deployment docs, and service READMEs.
- High-level architecture diagrams are generated from hard-coded node lists that are useful placeholders but not yet comparable to the hand-authored top-level diagram or the service-generated diagrams.
- The content tone varies across README, docs, service pages, generated pages, wiki pages, and planning artifacts.
- The docs generator is a single large script, which makes substantial content and theme evolution harder than it needs to be.

## 5. Research Anchors

### 5.1. MkDocs And Theme Direction

Material for MkDocs supports multiple color palettes, including automatic light and dark behavior, explicit dark/light toggles, and custom color variables. That makes it a good fit for an Atlas-branded dark-default site with a light option. Source: [Material for MkDocs color customization](https://squidfunk.github.io/mkdocs-material/setup/changing-the-colors/).

MkDocs uses `mkdocs.yml` as the project configuration entrypoint and supports generated navigation and build settings, which matches Atlas' existing generator pattern. Source: [MkDocs configuration](https://www.mkdocs.org/user-guide/configuration/).

Material also supports additional CSS and theme extension without forking the theme, which is the right level of customization for Atlas' brand layer. Source: [Material for MkDocs customization](https://squidfunk.github.io/mkdocs-material/customization/).

### 5.2. Publication Direction

GitHub Pages supports custom GitHub Actions workflows as a publication source. Atlas already uses this model, so the redesign should strengthen the current workflow instead of moving to a branch-push publishing strategy. Source: [GitHub Pages custom Actions workflow](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).

The GitHub repository About URL can be updated through the repository update API, but the operation requires repository administration write permission. The implementation should attempt this only when the authenticated `gh` user has the required permission and should otherwise print a clear manual command or instruction. Source: [GitHub repository update API](https://docs.github.com/rest/repos/repos#update-a-repository).

## 6. Recommended Approach

### 6.1. Approach

Use the current generator and CI pipeline as the foundation, but rebuild the generated documentation product around a richer internal docs model:

- Add a docs model layer that collects service, track, topology, route, source, env, dependency, diagram, and README metadata.
- Generate both MkDocs pages and GitHub Wiki pages from that shared model.
- Migrate the MkDocs theme to Material for MkDocs with Atlas palette overrides.
- Replace shallow generated service pages with substantial generated service profiles.
- Expand the wiki into a parallel, compact but complete reader surface.
- Add tests that make the desired quality and sync behavior enforceable.

### 6.2. Rationale

This approach preserves the strongest part of the existing system: single-source generation. It also fixes the visible problem: the public docs and wiki do not yet look or read like a mature platform's documentation. A full static rewrite would look better briefly but would drift immediately. A cosmetic theme pass would be faster but would leave the content shallow. The right answer is to upgrade both the generator and the editorial structure.

## 7. Documentation Architecture

### 7.1. Source Model

The generator should build one `DocsModel` from canonical inputs:

- `README.md` for project identity, hero art, primary screenshot, quick-start positioning, and top-level narrative.
- `assets/atlas-source.png`, `assets/atlas-poster.png`, and `docs/screenshots/wizard-running.png` for visual identity.
- `services/<name>/service.yml` for title, category, source values, defaults, env vars, dependencies, runtime behavior, adaptive behavior, and data-flow calls.
- `services/<name>/README.md` for service-owned explanation, setup, troubleshooting, and future-authored details.
- `services/<name>/architecture.{svg,html}` for per-service diagrams.
- `bootstrapper/tracks.yml` for track pages and service membership.
- `services/topology.py` for category, display order, aliases, default ports, and topology rows.
- `.env.example` for concrete default environment values.
- `docs/deployment/ports-and-routes.md` and Kong route generation outputs for access patterns.
- `docs/research/` and `docs/strategy/` for future roadmap references, without making speculative notes part of the main quick-start path.

### 7.2. Generator Modules

The current `scripts/generate-docs-site.py` should be split into focused modules under a new docs generation package. The exact names can vary during implementation, but the responsibilities should be stable:

- `docs_model.py`: load and normalize repo metadata.
- `mkdocs_nav.py`: build numbered navigation.
- `pages_home.py`: render the home and overview pages.
- `pages_services.py`: render service catalog and service profile pages.
- `pages_architecture.py`: render architecture pages and diagram catalogs.
- `pages_tracks.py`: render track pages and matrices.
- `pages_reference.py`: render generated references.
- `wiki_export.py`: render wiki pages from the same model.
- `theme.py`: write or verify theme assets and Material config fragments.

The public commands may remain `scripts/generate-docs-site.py` and `scripts/export-docs-wiki.py` so existing CI and contributor habits do not break.

## 8. Information Architecture

### 8.1. MkDocs Site Navigation

The `.io` site should use a clear numbered navigation tree:

- `1. Overview`
- `2. Quick Start`
- `3. Core Concepts`
- `4. Tracks`
- `5. Service Catalog`
- `6. Architecture`
- `7. Configuration`
- `8. Operations`
- `9. Development`
- `10. Reference`

Each section should move from broad explanation to specific details. The site home should lead with Atlas identity, value, visual proof, and the first practical path. It should not describe the screenshot as a screenshot or explain that a diagram is a diagram; captions should add context that helps the reader understand Atlas.

### 8.2. Service Catalog

The service catalog should be grouped by Atlas categories and tracks rather than by raw folder order:

- Infrastructure
- Data
- LLM
- Agents
- Apps
- Media
- Observability
- Virtual configuration surfaces
- Doc-only aggregate surfaces

Each service card or table row should include:

- Service title and folder name.
- Category.
- Track membership.
- Default enabled state.
- SOURCE variable and default source.
- Available source values.
- Primary direct URL and Kong alias when applicable.
- Required dependencies.
- Downstream callers and upstream services.
- Link to service profile.
- Link to source README.

### 8.3. Service Profile Pages

Each generated service profile should be substantial enough to stand alone in the `.io` site while still linking back to the source README. Required sections:

- `1. Overview`
- `2. Role In Atlas`
- `3. Tracks And Category`
- `4. Access`
- `5. Configuration`
- `6. Dependencies And Topology`
- `7. Source Values`
- `8. Runtime Integration`
- `9. Architecture`
- `10. Operations`
- `11. Source Documentation`

The generator should fill these sections from manifests, topology, track membership, ports/routes, `data_flow.calls`, and service README metadata. When a field is unavailable, the page should say what is known from the manifest rather than using vague placeholder language.

### 8.4. Architecture Section

The architecture section should contain:

- Full-stack overview.
- Bootstrapper lifecycle.
- SOURCE configuration model.
- Track-selection model.
- Kong routing and access topology.
- LLM provider flow.
- RAG and data flow.
- Data engineering lakehouse flow.
- Observability flow.
- Security, auth, and secrets boundary.
- Service admission workflow.
- Per-service dependency diagrams.

The main stack diagrams and any newly regenerated high-level diagrams must use the `architecture-diagram` design system: dark slate background, JetBrains Mono, semantic colors, readable labels, clear spacing, SVG arrows behind component boxes, legends outside boundary boxes, and no overloaded mega-diagram.

## 9. Theme And Visual Design

### 9.1. MkDocs Theme

The site should migrate from `theme.name: mkdocs` to Material for MkDocs.

Required capabilities:

- Dark mode default using an Atlas slate background.
- Light mode toggle.
- Blue, cyan, and electric accents matching the Atlas ASCII/logo visual language.
- Strong search.
- Persistent left navigation.
- Right-side table of contents.
- Clean code blocks.
- Tabbed or grouped reference sections when useful.
- Responsive mobile layout.
- Local assets only for Atlas-specific images.

### 9.2. Visual Tone

The site should feel like a serious local-first engineering platform: dense enough for repeated technical use, visually polished enough for public evaluation, and restrained enough not to read like a marketing splash page.

Avoid:

- Decorative cards nested inside cards.
- Obvious captions such as "This image shows..."
- Giant hero copy that hides the documentation path.
- One-note dark-blue monotony without functional color distinction.
- Placeholder pages that only point elsewhere.

### 9.3. Assets

The following assets should be reused:

- `assets/atlas-poster.png` for brand-forward contexts.
- `assets/atlas-source.png` for the docs home and publication surfaces.
- `docs/screenshots/wizard-running.png` for the quick-start and setup wizard context.
- `docs/diagrams/architecture.svg` as the canonical top-level stack depiction until replaced by a newer `architecture-diagram` compliant asset.

## 10. Wiki Design

### 10.1. Wiki Purpose

The GitHub Wiki should be a compact, GitHub-native companion to the `.io` site. It should not be the full canonical site, but it should be useful without forcing the reader to leave immediately.

### 10.2. Wiki Pages

Generate at least:

- `Home.md`
- `_Sidebar.md`
- `Overview.md`
- `Quick-Start.md`
- `Core-Concepts.md`
- `Tracks.md`
- `Services.md`
- `Architecture.md`
- `Configuration.md`
- `Operations.md`
- `Development.md`
- `Reference.md`

The service wiki page should include a category-grouped service index with SOURCE variables, source values, track membership, dependency summary, and links to the `.io` service pages and source READMEs.

### 10.3. Wiki Sync

The wiki export must remain generated from the same `DocsModel` as the MkDocs site. `scripts/export-docs-wiki.py --check` should fail when the checked-in `docs/wiki/*.md` pages drift. The live wiki push should remain in the Pages workflow after the site build succeeds.

## 11. Synchronization Contract

### 11.1. Service Changes

When a worker changes a service setting or adds a service, the following docs outputs should update automatically through the generator:

- MkDocs navigation.
- Service catalog.
- Service profile page.
- Source values reference.
- Environment variables reference.
- Ports and routes reference.
- Dependency and topology references.
- Track pages.
- Wiki service summary.
- Architecture catalog entries where applicable.

### 11.2. Drift Checks

CI should verify:

- `scripts/generate-docs-site.py --check`
- `scripts/check-docs-site.py`
- `scripts/export-docs-wiki.py --check`
- `mkdocs build --strict`
- Built-site internal links.
- Generated wiki links.
- Every service folder with `service.yml` or `README.md` appears in site and wiki indexes.
- Every service with an architecture asset links it from the generated service profile.
- Every service with a direct port or Kong alias exposes access details in the generated docs.
- Every SOURCE variable in manifests appears in the generated reference.
- Every track in `bootstrapper/tracks.yml` has a track page and service membership table.

## 12. GitHub Pages And Repository About

### 12.1. Pages

Keep the current GitHub Actions publication model. The workflow should build the generated Material site into `site/`, upload it with `actions/upload-pages-artifact`, and deploy it with `actions/deploy-pages`.

### 12.2. Repository About

After the docs site is merged and deployed, update the repository homepage/About URL to:

`https://thekaveh.github.io/atlas/`

The implementation should attempt:

```bash
gh repo edit thekaveh/atlas --homepage https://thekaveh.github.io/atlas/
```

If permissions are insufficient, the implementation should report the required manual action instead of failing the docs build.

## 13. Testing Strategy

### 13.1. Unit And Structural Tests

Expand `bootstrapper/tests/test_docs_site_platform.py` or split it into focused tests:

- MkDocs config uses Material and has Atlas palette configuration.
- Navigation is numbered and points only to real pages.
- Site home includes Atlas hero art and wizard screenshot.
- Service catalog covers every service, virtual manifest, and doc-only README folder.
- Service profile pages include required sections.
- Service pages include category, track membership, SOURCE values, access, dependencies, and diagram links.
- Wiki export contains the required page set and full service summary.
- Generated references cover SOURCE, env vars, tracks, ports, and dependencies.
- Theme assets use local Atlas CSS and local images.

### 13.2. Build Tests

Preserve and strengthen:

- `mkdocs build --strict`
- built-site internal link validation
- wiki export drift validation
- generated docs drift checks

### 13.3. Visual Review

Before merging implementation, run a local MkDocs server or static build preview and inspect:

- Desktop home page.
- Mobile home page.
- Service catalog.
- A complex service profile, such as Supabase, Airflow, Spark, or Open WebUI.
- Architecture catalog.
- Light mode.
- Dark mode.

The implementation should include screenshots in the PR notes when the visual changes are substantial.

## 14. Implementation Phases

### 14.1. Phase 1: Docs Model And Material Foundation

- Add Material for MkDocs dependency.
- Generate Material-compatible `mkdocs.yml`.
- Implement Atlas palette, dark-default mode, light toggle, search, nav, and local CSS.
- Preserve Pages workflow.
- Keep existing pages building while the new structure lands.

### 14.2. Phase 2: Information Architecture And Home Pages

- Replace shallow static pages with full Overview, Quick Start, Core Concepts, Tracks, Architecture, Configuration, Operations, Development, and Reference sections.
- Reuse Atlas hero art and wizard screenshot.
- Normalize tone and numbered hierarchy.

### 14.3. Phase 3: Service Catalog And Service Profiles

- Generate category-grouped service catalog.
- Generate substantial service profile pages for every service family.
- Add source values, track membership, access URLs, topology, dependencies, runtime calls, and diagram links.
- Ensure service additions automatically appear in all generated surfaces.

### 14.4. Phase 4: Wiki Expansion

- Expand wiki page set.
- Generate full service summaries.
- Keep wiki content compact but useful.
- Preserve live wiki push after successful Pages build.

### 14.5. Phase 5: Diagram Refresh

- Regenerate or replace high-level architecture pages using the `architecture-diagram` design system.
- Ensure every service profile links to existing per-service diagrams.
- For services missing diagrams, add generated diagrams through the established per-service docs generation path.

### 14.6. Phase 6: About URL And Release Hygiene

- Update repository About/homepage URL when permissions allow.
- Add contributor guidance for the docs generator.
- Update CI tests and docs maintainer commands.
- Merge via PR after CI is green.

## 15. Risks And Mitigations

### 15.1. Scope Creep

Risk: A full docs rewrite can expand indefinitely.

Mitigation: Keep this effort focused on the docs system, IA, theme, generated service pages, wiki, diagrams, and CI. Do not rewrite every service README by hand unless the generated profile exposes a specific broken source doc.

### 15.2. Generator Complexity

Risk: The generator becomes harder to maintain if all logic stays in one large script.

Mitigation: Split the generator into focused modules with tests around model loading and page rendering.

### 15.3. Broken Links

Risk: Reorganizing pages can break repo and site links.

Mitigation: Preserve compatibility stubs where needed, run built-site link validation, and keep link checks in CI.

### 15.4. Theme Dependency

Risk: Material for MkDocs adds a dependency beyond plain MkDocs.

Mitigation: Pin it in the bootstrapper dev dependencies with a bounded version range and keep customization in local CSS rather than a fork.

### 15.5. Wiki Rendering Limits

Risk: GitHub Wiki supports fewer theme and layout features than the `.io` site.

Mitigation: Generate wiki pages as clean GitHub Markdown with simple tables, relative wiki links, and links back to the `.io` site for richer pages.

## 16. Acceptance Criteria

- The `.io` site builds with Material for MkDocs, dark mode by default, a light option, Atlas blue/cyan accents, and local Atlas assets.
- The home page uses current Atlas hero art and the setup wizard screenshot.
- The navigation is hierarchically numbered and organized into the approved top-level sections.
- The generated service catalog includes every service family, virtual manifest, and doc-only service folder.
- Every generated service profile includes overview, tracks/category, access, configuration, dependencies/topology, source values, runtime integration, architecture, operations, and source documentation.
- The wiki export contains the expanded page set and a full category-grouped service index.
- The site and wiki are generated from the same normalized docs model.
- Service additions and service setting changes are reflected in MkDocs and wiki outputs through generator reruns.
- High-level diagrams and any newly generated diagrams follow the `architecture-diagram` design system.
- CI fails on generated site drift, wiki drift, broken internal site links, missing service docs entries, missing SOURCE references, and missing track references.
- The repository About/homepage URL is set to `https://thekaveh.github.io/atlas/` or a clear permission-limited manual action is reported.

## 17. Open Decisions For Implementation Planning

- Decide exact module names and package location for the docs generator refactor.
- Decide whether to generate all service profile prose entirely from structured data or blend generated structured sections with selected README excerpts.
- Decide whether missing service diagrams should be generated in the docs-site branch or handled through the existing service docs regen path first.
- Decide whether compatibility redirects/stubs are needed for old generated page paths after the navigation is reorganized.

## 18. Review Notes

This design intentionally treats the existing docs pipeline as valuable infrastructure while rejecting the current output quality. The implementation should therefore feel like a product-quality documentation rebuild, not a new coat of CSS over shallow generated pages.
