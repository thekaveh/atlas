#!/usr/bin/env python3
"""Check docker-compose hard dependencies against SOURCE-replaceable services.

Compose `depends_on` is only safe for services that must always be started as
containers. SOURCE-replaceable services can be container-backed, `localhost`,
or `disabled`, so consumers should reference them through endpoint environment
variables and runtime readiness/feature checks instead of static `depends_on`.

Invoke as ``python scripts/check-compose-source-deps.py [--env-file PATH]``.
Compose is always rendered against ``.env.example`` unless ``--env-file`` names
another file, so a developer's local ``.env`` cannot change the result (#1389).

A hard edge into any container of a family whose ``sources:`` offer
``localhost`` or ``disabled``, or that such a source's endpoint names (the
provider-scaled engines), fails unless the dependent is in the same family,
the edge is in REQUIRED_DEPENDS_ON, or it is reviewed in
ALLOWED_REPLACEABLE_DEPENDS_ON (#1389).

Exit codes:
    0  — all PASS
    1  — at least one FAIL line printed
    2  — usage error, or internal failure (PyYAML missing, or docker compose
         config errored with stderr surfaced)
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

from bounded_subprocess import (
    CommandLaunchError,
    CommandOutputTooLarge,
    CommandTimedOut,
    redacted_failure,
    run_bounded,
)

try:
    import yaml
except ImportError:  # pragma: no cover - developer environment guard
    print("FAIL import: PyYAML is required to parse docker-compose.yml", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.yml"
ENV_FILE = ROOT / ".env.example"

# Edges where the dependency target is SOURCE-replaceable and should not be a
# hard compose startup prerequisite. Keep this list intentionally explicit so
# changes are reviewed service-by-service instead of hidden in broad heuristics.
FORBIDDEN_OPTIONAL_DEPENDS_ON = {
    ("backend", "weaviate-init"),
    ("n8n", "weaviate"),
    ("n8n-worker", "weaviate"),
    ("jupyterhub", "weaviate"),
    ("jupyterhub", "ollama"),
    ("jupyterhub", "neo4j-graph-db"),
    ("weaviate", "multi2vec-clip"),
    # LightRAG (2026-06-05): storage + capability services are
    # SOURCE-replaceable: with a source disabled, LIGHTRAG_*_STORAGE can name
    # an in-process backend (NanoVectorDB / NetworkX / JsonKV); there is no
    # automatic fallback. LiteLLM is the only hard dependency — see
    # REQUIRED_DEPENDS_ON below.
    ("lightrag", "supabase-db"),
    ("lightrag", "redis"),
    ("lightrag", "neo4j-graph-db"),
    ("lightrag", "docling-gpu"),
    ("lightrag", "tei-reranker"),
}

# Reviewed hard edges into SOURCE-replaceable families: each dependent cannot
# do its job without that dependency, and is only enabled alongside it (#1389).
ALLOWED_REPLACEABLE_DEPENDS_ON = {
    ("asset-baker", "minio"),
    ("asset-baker", "minio-init"),
    ("asset-worker", "minio"),
    ("asset-worker", "minio-init"),
    ("comfyui-init", "ollama-pull"),
    ("iceberg-rest", "minio-init"),
    ("jenkins", "minio-init"),
    ("label-studio-init", "minio-init"),
    ("langfuse-init", "minio-init"),
    ("llm-graph-builder-backend", "neo4j-graph-db"),
    ("local-deep-researcher", "searxng"),
    ("mcp-servers", "neo4j-graph-db"),
    ("mcp-servers", "searxng"),
    ("mlflow-init", "minio-init"),
    ("otel-collector", "loki"),
    ("otel-collector", "tempo"),
    ("spark-init", "minio-init"),
    ("trino", "iceberg-rest"),
    ("trino", "minio-init"),
    ("verba", "weaviate"),
    ("zeppelin", "minio-init"),
    ("zeppelin-init", "minio-init"),
    ("zeppelin-init", "spark-init"),
}

# Edges that are expected after the SOURCE-safe dependency cleanup. These are
# guardrails for the normal launch flow: core services should still wait for the
# infrastructure they genuinely require.
REQUIRED_DEPENDS_ON = {
    ("n8n", "supabase-db-init"),
    ("n8n", "redis"),
    ("n8n-worker", "supabase-db-init"),
    ("n8n-worker", "redis"),
    ("jupyterhub", "supabase-db-init"),
    ("jupyterhub", "redis"),
    ("weaviate", "supabase-db"),
    ("weaviate", "weaviate-init"),
    # LiteLLM is mandatory and not source-replaceable. Every LLM consumer
    # hard-depends on it. Ollama remains source-replaceable (see FORBIDDEN
    # above) — consumers reach Ollama through LiteLLM.
    ("litellm", "litellm-init"),
    ("litellm", "supabase-db"),
    ("litellm", "redis"),
    ("open-web-ui", "litellm"),
    ("backend", "litellm"),
    ("n8n", "litellm"),
    ("n8n-worker", "litellm"),
    # n8n-init is an offline, pre-start package installer. It must not wait on
    # LiteLLM (or n8n); the runtime services own their own LiteLLM health gates.
    ("jupyterhub", "litellm"),
    ("local-deep-researcher", "litellm"),
    ("openclaw-gateway", "litellm"),
    ("hermes-init", "litellm"),
    ("hermes", "litellm"),
    ("weaviate-init", "litellm"),
    ("weaviate", "litellm"),
    # Observability sidecars — exporters embedded in the data tier's families.
    # Each hard-depends on its target service so the sidecar doesn't start
    # before the database is healthy. Both are scaled 0 when PROMETHEUS_SOURCE
    # is disabled, so the depends_on doesn't gate compose unnecessarily.
    ("postgres-exporter", "supabase-db"),
    ("redis-exporter", "redis"),
    # Grafana depends on Prometheus for its provisioned datasource. Both
    # scale together with their respective SOURCE values; the edge is only
    # active when both are running.
    ("grafana", "prometheus"),
    # Compute tier additions (2026-06-04):
    # - Spark workers + history must wait on the master being healthy +
    #   the spark-init bucket-bootstrap respectively.
    # - Zeppelin is gated on Spark — its Spark interpreter is the whole point.
    # - Airflow's init container waits for central scoped-role/database
    #   provisioning before migrating its metadata DB; webserver + scheduler
    #   depend on the init container completing.
    ("spark-worker", "spark-master"),
    ("spark-history", "spark-init"),
    ("zeppelin", "spark-master"),
    ("airflow-init", "supabase-db-init"),
    ("airflow-webserver", "airflow-init"),
    ("airflow-scheduler", "airflow-init"),
    # Airflow 3.x dag-processor — required as a standalone service (the
    # scheduler no longer parses DAGs in-process); must wait for init.
    ("airflow-dag-processor", "airflow-init"),
    # Pass 16 spark cold-start race fix: spark.eventLog.dir is read at
    # session start; Spark doesn't auto-create the s3a:// base dir, so
    # spark-connect + zeppelin must wait for spark-init (which creates
    # the spark-history MinIO bucket via minio/mc).
    ("spark-connect", "spark-init"),
    ("zeppelin", "spark-init"),
    # LightRAG (2026-06-05): init container has service_completed_successfully
    # condition gate; must wait before the main service starts.
    ("lightrag", "lightrag-init"),
    # LightRAG hard-depends on LiteLLM (the only mandatory backend — every
    # other backend has an in-process fallback). Uses Compose's
    # service_healthy condition instead of in-script polling, matching the
    # hermes-init / open-web-ui / backend / n8n pattern.
    ("lightrag", "litellm"),
    ("lightrag-init", "litellm"),
}


def _endpoint_hosts(manifest) -> set[str]:
    """Hosts the manifest's endpoint variables point at across its sources,
    such as `speaches` for TTS_PROVIDER_SOURCE's TTS_ENDPOINT."""
    endpoint_vars = {row.localhost_endpoint_var for row in manifest.rows} - {None}
    return {
        host
        for options in manifest.runtime_sc.values()
        for option in options.values()
        for var, value in ((option or {}).get("environment") or {}).items()
        if var in endpoint_vars
        for host in re.findall(r"https?://([a-z0-9-]+)[:/]", str(value))
    }


def _source_replaceable(manifest) -> bool:
    options = manifest.sources.options if manifest.sources else []
    return any(option.id == "disabled" or "localhost" in option.id for option in options)


def replaceable_families() -> dict[str, str]:
    """Container -> family, for the containers of every family whose
    ``sources:`` offer ``localhost`` or ``disabled``, and for the containers
    such a source's endpoint names (provider-scaled engines)."""
    sys.path.insert(0, str(ROOT / "bootstrapper"))
    from services.manifests import load_manifests

    manifests = load_manifests(ROOT / "services")
    families = {container: manifest.name for manifest in manifests for container in manifest.containers}
    replaceable: set[str] = set()
    for manifest in filter(_source_replaceable, manifests):
        replaceable |= set(manifest.containers) | _endpoint_hosts(manifest)
    return {container: families[container] for container in replaceable if container in families}


def forbidden_edges(edges: set[tuple[str, str]], replaceable: dict[str, str]) -> list[tuple[str, str]]:
    """The listed forbidden edges plus every unreviewed hard edge into another
    family's SOURCE-replaceable container."""
    derived = {
        (service, dependency)
        for service, dependency in edges
        if dependency in replaceable and replaceable.get(service) != replaceable[dependency]
    }
    reviewed = ALLOWED_REPLACEABLE_DEPENDS_ON | REQUIRED_DEPENDS_ON
    return sorted((FORBIDDEN_OPTIONAL_DEPENDS_ON & edges) | (derived - reviewed))


def load_compose(env_file: Path = ENV_FILE) -> dict:
    """Load the merged compose shape.

    The top-level docker-compose.yml uses `include:` to pull in per-service
    fragments under services/<name>/compose.yml. yaml.safe_load on the raw
    file would only see the empty `services:` block at the top, so we
    delegate to `docker compose config` which renders the merged shape.

    Renders against ``env_file`` (``.env.example`` by default, what CI
    copies to ``.env``), never the developer's ``.env``. A missing docker CLI or a `docker compose
    config` that exits non-zero is an audit-script failure (exit 2), not a
    recoverable condition. Silently returning the wrapper's empty `services:` block
    would emit spurious `missing required dependency` lines for every
    edge in REQUIRED_DEPENDS_ON.
    """
    args = ["docker", "compose", "--env-file", str(env_file), "-f", str(COMPOSE_FILE), "config"]
    try:
        result = run_bounded(args, cwd=ROOT)
    except CommandLaunchError:
        # docker not on PATH. The raw parse sees only the include-only
        # wrapper's empty `services:` and reported every required edge as a
        # regression (exit 1); a missing tool is an internal failure (exit 2).
        print("FAIL load_compose: docker CLI not found on PATH", file=sys.stderr)
        sys.exit(2)
    except CommandTimedOut:
        print("FAIL load_compose: docker compose config timed out", file=sys.stderr)
        sys.exit(2)
    except CommandOutputTooLarge:
        print(
            "FAIL load_compose: docker compose config output exceeded its limit",
            file=sys.stderr,
        )
        sys.exit(2)
    if result.returncode != 0:
        # docker is present but `compose config` failed — surface the
        # stderr instead of producing wrong-answer output.
        print(
            "FAIL load_compose: `docker compose config` exited "
            f"{result.returncode}",
            file=sys.stderr,
        )
        print(redacted_failure("docker compose config", result.returncode), file=sys.stderr)
        sys.exit(2)
    return yaml.safe_load(result.stdout) or {}


def dependency_names(service_def: dict) -> set[str]:
    """Return hard Compose dependencies, excluding explicit soft edges."""
    depends_on = service_def.get("depends_on") or {}
    if isinstance(depends_on, dict):
        return {
            name
            for name, config in depends_on.items()
            if not (isinstance(config, dict) and config.get("required") is False)
        }
    if isinstance(depends_on, list):
        return set(depends_on)
    return set()


def _env_file_argument(argv: list[str]) -> Path | None:
    """``--env-file PATH`` or nothing; None for any other arguments."""
    if not argv:
        return ENV_FILE
    if len(argv) == 1 and argv[0].startswith("--env-file="):
        argv = ["--env-file", argv[0].split("=", 1)[1]]
    # Resolved against the caller's directory, not the repo root it runs in.
    return Path(argv[1]).resolve() if len(argv) == 2 and argv[0] == "--env-file" else None


def compose_edges(compose: dict) -> set[tuple[str, str]]:
    return {
        (service_name, dependency)
        for service_name, service_def in (compose.get("services") or {}).items()
        for dependency in dependency_names(service_def)
    }


def main(argv: list[str] | None = None) -> int:
    env_file = _env_file_argument(sys.argv[1:] if argv is None else argv)
    if env_file is None:
        print("usage: check-compose-source-deps.py [--env-file PATH]", file=sys.stderr)
        return 2
    edges = compose_edges(load_compose(env_file))

    forbidden = forbidden_edges(edges, replaceable_families())
    missing_required = sorted(REQUIRED_DEPENDS_ON - edges)

    failed = False
    if forbidden:
        failed = True
        print("FAIL optional_provider_depends_on")
        for service, dependency in forbidden:
            print(f"  {service} must not hard depend_on SOURCE-replaceable {dependency}")
    else:
        print("PASS optional_provider_depends_on")

    if missing_required:
        failed = True
        print("FAIL required_core_depends_on")
        for service, dependency in missing_required:
            print(f"  {service} is missing required core dependency {dependency}")
    else:
        print("PASS required_core_depends_on")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
