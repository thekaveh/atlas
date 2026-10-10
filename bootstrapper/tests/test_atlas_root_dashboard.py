from __future__ import annotations

from pathlib import Path


def _config_parser_for_env(tmp_path: Path, body: str):
    from core.config_parser import ConfigParser

    env_path = tmp_path / ".env"
    env_path.write_text(body, encoding="utf-8")
    cp = ConfigParser(str(tmp_path))
    cp.env_file_path = env_path
    return cp


def test_dashboard_model_distinguishes_track_disabled_from_manual_disabled(tmp_path):
    from utils.atlas_dashboard import build_dashboard_model

    cp = _config_parser_for_env(
        tmp_path,
        "\n".join([
            "BASE_PORT=63000",
            "KONG_HTTP_PORT=63000",
            "COMFYUI_SOURCE=disabled",
            "WEAVIATE_SOURCE=disabled",
            "OPEN_WEB_UI_SOURCE=container",
            "BACKEND_SOURCE=container",
        ]),
    )

    model = build_dashboard_model(cp, track_key="gen-ai-rag")
    by_name = {row.name: row for row in model.services}

    assert by_name["ComfyUI"].status == "disabled"
    assert by_name["ComfyUI"].disabled_reason == "disabled-by-track"
    assert by_name["Weaviate"].status == "disabled"
    assert by_name["Weaviate"].disabled_reason == "manually-disabled"


def test_dashboard_html_contains_service_directory_links_and_reachability_probe(tmp_path):
    from utils.atlas_dashboard import build_dashboard_model, render_dashboard_html

    cp = _config_parser_for_env(
        tmp_path,
        "\n".join([
            "BASE_PORT=64000",
            "KONG_HTTP_PORT=64000",
            "OPEN_WEB_UI_SOURCE=container",
            "LITELLM_SOURCE=container",
            "N8N_SOURCE=container",
            "JUPYTERHUB_SOURCE=container",
            "PROMETHEUS_SOURCE=container",
            "GRAFANA_SOURCE=container",
        ]),
    )

    html = render_dashboard_html(
        build_dashboard_model(cp, track_key="all", hosts_configured=False)
    )

    assert "Atlas service directory" in html
    assert "Track: All / Custom" in html
    assert "chat.localhost:64000" in html
    assert "litellm.localhost:64000" in html
    assert "n8n.localhost:64000" in html
    assert "jupyter.localhost:64000" in html
    assert "grafana.localhost:64000" in html
    assert "data-health-url" in html
    assert "fetch(" in html
    assert "hosts entries are not configured" in html


def _sample_html(tmp_path: Path, **env_extra) -> str:
    from utils.atlas_dashboard import build_dashboard_model, render_dashboard_html

    body = "\n".join([
        "BASE_PORT=64000",
        "KONG_HTTP_PORT=64000",
        "OPEN_WEB_UI_SOURCE=container",
        "LITELLM_SOURCE=container",
        "COMFYUI_SOURCE=disabled",
        *[f"{k}={v}" for k, v in env_extra.items()],
    ])
    cp = _config_parser_for_env(tmp_path, body)
    return render_dashboard_html(
        build_dashboard_model(cp, track_key="all", hosts_configured=True)
    )


def test_dashboard_renders_category_grouped_cards_in_canonical_order(tmp_path):
    """#534: the flat table is replaced by category-grouped cards, sections in
    canonical CATEGORY_ORDER with the shared per-category accent colors."""
    from services.topology import CATEGORY_COLORS, CATEGORY_ORDER

    html = _sample_html(tmp_path)
    assert "<table" not in html  # the table is gone
    assert 'class="card' in html

    positions = [html.index(f'id="cat-{key}"') for key in CATEGORY_ORDER]
    assert positions == sorted(positions), "category sections must follow CATEGORY_ORDER"
    for key in CATEGORY_ORDER:
        assert f"--accent:{CATEGORY_COLORS[key]}" in html, f"missing accent for {key}"


def test_dashboard_ships_dark_and_light_themes_with_toggle(tmp_path):
    """#534: two themes, a toggle, prefers-color-scheme default applied before
    first paint, and localStorage persistence — all inline."""
    html = _sample_html(tmp_path)
    assert 'html[data-theme="dark"]' in html
    assert 'html[data-theme="light"]' in html
    assert 'id="theme-toggle"' in html
    assert "prefers-color-scheme" in html
    assert "localStorage" in html and "atlas-theme" in html
    # The pre-paint script lives in <head> so there is no theme flash.
    assert html.index("prefers-color-scheme") < html.index("<body>")
    # Self-contained: no external assets.
    for marker in ("http://cdn", "https://cdn", "@import", "<link rel"):
        assert marker not in html


def test_dashboard_cards_click_through_to_kong_alias(tmp_path):
    """#534: a service with a Kong alias dashboard is a whole-card link to it;
    the reachability probe machinery survives the redesign."""
    html = _sample_html(tmp_path)
    assert '<a class="card" href="http://chat.localhost:64000"' in html
    assert '<a class="card" href="http://litellm.localhost:64000"' in html
    assert "data-health-url" in html
    assert "fetch(" in html


def test_dashboard_internal_and_disabled_services_render_inert_cards(tmp_path):
    """#534: disabled / internal-only services are non-clickable cards with a
    plain-language reason (never a dead link)."""
    html = _sample_html(tmp_path)
    assert 'class="card inert"' in html
    # ComfyUI is disabled in the fixture env → its reason renders on the card.
    assert "manually-disabled" in html or "disabled-by-track" in html
    # Internal-only affordance text exists for portless/aliasless services.
    assert "Internal service" in html or "direct URL only" in html


def test_dashboard_preserves_brand_header_counts_and_warnings(tmp_path):
    """#534: BRAND_* header, Track + Kong port metadata, active/disabled
    counts, and the warnings block survive the redesign."""
    html = _sample_html(tmp_path, BRAND_NAME="Acme", BRAND_TAGLINE="Acme stack")
    assert "Acme service directory" in html
    assert "Acme stack" in html
    assert "Track: All / Custom" in html
    assert "Kong: localhost:64000" in html
    assert "active</span>" in html and "disabled</span>" in html
    assert "<h2>Warnings</h2>" in html


def test_dashboard_service_cards_show_descriptions(tmp_path):
    """#534: cards carry the manifest-declared row description (degrading
    gracefully when a row has none)."""
    from utils.atlas_dashboard import build_dashboard_model

    cp = _config_parser_for_env(
        tmp_path, "BASE_PORT=64000\nKONG_HTTP_PORT=64000\n"
    )
    model = build_dashboard_model(cp, track_key="all")
    descriptions = [s.description for s in model.services if s.description]
    assert descriptions, "at least some services must carry a description"
    html = _sample_html(tmp_path)
    assert 'class="card-desc"' in html


# ── #1189: a card's auth note matches what its Kong route enforces ──────────

_REPO = Path(__file__).resolve().parents[2]


def _kong_services_with_every_gated_service_enabled(tmp_path: Path) -> list[dict]:
    """Generate the real Kong config with each dashboard-gated service on."""
    import yaml

    from core.config_parser import ConfigParser
    from utils.kong_config_generator import KongConfigGenerator

    enabled = {
        **{f"{name}_SOURCE": "container" for name in (
            "LANGFUSE", "MLFLOW", "TRINO", "TRUEFORGE", "LABEL_STUDIO", "VERBA",
            "LLM_GRAPH_BUILDER", "MCP_SERVERS", "REDPANDA", "TIKA", "CRAWL4AI",
            "CELERY", "GRAFANA", "JUPYTERHUB",
        )},
        "RAY_SOURCE": "ray-container-cpu",
        "SUPABASE_ANON_KEY": "test-anon", "SUPABASE_SERVICE_KEY": "test-service",
    }
    env = (_REPO / ".env.example").read_text(encoding="utf-8").splitlines()
    env = [line for line in env if line.split("=", 1)[0] not in enabled]
    env += [f"{key}={value}" for key, value in enabled.items()]
    env_path = tmp_path / ".env"
    env_path.write_text("\n".join(env) + "\n", encoding="utf-8")
    parser = ConfigParser(str(_REPO))
    parser.env_file_path = env_path
    raw = KongConfigGenerator(parser).generate_kong_config()
    return (yaml.safe_load(raw) if isinstance(raw, str) else raw)["services"]


def _dashboard_cards(dashboard_service: dict) -> dict[str, str]:
    """Kong host -> auth note for every linked card in the dashboard HTML."""
    import re

    lua = dashboard_service["routes"][0]["plugins"][0]["config"]["access"][0]
    return {
        m.group(1): m.group(2)
        for m in re.finditer(
            r'<a class="card" href="http://([\w.-]+\.localhost):\d+/?".*?'
            r'<span class="auth">([^<]+)</span>',
            lua, re.S,
        )
    }


def test_card_auth_notes_come_from_the_generated_basic_auth_routes(tmp_path):
    """The dashboard used to hard-code two aliases as "Kong basic-auth"
    (Studio, Ray) and call every other gated route "Service-specific", so
    Langfuse, MLflow, Trino, TrueForge and the rest looked ungated. The Kong
    generator now hands the dashboard the hosts its own routes gate."""
    from utils.kong_config_generator import _basic_auth_hosts

    services = _kong_services_with_every_gated_service_enabled(tmp_path)
    assert services[0]["name"] == "atlas-root-dashboard", "the dashboard stays first"
    gated = _basic_auth_hosts(services[1:])
    assert "grafana.localhost" not in gated  # Grafana's own login, no Kong gate

    cards = _dashboard_cards(services[0])
    assert {"langfuse.localhost", "trino.localhost", "ray.localhost"} <= set(cards)
    for host in gated & set(cards):
        assert cards[host].startswith("Kong basic-auth"), (host, cards[host])
    assert cards.get("grafana.localhost") == "Grafana login"
    assert cards.get("jupyter.localhost") == "Jupyter token"


def _documented_gated_hosts() -> set[str]:
    import re

    page = (_REPO / "docs/operations/access-and-credentials.md").read_text(encoding="utf-8")
    rows = [
        line.split("|")[2] for line in page.splitlines()
        if line.startswith("|") and "Kong dashboard basic-auth" in line
    ]
    return {host for cell in rows for host in re.findall(r"`([\w.-]+\.localhost)`", cell)}


def test_the_access_page_names_exactly_the_hosts_kong_gates(tmp_path):
    """The Access and Credentials page claims the dashboard basic-auth row by
    row (#1189). With every gated service enabled, its list must equal what
    the generated Kong routes enforce -- so a new gated route, or a dropped
    one, fails here until the page says so."""
    from utils.kong_config_generator import _basic_auth_hosts

    gated = _basic_auth_hosts(_kong_services_with_every_gated_service_enabled(tmp_path))
    documented = _documented_gated_hosts()
    assert documented == gated, (sorted(documented - gated), sorted(gated - documented))


def test_auth_note_combines_the_gate_with_the_services_own_credential():
    from utils.atlas_dashboard import _auth_note

    gated = frozenset({"minio.localhost", "trino.localhost"})
    assert _auth_note("Trino", "trino.localhost", gated) == "Kong basic-auth"
    assert _auth_note("MinIO", "minio.localhost", gated) == "Kong basic-auth + MinIO credentials"
    assert _auth_note("MinIO", "minio.localhost") == "MinIO credentials"
    assert _auth_note("Weaviate", "weaviate.localhost", gated) == "Service-specific"
    assert _auth_note("Redis", None) == "Internal"


def test_a_slot_ordering_pin_is_not_reported_as_a_missing_dependency():
    """prometheus/grafana/loki/tempo/langfuse list ray in depends_on.required
    only to pin a port slot; the dashboard warned "disabled required
    dependencies: ray" (2026-10-08 run, cycle 53)."""
    from services.topology import get_topology
    from utils.atlas_dashboard import _dependency_warnings

    sources = {"RAY_SOURCE": "disabled", "PROMETHEUS_SOURCE": "container", "GRAFANA_SOURCE": "container",
               "WEAVIATE_SOURCE": "disabled", "VERBA_SOURCE": "container"}
    warnings = _dependency_warnings(get_topology().rows, sources, {})
    assert not any("ray" in w for w in warnings), warnings
    assert any("verba" in w.lower() and "weaviate" in w for w in warnings), warnings
    # Real hard dependencies still warn (only runtime_deps lost these, cycle 63).
    sources.update(MINIO_SOURCE="disabled", SPARK_SOURCE="container", ASSET_WORKER_SOURCE="container")
    warnings = _dependency_warnings(get_topology().rows, sources, {})
    assert any("spark" in w.lower() and "minio" in w for w in warnings), warnings
    assert any("asset worker" in w.lower() and "minio" in w for w in warnings), warnings
