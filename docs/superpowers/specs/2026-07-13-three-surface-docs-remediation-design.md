# Atlas Three-Surface Documentation Remediation Design

## 1. Purpose

Atlas documentation must present the same public information through the repository, the MkDocs site, and the GitHub wiki without requiring authors to maintain three copies. The public surfaces must be deterministic, self-contained, locally illustrated, and derived from committed canonical documentation plus Atlas's existing service manifests and generated dependency sections.

## 2. Canonical Sources

The canonical public sources are:

- Authored Markdown under `docs/`, excluding internal `research/`, `strategy/`, `maintenance/`, and `superpowers/` material unless explicitly admitted by the public manifest.
- Full service documentation in `services/<name>/README.md`.
- Service metadata in `services/<name>/service.yml`, `bootstrapper/tracks.yml`, and the topology registry.
- Architecture diagram masters in `docs/diagrams/*.html` and `services/<name>/architecture.html`.
- `docs/manifest.yaml`, which declares the public hierarchy, numbering, source paths, generated reference pages, and diagram associations.

Humans do not edit generated site or wiki trees. Dynamic references remain reproducible products of the same service metadata already used by Atlas.

## 3. Projection Pipeline

The `scripts.docs` Python package owns manifest validation, link classification, Markdown transformation, diagram rendering, page assembly, contract checks, and wiki publication. It produces:

- `generated/site/`, consumed by a generated root `mkdocs.yml`.
- `generated/wiki/`, containing `Home.md`, numbered pages, `_Sidebar.md`, `_Footer.md`, and local images.
- `docs/diagrams/img/*.png`, committed raster exports of HTML diagram masters for repository and wiki rendering.

Both generated surfaces consume the same ordered page records. Surface transforms only change navigation targets, headings, and local asset paths; they do not maintain independent prose templates.

## 4. Compatibility

Atlas's existing `bootstrapper.docs.regen` service documentation generator remains authoritative for dependency sections and service diagrams. Existing documentation commands become compatibility wrappers around `python -m scripts.docs...` so downstream automation has a migration path. The public manifest uses full service READMEs rather than the previous abbreviated service summaries.

## 5. Self-Containment

Generated Markdown may link to external product references but may not link to GitHub source views, the GitHub wiki, or the other Atlas documentation surface. Known public Markdown links are rewritten through the manifest. Repository-only files and unmanifested Markdown links become readable text rather than dead links. Every diagram and image required by a page is copied into that surface.

The root README describes Atlas and repository navigation only. It does not discuss MkDocs, wiki synchronization, publishing mechanics, or the `.io` site.

## 6. Validation And Publication

The documentation gate verifies manifest integrity, page completeness, deterministic content hashes, self-containment, placeholders, local assets, heading numbering, strict MkDocs output, and wiki dry-run behavior. Existing Atlas drift, link, compose, route, research, track, and test suites remain in place.

Feature branches merge into `develop`; all merge-gating workflows cover both `develop` and `main`. Pages and wiki publication remain `main`-only. Wiki publication uses a dedicated write deploy key when configured and refuses a network push without it.

## 7. Deliberate Scope

Notebook execution remains a separate reproducibility concern, as required by the three-surface contract. This remediation adds a fast, separate notebook source-reproducibility job that enforces notebook format, stable cell IDs, null execution counts, and empty committed outputs. Full service-backed re-execution remains an integration concern because those notebooks require a running Atlas stack. Internal plans and research records remain available in the repository but are not silently published as unlisted site pages.
