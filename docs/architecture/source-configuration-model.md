# SOURCE Configuration Model

Container, localhost, disabled, none, cloud-provider enablement, and adaptive-service behavior.

## 1. Diagram

[Open the interactive diagram](./source-configuration-model.html).

## 2. How To Read This View

A service's SOURCE value selects its deployment mode, not merely an image variant. Container modes create Compose workloads, localhost modes redirect consumers to the host, and disabled modes remove workloads. The LLM-specific `none` mode leaves LiteLLM available for cloud-only routing.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `services/topology.py`
- `docs/deployment/source-configuration.md`

## 4. Maintenance

Regenerate this page and `source-configuration-model.html` after changing a represented service,
route, SOURCE mode, track, dependency, or data-flow boundary.
