# Observability Flow

Prometheus, Grafana, Langfuse, OpenTelemetry Collector, Tempo, Loki, and service instrumentation boundaries.

[Open the interactive diagram](./observability-flow.html).

## Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `services/topology.py`
- `docs/deployment/source-configuration.md`

## Update Rule

Update this page and `observability-flow.html` when the represented architecture surface
changes. Use the `architecture-diagram` design system: dark slate background,
JetBrains Mono, split perspectives, readable labels, and no overloaded mega-diagram.
