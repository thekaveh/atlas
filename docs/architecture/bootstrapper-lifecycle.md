# Bootstrapper Lifecycle

How start.sh flows through env loading, migrations, manifest synthesis, track filtering, Kong generation, compose assembly, and launch logs.

[Open the interactive diagram](./bootstrapper-lifecycle.html).

## Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `services/topology.py`
- `docs/deployment/source-configuration.md`

## Update Rule

Update this page and `bootstrapper-lifecycle.html` when the represented architecture surface
changes. Use the `architecture-diagram` design system: dark slate background,
JetBrains Mono, split perspectives, readable labels, and no overloaded mega-diagram.
