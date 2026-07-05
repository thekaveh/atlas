# Architecture

## 1. Stack Shape

Atlas routes browser and API traffic through Kong, composes services through Docker Compose fragments, and adapts application services based on enabled upstreams.

The architecture is intentionally split into focused diagrams instead of one overloaded mega-diagram. Each diagram covers one operating perspective and points back to the source files that should change with it.

## 2. Generated Diagram Catalog

| Slug | Title | Purpose |
| --- | --- | --- |
| platform-overview | Atlas Platform Overview | User entrypoints, Kong, apps, agents, LLM core, data stores, and cloud-provider boundaries. |
| bootstrapper-lifecycle | Bootstrapper Lifecycle | How start.sh flows through env loading, migrations, manifest synthesis, track filtering, Kong generation, compose assembly, and launch logs. |
| source-configuration-model | SOURCE Configuration Model | Container, localhost, disabled, none, cloud-provider enablement, and adaptive-service behavior. |
| track-selection-matrix | Track Selection Matrix | How Atlas tracks map to service families and force-disable out-of-track services. |
| network-routing-topology | Network And Routing Topology | Host ports, Kong aliases, direct service ports, backend-network-only services, and localhost-mode boundaries. |
| data-rag-flow | Data And RAG Flow | Ingestion, document processing, object storage, vector and graph stores, backend APIs, Open WebUI, and tool/MCP-adjacent flows. |
| llm-provider-flow | LLM Provider Flow | Ollama, LiteLLM, cloud passthroughs, Open WebUI, backend, MCP/tool access, and trace hooks. |
| data-engineering-lakehouse-flow | Data Engineering Lakehouse Flow | MinIO, Iceberg REST, Spark, JupyterHub, Zeppelin, Airflow, Trino, Redpanda, Jenkins, and init containers. |
| observability-flow | Observability Flow | Prometheus, Grafana, Langfuse, OpenTelemetry Collector, Tempo, Loki, and service instrumentation boundaries. |
| security-auth-secrets-boundary | Security, Auth, And Secrets Boundary | Supabase, Kong, service auth notes, API keys, local secrets, cloud keys, and intentionally unauthenticated local surfaces. |
| service-admission-workflow | Service Admission Workflow | Manifest, compose fragment, topology row, env assembler, docs regeneration, diagrams, tests, and CI drift gates. |

## 3. Service Diagrams

Per-service diagrams live beside each service README under `services/<name>/architecture.svg` and `services/<name>/architecture.html`.

## 4. Update Rule

When a manifest, topology row, track, SOURCE value, route, or data-flow call changes, regenerate the docs and diagrams before merging.

## 5. Dependency Topology

Atlas distinguishes required dependencies, optional dependencies, and runtime call relationships.

- Required dependencies affect startup ordering and service viability.
- Optional dependencies describe integrations that should be enabled when present.
- Runtime calls describe how services communicate after launch.
- Kong aliases describe browser-facing and API-facing local access.

## 6. Diagram Responsibilities

Architecture diagrams should explain a bounded perspective, use readable labels, and avoid turning into a service inventory.

Per-service diagrams belong with service READMEs. Platform-level diagrams belong in the architecture catalog.

## 7. Related Pages

- [Services](Services)
- [Configuration](Configuration)
- [Reference](Reference)
