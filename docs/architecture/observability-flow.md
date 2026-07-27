# 6.10. Observability Flow

Prometheus, Grafana, Langfuse, OpenTelemetry Collector, Tempo, Loki, and service instrumentation boundaries.

## 1. Diagram

[Open the interactive diagram](./observability-flow.html).

## 2. Notes

Langfuse is deliberately outside the OTel path: LiteLLM emits Langfuse traces via its own `success_callback`, not through the Collector, because Langfuse is the LLM-behavior layer while Prometheus/Grafana stay the infrastructure-metrics layer. Only backend and LiteLLM OTLP traces currently reach Tempo via the Collector; Loki log export isn't wired up yet.

## 3. Source Files

- `services/prometheus/service.yml`
- `services/grafana/service.yml`
- `services/loki/service.yml`
- `services/tempo/service.yml`
- `services/otel-collector/service.yml`
