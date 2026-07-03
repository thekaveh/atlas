#!/usr/bin/env python3
"""Baseline-defaults regression test for the Kong route generator.

``volumes/api/kong-dynamic.yml`` is a generated runtime artifact (not
checked in; .gitignore'd). Validating the user's local copy gives a
result that depends on whatever is in their .env right now — useless
as a regression check.

This script instead invokes ``bootstrapper.utils.kong_config_generator``
against ``.env.example`` (in a tmp working dir so the user's actual
.env is never read), parses the generated YAML, and verifies the
default container-source routes. It tells you "given the published
defaults, does the generator still produce the documented routes?" —
deterministic regardless of local config.

Exit codes:
  0 — generated config matches the default-route table
  1 — mismatch (route changed; either the table or the generator is wrong)
  2 — generation/parse failure (typically a missing dependency or a
      generator bug — fix the generator, not this script)
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - developer environment guard
    print("FAIL import: PyYAML is required to parse Kong config", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"

# Make the bootstrapper package importable.
sys.path.insert(0, str(ROOT / "bootstrapper"))


EXPECTED_HOST_ROUTES = {
    "comfyui.localhost": "http://comfyui:18188/",
    "n8n.localhost": "http://n8n:5678/",
    # MinIO S3 API friendly host (separate from the minio.localhost console
    # route -> :9001). Declared via extra_kong_aliases in the manifest.
    "s3.minio.localhost": "http://minio:9000/",
    "search.localhost": "http://searxng:8080/",
    "jupyter.localhost": "http://jupyterhub:8888/",
    "api.localhost": "http://backend:8000/",
    "chat.localhost": "http://open-web-ui:8080/",
    # Hermes Agent's web dashboard. Default-on (HERMES_SOURCE=container,
    # HERMES_DASHBOARD_ENABLED=true), so the generator emits this route
    # for .env.example. If the default ever flips to disabled, drop this
    # entry from the expected map.
    "hermes.localhost": "http://hermes:9119/",
    # LiteLLM gateway + admin dashboard. Always-on (no SOURCE variation).
    # Same alias exposes /ui/ (dashboard), /v1/* (proxy API), and
    # /spend/* (usage telemetry) — Kong routes the entire surface, not
    # just the dashboard path.
    "litellm.localhost": "http://litellm:4000/",
    # Remaining default-on routes (previously unlisted — regressions in
    # these hosts passed this script silently):
    "studio.localhost": "http://supabase-studio:3000/",
    # Bare gateway root is a generated Atlas service directory. The
    # pre-function route exits before proxying; the loopback URL only
    # satisfies Kong's declarative service schema.
    "localhost": "http://127.0.0.1:1/",
    "graph.localhost": "http://neo4j-graph-db:7474/",
    "weaviate.localhost": "http://weaviate:8080/",
    "ollama.localhost": "http://ollama:11434/",
    "research.localhost": "http://local-deep-researcher:2024/",
    # STT/TTS both default to speaches (speaches-container-cpu serves
    # both roles on its single port).
    "stt.localhost": "http://speaches:8000/",
    "tts.localhost": "http://speaches:8000/",
    # MinIO admin console (port 9001). Default-on (MINIO_SOURCE=container).
    # The S3 API at port 9000 is deliberately NOT aliased — S3 clients
    # use full URLs with explicit ports anyway and don't benefit from a
    # friendly hostname.
    "minio.localhost": "http://minio:9001/",
    # openclaw is opt-in: .env.example defaults OPENCLAW_SOURCE=disabled, so
    # the generator omits its route. Add an opt-in check separately if the
    # default ever flips to OPENCLAW_SOURCE=container.
    #
    # ray.localhost is NOT listed here. .env.example defaults
    # RAY_SOURCE=disabled; the generator only emits the Ray dashboard
    # route for RAY_SOURCE ∈ {ray-container-cpu, ray-container-gpu}.
    # At default-env runtime there is no ray-head container to route to,
    # so the route is correctly absent. If the default ever flips to a
    # container source, add "ray.localhost": "http://ray-head:8265/" here.
    #
    # spark.localhost / spark-history.localhost / zeppelin.localhost /
    # airflow.localhost are NOT listed for the same reason. .env.example
    # defaults SPARK_SOURCE / ZEPPELIN_SOURCE / AIRFLOW_SOURCE to disabled;
    # the generator only emits these routes when SOURCE=container.
    # Regression coverage at default-env lives here (absence is correct);
    # opt-in route shape is locked by bootstrapper/tests/test_kong_alias_routes.py.
    # If a default ever flips, add the matching entries:
    #   "spark.localhost": "http://spark-master:8080",
    #   "spark-history.localhost": "http://spark-history:18080",
    #   "zeppelin.localhost": "http://zeppelin:8080",
    #   "airflow.localhost": "http://airflow-webserver:8080",
    # (no trailing slash — matches the generator's emitted URLs for
    # these four; copy-pasting a trailing-slash form would fail the
    # equality check.)
    #
    # rerank.localhost is NOT listed here. .env.example defaults
    # TEI_RERANKER_SOURCE=disabled; the generator only emits the reranker
    # route for TEI_RERANKER_SOURCE ∈ {container-cpu, container-gpu, localhost}.
    # At default-env runtime there is no tei-reranker container to route to,
    # so the route is correctly absent. Opt-in route shape is locked by
    # bootstrapper/tests/test_kong_alias_routes.py::test_tei_reranker_*.
    # If the default ever flips, add:
    #   "rerank.localhost": "http://tei-reranker:80/",
    #
    # lightrag.localhost is NOT listed here. .env.example defaults
    # LIGHTRAG_SOURCE=disabled; the generator only emits the LightRAG route
    # for LIGHTRAG_SOURCE ∈ {container, localhost}.
    # At default-env runtime there is no lightrag container to route to,
    # so the route is correctly absent. Opt-in route shape (including
    # preserve_host=True for the WebUI SPA) is locked by
    # bootstrapper/tests/test_kong_alias_routes.py::test_lightrag_*.
    # If the default ever flips, add:
    #   "lightrag.localhost": "http://lightrag:9621/",
}


def generate_default_kong_config(out_dir: Path) -> Path:
    """Run the kong_config_generator against the published defaults.

    KongConfigGenerator only reads ``.env`` (env-var values), so we copy
    ``.env.example`` into ``out_dir`` as ``.env`` and point ConfigParser
    at that tempdir. No services/ or bootstrapper/ symlinks needed —
    Kong route generation is driven by env vars alone.

    Returns the path to the generated kong-dynamic.yml inside out_dir.
    """
    if not ENV_EXAMPLE.exists():
        raise FileNotFoundError(f"{ENV_EXAMPLE} missing — repo layout broken")
    shutil.copyfile(ENV_EXAMPLE, out_dir / ".env")
    (out_dir / "volumes" / "api").mkdir(parents=True, exist_ok=True)

    # Late imports — these need bootstrapper on sys.path.
    from core.config_parser import ConfigParser
    from utils.kong_config_generator import KongConfigGenerator

    config_parser = ConfigParser(str(out_dir))
    generator = KongConfigGenerator(config_parser)
    config = generator.generate_kong_config()

    out_path = out_dir / "volumes" / "api" / "kong-dynamic.yml"
    with out_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, sort_keys=False, default_flow_style=False)
    return out_path


def host_url_map(config: dict) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for service in config.get("services") or []:
        url = service.get("url")
        for route in service.get("routes") or []:
            for host in route.get("hosts") or []:
                mapping[host] = url
    return mapping


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="kong-route-check-") as td:
        td_path = Path(td)
        try:
            kong_path = generate_default_kong_config(td_path)
            with kong_path.open("r", encoding="utf-8") as fh:
                config = yaml.safe_load(fh) or {}
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL generation: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

    hosts = host_url_map(config)
    issues = []
    for host, expected_url in EXPECTED_HOST_ROUTES.items():
        actual_url = hosts.get(host)
        if actual_url != expected_url:
            issues.append(f"  {host}: expected {expected_url}, got {actual_url or 'MISSING'}")

    if issues:
        print("FAIL default_host_routes")
        for line in issues:
            print(line)
        return 1
    print("PASS default_host_routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
