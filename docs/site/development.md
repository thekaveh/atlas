# Development

## 1. Service Admission

Adding a service requires a manifest, compose fragment when applicable, topology row, docs regeneration, route checks, and CI validation.

## 2. Parent-Repo Consumer Layout

Submodule consumers should keep project-owned overlays, branding, wrapper scripts, and secret references in the parent repository while `infra/` remains a pinned Atlas checkout. The recommended shape is:

- `compose/<name>-overlay.yml` in the parent repository.
- `infra/services/_user/<name>/compose.yml` as a symlink or generated discovery pointer to that parent-owned overlay.
- `scripts/setup-overlay.sh` as an idempotent wrapper that creates the discovery slot before every start.
- `scripts/start-infra.sh` as the parent-owned launcher that force-sets `PROJECT_NAME`, `BRAND_*`, and required `*_SOURCE` values.

Do not rely on "set only if absent" helpers for critical `*_SOURCE` keys. Atlas's `.env.example` intentionally contains defaults, so project wiring should force-set required values or pass explicit `--<service>-source` flags. Explicit source flags override `--track`, which is how consumers request an extra service outside a track or disable a service the track would normally prompt for.

Parent-owned object-storage consumers should extend `minio-init` with `MINIO_EXTRA_CONSUMERS`, for example `daydreams:MINIO_BUCKET_DAYDREAMS:MINIO_DAYDREAMS_ACCESS_KEY:MINIO_DAYDREAMS_SECRET_KEY`, and keep the referenced bucket/access/secret variables in `.env.user` or `ATLAS_ENV_USER_FILE`. The hook creates the extra bucket and scoped MinIO service account without forking Atlas.

Before committing a parent consumer update, verify the `infra/` submodule status is clean except for ignored `.env`, `.env.user`, `_user` slots, and runtime volumes; the parent pins a specific Atlas commit or tag; and overlays remain parent-owned.

## 3. Required Docs Checks

```bash
PYTHONPATH=bootstrapper uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check
uv run --project bootstrapper python scripts/check_doc_links.py
uv run --project bootstrapper python scripts/check-docs-drift.py
uv run --project bootstrapper python scripts/check-docs-site.py
uv run --project bootstrapper python scripts/export-docs-wiki.py --check
uv run --project bootstrapper python scripts/check-compose-source-deps.py
uv run --project bootstrapper python scripts/check-kong-routes.py
uv run --project bootstrapper python scripts/validate_research_schema.py --all
uv run --project bootstrapper python scripts/check-track-membership.py
(cd services/docling/provider/localhost && uv lock --locked)
```
