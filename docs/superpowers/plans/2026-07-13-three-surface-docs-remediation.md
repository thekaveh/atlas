# Atlas Three-Surface Documentation Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Atlas's tracked, independently generated site and wiki summaries with one deterministic, manifest-driven, self-contained three-surface documentation projection.

**Architecture:** Canonical authored docs, full service READMEs, service manifests, and HTML diagram masters feed an ordered page model declared by `docs/manifest.yaml`. Focused modules under `scripts/docs/` render site and wiki trees from that model, generate local diagram assets, and enforce parity and self-containment before MkDocs or wiki publication.

**Tech Stack:** Python 3.10+, PyYAML, MkDocs Material, CairoSVG, pytest, uv, GitHub Actions, GitHub Pages, GitHub Wiki.

## Global Constraints

- Preserve existing service README regeneration and all generated dependency sections.
- Do not publish `docs/research`, `docs/strategy`, `docs/maintenance`, or `docs/superpowers` unless a manifest entry explicitly opts in.
- Do not link one Atlas documentation surface to another.
- Invoke documentation tooling with `python -m scripts.docs.<module>`.
- Generated site/wiki trees, root `mkdocs.yml`, and MkDocs output are gitignored.
- Pages and wiki publication run only after merge to `main`; merge gates run for `develop` and `main`.

---

### Task 1: Manifest And Link Contracts

**Files:**
- Create: `docs/manifest.yaml`
- Create: `scripts/docs/__init__.py`
- Create: `scripts/docs/manifest.py`
- Create: `scripts/docs/links.py`
- Create: `scripts/docs/transforms.py`
- Test: `bootstrapper/tests/test_three_surface_manifest.py`
- Test: `bootstrapper/tests/test_three_surface_links.py`
- Test: `bootstrapper/tests/test_three_surface_transforms.py`

**Interfaces:**
- Produces: `load_manifest(path, repo_root) -> Manifest`, `find_links(markdown)`, `is_forbidden(target, surface)`, `build_source_map(manifest, surface)`, and `rewrite_for_surface(...)`.

- [ ] Write focused tests for malformed manifests, missing sources, source/children exclusivity, the three-surface link matrix, mapped Markdown links, repository-only links, anchors, and images.
- [ ] Run each test file and confirm failures are caused by the absent modules.
- [ ] Implement the minimum dataclasses, parser, classifiers, and transforms required by the tests.
- [ ] Run the focused tests to green.

### Task 2: Shared Page Model And Deterministic Builds

**Files:**
- Create: `scripts/docs/pages.py`
- Create: `scripts/docs/build_docs.py`
- Create: `scripts/docs/check_docs.py`
- Test: `bootstrapper/tests/test_three_surface_build.py`
- Test: `bootstrapper/tests/test_three_surface_checks.py`

**Interfaces:**
- Consumes: validated manifest and surface transforms from Task 1.
- Produces: one ordered list of public pages, `generated/site`, `generated/wiki`, generated `mkdocs.yml`, deterministic hash comparison, and contract findings.

- [ ] Write failing tests proving site and wiki use the same source content, produce local navigation, exclude non-manifest docs, include wiki sidebar/footer, and reject cross-surface links or nondeterministic output.
- [ ] Run the tests and confirm the expected failures.
- [ ] Implement shared page assembly, generated references, surface writers, MkDocs rendering, and checks.
- [ ] Run focused tests to green.

### Task 3: Diagram Projection

**Files:**
- Create: `scripts/docs/render_diagrams.py`
- Modify: `docs/manifest.yaml`
- Create: `docs/diagrams/img/*.png`
- Test: `bootstrapper/tests/test_three_surface_diagrams.py`

**Interfaces:**
- Produces: sanitized SVG for `generated/site/assets/img/` and PNG for committed repo assets plus `generated/wiki/img/`.

- [ ] Write failing tests for inline SVG extraction, named-entity sanitization, PNG signatures, and site/wiki asset copying.
- [ ] Run the tests and confirm expected failures.
- [ ] Implement rendering with lazy CairoSVG import and deterministic filenames.
- [ ] Generate and inspect the committed PNG inventory.
- [ ] Run diagram tests to green.

### Task 4: Repository And Workflow Migration

**Files:**
- Modify: `.gitignore`, `README.md`, `bootstrapper/pyproject.toml`, `uv.lock`
- Create: `Makefile`
- Modify: `scripts/generate-docs-site.py`, `scripts/check-docs-site.py`, `scripts/export-docs-wiki.py`
- Create: `scripts/docs/push_wiki.py`
- Modify: `.github/workflows/services-lint.yml`, `.github/workflows/docs-pages.yml`
- Remove from tracking: `mkdocs.yml`, `docs/site/**`, `docs/wiki/**`
- Test: `bootstrapper/tests/test_three_surface_wiki.py`

**Interfaces:**
- Preserves legacy command entry points as wrappers while CI and Make targets use module invocation.

- [ ] Write failing tests for wiki synchronization, stale-file removal, default identity, no-op pushes, and `master` destination.
- [ ] Implement the wiki publisher and compatibility wrappers.
- [ ] Migrate ignore rules, dependencies, Make targets, README wording, and workflow branch/auth contracts.
- [ ] Remove tracked generated outputs and generate clean disposable replacements.

### Task 5: Verification And Delivery

**Files:**
- Modify only files required by failures discovered during verification.

- [ ] Run `make docs-check` and require a warning-free strict MkDocs build.
- [ ] Run all focused three-surface tests and the complete bootstrapper suite.
- [ ] Run every existing Atlas documentation, compose, route, research, and track audit command.
- [ ] Inspect generated site/wiki content, local assets, git status, and the full diff.
- [ ] Request an independent code review and resolve every Critical or Important finding.
- [ ] Commit and push the feature branch, open a PR to `develop`, wait for green required checks, and squash-merge it.
- [ ] Open `develop` to `main`, wait for green required checks, squash-merge it, and verify live Pages and wiki publication.
