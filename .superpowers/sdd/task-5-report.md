# Task 5 Report: Generate Rich Service Catalog And Service Profiles

## Status

DONE_WITH_CONCERNS

## Summary

Implemented a dedicated rich service renderer at `bootstrapper/docs/sitegen/services.py`, wired it into `scripts/generate-docs-site.py`, regenerated `docs/site/services/**`, and updated the docs-site platform tests to cover the new catalog/profile contract.

The generated service pages now come from `DocsModel.services`, avoid copying README bodies, and link back to service-owned source docs instead.

## TDD Log

### Red

Added the failing tests requested in the brief:

- `test_service_profiles_are_substantial_and_generated_from_model`
- `test_service_catalog_groups_services_by_category_with_tracks_and_sources`

Initial verification:

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_service_profiles_are_substantial_and_generated_from_model bootstrapper/tests/test_docs_site_platform.py::test_service_catalog_groups_services_by_category_with_tracks_and_sources -q
```

Result: failed as expected because the existing generated service pages were still shallow and the catalog used the older format.

### Green

Implemented `service_pages(model: DocsModel) -> dict[Path, str]` in `bootstrapper/docs/sitegen/services.py` and replaced the old inline `_service_pages(...)` usage in `scripts/generate-docs-site.py`.

Regenerated the service docs:

```bash
uv run --project bootstrapper python scripts/generate-docs-site.py
```

Updated one existing test to match the new service catalog link shape:

- `test_docs_site_indexes_every_service_family`

### Verification

Fresh passing verification after the implementation and commit:

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_docs_site_indexes_every_service_family bootstrapper/tests/test_docs_site_platform.py::test_service_profiles_are_substantial_and_generated_from_model bootstrapper/tests/test_docs_site_platform.py::test_service_catalog_groups_services_by_category_with_tracks_and_sources -q
```

Result:

- `3 passed in 0.26s`

## Files Changed

- Added: `bootstrapper/docs/sitegen/services.py`
- Modified: `scripts/generate-docs-site.py`
- Modified: `bootstrapper/tests/test_docs_site_platform.py`
- Regenerated: `docs/site/services/index.md`
- Regenerated: `docs/site/services/*.md`

## Commit

- `092056f6 feat: generate rich docs service profiles`

## Self-Review Notes

- The new renderer is isolated in its own module instead of leaving service-page generation embedded in the main script.
- The generated pages use manifest/model metadata only and link to `services/<name>/README.md` rather than copying README content.
- The catalog now groups services by category and exposes tracks, SOURCE variable/default/value data, and dependencies.
- The service test coverage now checks both the richer profile structure and the category-grouped index shape.

## Concerns

There are pre-existing or out-of-scope failures elsewhere in `bootstrapper/tests/test_docs_site_platform.py` that this task did not address. A broader run produced unrelated failures in:

- wiki export completeness
- development page audit command coverage
- a broken `docs/site/overview.md` poster asset link caught by the structural docs audit

Those failures were present outside the service-profile slice and are not caused by the Task 5 renderer changes.

## Review Fix Follow-Up

Addressed the post-review gaps in the Task 5 service renderer without expanding into Task 6 wiki work.

### Additional Red Test

Added:

- `test_service_profiles_render_all_source_surfaces_and_canonical_readmes`

Initial verification:

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_docs_site_indexes_every_service_family bootstrapper/tests/test_docs_site_platform.py::test_service_profiles_are_substantial_and_generated_from_model bootstrapper/tests/test_docs_site_platform.py::test_service_catalog_groups_services_by_category_with_tracks_and_sources bootstrapper/tests/test_docs_site_platform.py::test_service_profiles_render_all_source_surfaces_and_canonical_readmes -q
```

Result:

- `1 failed, 3 passed in 0.30s`
- failure confirmed `cloud-providers.md` only rendered the first SOURCE surface and still linked `services/cloud-providers/README.md`

### Fix

Updated `bootstrapper/docs/sitegen/services.py` to:

- render all `service.source_surfaces` in service profiles
- render catalog SOURCE/default/value columns from all surfaces instead of only the primary one
- build README labels and GitHub blob URLs from `service.readme` relative to repo root

Regenerated service pages:

```bash
uv run --project bootstrapper python scripts/generate-docs-site.py
```

### Verification

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_docs_site_indexes_every_service_family bootstrapper/tests/test_docs_site_platform.py::test_service_profiles_are_substantial_and_generated_from_model bootstrapper/tests/test_docs_site_platform.py::test_service_catalog_groups_services_by_category_with_tracks_and_sources bootstrapper/tests/test_docs_site_platform.py::test_service_profiles_render_all_source_surfaces_and_canonical_readmes -q
git diff --check
```

Results:

- `4 passed in 0.25s`
- `git diff --check` passed with no output
