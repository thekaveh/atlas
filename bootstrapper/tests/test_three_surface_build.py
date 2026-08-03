from pathlib import Path

import pytest

from scripts.docs.build_docs import _validate_numbered_h1, build
from scripts.docs.manifest import load_manifest


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
        "# 1. Overview\n\nA canonical sentence.\n\n[Guide](guide.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "guide.md").write_text(
        "# 2. Guide\n\nA canonical sentence.\n\n[Overview](index.md)\n",
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
    assert "[Guide](2-Guide)" in wiki_home
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
