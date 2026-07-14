# Observability Flow

Prometheus, Grafana, Langfuse, OpenTelemetry Collector, Tempo, Loki, and service instrumentation boundaries.

## 1. Diagram

[Open the interactive diagram](./observability-flow.html).

## 2. How To Read This View

Metrics, traces, logs, and LLM telemetry follow separate collection paths. Prometheus scrapes metrics, the OpenTelemetry Collector forwards traces, Loki stores logs, and Grafana correlates those stores; Langfuse remains the LLM-specific request and evaluation surface.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `services/topology.py`
- `docs/deployment/source-configuration.md`

## 4. Maintenance

Regenerate this page and `observability-flow.html` after changing a represented service,
route, SOURCE mode, track, dependency, or data-flow boundary.
