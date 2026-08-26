#!/usr/bin/env python3
"""Check docker-compose hard dependencies against SOURCE-replaceable services.

Compose `depends_on` is only safe for services that must always be started as
containers. SOURCE-replaceable services can be container-backed, `localhost`,
or `disabled`, so consumers should reference them through endpoint environment
variables and runtime readiness/feature checks instead of static `depends_on`.

Zero-arg checker. Invoke as ``python scripts/check-compose-source-deps.py``.

Exit codes:
    0  — all PASS
    1  — at least one FAIL line printed
    2  — internal failure (PyYAML missing, or docker compose config
         errored with stderr surfaced)
"""
from __future__ import annotations

from pathlib import Path
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
    # SOURCE-replaceable. LightRAG transparently falls back to in-process
    # backends (NanoVectorDB / NetworkX / JsonKV) when the corresponding
    # source is disabled. LiteLLM is the only hard dependency — see
    # REQUIRED_DEPENDS_ON below.
    ("lightrag", "supabase-db"),
    ("lightrag", "redis"),
    ("lightrag", "neo4j-graph-db"),
    ("lightrag", "docling-gpu"),
    ("lightrag", "tei-reranker"),
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
    # - Airflow's init container migrates the metadata DB on supabase-db;
    #   webserver + scheduler depend on the init container completing.
    ("spark-worker", "spark-master"),
    ("spark-history", "spark-init"),
    ("zeppelin", "spark-master"),
    ("airflow-init", "supabase-db"),
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


def load_compose() -> dict:
    """Load the merged compose shape.

    The top-level docker-compose.yml uses `include:` to pull in per-service
    fragments under services/<name>/compose.yml. yaml.safe_load on the raw
    file would only see the empty `services:` block at the top, so we
    delegate to `docker compose config` which renders the merged shape.

    Falls back to `.env.example` when `.env` is missing (matching CI's
    `cp .env.example .env` step), and only falls back to the raw parse
    when the docker CLI itself isn't available — a `docker compose config`
    that exits non-zero is an audit-script failure, not a recoverable
    condition. Silently returning the wrapper's empty `services:` block
    would emit spurious `missing required dependency` lines for every
    edge in REQUIRED_DEPENDS_ON.
    """
    env_file = ROOT / ".env"
    env_fallback = ROOT / ".env.example"
    args = ["docker", "compose"]
    if env_file.is_file():
        args.extend(["--env-file", str(env_file)])
    elif env_fallback.is_file():
        args.extend(["--env-file", str(env_fallback)])
    args.extend(["-f", str(COMPOSE_FILE), "config"])
    try:
        result = run_bounded(args, cwd=ROOT)
    except CommandLaunchError:
        # docker not on PATH — fall through to the raw parse so the script
        # is still importable / linter-runnable on machines without docker.
        with COMPOSE_FILE.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
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


def main() -> int:
    compose = load_compose()
    services = compose.get("services") or {}
    edges = {
        (service_name, dependency)
        for service_name, service_def in services.items()
        for dependency in dependency_names(service_def)
    }

    forbidden = sorted(FORBIDDEN_OPTIONAL_DEPENDS_ON & edges)
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
