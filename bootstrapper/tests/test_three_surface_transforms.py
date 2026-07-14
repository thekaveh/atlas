from pathlib import Path

from scripts.docs.manifest import load_manifest
from scripts.docs.transforms import build_source_map, rewrite_for_surface


def _manifest(tmp_path: Path):
    (tmp_path / "docs" / "guides").mkdir(parents=True)
    (tmp_path / "docs" / "index.md").write_text("# Overview\n", encoding="utf-8")
    (tmp_path / "docs" / "guides" / "setup.md").write_text("# Setup\n", encoding="utf-8")
    path = tmp_path / "docs" / "manifest.yaml"
    path.write_text(
        """
surfaces: [repo, site, wiki]
numbering: baked
sections:
  - {id: overview, number: "1", title: Overview, source: docs/index.md}
  - id: guides
    number: "2"
    title: Guides
    children:
      - {id: setup, number: "2.1", title: Setup, source: docs/guides/setup.md}
diagrams: []
""",
        encoding="utf-8",
    )
    return load_manifest(path, tmp_path)


def test_source_map_uses_local_site_and_wiki_paths(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)

    assert build_source_map(manifest, "site") == {
        "docs/index.md": "index.md",
        "docs/guides/setup.md": "guides/setup.md",
    }
    assert build_source_map(manifest, "wiki") == {
        "docs/index.md": "Home.md",
        "docs/guides/setup.md": "2.1-Setup.md",
    }


def test_rewrite_strips_forbidden_and_unpublished_links(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    markdown = """See [overview](../index.md), [draft](draft.md),
[notebook](demo.ipynb), [source](https://github.com/thekaveh/atlas/blob/main/x),
and [Docker](https://docs.docker.com/).
"""

    rendered = rewrite_for_surface(
        markdown,
        surface="site",
        source_path="docs/guides/setup.md",
        output_path="guides/setup.md",
        source_map=build_source_map(manifest, "site"),
    )

    assert "[overview](../index.md)" in rendered
    assert "draft" in rendered and "draft.md" not in rendered
    assert "notebook" in rendered and "demo.ipynb" not in rendered
    assert "source" in rendered and "github.com/thekaveh/atlas" not in rendered
    assert "[Docker](https://docs.docker.com/)" in rendered


def test_rewrite_maps_html_anchors_to_numbered_wiki_pages() -> None:
    markdown = """<a href="quick-start/">Quick Start</a>
<a href="services/">Service Catalog</a>
<a href="https://docs.docker.com/">Docker</a>
"""

    rendered = rewrite_for_surface(
        markdown,
        surface="wiki",
        source_path="docs/index.md",
        output_path="Home.md",
        source_map={
            "docs/quick-start/index.md": "2.1-Launch-Atlas.md",
            "docs/services.md": "5.1-Service-Catalog.md",
        },
    )

    assert '<a href="2.1-Launch-Atlas.md">Quick Start</a>' in rendered
    assert '<a href="5.1-Service-Catalog.md">Service Catalog</a>' in rendered
    assert '<a href="https://docs.docker.com/">Docker</a>' in rendered


def test_rewrite_leaves_site_html_anchors_on_pretty_urls() -> None:
    markdown = '<a href="quick-start/">Quick Start</a>\n'

    rendered = rewrite_for_surface(
        markdown,
        surface="site",
        source_path="docs/index.md",
        output_path="index.md",
        source_map={"docs/quick-start/index.md": "quick-start/index.md"},
    )

    assert rendered == markdown
