# 6.3. Bootstrapper Lifecycle

How start.sh flows through env loading, migrations, manifest synthesis, track filtering, Kong generation, compose assembly, and launch logs.

## 1. Diagram

[Open the interactive diagram](./bootstrapper-lifecycle.html).

## 2. How To Read This View

Startup is an ordered configuration pipeline. Atlas loads and migrates the environment, synthesizes manifests, applies track decisions, and generates routes before Compose receives the final service graph. A failure before launch must not be reported as a running stack.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `bootstrapper/services/topology.py`
- `docs/deployment/source-configuration.md`
