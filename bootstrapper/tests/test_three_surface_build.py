from pathlib import Path

import pytest

from scripts.docs.build_docs import _validate_numbered_h1, build
from scripts.docs.manifest import load_manifest


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guides").mkdir()
    (tmp_path / "docs" / "assets").mkdir()
    (tmp_path / "docs" / "assets" / "poster.png").write_bytes(b"poster")
    (tmp_path / "docs" / "screenshots").mkdir()
    (tmp_path / "docs" / "screenshots" / "wizard.png").write_bytes(b"wizard")
    (tmp_path / "docs" / "diagram.html").write_text(
        '<html><svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
        '<rect width="20" height="20" fill="#020617"/></svg></html>',
        encoding="utf-8",
    )
    (tmp_path / "docs" / "index.md").write_text(
        "# 1. Overview\n\nA canonical sentence.\n\n[Guide](guides/guide.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "guides" / "guide.md").write_text(
        "# 2. Guide\n\nA canonical sentence.\n\n"
        "[Overview](../index.md)\n\n"
        "![Wizard](../screenshots/wizard.png)\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "manifest.yaml").write_text(
        """
surfaces: [repo, site, wiki]
numbering: baked
index: overview
sections:
  - {id: overview, number: "1", title: Overview, source: docs/index.md, diagrams: [overview]}
  - {id: guide, number: "2", title: Guide, source: docs/guides/guide.md}
diagrams:
  - {id: overview, master: docs/diagram.html}
""",
        encoding="utf-8",
    )
    return tmp_path


def test_build_projects_same_content_to_site_and_wiki(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    build(root / "docs" / "manifest.yaml", root, site=True, wiki=True, check=False)
    build(root / "docs" / "manifest.yaml", root, site=True, wiki=True, check=True)

    site_home = (root / "generated" / "site" / "index.md").read_text(encoding="utf-8")
    wiki_home = (root / "generated" / "wiki" / "Home.md").read_text(encoding="utf-8")
    assert "# 1. Overview" in site_home
    assert "# 1. Overview" in wiki_home
    assert "A canonical sentence." in site_home and "A canonical sentence." in wiki_home
    assert "[Guide](guides/guide.md)" in site_home
    assert "[Guide](Guide)" in wiki_home
    site_guide = (root / "generated" / "site" / "guides" / "guide.md").read_text(
        encoding="utf-8"
    )
    wiki_guide = (root / "generated" / "wiki" / "Guide.md").read_text(
        encoding="utf-8"
    )
    assert "![Wizard](../screenshots/wizard.png)" in site_guide
    assert "![Wizard](screenshots/wizard.png)" in wiki_guide
    assert (root / "generated" / "wiki" / "_Sidebar.md").exists()
    assert (root / "generated" / "wiki" / "_Footer.md").exists()
    assert (root / "generated" / "site" / "assets" / "poster.png").read_bytes() == b"poster"
    assert (root / "generated" / "wiki" / "assets" / "poster.png").read_bytes() == b"poster"
    assert (root / "generated" / "site" / "screenshots" / "wizard.png").read_bytes() == b"wizard"
    assert (root / "generated" / "wiki" / "screenshots" / "wizard.png").read_bytes() == b"wizard"

    mkdocs = (root / "mkdocs.yml").read_text(encoding="utf-8")
    assert "docs_dir: generated/site" in mkdocs
    assert '"1. Overview": index.md' in mkdocs
    assert "repo_url" not in mkdocs
    assert "edit_uri" not in mkdocs


def test_numbered_h1_validation_preserves_canonical_heading(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    page = load_manifest(root / "docs" / "manifest.yaml", root).pages[0]
    markdown = "# 1. Atlas Documentation\n\nCanonical content.\n"

    assert _validate_numbered_h1(markdown, page) == markdown


def test_numbered_h1_validation_rejects_manifest_drift(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    manifest = load_manifest(root / "docs" / "manifest.yaml", root)
    page = manifest.pages[0]

    with pytest.raises(ValueError, match="canonical H1 must start with '# 1\\. '"):
        _validate_numbered_h1("# Overview\n", page)


def test_build_check_detects_no_difference_after_second_render(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    build(root / "docs" / "manifest.yaml", root, site=True, wiki=True, check=False)
    build(root / "docs" / "manifest.yaml", root, site=True, wiki=True, check=True)


def test_build_check_never_renders_into_repository_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    manifest = root / "docs" / "manifest.yaml"
    build(manifest, root, site=True, wiki=True, check=False)

    from scripts.docs import build_docs

    real_render_site = build_docs.render_site
    real_render_wiki = build_docs.render_wiki

    def guarded_site(model, repo_root, destination):
        assert destination != root / "generated" / "site"
        return real_render_site(model, repo_root, destination)

    def guarded_wiki(model, repo_root, destination):
        assert destination != root / "generated" / "wiki"
        return real_render_wiki(model, repo_root, destination)

    monkeypatch.setattr(build_docs, "render_site", guarded_site)
    monkeypatch.setattr(build_docs, "render_wiki", guarded_wiki)

    build(manifest, root, site=True, wiki=True, check=True)


def test_build_check_does_not_require_ignored_generated_outputs(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    manifest = root / "docs" / "manifest.yaml"
    build(manifest, root, site=True, wiki=True, check=False)
    import shutil

    shutil.rmtree(root / "generated")

    build(manifest, root, site=True, wiki=True, check=True)


def test_build_check_does_not_rerender_committed_pngs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    manifest = root / "docs" / "manifest.yaml"
    build(manifest, root, site=True, wiki=True, check=False)

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("check mode must not compare platform-specific renderer output")

    monkeypatch.setattr("scripts.docs.render_diagrams.svg_to_png", fail_if_called)

    build(manifest, root, site=True, wiki=True, check=True)


def test_projection_build_does_not_rerender_current_committed_pngs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    manifest = root / "docs" / "manifest.yaml"
    build(manifest, root, site=True, wiki=True, check=False)

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("projection builds must reuse current committed PNGs")

    monkeypatch.setattr("scripts.docs.render_diagrams.svg_to_png", fail_if_called)

    build(manifest, root, site=True, wiki=True, check=False)


def test_validation_projection_rejects_missing_committed_png_without_repair(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    manifest = root / "docs" / "manifest.yaml"
    build(manifest, root, site=True, wiki=True, check=False)
    png = root / "docs" / "diagrams" / "img" / "overview.png"
    png.unlink()

    with pytest.raises(RuntimeError, match="diagram PNG is stale"):
        build(
            manifest,
            root,
            site=True,
            wiki=True,
            check=False,
            verify_png=True,
        )

    assert not png.exists()


def test_build_check_detects_committed_diagram_png_drift_without_rewriting_it(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    manifest = root / "docs" / "manifest.yaml"
    build(manifest, root, site=True, wiki=True, check=False)
    png = root / "docs" / "diagrams" / "img" / "overview.png"
    png.write_bytes(b"stale")

    with pytest.raises(RuntimeError, match="diagram PNG is stale"):
        build(manifest, root, site=True, wiki=True, check=True)

    assert png.read_bytes() == b"stale"


def test_build_check_detects_png_stale_after_master_changes(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    manifest = root / "docs" / "manifest.yaml"
    build(manifest, root, site=True, wiki=True, check=False)
    master = root / "docs" / "diagram.html"
    master.write_text(
        master.read_text(encoding="utf-8").replace("#020617", "#0ea5e9"),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="diagram PNG is stale"):
        build(manifest, root, site=True, wiki=True, check=True)


@pytest.mark.parametrize("variable", [
    "RAY_WORKER_COUNT", "RAY_HEAD_MEMORY_LIMIT", "RAY_HEAD_CPU_LIMIT",
])
def test_ray_source_conditions_remain_visible(variable: str, tmp_path: Path) -> None:
    import html
    import io
    import re
    import xml.etree.ElementTree as ET
    import markdown
    from mkdocs.config import load_config
    from scripts.docs.build_docs import render_mkdocs_yml
    from tests.three_surface_test_utils import (
        ROOT, PROJECTION_ROOT, ensure_generated_docs, surface_text,
    )

    config = load_config(
        io.StringIO(render_mkdocs_yml(ensure_generated_docs())),
        config_file_path=str(ROOT / "mkdocs.yml"),
        docs_dir=str(PROJECTION_ROOT / "site"),
        site_dir=str(tmp_path / "site"),
    )
    rendered = markdown.markdown(
        surface_text("services/ray/README.md", "site"),
        extensions=config.markdown_extensions,
        extension_configs=config.mdx_configs,
    )
    rendered = re.sub(
        r"&([A-Za-z][A-Za-z0-9]+);",
        lambda match: html.escape(html.unescape(match.group(0))), rendered,
    )
    document = ET.fromstring("<div>" + rendered + "</div>")
    row, table = next(
        (row, table)
        for table in document.findall(".//table")
        for row in table.findall("tbody/tr")
        if any(code.text == variable for code in row[0].findall("code"))
    )
    headers = ["".join(cell.itertext()).strip() for cell in table.findall("thead/tr/th")]
    condition = " ".join(row[headers.index("When")].itertext()).casefold()
    assert re.search(r"\bcpu\b", condition) and re.search(r"\bgpu\b", condition), (variable, condition)


@pytest.mark.parametrize("surface", ["repo", "site", "wiki"])
@pytest.mark.parametrize("case", [
    ({"NEO4J_GRAPH_DB_SOURCE": "localhost", "GRAPH_DB_USER": "graph-reader",
      "NEO4J_LOCALHOST_BOLT_PORT": "17687", "GRAPH_DB_PORT": "16323"}, "graph-reader", "17687"),
    ({"NEO4J_GRAPH_DB_SOURCE": "localhost"}, "neo4j", "7687"),
    ({"NEO4J_GRAPH_DB_SOURCE": "container", "GRAPH_DB_USER": "host-only-user",
      "NEO4J_LOCALHOST_BOLT_PORT": "17687", "GRAPH_DB_PORT": "16323"}, "neo4j", "16323"),
    ({"GRAPH_DB_USER": "host-only-user"}, "neo4j", "63023"),
])
def test_neo4j_python_example_uses_source_credentials(surface: str, case) -> None:
    import ast
    import os
    import types
    from unittest.mock import MagicMock, patch
    from markdown_it import MarkdownIt
    from tests.three_surface_test_utils import ROOT, surface_text

    source = "services/neo4j/README.md"
    text = (ROOT / source).read_text(encoding="utf-8") if surface == "repo" else surface_text(source, surface)
    snippets = [
        token.content for token in MarkdownIt("commonmark").parse(text)
        if token.type == "fence" and token.info.strip() == "python"
        and any(isinstance(node, ast.ImportFrom) and node.module == "neo4j"
                for node in ast.walk(ast.parse(token.content)))
    ]
    assert snippets, "No executable Neo4j connection example found"
    environment, username, port = case
    environment = {**environment, "GRAPH_DB_PASSWORD": "owned-example-password"}
    driver = MagicMock()
    module = types.SimpleNamespace(GraphDatabase=types.SimpleNamespace(driver=driver))
    with patch.dict("sys.modules", {"neo4j": module}), patch.dict(os.environ, environment, clear=True):
        exec(compile(snippets[0], source, "exec"), {})
    driver.assert_called_once_with(
        f"bolt://localhost:{port}", auth=(username, "owned-example-password"),
    )


@pytest.mark.parametrize("surface", ["generator", "repo", "site", "wiki", "html"])
def test_lakehouse_caption_qualifies_sqlite_fallback(surface: str) -> None:
    import re
    from html.parser import HTMLParser
    from bootstrapper.docs.sitegen.pages import ARCHITECTURE_INTERPRETATIONS
    from tests.three_surface_test_utils import ROOT, surface_text

    source = "docs/architecture/data-engineering-lakehouse-flow.md"
    if surface == "generator":
        text = ARCHITECTURE_INTERPRETATIONS["data-engineering-lakehouse-flow"]
    elif surface in {"repo", "html"}:
        path = (ROOT / source).with_suffix(".html") if surface == "html" else ROOT / source
        text = path.read_text(encoding="utf-8")
    else:
        text = surface_text(source, surface)
    if surface == "html":
        class Text(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts = []

            def handle_data(self, data):
                self.parts.append(data)

        parser = Text()
        parser.feed(text)
        text = "".join(parser.parts)
    sentences = [part for part in re.split(r"[.!?]\s+", text) if "SQLite" in part]
    assert any(re.search(r"\b(absent|unset|missing|without)\b|\bnot (set|supplied|provided)\b", part, re.I) for part in sentences), sentences
    assert re.search(r"\b(supplied|provided|configured) URI\b[^.!?]*\b(used|retained|honored|kept)\b", text, re.I), text


@pytest.fixture(scope="module")
def env_reference_config(tmp_path_factory):
    import io
    from mkdocs.config import load_config
    from scripts.docs.build_docs import render_mkdocs_yml
    from tests.three_surface_test_utils import ROOT, PROJECTION_ROOT, ensure_generated_docs

    return load_config(
        io.StringIO(render_mkdocs_yml(ensure_generated_docs())),
        config_file_path=str(ROOT / "mkdocs.yml"),
        docs_dir=str(PROJECTION_ROOT / "site"),
        site_dir=str(tmp_path_factory.mktemp("env-reference-site")),
    )


def _rendered_env_description(text, variable, config):
    import html
    import re
    import xml.etree.ElementTree as ET
    import markdown

    rendered = markdown.markdown(
        text, extensions=config.markdown_extensions, extension_configs=config.mdx_configs,
    )
    rendered = re.sub(
        r"&([A-Za-z][A-Za-z0-9]+);",
        lambda match: html.escape(html.unescape(match.group(0))), rendered,
    )
    document = ET.fromstring("<div>" + rendered + "</div>")
    for table in document.findall(".//table"):
        headers = ["".join(cell.itertext()).strip() for cell in table.findall("thead/tr/th")]
        if not {"Variable", "Description"}.issubset(headers):
            continue
        for row in table.findall("tbody/tr"):
            name = "".join(row[headers.index("Variable")].itertext()).strip()
            if name == variable:
                return "".join(row[headers.index("Description")].itertext())
    raise AssertionError(f"No environment Description cell for {variable}")


@pytest.mark.parametrize("surface", ["generator", "repo", "site", "wiki"])
@pytest.mark.parametrize("case", [
    ("BACKUP_S3_MODE", ("MINIO_ROOT_*", "BACKUP_S3_*")),
    ("COMFYUI_MPS_LOCALHOST_PORT", ("_LOCALHOST_",)),
    ("MINIO_IMAGE", ("MINIO_*", "/minio/*")),
    ("STT_PROVIDER_PORT", ("PARAKEET_*", "SPEACHES_*")),
])
def test_environment_description_literals_remain_visible(surface, case, env_reference_config):
    from scripts.docs.canonical_references import render_canonical_references
    from tests.three_surface_test_utils import ROOT, surface_text

    source = "docs/reference/env-vars.md"
    if surface == "generator":
        text = render_canonical_references(ROOT)[ROOT / source]
    elif surface == "repo":
        text = (ROOT / source).read_text(encoding="utf-8")
    else:
        text = surface_text(source, surface)
    variable, literals = case
    description = _rendered_env_description(text, variable, env_reference_config)
    for literal in literals:
        assert literal in description, (surface, variable, literal, description)


_CANDIDATE_GUIDE_LINKS = [
    ("doc-processor", "docling-mcp"), ("kong", "keycloak"),
    ("local-deep-researcher", "firecrawl"), ("n8n", "browserless"),
    ("n8n", "nocodb"), ("ollama", "openlit"), ("open-webui", "mcpo"),
    ("openclaw", "honcho"), ("redis", "redisinsight"), ("redis", "redis-stack"),
    ("searxng", "perplexica"), ("searxng", "browserless"),
    ("stt-provider", "whisperx"), ("supabase", "supabase-edge-functions"),
    ("supabase", "imgproxy"), ("tts-provider", "unmute"), ("tts-provider", "omnivoice"),
]


def _candidate_document(text, config):
    import html
    import re
    import xml.etree.ElementTree as ET
    import markdown

    rendered = markdown.markdown(
        text, extensions=config.markdown_extensions, extension_configs=config.mdx_configs,
    )
    rendered = re.sub(r"&([A-Za-z][A-Za-z0-9]+);",
                      lambda match: html.escape(html.unescape(match.group(0))), rendered)
    return ET.fromstring("<div>" + rendered + "</div>")


@pytest.mark.parametrize("surface", ["repo", "site", "wiki"])
@pytest.mark.parametrize("guide,slug", _CANDIDATE_GUIDE_LINKS)
def test_candidate_decision_navigation_reaches_recorded_section(
    surface, guide, slug, env_reference_config,
):
    from docs.merge_research import load_public_decisions
    from tests.three_surface_test_utils import ROOT, ensure_generated_docs, surface_text

    source = f"services/{guide}/README.md"
    text = (ROOT / source).read_text() if surface == "repo" else surface_text(source, surface)
    document = _candidate_document(text, env_reference_config)
    links = [node.get("href") for node in document.findall(".//a")
             if node.get("href", "").endswith(f"#candidate-{slug}")]
    assert links, (surface, source, slug, "missing clickable decision destination")
    page = next(page for page in ensure_generated_docs().pages
                if page.source == "docs/reference/candidate-decisions.md")
    expected = {"repo": "../../docs/reference/candidate-decisions.md",
                "site": "../reference/candidate-decisions.md", "wiki": page.wiki_path.stem}[surface]
    assert all(link == expected + f"#candidate-{slug}" for link in links)
    target = (ROOT / page.source).read_text() if surface == "repo" else surface_text(page.source, surface)
    records = {record["slug"]: record for record in load_public_decisions(ROOT / "docs/research")}
    _assert_recorded_candidate_section(target, records[slug], env_reference_config)


def _candidate_section(nodes, slug):
    anchor = next(i for i, node in enumerate(nodes)
                  if node.find(f".//a[@id='candidate-{slug}']") is not None
                  or node.get("id") == f"candidate-{slug}")
    following = nodes[anchor + 1:]
    end = next((i for i, node in enumerate(following[1:], 1) if node.tag == "h2"), len(following))
    return following[:end]


def _assert_recorded_candidate_section(target, record, config):
    import re

    section = _candidate_section(list(_candidate_document(target, config)), record["slug"])
    assert section and section[0].tag == "h2", "missing candidate heading"
    for permalink in section[0].findall("a[@class='headerlink']"):
        section[0].remove(permalink)
    heading = "".join(section[0].itertext())
    assert re.sub(r"^\d+\.\s+", "", heading) == record["name"], "wrong candidate name"
    content = " ".join("".join(node.itertext()) for node in section)
    decided = str(record["decided"]) if record["decided"] is not None else "not recorded"
    assert f'Lifecycle: {record["lifecycle"]}.' in content, "missing or wrong lifecycle"
    assert f"Recorded decision date: {decided}." in content, "missing or wrong decision date"
    assert any("".join(node.itertext()) == record["public-summary"] for node in section
               if node.tag == "p"), "missing or wrong public summary"
    _assert_candidate_upstream(section, record["upstream"])


def _assert_candidate_upstream(section, upstream):
    links = [link for node in section for link in node.findall(".//a")]
    assert any(link.get("href") == upstream for link in links), "missing or wrong upstream URL"


@pytest.mark.parametrize("changes", [
    {"public-summary": "Authentication and ownership require an integration design."},
    {"slug": "http-candidate", "name": "HTTP Candidate", "lifecycle": "planned",
     "decided": "2025-02-03", "upstream": "http://example.org/project"},
    {"slug": "brief", "name": "Brief & proposed", "lifecycle": "proposed", "decided": None,
     "public-summary": "Deferred.", "upstream": "http://example.org/brief?owner=atlas&mode=review"},
])
def test_recorded_candidate_section_accepts_canonical_metadata(
    changes, tmp_path, env_reference_config,
):
    import yaml
    from docs.merge_research import load_public_decisions
    from scripts.docs.canonical_references import render_candidate_decisions

    expected = {"slug": "fixture-candidate", "name": "Fixture *Candidate* & review",
                "lifecycle": "deferred", "decided": "2026-07-04",
                "public-summary": "The recorded integration requires scoped credentials and a separate operational ownership review.",
                "upstream": "https://example.org/short", **changes}
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / f'{expected["slug"]}.md').write_text(
        "---\n" + yaml.safe_dump(expected) + "---\n\nPrivate research body.\n",
        encoding="utf-8",
    )
    target = render_candidate_decisions(load_public_decisions(tmp_path))
    _assert_recorded_candidate_section(target, expected, env_reference_config)


@pytest.mark.parametrize("original,replacement,failure", [
    ("Expected Candidate", "Unrelated Candidate", "wrong candidate name"),
    ("Expected Candidate", "", "wrong candidate name"),
    ("Lifecycle: deferred.", "Lifecycle: planned.", "missing or wrong lifecycle"),
    ("Lifecycle: deferred.", "", "missing or wrong lifecycle"),
    ("Recorded decision date: 2026-07-04.", "Recorded decision date: 2025-02-03.", "missing or wrong decision date"),
    ("Recorded decision date: 2026-07-04.", "", "missing or wrong decision date"),
    ("The actual summary records authentication and ownership prerequisites for the future integration.",
     "", "missing or wrong public summary"),
    ("The actual summary records authentication and ownership prerequisites for the future integration.",
     "An unrelated paragraph still contains more than ten words about a completely different decision.",
     "missing or wrong public summary"),
    ('<a href="https://example.org/expected">Upstream project</a>.', "", "missing or wrong upstream URL"),
    ("https://example.org/expected", "https://example.org/unrelated", "missing or wrong upstream URL"),
    ("## 1. Expected Candidate\n\nLifecycle: deferred. Recorded decision date: 2026-07-04.",
     "", "missing candidate heading"),
    ("section", "", "missing candidate heading"),
])
def test_recorded_candidate_section_rejects_missing_or_wrong_metadata(
    original, replacement, failure, env_reference_config,
):
    expected = {"slug": "expected", "name": "Expected Candidate", "lifecycle": "deferred",
                "decided": "2026-07-04", "upstream": "https://example.org/expected",
                "public-summary": "The actual summary records authentication and ownership prerequisites for the future integration."}
    body = (
        "## 1. Expected Candidate\n\nLifecycle: deferred. Recorded decision date: 2026-07-04.\n\n"
        "The actual summary records authentication and ownership prerequisites for the future integration.\n\n"
        "Unrelated context supplies many extra words but cannot replace the expected canonical decision fields.\n\n"
        '<a href="https://example.org/expected">Upstream project</a>.\n\n'
    )
    changed = "" if original == "section" else body.replace(original, replacement)
    target = '<a id="candidate-expected"></a>\n\n' + changed
    target += '<a id="candidate-decoy"></a>\n\n' + body
    with pytest.raises(AssertionError, match=failure):
        _assert_recorded_candidate_section(target, expected, env_reference_config)


def test_candidate_decision_navigation_rejects_missing_guide_link(monkeypatch, env_reference_config):
    monkeypatch.setattr("tests.three_surface_test_utils.surface_text", lambda *args: "# Guide\n")
    with pytest.raises(AssertionError, match="missing clickable decision destination"):
        test_candidate_decision_navigation_reaches_recorded_section(
            "site", "kong", "keycloak", env_reference_config,
        )



@pytest.fixture(scope="session", autouse=True)
def _candidate_projection_lifetime():
    yield
    from tests.three_surface_test_utils import _PROJECTION_TEMP

    _PROJECTION_TEMP.cleanup()


@pytest.mark.parametrize("surface", ["repo", "site", "wiki"])
@pytest.mark.parametrize("context", ["head", "storage"])
def test_ray_gcs_protocol_is_grpc(surface, context, env_reference_config):
    import re
    from tests.three_surface_test_utils import ROOT, surface_text

    source = "services/ray/README.md"
    text = (ROOT / source).read_text() if surface == "repo" else surface_text(source, surface)
    document = _candidate_document(text, env_reference_config)
    tag, marker = ("li", "ray start --head") if context == "head" else ("p", "Plasma")
    claims = [" ".join(node.itertext()) for node in document.iter(tag)
              if "GCS" in " ".join(node.itertext()) and marker in " ".join(node.itertext())]
    assert claims, (surface, context, "missing GCS explanation")
    assert all(re.search(r"\bgRPC\b", claim) for claim in claims), (surface, context, claims)


def _litellm_credential_claims(document, context):
    if context == "future":
        return [" ".join(node.itertext()) for node in document.iter("li")
                if node.find("strong") is not None
                and "Virtual keys" in "".join(node.find("strong").itertext())]
    rows = document.findall(".//tr[td='Per-service gateway credentials and budgets']")
    assert len(rows) == 1, "missing credential capability"
    cells = rows[0].findall("td")
    assert "".join(cells[1].itertext()).strip() == "partial", "existing partial provisioning"
    return [" ".join(cells[-1].itertext())]


@pytest.mark.parametrize("surface", ["repo", "site", "wiki"])
@pytest.mark.parametrize("context", ["capability", "future"])
def test_litellm_credentials_describe_trueforge_exception(surface, context, env_reference_config):
    import re
    from tests.three_surface_test_utils import ROOT, surface_text

    source = "services/litellm/README.md"
    text = (ROOT / source).read_text() if surface == "repo" else surface_text(source, surface)
    claims = _litellm_credential_claims(_candidate_document(text, env_reference_config), context)
    assert claims, (surface, context, "missing credential explanation")
    for claim in claims:
        assert re.search(r"\bTrueForge\b[^.!?]*\bvirtual key\b", claim, re.I), (surface, context, claim)
        assert re.search(r"\bmaster.key\b[^.!?]*\bfall(?:back|s back)\b|\bfall(?:back|s back)\b[^.!?]*\bmaster.key\b", claim, re.I), (surface, context, claim)
        for limitation in ["spend caps", "team budgets"]:
            assert re.search(r"\bno\b[^.!?]*\b" + limitation + r"\b", claim, re.I), (surface, context, claim)


@pytest.mark.parametrize("surface", ["repo", "site", "wiki"])
def test_hermes_omitted_integration_keeps_upstream_defaults(surface, env_reference_config):
    import re
    from tests.three_surface_test_utils import ROOT, surface_text

    source = "services/hermes/README.md"
    text = (ROOT / source).read_text() if surface == "repo" else surface_text(source, surface)
    document = _candidate_document(text, env_reference_config)
    claims = [" ".join(node.itertext()) for node in document.iter("p")
              if re.search(r"\bomits\b.*config\.yaml", " ".join(node.itertext()), re.S)]
    assert len(claims) == 1, (surface, "missing optional-integration explanation")
    claim = claims[0]
    assert re.search(r"upstream[^.!?]*defaults", claim, re.I), (surface, claim)
    assert re.search(r"default[^.!?]*\bedge\b", claim, re.I), (surface, claim)
    assert re.search(r"\bif\b[^.!?]*edge-tts[^.!?]*available", claim, re.I), (surface, claim)
    assert re.search(r"Microsoft[^.!?]*cloud", claim, re.I), (surface, claim)
