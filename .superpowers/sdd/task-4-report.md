# Task 4 Report

## Status

DONE

## What Changed

- Added `test_generated_site_has_full_information_architecture()` to `bootstrapper/tests/test_docs_site_platform.py` to lock the required IA pages plus the homepage hero and screenshot content from the brief.
- Verified the red step by running the new test first and confirming it failed against the old generated homepage structure.
- Added `bootstrapper/docs/sitegen/pages.py` with `static_pages(model: DocsModel) -> dict[Path, str]` using `DocsModel`, `rendering.table`, and `rendering.csv_or_dash`.
- Wired `scripts/generate-docs-site.py` to import `static_pages` and use it in `build_artifacts()`.
- Removed the obsolete in-script `_static_pages()` implementation so the generator has a single source of truth for the static IA pages.
- Regenerated the site pages:
  - `docs/index.md`
  - `docs/site/overview.md`
  - `docs/site/quick-start.md`
  - `docs/site/core-concepts.md`
  - `docs/site/tracks.md`
  - `docs/site/architecture/index.md`
  - `docs/site/configuration.md`
  - `docs/site/operations.md`
  - `docs/site/development.md`
  - `docs/site/reference/index.md`

## TDD Evidence

### Red

Command:

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_generated_site_has_full_information_architecture -q
```

Observed result:

- Failed as expected.
- Assertion failure was on `assert '<div class="atlas-hero">' in home`, confirming the old generated homepage did not yet match the required Task 4 structure.

### Green

Commands:

```bash
uv run --project bootstrapper python scripts/generate-docs-site.py
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_generated_site_has_full_information_architecture bootstrapper/tests/test_docs_site_platform.py::test_atlas_theme_uses_material_dark_default_with_light_toggle -q
```

Observed result:

- Generator completed successfully.
- Targeted tests passed: `2 passed`.

## Self-Review

- Confirmed the new renderer matches the brief’s required page set and homepage strings.
- Confirmed `scripts/generate-docs-site.py` now delegates static IA rendering to `bootstrapper/docs/sitegen/pages.py`.
- Reviewed the generated markdown diffs to ensure Task 3’s temporary `core-concepts.md` compatibility content was replaced by the full IA content instead of reverted arbitrarily.
- No additional issues found in the touched files during review.

## Tests Run

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_generated_site_has_full_information_architecture -q
uv run --project bootstrapper python scripts/generate-docs-site.py
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_generated_site_has_full_information_architecture bootstrapper/tests/test_docs_site_platform.py::test_atlas_theme_uses_material_dark_default_with_light_toggle -q
```

## Commit

- `e65e0d4b` `Add full docs site information architecture`

## Review Fix Round

### Changes

- Preserved the `tracks.yml` `services: "*"` sentinel in the docs-site model via `TrackPage.all_services` and `TrackPage.services_display`, so the generated track tables now render `all services (no filtering)` instead of implying an empty service list.
- Reused shared docs-site visual paths from `DocsModel` in `bootstrapper/docs/sitegen/pages.py` instead of hardcoded strings, and surfaced the required assets in generated IA pages:
  - `assets/atlas-poster.png` on `docs/index.md` and `docs/site/overview.md`
  - `docs/diagrams/architecture.svg` on `docs/site/architecture/index.md`
  - existing hero and wizard screenshot paths now also flow through the model
- Updated `scripts/generate-docs-site.py` track reference generation to reuse the model-backed track display so both track tables stay consistent.
- Added tests to pin the `all` sentinel wording plus the shared-visual references in generated pages.

### Commands And Outputs

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_sitegen_model.py::test_docs_model_preserves_all_track_as_no_filtering_sentinel -q
```

- Before the fix: failed with `AttributeError: 'TrackPage' object has no attribute 'all_services'`.

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_generated_site_has_full_information_architecture -q
```

- Before the fix: failed on `assert "assets/atlas-poster.png" in home`.

```bash
uv run --project bootstrapper python scripts/generate-docs-site.py
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_generated_site_has_full_information_architecture bootstrapper/tests/test_docs_site_platform.py::test_atlas_theme_uses_material_dark_default_with_light_toggle bootstrapper/tests/test_docs_site_platform.py::test_generated_reference_pages_cover_core_sources bootstrapper/tests/test_docs_sitegen_model.py::test_docs_model_indexes_services_tracks_and_assets bootstrapper/tests/test_docs_sitegen_model.py::test_docs_model_preserves_all_track_as_no_filtering_sentinel -q
git diff --check
```

- Generator completed successfully.
- Verification suite passed: `5 passed in 0.70s`.
- `git diff --check` returned cleanly.

### Review Fix Commit

- `a4b05009` `Fix Task 4 docs-site review issues`

## Post-Task Regression Fix

- Restored the full local docs/audit command block in `docs/site/development.md` by updating the docs-site page generator source and regenerating the site.
- Added the poster image to the generated `docs/assets/` copy set so the overview page's existing helper-generated link resolves during structural docs checks.
- Verified with the two targeted docs-site tests, `scripts/check-docs-drift.py`, and `git diff --check`.
