# Atlas Documentation

Generated from the MkDocs source model. Do not hand-edit the live wiki; run `uv run --project bootstrapper python scripts/export-docs-wiki.py --check` to verify drift.

## 1. Start Here

- [Overview](Overview)
- [Quick Start](Quick-Start)
- [Core Concepts](Core-Concepts)
- [Tracks](Tracks)
- [Services](Services)
- [Architecture](Architecture)
- [Configuration](Configuration)
- [Operations](Operations)
- [Development](Development)
- [Reference](Reference)

## 2. What Atlas Covers

- Service families: `55`
- Tracks: `7`
- SOURCE-configurable surfaces: `58`
- Primary entrypoint: Kong and the Atlas root dashboard
- Runtime model: Docker Compose fragments generated from manifests, topology, tracks, and SOURCE selections

## 3. Public Site

The full documentation site is published at [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/).

## 4. Editing Rule

Update the repository source files, then regenerate this wiki export. The live GitHub Wiki should be treated as a published mirror, not as the source of truth.

## 5. Local Verification

```bash
uv run --project bootstrapper python scripts/generate-docs-site.py --check
uv run --project bootstrapper python scripts/export-docs-wiki.py --check
uv run --project bootstrapper python scripts/check-docs-site.py
```
