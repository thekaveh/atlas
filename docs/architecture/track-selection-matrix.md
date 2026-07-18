# 6.5. Track Selection Matrix

How Atlas tracks map to service families and force-disable out-of-track services.

## 1. Diagram

[Open the interactive diagram](./track-selection-matrix.html).

## 2. How To Read This View

Tracks reduce the wizard to a workflow-oriented service set. Services outside that set are force-disabled after prompting, while an explicit CLI source override remains authoritative and is reported to the operator.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `bootstrapper/services/topology.py`
- `docs/deployment/source-configuration.md`
