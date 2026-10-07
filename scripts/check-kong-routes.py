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

import os
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


# Must equal KongConfigGenerator.SUPABASE_API_ROUTE_TAG (a test pins it).
SUPABASE_API_ROUTE_TAG = "atlas-supabase-api"

EXPECTED_HOST_ROUTES = {
    "comfyui.localhost": "http://comfyui:18188/",
    "n8n.localhost": "http://n8n:5678/",
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
    "supabase-studio.localhost": "http://supabase-studio:3000/",
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
    # MinIO admin console (port 9001) and S3 API (port 9000), both
    # default-on (MINIO_SOURCE=container): minio.localhost → console,
    # s3.minio.localhost → the S3 API (added 2026-06-19).
    "minio.localhost": "http://minio:9001/",
    "s3.minio.localhost": "http://minio:9000/",
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
    # spark.localhost / spark-history.localhost / airflow.localhost are NOT
    # listed for the same reason. .env.example defaults SPARK_SOURCE and
    # AIRFLOW_SOURCE to disabled; the generator only emits these routes when
    # SOURCE=container. Zeppelin is intentionally loopback-only and never has
    # a Kong route because its UI has no built-in authentication.
    # Regression coverage at default-env lives here (absence is correct);
    # opt-in route shape is locked by bootstrapper/tests/test_kong_alias_routes.py.
    # If a default ever flips, add the matching entries:
    #   "spark.localhost": "http://spark-master:8080",
    #   "spark-history.localhost": "http://spark-history:18080",
    #   "airflow.localhost": "http://airflow-webserver:8080",
    # (no trailing slash — matches the generator's emitted URLs for
    # these three; copy-pasting a trailing-slash form would fail the
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


# Supabase API routes share one host allowlist (#1382).
_SUPABASE_HOSTS = ["localhost", "127.0.0.1", "kong-api-gateway", "host.docker.internal", "atlas-kong-api-gateway"]

# Every default route by name, generated from .env.example and reviewed. It
# catches what host checks cannot: path-only routes, plugins, strip_path and
# preserve_host (#1389). Update it when a default route intentionally changes.
EXPECTED_ROUTES: dict[str, dict] = {
    "atlas-root-dashboard-root": {
        "service": "atlas-root-dashboard", "url": "http://127.0.0.1:1/",
        "hosts": ["localhost"], "paths": ["/$"],
        "strip_path": False, "preserve_host": None,
        "service_plugins": ["cors"], "route_plugins": ["pre-function"],
    },
    "auth-v1-all": {
        "service": "auth-v1", "url": "http://supabase-auth:9999/",
        "hosts": _SUPABASE_HOSTS, "paths": ["/auth/v1/"],
        "strip_path": True, "preserve_host": None,
        "service_plugins": ["acl", "cors", "key-auth"], "route_plugins": [],
    },
    "auth-v1-open": {
        "service": "auth-v1-open", "url": "http://supabase-auth:9999/verify",
        "hosts": _SUPABASE_HOSTS, "paths": ["/auth/v1/verify"],
        "strip_path": True, "preserve_host": None,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "auth-v1-open-authorize": {
        "service": "auth-v1-open-authorize", "url": "http://supabase-auth:9999/authorize",
        "hosts": _SUPABASE_HOSTS, "paths": ["/auth/v1/authorize"],
        "strip_path": True, "preserve_host": None,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "auth-v1-open-callback": {
        "service": "auth-v1-open-callback", "url": "http://supabase-auth:9999/callback",
        "hosts": _SUPABASE_HOSTS, "paths": ["/auth/v1/callback"],
        "strip_path": True, "preserve_host": None,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "backend-api-all": {
        "service": "backend-api", "url": "http://backend:8000/",
        "hosts": ["api.localhost"], "paths": [],
        "strip_path": False, "preserve_host": None,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "comfyui-api-all": {
        "service": "comfyui-api", "url": "http://comfyui:18188/",
        "hosts": ["comfyui.localhost"], "paths": [],
        "strip_path": False, "preserve_host": True,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "dashboard-all": {
        "service": "dashboard", "url": "http://supabase-studio:3000/",
        "hosts": ["supabase-studio.localhost"], "paths": ["/"],
        "strip_path": False, "preserve_host": None,
        "service_plugins": ["acl", "basic-auth", "cors"], "route_plugins": [],
    },
    "graphql-v1-all": {
        "service": "graphql-v1", "url": "http://supabase-api:3000/rpc/graphql",
        "hosts": _SUPABASE_HOSTS, "paths": ["/graphql/v1"],
        "strip_path": True, "preserve_host": None,
        "service_plugins": ["acl", "cors", "key-auth"], "route_plugins": [],
    },
    "hermes-dashboard-all": {
        "service": "hermes-dashboard", "url": "http://hermes:9119/",
        "hosts": ["hermes.localhost"], "paths": [],
        "strip_path": False, "preserve_host": True,
        "service_plugins": ["acl", "basic-auth", "cors"], "route_plugins": [],
    },
    "jupyterhub-api-all": {
        "service": "jupyterhub-api", "url": "http://jupyterhub:8888/",
        "hosts": ["jupyter.localhost"], "paths": [],
        "strip_path": False, "preserve_host": True,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "litellm-gateway-all": {
        "service": "litellm-gateway", "url": "http://litellm:4000/",
        "hosts": ["litellm.localhost"], "paths": [],
        "strip_path": False, "preserve_host": True,
        "service_plugins": ["cors"], "route_plugins": ["pre-function"],
    },
    "meta-all": {
        "service": "meta", "url": "http://supabase-meta:8080/",
        "hosts": _SUPABASE_HOSTS, "paths": ["/pg/"],
        "strip_path": True, "preserve_host": None,
        "service_plugins": ["acl", "basic-auth", "cors"], "route_plugins": [],
    },
    "minio-console-all": {
        "service": "minio-console", "url": "http://minio:9001/",
        "hosts": ["minio.localhost"], "paths": [],
        "strip_path": False, "preserve_host": True,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "minio-s3-all": {
        "service": "minio-s3", "url": "http://minio:9000/",
        "hosts": ["s3.minio.localhost"], "paths": [],
        "strip_path": False, "preserve_host": True,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "minio-s3-metrics-blocked": {
        "service": "minio-s3", "url": "http://minio:9000/",
        "hosts": ["s3.minio.localhost"], "paths": ["/minio(?:/|\\x252[Ff])+(?:v[23](?:/|\\x252[Ff])+metrics|metrics(?:/|\\x252[Ff])+v3|prometheus(?:/|\\x252[Ff])+metrics)"],
        "strip_path": False, "preserve_host": None,
        "service_plugins": ["cors"], "route_plugins": ["request-termination"],
    },
    "n8n-api-all": {
        "service": "n8n-api", "url": "http://n8n:5678/",
        "hosts": ["n8n.localhost"], "paths": [],
        "strip_path": False, "preserve_host": True,
        "service_plugins": ["cors", "request-transformer"], "route_plugins": [],
    },
    "neo4j-browser-all": {
        "service": "neo4j-browser", "url": "http://neo4j-graph-db:7474/",
        "hosts": ["graph.localhost"], "paths": [],
        "strip_path": False, "preserve_host": True,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "ollama-api-all": {
        "service": "ollama-api", "url": "http://ollama:11434/",
        "hosts": ["ollama.localhost"], "paths": [],
        "strip_path": False, "preserve_host": True,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "openwebui-api-all": {
        "service": "openwebui-api", "url": "http://open-web-ui:8080/",
        "hosts": ["chat.localhost"], "paths": [],
        "strip_path": False, "preserve_host": True,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "realtime-v1-rest": {
        "service": "realtime-v1-rest", "url": "http://supabase-realtime:4000/api",
        "hosts": _SUPABASE_HOSTS, "paths": ["/realtime/v1/api/"],
        "strip_path": True, "preserve_host": None,
        "service_plugins": ["acl", "cors", "key-auth"], "route_plugins": [],
    },
    "realtime-v1-ws": {
        "service": "realtime-v1-ws", "url": "http://supabase-realtime:4000/socket",
        "hosts": _SUPABASE_HOSTS, "paths": ["/realtime/v1/"],
        "strip_path": True, "preserve_host": None,
        "service_plugins": ["acl", "cors", "key-auth"], "route_plugins": [],
    },
    "research-api-all": {
        "service": "research-api", "url": "http://local-deep-researcher:2024/",
        "hosts": ["research.localhost"], "paths": [],
        "strip_path": False, "preserve_host": True,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "rest-v1-all": {
        "service": "rest-v1", "url": "http://supabase-api:3000/",
        "hosts": _SUPABASE_HOSTS, "paths": ["/rest/v1/"],
        "strip_path": True, "preserve_host": None,
        "service_plugins": ["acl", "cors", "key-auth"], "route_plugins": [],
    },
    "searxng-api-all": {
        "service": "searxng-api", "url": "http://searxng:8080/",
        "hosts": ["search.localhost"], "paths": [],
        "strip_path": False, "preserve_host": True,
        "service_plugins": ["cors", "rate-limiting"], "route_plugins": [],
    },
    "storage-v1-all": {
        "service": "storage-v1", "url": "http://supabase-storage:5000/",
        "hosts": _SUPABASE_HOSTS, "paths": ["/storage/v1/"],
        "strip_path": True, "preserve_host": None,
        "service_plugins": ["acl", "cors", "key-auth"], "route_plugins": [],
    },
    "storage-v1-object-public": {
        "service": "storage-v1-object-public", "url": "http://supabase-storage:5000/object/public/",
        "hosts": _SUPABASE_HOSTS, "paths": ["/storage/v1/object/public/"],
        "strip_path": True, "preserve_host": None,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "storage-v1-object-sign": {
        "service": "storage-v1-object-sign", "url": "http://supabase-storage:5000/object/sign/",
        "hosts": _SUPABASE_HOSTS, "paths": ["/storage/v1/object/sign/"],
        "strip_path": True, "preserve_host": None,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "storage-v1-object-upload-sign": {
        "service": "storage-v1-object-upload-sign", "url": "http://supabase-storage:5000/object/upload/sign/",
        "hosts": _SUPABASE_HOSTS, "paths": ["/storage/v1/object/upload/sign/"],
        "strip_path": True, "preserve_host": None,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "stt-api-all": {
        "service": "stt-api", "url": "http://speaches:8000/",
        "hosts": ["stt.localhost"], "paths": [],
        "strip_path": False, "preserve_host": True,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "tts-api-all": {
        "service": "tts-api", "url": "http://speaches:8000/",
        "hosts": ["tts.localhost"], "paths": [],
        "strip_path": False, "preserve_host": True,
        "service_plugins": ["cors"], "route_plugins": [],
    },
    "weaviate-api-all": {
        "service": "weaviate-api", "url": "http://weaviate:8080/",
        "hosts": ["weaviate.localhost"], "paths": [],
        "strip_path": False, "preserve_host": True,
        "service_plugins": ["cors"], "route_plugins": [],
    },
}
EXPECTED_GLOBAL_PLUGINS = ["prometheus"]


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

    # ATLAS_ENV_FILE would point ConfigParser at a developer's own env file
    # instead of the published defaults copied above.
    saved = os.environ.pop("ATLAS_ENV_FILE", None)
    try:
        config = KongConfigGenerator(ConfigParser(str(out_dir))).generate_kong_config()
    finally:
        if saved is not None:
            os.environ["ATLAS_ENV_FILE"] = saved

    out_path = out_dir / "volumes" / "api" / "kong-dynamic.yml"
    with out_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, sort_keys=False, default_flow_style=False)
    return out_path


def host_url_map(config: dict) -> dict[str, str]:
    """Host -> upstream for every route with hosts, except the Supabase API
    routes, whose hosts are an allowlist rather than an alias (#1382)."""
    mapping: dict[str, str] = {}
    for service in config.get("services") or []:
        url = service.get("url")
        for route in service.get("routes") or []:
            if SUPABASE_API_ROUTE_TAG in (route.get("tags") or []):
                continue
            for host in route.get("hosts") or []:
                mapping[host] = url
    return mapping


def _plugin_names(owner: dict) -> list[str]:
    return sorted(plugin["name"] for plugin in owner.get("plugins") or [])


def _route_signature(service: dict, route: dict) -> dict:
    return {
        "service": service.get("name"),
        "url": service.get("url"),
        "hosts": list(route.get("hosts") or []),
        "paths": list(route.get("paths") or []),
        "strip_path": route.get("strip_path"),
        "preserve_host": route.get("preserve_host"),
        "service_plugins": _plugin_names(service),
        "route_plugins": _plugin_names(route),
    }


def route_signatures(config: dict) -> dict[str, dict]:
    """Route name -> everything Kong uses to match and guard it (#1389)."""
    return {
        route.get("name") or "<unnamed>": _route_signature(service, route)
        for service in config.get("services") or []
        for route in service.get("routes") or []
    }


def _signature_issues(config: dict) -> list[str]:
    """Each route's full signature by name, in both directions, plus the
    global plugins: hosts alone missed path-only routes, plugins, strip_path
    and preserve_host (#1389)."""
    issues = []
    actual = route_signatures(config)
    for name in sorted(set(EXPECTED_ROUTES) | set(actual)):
        expected, got = EXPECTED_ROUTES.get(name), actual.get(name)
        if expected is None:
            issues.append(f"  route {name}: UNEXPECTED {got} (not in EXPECTED_ROUTES)")
        elif got is None:
            issues.append(f"  route {name}: MISSING")
        else:
            issues += [f"  route {name}: {key} expected {expected[key]!r}, got {got[key]!r}"
                       for key in expected if expected[key] != got[key]]
    if _plugin_names(config) != EXPECTED_GLOBAL_PLUGINS:
        issues.append(f"  global plugins: expected {EXPECTED_GLOBAL_PLUGINS}, got {_plugin_names(config)}")
    return issues


def route_issues(config: dict) -> list[str]:
    """Every difference from the expected default routes, in both directions."""
    issues = []
    hosts = host_url_map(config)
    for host, expected_url in EXPECTED_HOST_ROUTES.items():
        actual_url = hosts.get(host)
        if actual_url != expected_url:
            issues.append(f"  {host}: expected {expected_url}, got {actual_url or 'MISSING'}")
    # ...and the other direction. Checking only that each EXPECTED host is
    # present meant an ADDED route passed silently — a regression that
    # publishes a new upstream through the gateway is exactly what this audit
    # should catch, and it could not see one. Verified by injecting a rogue
    # `admin.localhost -> supabase-db:5432` route: the gate printed PASS.
    for host in sorted(set(hosts) - set(EXPECTED_HOST_ROUTES)):
        issues.append(f"  {host}: UNEXPECTED route -> {hosts[host]} (not in EXPECTED_HOST_ROUTES)")
    return issues + _signature_issues(config)


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

    issues = route_issues(config)
    if issues:
        print("FAIL default_host_routes")
        for line in issues:
            print(line)
        return 1
    print("PASS default_host_routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
