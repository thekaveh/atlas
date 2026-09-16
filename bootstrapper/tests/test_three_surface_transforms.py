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
index: overview
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
        "docs/index.md": "Home",
        "docs/guides/setup.md": "2.1-Setup",
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


def test_rewrite_maps_manifest_owned_atlas_blob_links(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    markdown = (
        "[setup](https://github.com/thekaveh/atlas/blob/feature/docs/"
        "docs/guides/setup.md#configuration)\n"
    )

    rendered = rewrite_for_surface(
        markdown,
        surface="site",
        source_path="docs/index.md",
        output_path="index.md",
        source_map=build_source_map(manifest, "site"),
    )

    assert rendered == "[setup](guides/setup.md#configuration)\n"


def test_rewrite_strips_unknown_atlas_blob_links(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    markdown = "[source](https://github.com/thekaveh/atlas/blob/main/private.md)\n"

    rendered = rewrite_for_surface(
        markdown,
        surface="wiki",
        source_path="docs/index.md",
        output_path="Home.md",
        source_map=build_source_map(manifest, "wiki"),
    )

    assert rendered == "source\n"


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
            "docs/quick-start/index.md": "2.1-Launch-Atlas",
            "docs/services.md": "5.1-Service-Catalog",
        },
    )

    assert '<a href="2.1-Launch-Atlas">Quick Start</a>' in rendered
    assert '<a href="5.1-Service-Catalog">Service Catalog</a>' in rendered
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


def test_rewrite_maps_canonical_html_anchors_to_site_pretty_urls() -> None:
    """Canonical pages link raw HTML anchors to files so GitHub resolves them.

    MkDocs rewrites Markdown links but leaves raw HTML alone, so the site
    surface must translate file targets into the directory URLs it serves.
    """
    markdown = """<a href="quick-start/index.md">Quick Start</a>
<a href="services.md">Service Catalog</a>
<a href="architecture/index.md#2-layers">Architecture</a>
<a href="https://docs.docker.com/">Docker</a>
<a href="#1-capabilities">Capabilities</a>
"""

    rendered = rewrite_for_surface(
        markdown,
        surface="site",
        source_path="docs/index.md",
        output_path="index.md",
        source_map={
            "docs/quick-start/index.md": "quick-start/index.md",
            "docs/services.md": "services/index.md",
            "docs/architecture/index.md": "architecture/index.md",
        },
    )

    assert '<a href="quick-start/">Quick Start</a>' in rendered
    assert '<a href="services/">Service Catalog</a>' in rendered
    assert '<a href="architecture/#2-layers">Architecture</a>' in rendered
    assert '<a href="https://docs.docker.com/">Docker</a>' in rendered
    assert '<a href="#1-capabilities">Capabilities</a>' in rendered


def test_rewrite_maps_site_html_anchors_relative_to_nested_pretty_urls() -> None:
    """A nested page is served from its own directory, so siblings need ``../``."""
    rendered = rewrite_for_surface(
        '<a href="setup.md">Setup</a> <a href="../index.md">Home</a>\n',
        surface="site",
        source_path="docs/guides/intro.md",
        output_path="guides/intro.md",
        source_map={
            "docs/index.md": "index.md",
            "docs/guides/intro.md": "guides/intro.md",
            "docs/guides/setup.md": "guides/setup.md",
        },
    )

    assert rendered == '<a href="../setup/">Setup</a> <a href="../../">Home</a>\n'


def test_rewrite_leaves_unpublished_site_html_anchors_untouched() -> None:
    """The site surface never invents a destination for an unknown target."""
    markdown = '<a href="private.md">Private</a>\n'

    rendered = rewrite_for_surface(
        markdown,
        surface="site",
        source_path="docs/index.md",
        output_path="index.md",
        source_map={"docs/index.md": "index.md"},
    )

    assert rendered == markdown


def test_rewrite_maps_canonical_html_anchors_to_wiki_pages() -> None:
    markdown = """<a href="quick-start/index.md">Quick Start</a>
<a href="services.md#3-catalog">Service Catalog</a>
"""

    rendered = rewrite_for_surface(
        markdown,
        surface="wiki",
        source_path="docs/index.md",
        output_path="Home.md",
        source_map={
            "docs/quick-start/index.md": "2.1-Launch-Atlas",
            "docs/services.md": "5.1-Service-Catalog",
        },
    )

    assert '<a href="2.1-Launch-Atlas">Quick Start</a>' in rendered
    assert '<a href="5.1-Service-Catalog#3-catalog">Service Catalog</a>' in rendered


def test_rewrite_maps_markdown_links_to_extensionless_wiki_pages(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    rendered = rewrite_for_surface(
        "[setup](guides/setup.md#configuration)\n",
        surface="wiki",
        source_path="docs/index.md",
        output_path="Home.md",
        source_map=build_source_map(manifest, "wiki"),
    )

    assert rendered == "[setup](2.1-Setup#configuration)\n"


def test_rewrite_strips_mkdocs_attribute_lists_from_wiki() -> None:
    rendered = rewrite_for_surface(
        "[Launch](quick-start/index.md){: .atlas-card__link}\n",
        surface="wiki",
        source_path="docs/index.md",
        output_path="Home.md",
        source_map={"docs/quick-start/index.md": "2.1-Launch-Atlas"},
    )

    assert rendered == "[Launch](2.1-Launch-Atlas)\n"


def test_rewrite_strips_mkdocs_attribute_lists_before_punctuation() -> None:
    rendered = rewrite_for_surface(
        "[Launch](quick-start/index.md){: .atlas-card__link}.\n",
        surface="wiki",
        source_path="docs/index.md",
        output_path="Home.md",
        source_map={"docs/quick-start/index.md": "2.1-Launch-Atlas"},
    )

    assert rendered == "[Launch](2.1-Launch-Atlas).\n"
