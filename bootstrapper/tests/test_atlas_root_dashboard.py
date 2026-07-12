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
