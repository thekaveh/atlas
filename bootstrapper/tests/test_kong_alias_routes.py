"""Regression: Kong must have explicit host-routed services for every
alias declared by manifests. Otherwise the dashboard catch-all swallows
``graph.localhost``, ``weaviate.localhost``, etc. → Supabase Studio.
"""

from __future__ import annotations

from pathlib import Path


def _generate(env_body: str) -> dict:
    """Render Kong config against a synthetic env override.

    The generator's ``generate_kong_config`` calls
    ``load_environment_variables`` first, which would otherwise reset
    ``env_vars`` from disk. We stub that loader so it starts from the
    real .env (unrelated vars resolve normally) and applies the test
    overrides on top.
    """
    from core.config_parser import ConfigParser
    from utils.kong_config_generator import KongConfigGenerator

    repo_root = Path(__file__).resolve().parent.parent.parent
    cp = ConfigParser(str(repo_root))
    g = KongConfigGenerator(cp)
    overrides: dict[str, str] = {}
    for line in env_body.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            overrides[k.strip()] = v.strip()

    def _stub_load():
        g.env_vars = cp.parse_env_file()
        g.env_vars.update(overrides)
    g.load_environment_variables = _stub_load  # type: ignore[method-assign]
    return g.generate_kong_config()


def _hosts_to_service(config: dict) -> dict[str, str]:
    """Build a {hostname: kong_service_name} index from the config."""
    index: dict[str, str] = {}
    for svc in config["services"]:
        for route in svc.get("routes", []):
            for host in route.get("hosts") or []:
                index[host] = svc["name"]
    return index


def test_supabase_studio_route_is_restricted_to_studio_alias():
    """Supabase Studio must not be a global or bare-root catch-all.

    Regression: it used to match ``paths: ['/']`` with no host filter,
    so every unaliased *.localhost request fell through to Studio.
    Atlas now owns the bare ``localhost`` root dashboard, while Studio
    remains available at its explicit alias.
    """
    config = _generate("")
    dashboard = next(
        svc for svc in config["services"] if svc["name"] == "dashboard"
    )
    dashboard_routes = dashboard["routes"]
    assert len(dashboard_routes) == 1
    hosts = set(dashboard_routes[0].get("hosts") or [])
    assert hosts == {"supabase-studio.localhost"}, (
        f"dashboard route should be locked to supabase-studio.localhost, got {hosts}"
    )


def test_atlas_root_dashboard_owns_bare_localhost_route():
    """The gateway root should be an Atlas entrypoint, not Studio."""
    config = _generate("")
    by_host = _hosts_to_service(config)
    assert by_host["localhost"] == "atlas-root-dashboard"
    assert by_host["supabase-studio.localhost"] == "dashboard"
    assert "studio.localhost" not in by_host

    svc = next(s for s in config["services"] if s["name"] == "atlas-root-dashboard")
    route = svc["routes"][0]
    assert route["hosts"] == ["localhost"]
    assert route["paths"] == ["/"]
    route_plugins = route.get("plugins", [])
    assert any(p["name"] == "pre-function" for p in route_plugins)
    access_code = "\n".join(
        line
        for plugin in route_plugins
        if plugin["name"] == "pre-function"
        for line in plugin["config"]["access"]
    )
    assert "Atlas" in access_code
    assert "service directory" in access_code
    assert "kong.response.exit(200, [=[" in access_code
    assert "\\u003c" not in access_code


def test_alias_only_services_route_to_expected_containers():
    """Container-mode sources produce a Kong route per alias, pointing
    at the right internal container URL."""
    config = _generate(
        "NEO4J_GRAPH_DB_SOURCE=container\n"
        "WEAVIATE_SOURCE=container\n"
        "LLM_PROVIDER_SOURCE=ollama-container-cpu\n"
        "DOC_PROCESSOR_SOURCE=docling-container-gpu\n"
        "LOCAL_DEEP_RESEARCHER_SOURCE=container\n"
        "JENKINS_SOURCE=container\n"
        "MCP_SERVERS_SOURCE=container\n"
        "STT_PROVIDER_SOURCE=speaches-container-cpu\n"
        "TTS_PROVIDER_SOURCE=speaches-container-cpu\n"
    )
    by_host = _hosts_to_service(config)
    for alias in [
        "graph.localhost", "weaviate.localhost", "ollama.localhost",
        "docling.localhost", "research.localhost",
        "jenkins.localhost", "mcp.localhost", "stt.localhost", "tts.localhost",
    ]:
        assert alias in by_host, f"missing Kong route for {alias}: {sorted(by_host)}"

    assert by_host["jenkins.localhost"] == "jenkins"
    jenkins = next(svc for svc in config["services"] if svc["name"] == "jenkins")
    assert jenkins["url"] == "http://jenkins:8080/"
    assert by_host["mcp.localhost"] == "mcp-servers"
    mcp = next(svc for svc in config["services"] if svc["name"] == "mcp-servers")
    assert mcp["url"] == "http://mcp-servers:8000/"
    assert {plugin["name"] for plugin in mcp["plugins"]} >= {
        "basic-auth",
        "acl",
        "cors",
    }


def test_localhost_source_routes_via_host_docker_internal():
    """Host-install ('localhost') sources still get a Kong alias — the
    route proxies through ``host.docker.internal`` to the user's host
    port. Mirrors the existing ComfyUI/OpenClaw/Hermes pattern so a
    single entry point (``ollama.localhost``, ``graph.localhost``, etc.)
    works in both container and host-install modes.

    Test exercises the *manifest default* URLs (no ``<SVC>_LOCALHOST_PORT``
    override). The dev's real .env may already define those overrides
    against a non-default BASE_PORT, so we explicitly pin them to the
    manifest defaults.
    """
    config = _generate(
        "NEO4J_GRAPH_DB_SOURCE=localhost\n"
        "WEAVIATE_SOURCE=localhost\n"
        "LLM_PROVIDER_SOURCE=ollama-localhost\n"
        "DOC_PROCESSOR_SOURCE=docling-localhost\n"
        "DOCLING_LOCALHOST_PORT=63040\n"
        "STT_PROVIDER_SOURCE=parakeet-localhost\n"
        "PARAKEET_LOCALHOST_PORT=63042\n"
        "TTS_PROVIDER_SOURCE=chatterbox-localhost\n"
        "CHATTERBOX_LOCALHOST_PORT=63044\n"
    )
    by_host = _hosts_to_service(config)
    # Every alias still resolves.
    expected_urls = {
        "graph.localhost":    "http://host.docker.internal:7474/",
        "weaviate.localhost": "http://host.docker.internal:8080/",
        "ollama.localhost":   "http://host.docker.internal:11434/",
        # docling-localhost default port from manifest fallback
        "docling.localhost":  "http://host.docker.internal:63040/",
        "stt.localhost":      "http://host.docker.internal:63042/",
        "tts.localhost":      "http://host.docker.internal:63044/",
    }
    by_host_with_url = {
        host: svc
        for svc in config["services"]
        for route in svc.get("routes", [])
        for host in route.get("hosts") or []
    }
    for alias, expected in expected_urls.items():
        assert alias in by_host, (
            f"localhost-mode alias {alias} should still be Kong-routed: "
            f"{sorted(by_host)}"
        )
        assert by_host_with_url[alias]["url"] == expected, (
            f"localhost-mode alias {alias} should target {expected}, "
            f"got {by_host_with_url[alias]['url']}"
        )


def test_localhost_port_env_override_is_honored():
    """Users can override the localhost target via ``<SVC>_LOCALHOST_PORT``
    env vars. Kong's route URL is built by the ``_localhost_url`` helper,
    which reads the same PORT var compose's runtime_sc consumes — so
    Kong and the in-container clients always agree.
    """
    config = _generate(
        "STT_PROVIDER_SOURCE=whisper-cpp-localhost\n"
        "WHISPER_CPP_LOCALHOST_PORT=7777\n"
        "DOC_PROCESSOR_SOURCE=docling-localhost\n"
        "DOCLING_LOCALHOST_PORT=8888\n"
        "TTS_PROVIDER_SOURCE=chatterbox-localhost\n"
        "CHATTERBOX_LOCALHOST_PORT=6666\n"
    )
    by_host_with_url = {
        host: svc["url"]
        for svc in config["services"]
        for route in svc.get("routes", [])
        for host in route.get("hosts") or []
    }
    assert by_host_with_url["stt.localhost"] == "http://host.docker.internal:7777/"
    assert by_host_with_url["docling.localhost"] == "http://host.docker.internal:8888/"
    assert by_host_with_url["tts.localhost"] == "http://host.docker.internal:6666/"


def test_disabled_source_skips_kong_route():
    """Disabled service: no Kong route."""
    config = _generate(
        "WEAVIATE_SOURCE=disabled\nDOC_PROCESSOR_SOURCE=disabled\nMCP_SERVERS_SOURCE=disabled\n"
    )
    by_host = _hosts_to_service(config)
    assert "weaviate.localhost" not in by_host
    assert "docling.localhost" not in by_host
    assert "mcp.localhost" not in by_host


def test_prometheus_kong_route_only_when_container():
    """`prometheus.localhost` exists when PROMETHEUS_SOURCE=container and
    not when disabled (no prometheus container would be available to
    route to)."""
    on = _hosts_to_service(_generate("PROMETHEUS_SOURCE=container\n"))
    off = _hosts_to_service(_generate("PROMETHEUS_SOURCE=disabled\n"))
    assert on.get("prometheus.localhost") == "prometheus"
    assert "prometheus.localhost" not in off


def test_prometheus_kong_route_targets_internal_port_9090():
    """The Prometheus Kong route must point at the in-network upstream
    (http://prometheus:9090/), not the host-mapped PROMETHEUS_PORT
    (which is the topology-allocated public port). Tests the URL set
    in generate_prometheus_service()."""
    config = _generate("PROMETHEUS_SOURCE=container\n")
    prom_svc = next(
        (svc for svc in config["services"] if svc["name"] == "prometheus"),
        None,
    )
    assert prom_svc is not None
    assert prom_svc["url"] == "http://prometheus:9090/"


def test_grafana_kong_route_only_when_container():
    """`grafana.localhost` exists when GRAFANA_SOURCE=container and
    not when disabled."""
    on = _hosts_to_service(_generate("GRAFANA_SOURCE=container\n"))
    off = _hosts_to_service(_generate("GRAFANA_SOURCE=disabled\n"))
    assert on.get("grafana.localhost") == "grafana"
    assert "grafana.localhost" not in off


def test_grafana_kong_route_preserves_host():
    """Grafana is an SPA — its redirects build URLs from the Host header.
    Without preserve_host=True, Kong rewrites Host to grafana:3000 and
    the browser can't resolve it. Regression guard against the route
    flipping back to a no-preserve_host shape."""
    config = _generate("GRAFANA_SOURCE=container\n")
    grafana_svc = next(
        (svc for svc in config["services"] if svc["name"] == "grafana"),
        None,
    )
    assert grafana_svc is not None
    route = grafana_svc["routes"][0]
    assert route.get("preserve_host") is True, (
        "preserve_host: True is mandatory for SPAs that build redirects "
        "from the Host header — same lesson as n8n / MinIO / Ray."
    )


def test_tts_route_picks_correct_container_for_chatterbox():
    """TTS engine container varies by source-id prefix."""
    config = _generate("TTS_PROVIDER_SOURCE=chatterbox-container-gpu\n")
    tts_svc = next(
        (svc for svc in config["services"] if svc["name"] == "tts-api"),
        None,
    )
    assert tts_svc is not None
    assert tts_svc["url"] == "http://chatterbox:4123/"


def test_stt_and_tts_container_urls_match_compose_listen_ports():
    """STT/TTS engine containers expose specific listen ports — the
    Kong upstream URL must point at the right one (regression: an
    earlier version mistakenly routed chatterbox-container to :8000
    when the container actually listens on :4123).
    """
    cases = [
        ("STT_PROVIDER_SOURCE=parakeet-container-gpu\n", "stt-api",
         "http://parakeet-gpu:8000/"),
        ("STT_PROVIDER_SOURCE=speaches-container-cpu\n", "stt-api",
         "http://speaches:8000/"),
        ("STT_PROVIDER_SOURCE=speaches-container-gpu\n", "stt-api",
         "http://speaches:8000/"),
        ("TTS_PROVIDER_SOURCE=speaches-container-cpu\n", "tts-api",
         "http://speaches:8000/"),
    ]
    for env, name, expected in cases:
        config = _generate(env)
        svc = next((s for s in config["services"] if s["name"] == name), None)
        assert svc is not None, f"{name} missing for env={env!r}"
        assert svc["url"] == expected, (
            f"{name} should route to {expected} for env={env!r}, got {svc['url']}"
        )


def test_tei_reranker_route_generated_when_enabled():
    """rerank.localhost route is emitted when TEI_RERANKER_SOURCE is a
    container variant, targeting the in-network tei-reranker container."""
    config = _generate("TEI_RERANKER_SOURCE=container-cpu\n")
    by_host_with_svc = {
        host: svc
        for svc in config["services"]
        for route in svc.get("routes", [])
        for host in route.get("hosts") or []
    }
    assert "rerank.localhost" in by_host_with_svc, (
        f"Expected rerank.localhost route, got: {sorted(by_host_with_svc)}"
    )
    svc = by_host_with_svc["rerank.localhost"]
    assert svc["url"] == "http://tei-reranker:80/", (
        f"rerank.localhost should target http://tei-reranker:80/, got {svc['url']}"
    )
    # Pure REST inference endpoint — no SPA, so preserve_host must not be True
    route = next(
        r for r in svc["routes"]
        if "rerank.localhost" in (r.get("hosts") or [])
    )
    assert route.get("preserve_host") is not True, (
        "TEI Reranker is a pure REST API — preserve_host must not be True"
    )


def test_tei_reranker_route_omitted_when_disabled():
    """rerank.localhost route must be absent when TEI_RERANKER_SOURCE=disabled."""
    config = _generate("TEI_RERANKER_SOURCE=disabled\n")
    by_host = _hosts_to_service(config)
    assert "rerank.localhost" not in by_host, (
        "rerank.localhost should not appear when TEI_RERANKER_SOURCE=disabled"
    )


import pytest


def test_spark_master_route_exists_with_preserve_host():
    """Spark Master Web UI is an SPA — its redirects build URLs from the
    Host header, same lesson as Grafana / n8n / MinIO / Ray. Without
    preserve_host=True, Kong rewrites Host to ``spark-master:8080`` and
    the browser can't resolve it. Regression guard.
    """
    config = _generate("SPARK_SOURCE=container\n")
    svc = next(
        (s for s in config["services"] if s["name"] == "spark-master-ui"),
        None,
    )
    assert svc is not None, "spark-master-ui service missing"
    route = svc["routes"][0]
    assert "spark.localhost" in route["hosts"]
    assert route.get("preserve_host") is True, (
        "preserve_host must be True for SPA Web UI"
    )


def test_spark_history_route_exists_with_preserve_host():
    """Spark History Server is also an SPA with the same preserve_host
    requirement as the master UI."""
    config = _generate("SPARK_SOURCE=container\n")
    svc = next(
        (s for s in config["services"] if s["name"] == "spark-history-ui"),
        None,
    )
    assert svc is not None
    assert "spark-history.localhost" in svc["routes"][0]["hosts"]
    assert svc["routes"][0].get("preserve_host") is True


def test_zeppelin_route_exists_with_preserve_host():
    """Zeppelin notebook UI is an SPA — preserve_host=True keeps it routable
    via the alias. Gated on ZEPPELIN_SOURCE=container."""
    config = _generate("ZEPPELIN_SOURCE=container\nSPARK_SOURCE=container\n")
    svc = next(
        (s for s in config["services"] if s["name"] == "zeppelin"),
        None,
    )
    assert svc is not None, "zeppelin service missing from Kong config"
    assert "zeppelin.localhost" in svc["routes"][0]["hosts"]
    assert svc["routes"][0].get("preserve_host") is True


def test_airflow_route_exists_with_preserve_host():
    """Airflow Web UI is an SPA AND the same alias serves the REST API at
    /api/v2/. preserve_host=True is required for the SPA's asset URLs +
    redirects to bake the right host. Gated on AIRFLOW_SOURCE=container."""
    config = _generate("AIRFLOW_SOURCE=container\n")
    svc = next(
        (s for s in config["services"] if s["name"] == "airflow"),
        None,
    )
    assert svc is not None, "airflow service missing from Kong config"
    assert "airflow.localhost" in svc["routes"][0]["hosts"]
    assert svc["routes"][0].get("preserve_host") is True


def test_lightrag_route_generated_with_preserve_host():
    """lightrag.localhost route is emitted when LIGHTRAG_SOURCE=container,
    targeting the in-network lightrag container. SPA at /webui requires
    preserve_host=True."""
    config = _generate("LIGHTRAG_SOURCE=container\nLIGHTRAG_LOCALHOST_PORT=63068\n")
    by_host_with_svc = {
        host: svc
        for svc in config["services"]
        for route in svc.get("routes", [])
        for host in route.get("hosts") or []
    }
    assert "lightrag.localhost" in by_host_with_svc, (
        f"Expected lightrag.localhost route, got: {sorted(by_host_with_svc)}"
    )
    svc = by_host_with_svc["lightrag.localhost"]
    assert svc["url"] == "http://lightrag:9621/", (
        f"lightrag.localhost should target http://lightrag:9621/, got {svc['url']}"
    )
    # SPA at /webui — preserve_host required
    route = next(
        r for r in svc["routes"]
        if "lightrag.localhost" in (r.get("hosts") or [])
    )
    assert route.get("preserve_host") is True, (
        "LightRAG ships a WebUI SPA at /webui — preserve_host must be True"
    )


def test_lightrag_route_omitted_when_disabled():
    """lightrag.localhost route must be absent when LIGHTRAG_SOURCE=disabled."""
    config = _generate("LIGHTRAG_SOURCE=disabled\n")
    by_host = _hosts_to_service(config)
    assert "lightrag.localhost" not in by_host, (
        "lightrag.localhost should not appear when LIGHTRAG_SOURCE=disabled"
    )


@pytest.mark.parametrize("env_var,svc_source_var,svc_source_value,expected_port", [
    ("COMFYUI_LOCALHOST_PORT",      "COMFYUI_SOURCE",            "localhost",              "9999"),
    ("DOCLING_LOCALHOST_PORT",      "DOC_PROCESSOR_SOURCE",      "docling-localhost",      "9999"),
    # Hermes Kong route fronts the DASHBOARD (browser UI), not the API.
    # HERMES_LOCALHOST_PORT drives the API (consumed by runtime_sc's
    # HERMES_ENDPOINT); HERMES_LOCALHOST_DASHBOARD_PORT is the separate
    # host port the Kong route targets — same split as Neo4j HTTP/Bolt.
    ("HERMES_LOCALHOST_DASHBOARD_PORT", "HERMES_SOURCE",          "localhost",              "9999"),
    ("OPENCLAW_LOCALHOST_PORT",     "OPENCLAW_SOURCE",           "localhost",              "9999"),
    ("PARAKEET_LOCALHOST_PORT",     "STT_PROVIDER_SOURCE",       "parakeet-localhost",     "9999"),
    ("WHISPER_CPP_LOCALHOST_PORT",  "STT_PROVIDER_SOURCE",       "whisper-cpp-localhost",  "9999"),
    ("CHATTERBOX_LOCALHOST_PORT",   "TTS_PROVIDER_SOURCE",       "chatterbox-localhost",   "9999"),
    ("OLLAMA_LOCALHOST_PORT",       "LLM_PROVIDER_SOURCE",       "ollama-localhost",       "9999"),
    ("NEO4J_LOCALHOST_HTTP_PORT",   "NEO4J_GRAPH_DB_SOURCE",     "localhost",              "9999"),
    ("WEAVIATE_LOCALHOST_PORT",     "WEAVIATE_SOURCE",           "localhost",              "9999"),
])
def test_kong_localhost_route_reads_port_var(
    env_var, svc_source_var, svc_source_value, expected_port, tmp_path
):
    """Each localhost-mode route's `url` is derived from the matching
    LOCALHOST_PORT env var. Sets a non-default port and asserts the
    generated route URL reflects it."""
    from utils.kong_config_generator import KongConfigGenerator
    from core.config_parser import ConfigParser

    env_path = tmp_path / ".env"
    env_path.write_text(
        f"{svc_source_var}={svc_source_value}\n"
        f"{env_var}={expected_port}\n"
        "DASHBOARD_USERNAME=u\nDASHBOARD_PASSWORD=p\n",
        encoding="utf-8",
    )
    cp = ConfigParser(str(tmp_path))
    cp.env_file_path = env_path
    cp.parse_env_file()
    gen = KongConfigGenerator(cp)
    gen.load_environment_variables()
    cfg = gen.generate_kong_config()
    found = []
    for svc in cfg["services"]:
        url = svc.get("url", "") or ""
        if f":{expected_port}" in url and "host.docker.internal" in url:
            found.append((svc["name"], url))
    assert found, (
        f"Expected at least one Kong service route to target "
        f"host.docker.internal:{expected_port} (matching {env_var}). "
        f"Got services: {[(s['name'], s.get('url')) for s in cfg['services']]}"
    )


def test_n8n_forwarded_host_header_has_resolved_port():
    """Kong DB-less config performs no env interpolation: the n8n
    X-Forwarded-Host header must carry the literal resolved port, never
    an unexpanded ``${KONG_HTTP_PORT}`` token (n8n bakes this header into
    webhook/editor URLs)."""
    config = _generate("N8N_SOURCE=container\nKONG_HTTP_PORT=64000\n")
    n8n_services = [s for s in config["services"] if s["name"] == "n8n-api"]
    assert n8n_services, "n8n-api service missing from Kong config"
    headers = []
    for plugin in n8n_services[0].get("plugins", []):
        if plugin.get("name") == "request-transformer":
            headers = plugin["config"]["add"]["headers"]
    assert headers == ["X-Forwarded-Host: n8n.localhost:64000"], headers
    assert not any("${" in h for h in headers)


def test_dashboard_route_is_basic_auth_gated():
    """Supabase Studio has no auth of its own; the Kong dashboard
    service must carry basic-auth + the dashboard_user ACL (the
    documented DASHBOARD_USERNAME/PASSWORD gate). Regression: it shipped
    with only CORS, leaving the SQL editor open on the gateway root."""
    config = _generate("")
    dash = [s for s in config["services"] if s["name"] == "dashboard"]
    assert dash, "dashboard service missing"
    plugin_names = {p["name"] for p in dash[0].get("plugins", [])}
    assert "basic-auth" in plugin_names
    acl = [p for p in dash[0]["plugins"] if p["name"] == "acl"]
    assert acl and acl[0]["config"]["allow"] == ["dashboard_user"]
