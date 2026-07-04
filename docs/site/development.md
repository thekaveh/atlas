# Development

Adding or changing a service requires manifest, compose, topology, docs, route,
and test updates. Use:

```bash
PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all --check
python scripts/check-docs-site.py
python scripts/export-docs-wiki.py --check
```

See [Adding a service](../CONTRIBUTING-services.md).
