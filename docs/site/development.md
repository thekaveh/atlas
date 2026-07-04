# Development

Adding or changing a service requires manifest, compose, topology, docs, route,
and test updates. Use:

## 1. Required Checks

```bash
PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all --check
python scripts/check_doc_links.py
python scripts/check-docs-drift.py
python scripts/check-docs-site.py
python scripts/export-docs-wiki.py --check
python scripts/check-compose-source-deps.py
python scripts/check-kong-routes.py
python scripts/validate_research_schema.py --all
python scripts/check-track-membership.py
(cd services/docling/provider/localhost && uv lock --locked)
```

## 2. Service Admission

See [Adding a service](../CONTRIBUTING-services.md).
