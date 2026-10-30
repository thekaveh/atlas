from __future__ import annotations

import html
import re
from pathlib import Path

from .model import DocsModel
from .rendering import csv_or_dash, table


ARCHITECTURE_PERSPECTIVES: dict[str, tuple[str, str, list[str]]] = {
    "platform-overview": (
        "Atlas Platform Overview",
        "User entrypoints, Kong, apps, agents, LLM core, data stores, and cloud-provider boundaries.",
        ["Clients", "Kong", "Apps", "Agents", "LLM Core", "Data Stores", "Cloud Providers"],
    ),
    "bootstrapper-lifecycle": (
        "Bootstrapper Lifecycle",
        "How start.sh flows through env loading, migrations, manifest synthesis, track filtering, Kong generation, compose assembly, and launch logs.",
        ["start.sh", "Env Load", "Migrations", "Manifests", "Tracks", "Kong Routes", "Compose", "Logs"],
    ),
    "source-configuration-model": (
        "SOURCE Configuration Model",
        "Container, localhost, disabled, none, cloud-provider and managed-vLLM enablement, and adaptive-service behavior.",
        [
            "SOURCE Var", "container", "localhost", "disabled", "none",
            "cloud enabled", "vLLM Metal", "adaptive apps",
        ],
    ),
    "track-selection-matrix": (
        "Track Selection Matrix",
        "How Atlas tracks map to service families and force-disable out-of-track services.",
        [
            "Tracks", "Wizard", "Service Families", "Enabled",
            "Force Disabled", "Overrides", "Selected Source", "Disabled",
        ],
    ),
    "network-routing-topology": (
        "Network And Routing Topology",
        "Host ports, Kong aliases, direct service ports, backend-network-only services, and localhost-mode boundaries.",
        ["Browser", "*.localhost", "Kong", "Direct Ports", "Backend Network", "localhost mode"],
    ),
    "data-rag-flow": (
        "Data And RAG Flow",
        "Ingestion, document processing, object storage, vector and graph stores, backend APIs, Open WebUI, and tool/MCP-adjacent flows.",
        ["Ingestion", "Doc Processing", "MinIO", "Weaviate", "LightRAG", "Neo4j", "Backend", "Open WebUI"],
    ),
    "llm-provider-flow": (
        "LLM Provider Flow",
        "Ollama, managed vLLM Metal, LiteLLM, cloud passthroughs, Open WebUI, backend, MCP/tool access, and trace hooks.",
        [
            "Open WebUI", "Backend", "LiteLLM", "Ollama", "vLLM Metal",
            "Cloud LLMs", "Tools", "Tracing",
        ],
    ),
    "data-engineering-lakehouse-flow": (
        "Data Engineering Lakehouse Flow",
        "MinIO, Iceberg REST, Supabase Postgres, Spark, JupyterHub, Zeppelin, Airflow, Trino, and Redpanda.",
        [
            "MinIO", "Iceberg REST", "Supabase/Postgres", "Spark",
            "JupyterHub", "Zeppelin", "Airflow", "Trino", "Redpanda",
        ],
    ),
    "observability-flow": (
        "Observability Flow",
        "Prometheus, Grafana, Langfuse, OpenTelemetry Collector, Tempo, Loki, and service instrumentation boundaries.",
        [
            "Backend", "Celery Workers", "LiteLLM", "Atlas Services",
            "OTel Collector", "Prometheus", "Grafana", "Langfuse", "Tempo", "Loki",
        ],
    ),
    "security-auth-secrets-boundary": (
        "Security, Auth, And Secrets Boundary",
        "Route-specific Kong controls, backend identity validation, application-enforced plugin keys, runtime secrets, and explicitly public or operator-trusted surfaces.",
        [
            "Gateway Clients", "Direct Clients", "Kong", "Kong Route Policies",
            "Routed Services", "Backend API", "Supabase Auth", "Backend Identity",
            "Plugin Key Auth", "Protected APIs", "Public APIs", "Runtime Secrets",
            "Trusted UIs",
        ],
    ),
    "service-admission-workflow": (
        "Service Admission Workflow",
        "Manifest, compose fragment, topology row, env assembler, docs regeneration, diagrams, tests, and CI drift gates.",
        ["service.yml", "compose.yml", "Topology", ".env.example", "Docs Regen", "Diagrams", "CI"],
    ),
}

ARCHITECTURE_SOURCE_FILES: dict[str, list[str]] = {
    "platform-overview": [
        "services/*/service.yml",
        "bootstrapper/services/topology.py",
    ],
    "bootstrapper-lifecycle": [
        "bootstrapper/start.py",
        "bootstrapper/core/docker_manager.py",
    ],
    "source-configuration-model": [
        "bootstrapper/services/manifests.py",
        "bootstrapper/tracks.yml",
        "bootstrapper/services/topology.py",
        "services/vllm-metal/service.yml",
    ],
    "track-selection-matrix": [
        "bootstrapper/tracks.yml",
        "bootstrapper/tracks.py",
        "bootstrapper/services/topology.py",
    ],
    "network-routing-topology": [
        "bootstrapper/utils/kong_config_generator.py",
        "bootstrapper/utils/system.py",
        "bootstrapper/services/topology.py",
        "services/kong/service.yml",
    ],
    "data-rag-flow": [
        "services/weaviate/service.yml",
        "services/backend/service.yml",
        "services/lightrag/service.yml",
    ],
    "llm-provider-flow": [
        "services/litellm/service.yml",
        "services/litellm/models.yaml",
        "services/ollama/service.yml",
        "services/vllm-metal/service.yml",
    ],
    "data-engineering-lakehouse-flow": [
        "services/jupyterhub/service.yml",
        "services/zeppelin/service.yml",
        "services/airflow/service.yml",
        "services/redpanda/service.yml",
        "services/minio/service.yml",
        "services/trino/service.yml",
        "services/iceberg-rest/service.yml",
        "services/spark/service.yml",
        "services/supabase/service.yml",
    ],
    "observability-flow": [
        "services/backend/service.yml",
        "services/celery/service.yml",
        "services/litellm/service.yml",
        "services/prometheus/service.yml",
        "services/grafana/service.yml",
        "services/loki/service.yml",
        "services/tempo/service.yml",
        "services/otel-collector/service.yml",
    ],
    "security-auth-secrets-boundary": [
        "services/kong/service.yml",
        "services/supabase/service.yml",
        "services/backend/service.yml",
        "services/backend/app/app/backend_identity.py",
        "services/backend/app/app/main.py",
        "bootstrapper/generate_supabase_keys.py",
    ],
    "service-admission-workflow": [
        "bootstrapper/services/manifest_validator.py",
        "bootstrapper/services/source_validator.py",
        "bootstrapper/services/topology.py",
        "bootstrapper/services/env_assembler.py",
        "bootstrapper/docs/regen.py",
        "bootstrapper/docs/diagram_renderer.py",
        "bootstrapper/tools/validate_fragments.py",
        ".github/workflows/services-lint.yml",
    ],
}

ARCHITECTURE_EDGES: dict[str, list[tuple[str, str, str]]] = {
    "platform-overview": [
        ("Clients", "Kong", "HTTP"),
        ("Kong", "Apps", "routes"),
        ("Kong", "Agents", "routes"),
        ("Apps", "LLM Core", "inference"),
        ("Agents", "LLM Core", "inference"),
        ("Apps", "Data Stores", "state"),
        ("Agents", "Data Stores", "context"),
        ("LLM Core", "Cloud Providers", "passthrough"),
    ],
    "bootstrapper-lifecycle": [
        ("start.sh", "Env Load", "invoke"),
        ("Env Load", "Migrations", "normalize"),
        ("Migrations", "Manifests", "synthesize"),
        ("Manifests", "Tracks", "filter"),
        ("Tracks", "Kong Routes", "generate"),
        ("Kong Routes", "Compose", "assemble"),
        ("Compose", "Logs", "launch"),
    ],
    "source-configuration-model": [
        ("SOURCE Var", "container", "selects"),
        ("SOURCE Var", "localhost", "selects"),
        ("SOURCE Var", "disabled", "selects"),
        ("SOURCE Var", "none", "LLM only"),
        ("none", "cloud enabled", "pairs with"),
        ("none", "vLLM Metal", "pairs with"),
        ("container", "adaptive apps", "configures"),
        ("localhost", "adaptive apps", "configures"),
        ("disabled", "adaptive apps", "removes"),
        ("cloud enabled", "adaptive apps", "configures"),
        ("vLLM Metal", "adaptive apps", "configures"),
    ],
    "track-selection-matrix": [
        ("Tracks", "Wizard", "narrows"),
        ("Wizard", "Service Families", "prompts"),
        ("Service Families", "Enabled", "selected"),
        ("Service Families", "Force Disabled", "out of track"),
        ("Overrides", "Selected Source", "authoritative"),
        ("Selected Source", "Enabled", "non-disabled"),
        ("Selected Source", "Disabled", "explicit"),
    ],
    "network-routing-topology": [
        ("Browser", "*.localhost", "hostname"),
        ("*.localhost", "Kong", "gateway"),
        ("Browser", "Direct Ports", "direct"),
        ("Kong", "Backend Network", "routes"),
        ("Direct Ports", "Backend Network", "host NAT"),
        ("Backend Network", "localhost mode", "host gateway"),
    ],
    "data-rag-flow": [
        ("Ingestion", "Doc Processing", "extract"),
        ("Ingestion", "MinIO", "objects"),
        ("Doc Processing", "Weaviate", "vectors"),
        ("Doc Processing", "LightRAG", "documents"),
        ("LightRAG", "Neo4j", "graph"),
        ("MinIO", "Backend", "artifacts"),
        ("Weaviate", "Backend", "retrieve"),
        # Backend reaches the graph through LightRAG; its direct neo4j call
        # is `status: planned` in services/backend/service.yml.
        ("LightRAG", "Backend", "graph retrieval"),
        ("Backend", "Open WebUI", "API"),
    ],
    "llm-provider-flow": [
        ("Open WebUI", "LiteLLM", "chat"),
        ("Backend", "LiteLLM", "inference"),
        ("Tools", "LiteLLM", "inference"),
        ("LiteLLM", "Ollama", "local"),
        ("LiteLLM", "vLLM Metal", "managed local"),
        ("LiteLLM", "Cloud LLMs", "passthrough"),
        ("LiteLLM", "Tracing", "telemetry"),
    ],
    "data-engineering-lakehouse-flow": [
        ("JupyterHub", "Spark", "interactive"),
        ("Zeppelin", "Spark", "interactive"),
        ("Airflow", "Spark", "scheduled"),
        ("Redpanda", "Spark", "stream"),
        ("Spark", "Iceberg REST", "catalog"),
        ("Trino", "Iceberg REST", "catalog"),
        ("Spark", "MinIO", "objects"),
        ("Trino", "MinIO", "objects"),
        ("Iceberg REST", "Supabase/Postgres", "metadata"),
        ("Iceberg REST", "MinIO", "warehouse objects"),
    ],
    "observability-flow": [
        ("Backend", "OTel Collector", "OTLP"),
        ("Celery Workers", "OTel Collector", "OTLP"),
        ("LiteLLM", "OTel Collector", "OTLP"),
        ("Atlas Services", "Prometheus", "metrics"),
        ("LiteLLM", "Langfuse", "LLM traces"),
        ("OTel Collector", "Tempo", "traces"),
        ("OTel Collector", "Loki", "redacted logs"),
        ("Prometheus", "Grafana", "query"),
        ("Tempo", "Grafana", "query"),
        ("Loki", "Grafana", "query"),
    ],
    "security-auth-secrets-boundary": [
        ("Gateway Clients", "Kong", "gateway"),
        ("Kong", "Kong Route Policies", "policy"),
        ("Kong Route Policies", "Routed Services", "forward"),
        ("Routed Services", "Backend API", "backend"),
        ("Routed Services", "Public APIs", "public"),
        ("Backend API", "Public APIs", "public"),
        ("Backend API", "Backend Identity", "identity"),
        ("Supabase Auth", "Backend Identity", "JWT"),
        ("Backend Identity", "Protected APIs", "authorize"),
        ("Backend API", "Plugin Key Auth", "plugin"),
        ("Plugin Key Auth", "Protected APIs", "key"),
        ("Direct Clients", "Backend API", "direct port"),
        ("Direct Clients", "Trusted UIs", "operator access"),
        ("Runtime Secrets", "Protected APIs", "inject"),
    ],
    "service-admission-workflow": [
        ("service.yml", "Topology", "declares"),
        ("service.yml", ".env.example", "generates"),
        ("service.yml", "Docs Regen", "generates"),
        ("compose.yml", "CI", "validates"),
        ("Topology", "CI", "validates"),
        (".env.example", "CI", "drift gate"),
        ("Docs Regen", "Diagrams", "renders"),
        ("Diagrams", "CI", "drift gate"),
    ],
}

ARCHITECTURE_LAYOUTS: dict[str, dict[str, tuple[int, int]]] = {
    "platform-overview": {
        "Clients": (50, 240), "Kong": (250, 240),
        "Apps": (470, 100), "Agents": (470, 380),
        "LLM Core": (720, 100), "Data Stores": (720, 380),
        "Cloud Providers": (980, 100),
    },
    "bootstrapper-lifecycle": {
        node: (40 + index * 175, 230)
        for index, node in enumerate(ARCHITECTURE_PERSPECTIVES["bootstrapper-lifecycle"][2])
    },
    "source-configuration-model": {
        "SOURCE Var": (50, 240), "container": (300, 40),
        "localhost": (300, 170), "disabled": (300, 300),
        "none": (300, 430), "cloud enabled": (590, 380),
        "vLLM Metal": (590, 480),
        "adaptive apps": (900, 240),
    },
    "track-selection-matrix": {
        "Tracks": (50, 240), "Wizard": (270, 240),
        "Service Families": (500, 240), "Enabled": (1040, 110),
        "Force Disabled": (1040, 370), "Overrides": (500, 30),
        "Selected Source": (790, 30), "Disabled": (1040, 30),
    },
    "network-routing-topology": {
        "Browser": (50, 240), "*.localhost": (280, 90),
        "Direct Ports": (280, 390), "Kong": (520, 90),
        "Backend Network": (770, 240), "localhost mode": (1020, 390),
    },
    "data-rag-flow": {
        "Ingestion": (50, 240), "Doc Processing": (280, 100),
        "MinIO": (280, 390), "Weaviate": (550, 190),
        "LightRAG": (550, 40), "Neo4j": (810, 40), "Backend": (810, 300),
        "Open WebUI": (1060, 300),
    },
    "llm-provider-flow": {
        "Open WebUI": (50, 60), "Backend": (50, 240),
        "Tools": (50, 420), "LiteLLM": (390, 240),
        "Ollama": (700, 40), "vLLM Metal": (700, 300),
        "Cloud LLMs": (700, 400),
        "Tracing": (1000, 240),
    },
    "data-engineering-lakehouse-flow": {
        "JupyterHub": (40, 30), "Zeppelin": (40, 160),
        "Airflow": (40, 290), "Redpanda": (40, 420),
        "Spark": (390, 180), "Trino": (390, 430),
        "Iceberg REST": (720, 100), "MinIO": (1050, 400),
        "Supabase/Postgres": (1050, 30),
    },
    "observability-flow": {
        "Backend": (40, 20), "Celery Workers": (40, 130),
        "LiteLLM": (40, 240), "Atlas Services": (40, 410),
        "OTel Collector": (340, 120), "Langfuse": (340, 260),
        "Prometheus": (340, 410), "Tempo": (680, 120),
        "Loki": (680, 410), "Grafana": (1000, 260),
    },
    "security-auth-secrets-boundary": {
        "Gateway Clients": (40, 80), "Kong": (230, 80),
        "Kong Route Policies": (420, 80), "Routed Services": (610, 80),
        "Public APIs": (990, 20), "Backend API": (800, 150),
        "Plugin Key Auth": (990, 150), "Backend Identity": (990, 280),
        "Protected APIs": (1180, 215), "Supabase Auth": (800, 280),
        "Direct Clients": (40, 340), "Trusted UIs": (230, 470),
        "Runtime Secrets": (1180, 470),
    },
    "service-admission-workflow": {
        "service.yml": (50, 100), "compose.yml": (50, 400),
        "Topology": (340, 30), ".env.example": (340, 210),
        "Docs Regen": (340, 390), "Diagrams": (650, 390),
        "CI": (970, 240),
    },
}

# Full connector point lists for the few dense perspectives where straight
# diagonals would cross and make edge ownership ambiguous. Labels are placed
# on their dedicated gutters instead of at the bounding-box midpoint.
ARCHITECTURE_ROUTES: dict[
    str,
    dict[
        tuple[str, str],
        tuple[list[tuple[int, int]], tuple[float, float]],
    ],
] = {
    "security-auth-secrets-boundary": {
        ("Runtime Secrets", "Protected APIs"): (
            [(1250, 466), (1250, 279)],
            (1250, 370),
        ),
    },
    "service-admission-workflow": {
        ("compose.yml", "CI"): (
            [(190, 430), (260, 430), (260, 510), (1040, 510), (1040, 304)],
            (640, 500),
        ),
    },
    "bootstrapper-lifecycle": {
        (source, target): (
            [(180 + index * 175, 260), (215 + index * 175, 260)],
            (197.5 + index * 175, 220),
        )
        for index, (source, target, _) in enumerate(ARCHITECTURE_EDGES["bootstrapper-lifecycle"])
    },
    "observability-flow": {
        ("OTel Collector", "Loki"): (
            [(480, 150), (580, 150), (580, 440), (680, 440)],
            (580, 300),
        ),
    },
    "data-engineering-lakehouse-flow": {
        ("Spark", "MinIO"): (
            [(530, 210), (610, 260), (940, 260), (940, 415), (1050, 415)],
            (780, 250),
        ),
        ("Trino", "Iceberg REST"): (
            [(530, 460), (610, 520), (1210, 520), (1210, 180), (790, 180), (790, 160)],
            (1040, 510),
        ),
        ("Iceberg REST", "MinIO"): (
            [(860, 130), (1020, 130), (1020, 360), (1120, 360), (1120, 400)],
            (1010, 245),
        ),
        ("Trino", "MinIO"): (
            [(530, 460), (980, 460), (980, 430), (1050, 430)],
            (790, 450),
        ),
    },
}

_NODE_KINDS = {
    "Clients": "frontend", "Gateway Clients": "frontend",
    "Direct Clients": "frontend", "Browser": "frontend", "Open WebUI": "frontend",
    "JupyterHub": "frontend", "Zeppelin": "frontend",
    "Apps": "backend", "Agents": "backend", "Backend": "backend",
    "Services": "backend", "Atlas Services": "backend",
    "Celery Workers": "backend", "Service APIs": "backend", "adaptive apps": "backend",
    "Doc Processing": "backend", "Ingestion": "backend", "LightRAG": "backend",
    "Data Stores": "data", "MinIO": "data", "Weaviate": "data",
    "Neo4j": "data", "Iceberg REST": "data", "Supabase Auth": "data",
    "Supabase/Postgres": "data",
    "Cloud Providers": "cloud", "Cloud LLMs": "cloud", "Cloud Keys": "cloud",
    "cloud enabled": "cloud", "Kong": "security", "API Keys": "security",
    "Local Secrets": "security", "Runtime Secrets": "security",
    "Backend Identity": "security", "Plugin Key Auth": "security",
    "Kong Route Policies": "security", "Routed Services": "backend",
    "Backend API": "backend", "Protected APIs": "backend", "Public APIs": "backend",
    "disabled": "security", "none": "security", "Trusted UIs": "security",
    "Redpanda": "bus",
}

ARCHITECTURE_INTERPRETATIONS: dict[str, str] = {
    "platform-overview": (
        "Direct published ports bypass Kong deliberately, for host tools that "
        "can't use the `*.localhost` gateway. Atlas-managed consumers use "
        "LiteLLM as the default path for local and cloud model traffic, "
        "centralizing credentials and routing. Services with explicit "
        "native-provider role overrides, such as LightRAG, can bypass LiteLLM "
        "by design (see [LLM provider flow](./llm-provider-flow.md))."
    ),
    "bootstrapper-lifecycle": (
        "Each stage gates the next; a failure in any stage before Compose must "
        "abort the run rather than report a partially launched stack. Env "
        "loading also applies the chained env-file migrations "
        "(`bootstrapper/services/migrations/`) before manifests are synthesized."
    ),
    "source-configuration-model": (
        "SOURCE selects a deployment mode, not just an image variant — the same "
        "value gates Compose scale, env wiring, and Kong route generation "
        "together. `none` is unique to the LLM provider family; no other "
        "service exposes it. For that LLM-only mode, LiteLLM can be backed by "
        "enabled cloud providers, managed vLLM Metal, or both."
    ),
    "track-selection-matrix": (
        "An explicit CLI `--<svc>-source` override always wins over track "
        "selection and is reported to the operator as an advisory warning. "
        "SOURCE values declared in a consumer manifest's `env.values` also "
        "survive the track force-disable step — only implicit track defaults "
        "get overridden."
    ),
    "network-routing-topology": (
        (
            'The host-gateway address depends on the runtime; it is not a fixed IP. '
            'Docker resolves the literal `host-gateway` value in `extra_hosts` itself. '
            'Podman has no such shortcut: `resolve_host_gateway_ip()` in '
            "`bootstrapper/utils/system.py` queries the default bridge network's IPAM "
            'gateway, then falls back to a throwaway container. Localhost-source '
            'endpoints always name `host.docker.internal`, which each reading container '
            'maps to that gateway (#1361).'
        )
    ),
    "data-rag-flow": (
        (
            'Backend is not the only writer into these stores. LightRAG writes directly '
            'to Neo4j over Bolt and to Supabase pgvector. Other MinIO consumers (the '
            'Iceberg pipeline, asset-worker) write with their own scoped IAM credentials.'
            " This view shows only Backend's ingestion path."
        )
    ),
    "llm-provider-flow": (
        "Disabling a cloud provider in `.env` doesn't error — the model "
        "resolver silently produces zero catalog entries for it. Managed "
        "vLLM Metal is another local LiteLLM upstream, so "
        "`LLM_PROVIDER_SOURCE=none` does not require a cloud provider. Ollama "
        "models get two aliases (`ollama/<name>` and the bare name); either "
        "works. Tracing observes requests out-of-band and isn't part of the "
        "inference call path, so a tracing-backend outage doesn't affect "
        "completions."
    ),
    "data-engineering-lakehouse-flow": (
        (
            'Iceberg REST keeps its catalog metadata in Supabase Postgres through a JDBC '
            'catalog, not a Hive metastore. Atlas sets `CATALOG_URI` to '
            '`jdbc:postgresql://supabase-db:5432/iceberg`. The fixture uses in-memory '
            'SQLite when the catalog URI is absent; a restart loses that metadata. '
            'A supplied URI is used as configured; invalid connections can prevent '
            'startup. Trino runs one coordinator and '
            'no workers, by design. Spark still starts with '
            '`ICEBERG_REST_SOURCE=disabled` for ML-only use; only lakehouse SQL fails.'
        )
    ),
    "observability-flow": (
        (
            'Langfuse is deliberately outside the OTel path. LiteLLM sends Langfuse '
            'traces through its own `success_callback`/`failure_callback`, not through '
            'the Collector. Langfuse is the LLM-behavior layer; Prometheus and Grafana '
            'are the infrastructure-metrics layer. Backend, Celery workers, and LiteLLM '
            'OTLP traces reach Tempo through the Collector. OTLP logs use the same '
            'receiver. The Collector redacts a documented field allowlist and persists '
            'accepted logs in Loki with trace and span metadata.'
        )
    ),
    "security-auth-secrets-boundary": (
        (
            'Kong applies route-specific Basic, key-auth, pass-through, rate-limit, and '
            'CORS policies; it does not provide one uniform identity layer. Backend '
            'separately validates Supabase JWTs, scoped first-party tokens, and operator '
            'tokens. It enforces plugin `open|key-auth|inherit` modes again at the '
            'application boundary. Backend `/health`, `/ready`, `/metrics`, and API-doc '
            'routes are public by design. Direct ports and operator-trusted UIs can '
            'bypass Kong, so keep those surfaces inside their intended network boundary.'
        )
    ),
    "service-admission-workflow": (
        (
            'The fragment check in `manifest_validator.py` blocks a partial landing. It '
            'reports `missing_fragment` for a non-virtual manifest without `compose.yml` '
            'and `unexpected_fragment` for a virtual manifest that ships one. It reports '
            '`fragment_container_drift` when `containers[]` disagrees with the compose '
            '`services:` keys. `tools/validate_fragments.py` runs this check in CI, and '
            'also checks `.env.example` drift and the README `TOPOLOGY` block.'
        )
    ),
}


def _inline_code_csv(values: list[str]) -> str:
    clean = [value for value in values if value]
    if not clean:
        return "`-`"
    return ", ".join(f"`{value}`" for value in clean)


def track_index(model: DocsModel) -> str:
    """Render the live canonical track index."""
    track_rows = [
        [track.key, track.description, track.services_display]
        for track in model.tracks
    ]
    return (
        "# Tracks\n\n## 1. Track Matrix\n\n"
        + table(["Track", "Description", "Services"], track_rows)
    )


def reference_index(model: DocsModel) -> str:
    """Render the live canonical reference index."""
    category_rows = []
    categories = sorted({service.category for service in model.services})
    for category in categories:
        services = [service.name for service in model.services if service.category == category]
        category_rows.append([category, str(len(services)), ", ".join(services) if services else "-"])
    return (
        """# Reference

## 1. Generated References

- [SOURCE values](source-values.md)
- [Environment variables](env-vars.md)
- [Ports and routes](ports-routes.md)
- [Tracks](../tracks.md)
- [Service dependencies](service-dependencies.md)
- [Manifest fields](manifest-fields.md)
- [Supply-chain license inventory](license-inventory.md)
- [Candidate decisions](candidate-decisions.md)

## 2. Hand-written References

- [Consumer manifest](consumer-manifest.md)

## 3. Category Summary

"""
        + table(["Category", "Count", "Services"], category_rows)
    )


def _architecture_diagram_html(
    title: str,
    description: str,
    interpretation: str,
    nodes: list[str],
    edges: list[tuple[str, str, str]],
    positions: dict[str, tuple[int, int]],
    routes: dict[
        tuple[str, str],
        tuple[list[tuple[int, int]], tuple[float, float]],
    ] | None = None,
) -> str:
    boxes = []
    arrows = []
    labels = []
    palette = {
        ("rgba(8, 51, 68, 0.4)", "#22d3ee", "frontend"),
        ("rgba(6, 78, 59, 0.4)", "#34d399", "backend"),
        ("rgba(76, 29, 149, 0.4)", "#a78bfa", "data"),
        ("rgba(120, 53, 15, 0.3)", "#fbbf24", "cloud"),
        ("rgba(136, 19, 55, 0.4)", "#fb7185", "security"),
        ("rgba(251, 146, 60, 0.3)", "#fb923c", "bus"),
        ("rgba(30, 41, 59, 0.5)", "#94a3b8", "generic"),
    }
    palette_by_kind = {role: (fill, stroke) for fill, stroke, role in palette}
    box_width = 140
    for node in nodes:
        box_x, y = positions[node]
        role = _NODE_KINDS.get(node, "generic")
        fill, stroke = palette_by_kind[role]
        boxes.append(
            f'<rect x="{box_x}" y="{y}" width="{box_width}" height="60" rx="6" fill="#0f172a"/>'
            f'<rect x="{box_x}" y="{y}" width="{box_width}" height="60" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
            f'<text x="{box_x + box_width / 2}" y="{y + 25}" fill="white" font-size="11" font-weight="600" text-anchor="middle">{html.escape(node)}</text>'
            f'<text x="{box_x + box_width / 2}" y="{y + 43}" fill="#94a3b8" font-size="9" text-anchor="middle">{role}</text>'
        )
    occupied_labels: list[tuple[float, float, float, float]] = []
    routes = routes or {}
    for source, target, label in edges:
        source_x, source_y = positions[source]
        target_x, target_y = positions[target]
        if source_x <= target_x:
            x1, x2 = source_x + box_width, target_x
        else:
            x1, x2 = source_x, target_x + box_width
        y1, y2 = source_y + 30, target_y + 30
        routed = routes.get((source, target))
        if routed:
            points, (label_x, base_label_y) = routed
            point_text = " ".join(f"{x},{y}" for x, y in points)
            arrows.append(
                f'<polyline data-source="{html.escape(source)}" '
                f'data-target="{html.escape(target)}" points="{point_text}" '
                f'fill="none" stroke="#64748b" stroke-width="1.5" '
                f'marker-end="url(#arrowhead)"/>'
            )
        else:
            label_x = (x1 + x2) / 2
            base_label_y = (y1 + y2) / 2 - 5
            arrows.append(
                f'<line data-source="{html.escape(source)}" '
                f'data-target="{html.escape(target)}" x1="{x1}" y1="{y1}" '
                f'x2="{x2}" y2="{y2}" stroke="#64748b" '
                f'stroke-width="1.5" marker-end="url(#arrowhead)"/>'
            )
        label_width = max(40, len(label) * 5 + 12)
        label_y = base_label_y
        for offset in (0, -18, 18, -36, 36, -54, 54):
            candidate_y = base_label_y + offset
            candidate = (
                label_x - label_width / 2,
                candidate_y - 10,
                label_x + label_width / 2,
                candidate_y + 4,
            )
            if not any(
                candidate[0] < right + 2
                and candidate[2] > left - 2
                and candidate[1] < bottom + 2
                and candidate[3] > top - 2
                for left, top, right, bottom in occupied_labels
            ):
                label_y = candidate_y
                occupied_labels.append(candidate)
                break
        labels.append(
            f'<rect x="{label_x - label_width / 2}" y="{label_y - 10}" '
            f'width="{label_width}" height="14" rx="3" fill="#020617"/>'
            f'<text x="{label_x}" y="{label_y}" fill="#cbd5e1" font-size="8" '
            f'text-anchor="middle">{html.escape(label)}</text>'
        )
    width = max(1000, max(box_x for box_x, _ in positions.values()) + box_width + 60)
    height = max(560, max(y for _, y in positions.values()) + 60 + 60)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin: 0; background: #020617; color: #e2e8f0; font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 32px; }}
    .frame {{ border: 1px solid #1e293b; border-radius: 14px; background: #020617; padding: 20px; }}
    .interpretation {{ border-left: 3px solid #22d3ee; margin-top: 20px; padding: 2px 0 2px 16px; }}
    .interpretation h2 {{ color: #e2e8f0; font-size: 15px; margin: 0 0 8px; }}
    .interpretation p {{ color: #94a3b8; font-size: 13px; line-height: 1.6; margin: 0; }}
    .signal {{ background: #22d3ee; border-radius: 999px; box-shadow: 0 0 18px rgba(34, 211, 238, 0.74); content: ''; display: inline-block; height: 8px; width: 8px; }}
    .title {{ align-items: center; display: flex; gap: 10px; }}
  </style>
</head>
<body>
  <main>
    <h1 class="title"><span class="signal"></span>{html.escape(title)}</h1>
    <p>{html.escape(description)}</p>
    <div class="frame">
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">
        <defs>
          <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" stroke-width="0.5"/>
          </pattern>
          <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
            <polygon points="0 0, 10 3.5, 0 7" fill="#64748b"/>
          </marker>
        </defs>
        <rect width="{width}" height="{height}" fill="#020617"/>
        <rect width="{width}" height="{height}" fill="url(#grid)"/>
        {''.join(arrows)}
        {''.join(labels)}
        {''.join(boxes)}
      </svg>
    </div>
    <section class="interpretation">
      <h2>How to read this view</h2>
      <p>{_render_architecture_interpretation(interpretation)}</p>
    </section>
  </main>
</body>
</html>"""


_INTERPRETATION_TOKEN_RE = re.compile(
    r"`(?P<code>[^`]+)`|\[(?P<label>[^\]]+)\]\((?P<href>\./[^)]+\.md)\)"
)


def _render_architecture_interpretation(value: str) -> str:
    """Render the small inline-markup subset used by diagram notes safely."""
    rendered: list[str] = []
    cursor = 0
    for match in _INTERPRETATION_TOKEN_RE.finditer(value):
        rendered.append(html.escape(value[cursor:match.start()]))
        if match.group("code") is not None:
            rendered.append(f"<code>{html.escape(match.group('code'))}</code>")
        else:
            rendered.append(
                f'<a href="{html.escape(match.group("href"), quote=True)}">'
                f'{html.escape(match.group("label"))}</a>'
            )
        cursor = match.end()
    rendered.append(html.escape(value[cursor:]))
    return "".join(rendered)


def architecture_pages(model: DocsModel) -> dict[Path, str]:
    arch = model.root / "docs" / "architecture"
    pages: dict[Path, str] = {}
    rows = []
    for slug, (title, description, nodes) in ARCHITECTURE_PERSPECTIVES.items():
        interpretation = ARCHITECTURE_INTERPRETATIONS[slug]
        pages[arch / f"{slug}.html"] = _architecture_diagram_html(
            title,
            description,
            interpretation,
            nodes,
            ARCHITECTURE_EDGES[slug],
            ARCHITECTURE_LAYOUTS[slug],
            ARCHITECTURE_ROUTES.get(slug),
        )
        pages[arch / f"{slug}.md"] = f"""# {title}

{description}

## 1. Diagram

![{title} architecture diagram](../diagrams/img/architecture-{slug}.png)

[Open the full-size diagram](./{slug}.html).

## 2. Notes

{interpretation}

## 3. Source Files

{chr(10).join(f"- `{path}`" for path in ARCHITECTURE_SOURCE_FILES[slug])}

"""
        rows.append([f"[{title}]({slug}.md)", description])
    # One catalog page (#1451); the platform page shows its own diagram and
    # links to it instead of repeating the table.
    pages[arch / "README.md"] = (
        "# Diagram Catalog\n\n## 1. Generated Diagram Index\n\n"
        "Generated catalog of split Atlas architecture perspectives.\n\n"
        + table(["Diagram", "Purpose"], rows)
    )
    pages[arch / "index.md"] = (
        "# Platform Architecture\n\n"
        "The platform's major tiers and runtime call direction, from user entrypoints "
        "through Kong to apps, agents, the LLM core and the data stores.\n\n"
        "## 1. Diagram\n\n"
        "![Atlas platform architecture diagram](../diagrams/img/atlas-platform.png)\n\n"
        "[Open the full-size diagram](../diagrams/architecture.html).\n\n"
        "## 2. Perspectives\n\n"
        "Each perspective has its own page. Topics include the bootstrapper lifecycle, "
        "SOURCE model, tracks, routing, data and RAG flow, and LLM providers. "
        "The [Diagram Catalog](README.md) lists these and other perspectives.\n"
    )
    return pages


_MANIFEST_FIELD_PURPOSES = {
    "containers": "Container names in the service family",
    "env": "Environment variables owned by the manifest",
    "sources": "SOURCE var, default, and allowed values",
    "category": "Topology category and wizard grouping",
    "docs": "Repository-relative operator documentation path",
    "docs_exception": (
        "Printable reason with an explicit `because` clause, four "
        "substantive words, and three distinct terms"
    ),
    "depends_on": "Required and optional logical dependencies",
    "support": (
        "Support tier (`stable`, `experimental`, `community`, `unsupported`), "
        "its evidence and the tag or commit it was gathered at, the owner, "
        "and known limitations"
    ),
    "runtime_sc": "Per-source runtime scale/environment/deploy/extra_hosts slices",
    "runtime_deps": (
        "Launch-time dependency rules (requires / optional / conditional_requires); "
        "a missing requirement auto-disables the service"
    ),
    "data_flow": "Runtime call graph (`data_flow.calls`) used by docs and diagrams",
    "name": "Folder name under `services/`, in kebab-case",
    "extra_kong_aliases": "Extra `*.localhost` Kong hostnames beyond each row's alias",
    "images": "Image env var, pinned default image and the container that uses it",
    "runtime_dependency_tiers": "Stack-wide startup tier order (the `globals` manifest only)",
}


def _manifest_field_rows(root: Path) -> list[list[str]]:
    """Every top-level property of service.schema.json. A hand list showed
    10 of 22 fields and none of the required name/label/capabilities
    (2026-10-08 run, cycle 40)."""
    import json  # noqa: PLC0415

    schema = json.loads((root / "bootstrapper" / "schemas" / "service.schema.json").read_text(encoding="utf-8"))
    required = set(schema.get("required", ()))
    rows = []
    for name, spec in schema["properties"].items():
        purpose = _MANIFEST_FIELD_PURPOSES.get(name) or (spec.get("description") or "").split(". ")[0].rstrip(".")
        rows.append([name, "yes" if name in required else "no", purpose or "See the schema"])
    return rows


def reference_pages(model: DocsModel) -> dict[Path, str]:
    ref = model.root / "docs" / "site" / "reference"
    source_rows = []
    for service in model.services:
        for surface in service.source_surfaces:
            source_rows.append(
                [surface.var, service.name, surface.default, csv_or_dash(surface.values)]
            )
    deps_rows = [
        [
            service.name,
            csv_or_dash(service.required_dependencies),
            csv_or_dash(service.optional_dependencies),
            csv_or_dash(service.runtime_calls),
        ]
        for service in model.services
    ]
    env_rows = []
    for service in model.services:
        for env_var in service.env_vars:
            env_rows.append(
                [
                    env_var.name,
                    service.name,
                    env_var.default,
                    env_var.description or "-",
                ]
            )
    ports_rows = [
        [
            service.name,
            service.category,
            _inline_code_csv(service.port_vars),
            _inline_code_csv(service.kong_aliases),
        ]
        for service in model.services
        if service.port_vars or service.kong_aliases
    ]
    return {
        ref / "source-values.md": "# SOURCE Values\n\n## 1. Generated Source Matrix\n\n"
        + table(["SOURCE", "Service", "Default", "Values"], source_rows),
        ref / "env-vars.md": "# Environment Variables\n\n## 1. Generated Environment Matrix\n\n"
        + table(["Variable", "Service", "Default", "Description"], env_rows),
        ref / "ports-routes.md": "# Ports and Routes Reference\n\n## 1. Generated Ports and Routes Matrix\n\n"
        "Generated summary of model-backed service port variables and Kong aliases. "
        "For browser-facing hostnames and route behavior, see the "
        "[Ports and Routes](../../operations/ports-and-routes.md#2-kong-hostnames).\n\n"
        + table(
            ["Service", "Category", "Port Variables", "Kong Aliases"],
            ports_rows,
        ),
        ref / "service-dependencies.md": "# Service Dependencies\n\n## 1. Generated Dependency Matrix\n\n"
        "The start-order column comes from each manifest's `depends_on.required`. "
        "It orders startup and display; some entries only pin a port slot. "
        "It does not list runtime requirements. Runtime edges are in the Runtime Calls column.\n\n"
        + table(["Service", "Start order (depends_on.required)", "Optional", "Runtime Calls"], deps_rows),
        ref / "manifest-fields.md": "# Manifest Fields\n\n## 1. Manifest Schema Quick Reference\n\n"
        "Generated from the top-level keys of `bootstrapper/schemas/service.schema.json`. See "
        "[Contributing §18](../CONTRIBUTING-services.md#18-schema-cheatsheet) for nested fields.\n\n"
        "Schema-required marks the keys the JSON schema requires. The validator and tests also "
        "require `support`, `containers` on non-virtual manifests (matching `compose.yml`), and "
        "`docs`, a sibling `README.md` or `docs_exception`.\n\n"
        + table(["Field", "Schema-required", "Purpose"], _manifest_field_rows(model.root)),
    }
