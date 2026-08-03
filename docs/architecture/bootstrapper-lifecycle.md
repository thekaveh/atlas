# 6.3. Bootstrapper Lifecycle

How start.sh flows through env loading, migrations, manifest synthesis, track filtering, Kong generation, compose assembly, and launch logs.

## 1. Diagram

[Open the full-size diagram](./bootstrapper-lifecycle.html).

## 2. Notes

Each stage gates the next; a failure in any stage before Compose must abort the run rather than report a partially launched stack. Env loading also applies the chained env-file migrations (`bootstrapper/services/migrations/`) before manifests are synthesized.

## 3. Source Files

- `bootstrapper/start.py`
- `bootstrapper/core/docker_manager.py`
