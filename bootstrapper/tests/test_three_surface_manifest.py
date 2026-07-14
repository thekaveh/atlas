from pathlib import Path

import pytest

from scripts.docs.manifest import ManifestError, load_manifest, parse_manifest


def _write_public_sources(root: Path) -> None:
    (root / "docs" / "guides").mkdir(parents=True)
    (root / "docs" / "index.md").write_text("# Overview\n", encoding="utf-8")
    (root / "docs" / "guides" / "setup.md").write_text("# Setup\n", encoding="utf-8")


def _manifest_text() -> str:
    return """
surfaces: [repo, site, wiki]
numbering: baked
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
