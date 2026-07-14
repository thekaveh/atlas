# Track Selection Matrix

How Atlas tracks map to service families and force-disable out-of-track services.

## 1. Diagram

[Open the interactive diagram](./track-selection-matrix.html).

## 2. How To Read This View

Tracks reduce the wizard to a workflow-oriented service set. Services outside that set are force-disabled after prompting, while an explicit CLI source override remains authoritative and is reported to the operator.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `services/topology.py`
- `docs/deployment/source-configuration.md`

## 4. Maintenance

Regenerate this page and `track-selection-matrix.html` after changing a represented service,
route, SOURCE mode, track, dependency, or data-flow boundary.
