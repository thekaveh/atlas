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
