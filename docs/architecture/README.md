# 6.13. Architecture Diagram Catalog

## 1. Generated Diagram Index

Generated catalog of split Atlas architecture perspectives.

| Diagram | Purpose |
| --- | --- |
| [Atlas Platform Overview](platform-overview.md) | User entrypoints, Kong, apps, agents, LLM core, data stores, and cloud-provider boundaries. |
| [Bootstrapper Lifecycle](bootstrapper-lifecycle.md) | How start.sh flows through env loading, migrations, manifest synthesis, track filtering, Kong generation, compose assembly, and launch logs. |
| [SOURCE Configuration Model](source-configuration-model.md) | Container, localhost, disabled, none, cloud-provider enablement, and adaptive-service behavior. |
| [Track Selection Matrix](track-selection-matrix.md) | How Atlas tracks map to service families and force-disable out-of-track services. |
| [Network And Routing Topology](network-routing-topology.md) | Host ports, Kong aliases, direct service ports, backend-network-only services, and localhost-mode boundaries. |
| [Data And RAG Flow](data-rag-flow.md) | Ingestion, document processing, object storage, vector and graph stores, backend APIs, Open WebUI, and tool/MCP-adjacent flows. |
| [LLM Provider Flow](llm-provider-flow.md) | Ollama, LiteLLM, cloud passthroughs, Open WebUI, backend, MCP/tool access, and trace hooks. |
| [Data Engineering Lakehouse Flow](data-engineering-lakehouse-flow.md) | MinIO, Iceberg REST, Spark, JupyterHub, Zeppelin, Airflow, Trino, and Redpanda. |
| [Observability Flow](observability-flow.md) | Prometheus, Grafana, Langfuse, OpenTelemetry Collector, Tempo, Loki, and service instrumentation boundaries. |
| [Security, Auth, And Secrets Boundary](security-auth-secrets-boundary.md) | Supabase, Kong, service auth notes, API keys, local secrets, cloud keys, and intentionally unauthenticated local surfaces. |
| [Service Admission Workflow](service-admission-workflow.md) | Manifest, compose fragment, topology row, env assembler, docs regeneration, diagrams, tests, and CI drift gates. |
