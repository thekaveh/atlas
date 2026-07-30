import sys, yaml
from pathlib import Path
_R = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_R))

from scripts.docs.build_docs import render_mkdocs_yml  # noqa: E402
from scripts.docs.manifest import load_manifest  # noqa: E402


def _cfg():
    m = load_manifest(_R / "docs/manifest.yaml", _R)
    return yaml.safe_load(render_mkdocs_yml(m))


def test_palette_is_light_first():
    schemes = [p["scheme"] for p in _cfg()["theme"]["palette"]]
    assert schemes[0] == "default" and schemes[1] == "slate"


def test_theme_declares_font_logo_favicon():
    t = _cfg()["theme"]
    assert t["font"]["text"] == "Public Sans"
    assert t["font"]["code"] == "JetBrains Mono"
    assert t["logo"] == "assets/brand/atlas-logo.svg"
    assert t["favicon"] == "assets/brand/favicon.svg"


def test_privacy_plugin_self_hosts_fonts():
    """#841: the privacy plugin must be enabled so fonts self-host (no external
    fonts.googleapis.com/gstatic.com at runtime)."""
    plugins = _cfg().get("plugins", [])
    assert "privacy" in plugins, (
        f"mkdocs.yml plugins must include 'privacy' to self-host fonts (#841); got {plugins}"
    )
    assert "search" in plugins, (
        f"explicit plugins list must retain 'search'; got {plugins}"
    )
