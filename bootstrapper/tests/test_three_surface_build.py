from pathlib import Path

import pytest

from scripts.docs.build_docs import build


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
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
        "# Overview\n\nA canonical sentence.\n\n[Guide](guide.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "guide.md").write_text(
        "# Guide\n\nA canonical sentence.\n\n[Overview](index.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "manifest.yaml").write_text(
        """
surfaces: [repo, site, wiki]
numbering: baked
sections:
  - {id: overview, number: "1", title: Overview, source: docs/index.md, diagrams: [overview]}
  - {id: guide, number: "2", title: Guide, source: docs/guide.md}
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
    assert "[Guide](guide.md)" in site_home
    assert "[Guide](2-Guide.md)" in wiki_home
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


def test_build_check_detects_no_difference_after_second_render(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    build(root / "docs" / "manifest.yaml", root, site=True, wiki=True, check=False)
    build(root / "docs" / "manifest.yaml", root, site=True, wiki=True, check=True)


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
