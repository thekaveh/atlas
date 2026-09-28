import sys, yaml
from pathlib import Path
_R = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_R))

from scripts.docs.build_docs import render_mkdocs_yml  # noqa: E402
from scripts.docs.manifest import load_manifest  # noqa: E402


def _cfg():
    m = load_manifest(_R / "docs/manifest.yaml", _R)
    return yaml.safe_load(render_mkdocs_yml(m))


def test_palette_is_dark_first():
    schemes = [p["scheme"] for p in _cfg()["theme"]["palette"]]
    assert schemes[0] == "slate" and schemes[1] == "default"


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


# ── #1053: the build-time asset inventory ──────────────────────────────────
# The privacy plugin above downloads its self-hosted copies at build time.
# docs/external-assets.yaml records every one; these keep that record honest
# without network access.

import hashlib  # noqa: E402
import re  # noqa: E402

import pytest  # noqa: E402

from scripts.docs import external_assets as ea  # noqa: E402


def _inventory():
    return ea.load_inventory(_R / "docs/external-assets.yaml")


def test_inventory_pins_every_versioned_font():
    inventory = _inventory()
    assert inventory.cache_root == ".cache/plugin/privacy"
    fonts = [a for a in inventory.assets if a.kind == "font"]
    assert fonts, "the theme fonts must be inventoried"
    for font in fonts:
        assert re.search(r"/v\d+/", font.url), font.url
        assert font.sha256, f"{font.name} is versioned in its URL, so its content is pinnable"
        assert font.path == "assets/external/" + font.url.split("://", 1)[1]


def test_inventory_stylesheet_is_the_one_the_theme_fonts_request():
    """Changing the theme fonts changes this URL; the inventory must follow."""
    font = _cfg()["theme"]["font"]
    [sheet] = [a for a in _inventory().assets if a.kind == "stylesheet"]
    assert sheet.url.startswith("https://fonts.googleapis.com/css?family=")
    for family in (font["text"], font["code"]):
        assert family.replace(" ", "+") in sheet.url


def test_inventory_mermaid_url_is_the_one_material_bundles():
    """mkdocs-material's own JavaScript bundle names the Mermaid URL the
    privacy plugin fetches, so a theme upgrade that moves it must move the
    inventory too."""
    import material

    bundles = list(
        (Path(material.__file__).parent / "templates/assets/javascripts").glob("bundle.*.min.js")
    )
    assert bundles
    referenced = set(re.findall(r"https://unpkg\.com/mermaid@[^\"'`\s]+", bundles[0].read_text()))
    scripts = {a.url for a in _inventory().assets if a.kind == "script"}
    assert referenced == scripts


def _synthetic(tmp_path, *, pinned: bool = True):
    body = b"font bytes"
    digest = hashlib.sha256(body).hexdigest() if pinned else "null"
    text = (
        "cache_root: cache\nassets:\n"
        "- name: Example Sans v1, latin\n  kind: font\n  version: Example Sans v1\n"
        "  url: https://fonts.example.test/s/example/v1/a.woff2\n"
        "  path: assets/external/fonts.example.test/s/example/v1/a.woff2\n"
        f"  sha256: {digest}\n"
    )
    cache = tmp_path / "cache"
    return ea.parse_inventory(text), cache, body


def _no_network(url):
    raise AssertionError(f"a warm cache must not touch the network: {url}")


def test_preflight_needs_no_network_on_a_warm_cache(tmp_path):
    inventory, cache, body = _synthetic(tmp_path)
    target = cache / inventory.assets[0].path
    target.parent.mkdir(parents=True)
    target.write_bytes(body)
    assert ea.preflight(inventory, cache, fetch=_no_network) == []


def test_preflight_names_the_asset_and_url_it_cannot_fetch(tmp_path):
    """AC4: a blocked host fails before MkDocs, naming what and where."""
    inventory, cache, _body = _synthetic(tmp_path)

    def blocked(url):
        raise ConnectionError("host unreachable")

    [problem] = ea.preflight(inventory, cache, fetch=blocked)
    assert "Example Sans v1, latin" in problem
    assert "https://fonts.example.test/s/example/v1/a.woff2" in problem
    assert "host unreachable" in problem


def test_preflight_accepts_a_missing_asset_it_can_fetch(tmp_path):
    inventory, cache, _body = _synthetic(tmp_path)
    fetched = []
    assert ea.preflight(inventory, cache, fetch=fetched.append) == []
    assert fetched == ["https://fonts.example.test/s/example/v1/a.woff2"]


def test_preflight_rejects_a_corrupt_pinned_copy(tmp_path):
    inventory, cache, _body = _synthetic(tmp_path)
    target = cache / inventory.assets[0].path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"truncated")
    [problem] = ea.preflight(inventory, cache, fetch=_no_network)
    assert "does not match the recorded sha256" in problem


def test_verify_cache_reports_missing_unexpected_and_changed(tmp_path):
    """AC1's diff: a clean-cache build must produce exactly the inventory."""
    inventory, cache, body = _synthetic(tmp_path)
    assert ea.verify_cache(inventory, cache) == [
        "missing: assets/external/fonts.example.test/s/example/v1/a.woff2 "
        "(https://fonts.example.test/s/example/v1/a.woff2)"
    ]
    target = cache / inventory.assets[0].path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"other")
    (cache / "assets/external/stray.js").write_bytes(b"x")
    # A symlink is the plugin's own alias for an extensionless URL, not an asset.
    (cache / "assets/external/alias").symlink_to("stray.js")
    assert ea.verify_cache(inventory, cache) == [
        "unexpected: assets/external/stray.js",
        "changed: assets/external/fonts.example.test/s/example/v1/a.woff2 "
        "no longer matches its recorded sha256",
    ]
    target.write_bytes(body)
    (cache / "assets/external/stray.js").unlink()
    assert ea.verify_cache(inventory, cache) == []


@pytest.mark.parametrize("field,value,message", [
    ("kind", "binary", "kind must be one of"),
    ("sha256", "ABC", "sha256 must be"),
    ("url", "http://fonts.example.test/a.woff2", "url must be https"),
    ("path", "fonts/a.woff2", "must live under assets/external/"),
])
def test_malformed_inventory_entries_are_rejected(field, value, message):
    entry = {
        "name": "n", "kind": "font", "version": "v1",
        "url": "https://fonts.example.test/a.woff2",
        "path": "assets/external/fonts.example.test/a.woff2", "sha256": None,
    }
    entry[field] = value
    with pytest.raises(ea.InventoryError, match=message):
        ea.parse_inventory(yaml.safe_dump({"assets": [entry]}))


def test_every_make_target_that_runs_mkdocs_preflights_first():
    """AC4 is only true if nothing reaches MkDocs without the preflight."""
    makefile = (_R / "Makefile").read_text(encoding="utf-8")
    targets = re.findall(r"^([\w-]+):\n((?:\t.*\n?)+)", makefile, re.M)
    runs_mkdocs = {name: body for name, body in targets if "$(MKDOCS)" in body}
    assert set(runs_mkdocs) == {"docs-build", "docs-check", "docs-serve"}
    for name, body in runs_mkdocs.items():
        preflight = body.index("scripts.docs.external_assets --preflight")
        assert preflight < body.index("$(MKDOCS)"), name


def test_print_inventory_rebuilds_font_entries_from_the_cached_stylesheet(tmp_path):
    css = (
        "/* latin */\n@font-face {\n  font-family: 'Example Sans';\n  font-style: normal;\n"
        "  font-weight: 400;\n  src: url(https://fonts.example.test/s/example/v2/b.woff2) "
        "format('woff2');\n}\n"
    )
    text = (
        "cache_root: cache\nassets:\n"
        "- name: sheet\n  kind: stylesheet\n  version: unversioned\n"
        "  url: https://fonts.example.test/css?family=Example+Sans\n"
        "  path: assets/external/fonts.example.test/css.1.css\n  sha256: null\n"
    )
    inventory = ea.parse_inventory(text)
    cache = tmp_path / "cache"
    (cache / "assets/external/fonts.example.test/s/example/v2").mkdir(parents=True)
    (cache / "assets/external/fonts.example.test/css.1.css").write_text(css)
    (cache / "assets/external/fonts.example.test/s/example/v2/b.woff2").write_bytes(b"b")
    printed = yaml.safe_load(ea.print_inventory(inventory, cache))["assets"]
    assert [a["kind"] for a in printed] == ["stylesheet", "font"]
    assert printed[1]["name"] == "Example Sans normal 400, latin"
    assert printed[1]["version"] == "Example Sans v2"
    assert printed[1]["sha256"] == hashlib.sha256(b"b").hexdigest()
