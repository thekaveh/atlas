---
category-fit: infra
generated: 2026-07-04
license: Apache-2.0
name: OpenLIT
referenced-by: [ollama]
slug: openlit
type: external-service
upstream: https://github.com/openlit/openlit
---

# OpenLIT

## Headline
OpenTelemetry-native GenAI observability platform with SDK instrumentation,
ClickHouse-backed storage, an OpenTelemetry Collector, LLM/vector/GPU telemetry,
prompt/evaluation features, and an AI-engineering UI.

## Problem it solves
OpenLIT is useful when Atlas needs GenAI telemetry beyond generic traces:
one-line SDK instrumentation, model/vector/GPU visibility, prompt/evaluation
features, and OpenTelemetry-native dashboards. Current upstream self-hosting
docs describe a complete stack of OpenLIT platform, ClickHouse, and
OpenTelemetry Collector via Helm or Docker Compose, which makes it powerful but
also overlapping with Atlas' Langfuse plus OTel/Tempo/Loki direction.

## Deferred decision (2026-07-04)
Atlas should keep OpenLIT deferred and must not add `services/openlit/service.yml` yet.
Langfuse plus OTel/Tempo/Loki is the preferred first observability path:
Langfuse owns LLM traces, prompts, evals, and LiteLLM gateway telemetry, while
Atlas' OpenTelemetry Collector, Tempo, Loki, Prometheus, and Grafana own the
vendor-neutral metrics/logs/traces foundation.

OpenLIT remains interesting, but adding it now would create a second observability UI,
a second collector shape, and likely another ClickHouse-backed analytics surface
before Atlas has proven a gap. Revisit only for named OpenLIT-specific
functionality.

## Stack wiring sketch
No current Atlas wiring should be added while OpenLIT is deferred. If adopted
later, the expected topology would be:

- backend -> OpenLIT via explicit SDK instrumentation or OTLP export only when
  Langfuse/OTel cannot answer the target question.
- LiteLLM -> OpenLIT through OTLP/LiteLLM-compatible telemetry, without breaking
  the Langfuse callback path.
- Ollama -> OpenLIT for model/GPU telemetry only if OpenLIT's Ollama integration
  is the selected gap.
- Hermes and JupyterHub -> OpenLIT as opt-in developer instrumentation, not a
  default notebook or agent dependency.
- Weaviate/vector clients -> OpenLIT only where vector-call telemetry is more
  useful than generic OTel spans.
- Grafana may link to OpenLIT or its data source, but Grafana remains the Atlas
  observability entrypoint for infrastructure metrics/logs/traces.

## Effort
Medium. The upstream deployment is documented, but a conservative Atlas slice
would still need a manifest, optional ClickHouse ownership decision, collector
ownership decision, Kong route/auth handling, generated credentials, SDK
enablement switches, consumer tests, and docs explaining how OpenLIT differs
from Langfuse and the OTel/Tempo/Loki bundle.

## Risks & open questions
- UI overlap with Langfuse and Grafana can confuse users unless ownership is
  explicit.
- ClickHouse ownership may duplicate Langfuse's analytics dependency and needs a
  retention/storage story.
- OpenTelemetry Collector ownership must not conflict with Atlas' existing
  `otel-collector` service.
- SDK instrumentation in backend, Hermes, notebooks, or n8n can add dependency
  drift and high-cardinality spans.
- GPU metrics depend on runtime and host support; do not promise them by
  default.

## Revisit criteria
Reconsider OpenLIT only when all of these are true:

- Langfuse plus OTel/Tempo/Loki fails to cover a named observability need.
- Atlas needs OpenLIT-specific functionality such as one-line SDK
  auto-instrumentation, Ollama/GPU metrics, vector-call telemetry, prompt/eval
  features, or an OpenTelemetry-native GenAI dashboard.
- The integration cost is lower than extending the planned observability stack.
- The ClickHouse, collector, UI, retention, and auth ownership boundaries are
  explicit.

## Future service contract if adopted
- **Tracks:** `observability`, `gen-ai-eng`, `gen-ai-rag`, `ml-eng`, and `all`;
  avoid `data-eng` unless a concrete ML/data observability workflow needs it.
- **Category:** choose deliberately between `infra` and `agents`. It is an
  observability backend if Atlas uses it as telemetry infrastructure; it is an
  AI-engineering app if Atlas exposes the OpenLIT UI as a workflow surface.
- **Sources:** `OPENLIT_SOURCE=disabled|container`; disabled by default. Consider
  `localhost` only for an operator-managed OpenLIT/OTLP endpoint.
- **Wizard placement:** after Langfuse and the OTel/Tempo/Loki bundle, with
  prompt copy that OpenLIT is an alternative or augmentation for proven GenAI
  telemetry gaps.
- **Ports and routes:** allocate ports through Atlas topology/category slots and
  custom `BASE_PORT` math. Expected alias is `openlit.localhost` only when
  enabled and protected. Do not expose OTLP ingestion publicly by default.
- **Dependencies:** likely OpenLIT platform, ClickHouse, and OTel Collector.
  Consumers may include backend, LiteLLM, Ollama, Hermes, JupyterHub notebooks,
  Weaviate clients, n8n, and ComfyUI only where instrumentation is explicit.
- **Init/secrets:** generate admin credentials if needed, OTLP endpoint envs,
  SDK enablement envs, retention/storage settings, and separate credentials from
  Langfuse keys.
- **Edge cases:** disabled Langfuse, disabled OTel/Tempo/Loki, duplicate
  ClickHouse ownership, stale `.env`, custom `BASE_PORT`, missing SDK deps,
  notebook opt-in leaks, high-cardinality traces, GPU metric unavailability,
  route exposure without auth, and generated-doc drift.

## Tests required if adopted later
- Manifest schema and topology tests for source values, category, aliases,
  generated env vars, and track membership.
- Compose/source permutation coverage for disabled/container and any future
  localhost mode.
- Kong route audits proving `openlit.localhost` appears only when enabled and
  OTLP ingestion is not public.
- Consumer instrumentation tests for backend, LiteLLM, Ollama, Hermes,
  JupyterHub, and Weaviate disabled/container/localhost paths.
- Docs drift, research schema, link checks, and generated README/diagram checks.

## Why now (and why not sooner)
Not now. OpenLIT should wait until Atlas can name an observability gap that
Langfuse plus OTel/Tempo/Loki cannot cover cleanly.

## Upstream evidence
- https://github.com/openlit/openlit
- https://docs.openlit.io/latest/overview
- https://docs.openlit.io/latest/openlit/installation
- https://docs.openlit.io/latest/integrations/ollama
