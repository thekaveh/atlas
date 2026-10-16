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


def test_inventory_checksums_exactly_the_urls_that_name_a_version():
    """#1282: a sha256 belongs on a URL that names an exact version, and on no
    other. The font URLs carry `/v24/`-style versions. unpkg resolves
    `mermaid@11` to the newest 11.x at fetch time, so a checksum there would
    fail the first cold build after the next Mermaid 11 release. If
    mkdocs-material names an exact Mermaid version, the test above moves the
    inventory to it and this one then requires its checksum."""
    exact = re.compile(r"/v\d+/|@\d+\.\d+\.\d+(?:-[\w.]+)?/")
    for asset in _inventory().assets:
        assert bool(asset.sha256) == bool(exact.search(asset.url)), (asset.name, asset.url)


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


def test_the_docs_check_treats_a_missing_mkdocs_yml_as_not_stale(tmp_path, monkeypatch):
    """mkdocs.yml is gitignored, so a clean checkout failed the check with
    "configuration is stale" (2026-10-08 run, cycle 66). A differing file
    still fails."""
    import pytest

    from scripts.docs import build_docs

    for name in ("_render_tree", "_render_tree_in_subprocess", "_assert_dirs_equal"):
        monkeypatch.setattr(build_docs, name, lambda *a, **k: None)
    monkeypatch.setattr(build_docs, "load_manifest", lambda *a, **k: object())
    monkeypatch.setattr(build_docs, "render_mkdocs_yml", lambda _m: "site_name: x\n")
    build_docs.build(tmp_path / "m.yaml", tmp_path, site=True, wiki=False, check=True)
    (tmp_path / "mkdocs.yml").write_text("site_name: old\n")
    with pytest.raises(RuntimeError, match="stale"):
        build_docs.build(tmp_path / "m.yaml", tmp_path, site=True, wiki=False, check=True)


def _legacy_alias_offset_rules():
    import tinycss2

    css = (_R / "docs/assets/stylesheets/atlas.css").read_text(encoding="utf-8")
    for rule in tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True):
        if rule.type != "qualified-rule":
            continue
        declarations = tinycss2.parse_declaration_list(
            rule.content, skip_comments=True, skip_whitespace=True,
        )
        offsets = [declaration for declaration in declarations
                   if declaration.type == "declaration" and declaration.name == "--md-scroll-offset"]
        if offsets:
            assert len(declarations) == len(offsets) == 1, declarations
            yield rule.prelude, offsets[0]


def _legacy_alias_structural_selectors(prelude):
    import cssselect2
    import tinycss2

    # cssselect2 cannot model dynamic :target; require it on the selected element.
    tokens = list(prelude)
    while tokens and tokens[-1].type == "whitespace":
        tokens.pop()
    assert [tinycss2.serialize([token]) for token in tokens[-2:]] == [":", "target"]
    # Remove only that pseudo, preserving all structural selector tokens and spaces.
    return cssselect2.compile_selector_list(tinycss2.serialize(tokens[:-2]))


def _assert_legacy_alias_relative_offset(declaration):
    import math

    values = [token for token in declaration.value if token.type != "whitespace"]
    assert len(values) == 1 and values[0].type == "dimension", values
    offset = values[0]
    assert math.isfinite(offset.value) and offset.value < 0, offset
    assert offset.lower_unit in {"em", "rem", "ex", "ch", "lh", "rlh"}, offset


@pytest.mark.parametrize("element_id,is_target,receives_offset", [
    ("h2-alias", True, True),
    ("h3-alias", True, True),
    ("h2-first", True, True),
    ("h2-second", True, True),
    ("h3-first", True, True),
    ("h3-second", True, True),
    ("h3-first", False, False),
    ("current-h2", True, False),
    ("current-h3", True, False),
    ("permalink", True, False),
    ("href-link", True, False),
    ("nonempty-alias", True, False),
    ("child-alias", True, False),
    ("later-alias", True, False),
    ("h1-alias", True, False),
    ("outside-alias", True, False),
    ("footnote-alias", True, False),
])
def test_legacy_alias_scroll_offset_targets_only_heading_adjacent_empty_anchors(
    element_id: str, is_target: bool, receives_offset: bool,
) -> None:
    import xml.etree.ElementTree as ET

    import cssselect2

    document = ET.fromstring("""
        <html><body><article class="md-typeset">
          <h2 id="current-h2">Topic<a id="permalink" href="#current-h2">¶</a></h2>
          <p><a id="h2-alias"></a></p>
          <h3 id="current-h3">Subtopic</h3><p><a id="h3-alias"></a></p>
          <h2>Topic</h2><p><a id="h2-first"></a><a id="h2-second"></a></p>
          <h3>Subtopic</h3><p><a id="h3-first"></a><a id="h3-second"></a></p>
          <h2>Linked topic</h2><p><a id="href-link" href="#current-h2"></a></p>
          <h3>Text topic</h3><p><a id="nonempty-alias">Content</a></p>
          <h2>Nested topic</h2><p><a id="child-alias"><span></span></a></p>
          <h2>Later topic</h2><p>Introduction</p><p><a id="later-alias"></a></p>
          <h1>Document title</h1><p><a id="h1-alias"></a></p>
          <h2>Notes</h2><div class="footnote"><ol><li>
            <p><a id="footnote-alias"></a></p>
          </li></ol></div>
        </article><aside><h2>Elsewhere</h2><p><a id="outside-alias"></a></p></aside>
        </body></html>
    """)
    element = next(
        node for node in cssselect2.ElementWrapper.from_html_root(document).iter_subtree()
        if node.id == element_id
    )
    matching_offsets = [
        declaration for prelude, declaration in _legacy_alias_offset_rules()
        if is_target and any(selector.test(element)
                             for selector in _legacy_alias_structural_selectors(prelude))
    ]
    assert bool(matching_offsets) == receives_offset, (element_id, is_target, matching_offsets)
    if receives_offset:
        assert len(matching_offsets) == 1
        _assert_legacy_alias_relative_offset(matching_offsets[0])


def test_canonical_stylesheet_propagates_exact_bytes_and_loaded_site_wiring(tmp_path):
    from mkdocs.config import load_config
    from scripts.docs.build_docs import build

    docs = tmp_path / "docs"
    canonical = docs / "assets" / "stylesheets" / "atlas.css"
    canonical.parent.mkdir(parents=True)
    stylesheet = b"/* Synthetic canonical CSS. */\n:root { --atlas-probe: 3em; }\n"
    canonical.write_bytes(stylesheet)
    (docs / "index.md").write_text("# 1. Overview\n\nSynthetic canonical page.\n", encoding="utf-8")
    manifest = docs / "manifest.yaml"
    manifest.write_text(
        "surfaces: [repo, site, wiki]\nnumbering: baked\nindex: overview\nsections:\n"
        '  - {id: overview, number: "1", title: Overview, source: docs/index.md}\n'
        "diagrams: []\n", encoding="utf-8",
    )

    for check in (False, True):
        build(manifest, tmp_path, site=True, wiki=True, check=check)
        for path in (
            "generated/site/stylesheets/atlas.css",
            "generated/site/assets/stylesheets/atlas.css",
            "generated/wiki/assets/stylesheets/atlas.css",
        ):
            assert (tmp_path / path).read_bytes() == stylesheet, (check, path)
        config = load_config(config_file=str(tmp_path / "mkdocs.yml"))
        assert config.extra_css == ["stylesheets/atlas.css"]
        assert (Path(config.docs_dir) / config.extra_css[0]).read_bytes() == stylesheet
