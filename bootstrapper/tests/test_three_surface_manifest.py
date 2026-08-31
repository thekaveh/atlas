import posixpath
from pathlib import Path
from urllib.parse import unquote

import pytest

from scripts.docs.manifest import ManifestError, load_manifest, parse_manifest
from scripts.docs.links import navigable_link_targets


def _write_public_sources(root: Path) -> None:
    (root / "docs" / "guides").mkdir(parents=True)
    (root / "docs" / "index.md").write_text("# Overview\n", encoding="utf-8")
    (root / "docs" / "guides" / "setup.md").write_text("# Setup\n", encoding="utf-8")


def _manifest_text() -> str:
    return """
surfaces: [repo, site, wiki]
numbering: baked
index: overview
sections:
  - id: overview
    number: "1"
    title: Overview
    source: docs/index.md
  - id: guides
    number: "2"
    title: Guides
    children:
      - id: setup
        number: "2.1"
        title: Setup
        source: docs/guides/setup.md
diagrams: []
"""


def test_parse_manifest_wraps_yaml_errors() -> None:
    with pytest.raises(ManifestError, match="YAML"):
        parse_manifest("sections: [")


def test_manifest_rejects_source_and_children_on_same_section() -> None:
    text = _manifest_text().replace(
        "    children:\n",
        "    source: docs/index.md\n    children:\n",
    )

    with pytest.raises(ManifestError, match="source or children"):
        parse_manifest(text)


def test_manifest_rejects_nonsequential_hierarchical_numbering() -> None:
    text = _manifest_text().replace('number: "2.1"', 'number: "2.3"')

    with pytest.raises(ManifestError, match="expected 2.1"):
        parse_manifest(text)


def test_manifest_rejects_missing_canonical_index() -> None:
    text = _manifest_text().replace("index: overview\n", "")

    with pytest.raises(ManifestError, match="missing required key 'index'"):
        parse_manifest(text)


def test_manifest_rejects_unknown_canonical_index() -> None:
    text = _manifest_text().replace("index: overview", "index: absent")

    with pytest.raises(ManifestError, match="unknown page ID: absent"):
        parse_manifest(text)


def test_load_manifest_validates_sources_and_flattens_pages(tmp_path: Path) -> None:
    _write_public_sources(tmp_path)
    manifest_path = tmp_path / "docs" / "manifest.yaml"
    manifest_path.write_text(_manifest_text(), encoding="utf-8")

    manifest = load_manifest(manifest_path, tmp_path)

    assert [page.id for page in manifest.pages] == ["overview", "setup"]
    assert manifest.pages[0].site_path.as_posix() == "index.md"
    assert manifest.pages[0].wiki_path.as_posix() == "Home.md"
    assert manifest.pages[1].site_path.as_posix() == "guides/setup.md"
    assert manifest.pages[1].wiki_path.as_posix() == "2.1-Setup.md"


def test_load_manifest_rejects_missing_source(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    manifest_path = tmp_path / "docs" / "manifest.yaml"
    manifest_path.write_text(_manifest_text(), encoding="utf-8")

    with pytest.raises(ManifestError, match="does not exist"):
        load_manifest(manifest_path, tmp_path)


def test_load_manifest_rejects_symlinked_canonical_source(tmp_path: Path) -> None:
    (tmp_path / "docs" / "guides").mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    (tmp_path / "docs" / "index.md").symlink_to(outside)
    (tmp_path / "docs" / "guides" / "setup.md").write_text(
        "# Setup\n", encoding="utf-8"
    )
    manifest_path = tmp_path / "docs" / "manifest.yaml"
    manifest_path.write_text(_manifest_text(), encoding="utf-8")

    with pytest.raises(ManifestError, match="symlink"):
        load_manifest(manifest_path, tmp_path)


def test_load_manifest_rejects_canonical_source_below_symlinked_directory(
    tmp_path: Path,
) -> None:
    real_docs = tmp_path / "real-docs"
    (real_docs / "guides").mkdir(parents=True)
    (real_docs / "index.md").write_text("# Overview\n", encoding="utf-8")
    (real_docs / "guides" / "setup.md").write_text("# Setup\n", encoding="utf-8")
    (tmp_path / "docs").symlink_to(real_docs, target_is_directory=True)
    manifest_path = real_docs / "manifest.yaml"
    manifest_path.write_text(_manifest_text(), encoding="utf-8")

    with pytest.raises(ManifestError, match="symlink"):
        load_manifest(manifest_path, tmp_path)


def test_diagram_catalog_does_not_become_the_development_section_index() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = load_manifest(root / "docs" / "manifest.yaml", root)

    diagram_catalog = next(page for page in manifest.pages if page.id == "diagram-catalog")

    assert diagram_catalog.site_path.as_posix() == "diagrams/catalog.md"


def test_manifest_declares_the_canonical_repository_index() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = load_manifest(root / "docs" / "manifest.yaml", root)

    assert manifest.index_id == "documentation-map"


def test_every_manifest_page_is_reachable_from_the_canonical_repository_index() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = load_manifest(root / "docs" / "manifest.yaml", root)
    pages_by_source = {page.source: page for page in manifest.pages}
    start = next(page.source for page in manifest.pages if page.id == "documentation-map")

    reachable = {start}
    pending = [start]
    while pending:
        source = pending.pop()
        markdown = (root / source).read_text(encoding="utf-8")
        for raw_target in navigable_link_targets(markdown):
            target = unquote(raw_target.strip("<>").partition("#")[0])
            if not target or "://" in target or target.startswith(("/", "mailto:")):
                continue
            candidate = posixpath.normpath(
                posixpath.join(posixpath.dirname(source), target)
            )
            if candidate in pages_by_source and candidate not in reachable:
                reachable.add(candidate)
                pending.append(candidate)

    assert set(pages_by_source) - reachable == set()


def test_canonical_repository_index_links_each_authoritative_section_index_directly() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = load_manifest(root / "docs" / "manifest.yaml", root)
    index_page = next(page for page in manifest.pages if page.id == manifest.index_id)
    known_sources = {page.source for page in manifest.pages}
    direct_targets: set[str] = set()
    direct_order: list[str] = []
    for raw_target in navigable_link_targets(
        (root / index_page.source).read_text(encoding="utf-8")
    ):
        target = unquote(raw_target.strip("<>").partition("#")[0])
        if not target or "://" in target or target.startswith(("/", "mailto:")):
            continue
        candidate = posixpath.normpath(
            posixpath.join(posixpath.dirname(index_page.source), target)
        )
        for option in (candidate, f"{candidate}.md", f"{candidate}/index.md"):
            if option in known_sources:
                direct_targets.add(option)
                if option not in direct_order:
                    direct_order.append(option)
                break

    expected: set[str] = set()
    expected_order: list[str] = []
    pages_by_id = {page.id: page for page in manifest.pages}
    for section in manifest.sections:
        if section.source:
            if section.id != manifest.index_id:
                expected.add(section.source)
                expected_order.append(section.source)
            continue
        section_index = next(
            (
                child
                for child in section.children
                if child.source and child.id.endswith("-index")
            ),
            None,
        )
        assert section_index is not None, f"{section.id} has no authoritative index"
        expected.add(pages_by_id[section_index.id].source)
        expected_order.append(pages_by_id[section_index.id].source)

    assert expected - direct_targets == set()
    assert [source for source in direct_order if source in expected] == expected_order
