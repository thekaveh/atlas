# Service Admission Workflow

Manifest, compose fragment, topology row, env assembler, docs regeneration, diagrams, tests, and CI drift gates.

## 1. Diagram

[Open the interactive diagram](./service-admission-workflow.html).

## 2. How To Read This View

A service enters Atlas through one declarative chain: its manifest owns SOURCE values and metadata, Compose owns workloads, topology owns placement and ports, and the env/docs generators project those records. Drift and integration tests prevent a partial service definition from landing.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `services/topology.py`
- `docs/deployment/source-configuration.md`

## 4. Maintenance

Regenerate this page and `service-admission-workflow.html` after changing a represented service,
route, SOURCE mode, track, dependency, or data-flow boundary.
