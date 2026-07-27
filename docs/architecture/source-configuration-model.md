# 6.4. SOURCE Configuration Model

Container, localhost, disabled, none, cloud-provider enablement, and adaptive-service behavior.

## 1. Diagram

[Open the interactive diagram](./source-configuration-model.html).

## 2. Notes

SOURCE selects a deployment mode, not just an image variant — the same value gates Compose scale, env wiring, and Kong route generation together. `none` is unique to the LLM provider family; no other service exposes it.

## 3. Source Files

- `bootstrapper/services/manifests.py`
- `bootstrapper/tracks.yml`
- `bootstrapper/services/topology.py`
