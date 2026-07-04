# Development

Adding or changing a service requires manifest, compose, topology, docs, route,
and test updates. Use:

## 1. Required Checks

```bash
PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all --check
python scripts/check-docs-site.py
python scripts/export-docs-wiki.py --check
```

## 2. Service Admission

See [Adding a service](../CONTRIBUTING-services.md).
