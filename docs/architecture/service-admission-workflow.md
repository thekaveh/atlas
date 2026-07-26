# 6.12. Service Admission Workflow

Manifest, compose fragment, topology row, env assembler, docs regeneration, diagrams, tests, and CI drift gates.

## 1. Diagram

[Open the interactive diagram](./service-admission-workflow.html).

## 2. Notes

`manifest_validator.py`'s fragment check is what actually blocks a partial landing: `missing_fragment` for a non-virtual manifest with no `compose.yml`, `unexpected_fragment` for a virtual manifest that ships one anyway, and `fragment_container_drift` when the manifest's `containers[]` disagrees with the compose file's `services:` keys. `tools/validate_fragments.py` runs this in CI and separately checks `.env.example` drift and the README `TOPOLOGY` block.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `bootstrapper/services/topology.py`
- `docs/deployment/source-configuration.md`
