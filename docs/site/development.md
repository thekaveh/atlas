# Development

## 1. Service Admission

Adding a service requires a manifest, compose fragment when applicable, topology row, docs regeneration, route checks, and CI validation.

## 2. Required Docs Checks

```bash
uv run --project bootstrapper python scripts/check-docs-site.py
uv run --project bootstrapper python scripts/export-docs-wiki.py --check
```
