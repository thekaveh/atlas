# 6.5. Track Selection Matrix

How Atlas tracks map to service families and force-disable out-of-track services.

## 1. Diagram

[Open the interactive diagram](./track-selection-matrix.html).

## 2. Notes

An explicit CLI `--<svc>-source` override always wins over track selection and is reported to the operator as an advisory warning. SOURCE values declared in a consumer manifest's `env.values` also survive the track force-disable step — only implicit track defaults get overridden.

## 3. Source Files

- `bootstrapper/tracks.yml`
- `bootstrapper/tracks.py`
- `bootstrapper/services/topology.py`
