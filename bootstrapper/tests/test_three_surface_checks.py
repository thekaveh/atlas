from pathlib import Path

import pytest

from scripts.docs.check_docs import (
    check_completeness,
    check_placeholders,
    check_self_containment,
    check_wiki_links,
)
from scripts.docs import check_docs as docs_checks
from scripts.docs.manifest import load_manifest
from bootstrapper.tests import three_surface_test_utils


def _manifest(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.md").write_text("# Overview\n", encoding="utf-8")
    path = tmp_path / "docs" / "manifest.yaml"
    path.write_text(
        """
surfaces: [repo, site, wiki]
numbering: baked
index: overview
sections:
  - {id: overview, number: "1", title: Overview, source: docs/index.md}
diagrams: []
""",
        encoding="utf-8",
    )
    return load_manifest(path, tmp_path)


def test_self_containment_checks_repo_site_and_wiki(tmp_path: Path) -> None:
    generated = tmp_path / "generated"
    (generated / "site").mkdir(parents=True)
    (generated / "wiki").mkdir()
    (tmp_path / "README.md").write_text(
        "[Site](https://thekaveh.github.io/atlas/)", encoding="utf-8"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text(
        "[Wiki](https://github.com/thekaveh/atlas/wiki/Guide)", encoding="utf-8"
    )
    (generated / "site" / "index.md").write_text(
        "[Source](https://github.com/thekaveh/atlas/blob/main/README.md)", encoding="utf-8"
    )
    (generated / "wiki" / "Home.md").write_text(
        "[Site](https://thekaveh.github.io/atlas/)", encoding="utf-8"
    )

    findings = check_self_containment(tmp_path, generated)

    assert len(findings) == 4
    assert {finding.surface for finding in findings} == {"repo", "site", "wiki"}


def test_completeness_reports_unmanifested_public_docs_but_excludes_internal(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    (tmp_path / "docs" / "orphan.md").write_text("# Orphan\n", encoding="utf-8")
    (tmp_path / "docs" / "research").mkdir()
    (tmp_path / "docs" / "research" / "private.md").write_text("# Private\n", encoding="utf-8")

    findings = check_completeness(manifest, tmp_path)

    assert [finding.path for finding in findings] == ["docs/orphan.md"]


def test_completeness_reports_unmanifested_service_readmes(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    (tmp_path / "services" / "example").mkdir(parents=True)
    (tmp_path / "services" / "example" / "README.md").write_text(
        "# Example\n", encoding="utf-8"
    )

    findings = check_completeness(manifest, tmp_path)

    assert [finding.path for finding in findings] == ["services/example/README.md"]


def test_completeness_reports_nested_service_readmes(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    nested = tmp_path / "services" / "example" / "provider" / "README.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("# Provider\n", encoding="utf-8")

    findings = check_completeness(manifest, tmp_path)

    assert [finding.path for finding in findings] == [
        "services/example/provider/README.md"
    ]


def test_self_containment_checks_nested_service_readmes(tmp_path: Path) -> None:
    generated = tmp_path / "generated"
    (generated / "site").mkdir(parents=True)
    (generated / "wiki").mkdir()
    nested = tmp_path / "services" / "example" / "provider" / "README.md"
    nested.parent.mkdir(parents=True)
    nested.write_text(
        "[Site](https://thekaveh.github.io/atlas/)", encoding="utf-8"
    )

    findings = check_self_containment(tmp_path, generated)

    assert [(finding.path, finding.surface) for finding in findings] == [
        ("services/example/provider/README.md", "repo")
    ]


def test_placeholders_ignore_internal_plans(tmp_path: Path) -> None:
    (tmp_path / "docs" / "superpowers").mkdir(parents=True)
    (tmp_path / "docs" / "public.md").write_text("TODO publish", encoding="utf-8")
    (tmp_path / "docs" / "superpowers" / "plan.md").write_text("TODO implement", encoding="utf-8")

    findings = check_placeholders(tmp_path)

    assert [finding.path for finding in findings] == ["docs/public.md"]


def test_wiki_link_check_reports_missing_markdown_and_html_targets(tmp_path: Path) -> None:
    wiki = tmp_path / "generated" / "wiki"
    (wiki / "assets").mkdir(parents=True)
    (wiki / "Home.md").write_text(
        "[Good](2.1-Guide)\n"
        '<a href="missing/">Missing HTML</a>\n'
        "![Missing image](assets/missing.png)\n",
        encoding="utf-8",
    )
    (wiki / "2.1-Guide.md").write_text("# Guide\n", encoding="utf-8")

    findings = check_wiki_links(tmp_path, wiki)

    assert [finding.message for finding in findings] == [
        "missing local wiki target: missing/",
        "missing local wiki target: assets/missing.png",
    ]


def test_wiki_link_check_rejects_markdown_page_destinations(tmp_path: Path) -> None:
    wiki = tmp_path / "generated" / "wiki"
    wiki.mkdir(parents=True)
    (wiki / "Home.md").write_text("[Guide](2.1-Guide.md)\n", encoding="utf-8")
    (wiki / "2.1-Guide.md").write_text("# Guide\n", encoding="utf-8")

    findings = check_wiki_links(tmp_path, wiki)

    assert [finding.message for finding in findings] == [
        "wiki page links must be extensionless: 2.1-Guide.md"
    ]


def test_wiki_link_check_rejects_residual_mkdocs_attributes(tmp_path: Path) -> None:
    wiki = tmp_path / "generated" / "wiki"
    wiki.mkdir(parents=True)
    (wiki / "Home.md").write_text(
        "[Guide](2.1-Guide){: .atlas-card__link}.\n", encoding="utf-8"
    )
    (wiki / "2.1-Guide.md").write_text("# Guide\n", encoding="utf-8")

    findings = check_wiki_links(tmp_path, wiki)

    assert [finding.message for finding in findings] == [
        "residual MkDocs attribute list at line 1"
    ]


def test_generated_doc_helper_never_rasterizes_committed_pngs(monkeypatch) -> None:
    calls: list[tuple[str, Path]] = []

    def fake_site(_manifest, _root, destination):
        calls.append(("site", destination))

    def fake_wiki(_manifest, _root, destination):
        calls.append(("wiki", destination))

    def fake_diagrams(
        _manifest,
        _root,
        site_dir,
        _png_dir,
        wiki_dir,
        *,
        check_png,
    ):
        assert check_png is True
        calls.append(("site-diagrams", site_dir))
        calls.append(("wiki-diagrams", wiki_dir))

    three_surface_test_utils._manifest.cache_clear()
    monkeypatch.setattr(three_surface_test_utils, "render_site", fake_site)
    monkeypatch.setattr(three_surface_test_utils, "render_wiki", fake_wiki)
    monkeypatch.setattr(three_surface_test_utils, "render_all", fake_diagrams)
    monkeypatch.setattr(
        three_surface_test_utils,
        "load_manifest",
        lambda *_args: object(),
    )

    three_surface_test_utils.ensure_generated_docs()

    assert [name for name, _path in calls] == [
        "site",
        "wiki",
        "site-diagrams",
        "wiki-diagrams",
    ]
    assert all(
        three_surface_test_utils.PROJECTION_ROOT in path.parents
        for _name, path in calls
    )


def test_manifest_reachability_checks_repo_site_and_wiki_routes(tmp_path: Path) -> None:
    checker = getattr(docs_checks, "check_manifest_reachability", None)
    assert checker is not None
    docs = tmp_path / "docs"
    guide = docs / "guide"
    guide.mkdir(parents=True)
    (docs / "index.md").write_text(
        "# 1. Overview\n\n[escape](../../docs/guide/)\n", encoding="utf-8"
    )
    (guide / "index.md").write_text(
        "# 2. Guide\n\n[cycle](../guide/)\n", encoding="utf-8"
    )
    manifest_path = docs / "manifest.yaml"
    manifest_path.write_text(
        """
surfaces: [repo, site, wiki]
numbering: baked
index: overview
sections:
  - {id: overview, number: "1", title: Overview, source: docs/index.md}
  - {id: guide, number: "2", title: Guide, source: docs/guide/index.md}
diagrams: []
""",
        encoding="utf-8",
    )
    site = tmp_path / "generated" / "site"
    wiki = tmp_path / "generated" / "wiki"
    site.mkdir(parents=True)
    wiki.mkdir(parents=True)
    (site / "index.md").write_text("# 1. Overview\n", encoding="utf-8")
    (wiki / "Home.md").write_text("# 1. Overview\n", encoding="utf-8")
    (wiki / "2-Guide.md").write_text("# 2. Guide\n", encoding="utf-8")
    (wiki / "_Sidebar.md").write_text("- [Overview](Home)\n", encoding="utf-8")
    (tmp_path / "mkdocs.yml").write_text(
        "nav:\n  - Overview: index.md\n", encoding="utf-8"
    )
    manifest = load_manifest(manifest_path, tmp_path)

    findings = checker(manifest, tmp_path, tmp_path / "generated")

    assert [(item.surface, item.path) for item in findings] == [
        ("repo", "docs/guide/index.md"),
        ("site", "generated/site/guide/index.md"),
        ("wiki", "generated/wiki/2-Guide.md"),
    ]

    (docs / "index.md").write_text(
        "# 1. Overview\n\n[Guide](guide/)\n", encoding="utf-8"
    )
    (site / "guide").mkdir()
    (site / "guide" / "index.md").write_text("# 2. Guide\n", encoding="utf-8")
    (wiki / "_Sidebar.md").write_text(
        "- [Overview](Home)\n- [Guide](2-Guide)\n", encoding="utf-8"
    )
    (tmp_path / "mkdocs.yml").write_text(
        "nav:\n  - Overview: index.md\n  - Guide: guide/index.md\n",
        encoding="utf-8",
    )

    assert checker(manifest, tmp_path, tmp_path / "generated") == []

    (site / "guide" / "index.md").unlink()
    (site / "guide" / "index.md").symlink_to(docs / "guide" / "index.md")
    (wiki / "2-Guide.md").unlink()
    (wiki / "2-Guide.md").symlink_to(docs / "guide" / "index.md")

    assert [(item.surface, item.path) for item in checker(
        manifest, tmp_path, tmp_path / "generated"
    )] == [
        ("site", "generated/site/guide/index.md"),
        ("wiki", "generated/wiki/2-Guide.md"),
    ]


@pytest.mark.parametrize(
    ("edge", "reaches_guide"),
    [
        ("<!-- [Guide](guide.md) -->", False),
        ('<!-- <a href="guide.md">Guide</a> -->', False),
        ("`[Guide](guide.md)`", False),
        ("    [Guide](guide.md)", False),
        ("```text\n[Guide](guide.md)\n```", False),
        ("~~~text\n[Guide](guide.md)\n~~~", False),
        ("[Guide][guide]\n\n[guide]: guide.md", True),
        ("[Guide][]\n\n[guide]: guide.md", True),
        ("[Guide]\n\n[guide]: guide.md", True),
        ("[Guide][guide]\n\n<!-- [guide]: guide.md -->", False),
        ("[Guide][guide]\n\n`[guide]: guide.md`", False),
        ("[Guide][guide]\n\n```text\n[guide]: guide.md\n```", False),
        ("[Guide][guide]\n\n~~~text\n[guide]: guide.md\n~~~", False),
        ("[Guide][guide]\n\n[guide]: guide.md\n[guide]: missing.md", True),
        ("[Guide][guide]\n\n[guide]: missing.md\n[guide]: guide.md", False),
        ("[Guide](guide.md)", True),
        (r"\[Guide](guide.md)", False),
        ("[Guide](guide.md?mode=full#usage)", True),
        ("[Fragment](#usage)", False),
        ("[Escape](..%2F..%2Fdocs%2Fguide.md)", False),
        ('<a href="guide.md">Guide</a>', True),
        ('<a href="guide.md?mode=full#usage">Guide</a>', True),
        ('<a href="guide.md?one=1&amp;two=2#usage">Guide</a>', True),
        ('<script><a href="guide.md">Guide</a></script>', False),
        ("![Guide](guide.md)", False),
        ('<img src="guide.md" alt="Guide">', False),
        ("<https://github.com/thekaveh/atlas/blob/main/docs/guide.md>", False),
    ],
    ids=[
        "html-comment",
        "html-comment-anchor",
        "inline-code",
        "indented-code",
        "backtick-fence",
        "tilde-fence",
        "reference-link",
        "collapsed-reference-link",
        "shortcut-reference-link",
        "comment-reference-definition",
        "inline-code-reference-definition",
        "backtick-fence-reference-definition",
        "tilde-fence-reference-definition",
        "duplicate-reference-first-valid",
        "duplicate-reference-first-missing",
        "inline-link",
        "escaped-inline-link",
        "inline-link-query-fragment",
        "fragment-only",
        "encoded-path-escape",
        "html-anchor",
        "html-anchor-query-fragment",
        "html-anchor-entity-decoding",
        "script-anchor",
        "image",
        "html-image",
        "external-autolink",
    ],
)
def test_manifest_reachability_uses_rendered_link_semantics(
    tmp_path: Path,
    edge: str,
    reaches_guide: bool,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text(f"# 1. Overview\n\n{edge}\n", encoding="utf-8")
    (docs / "guide.md").write_text("# 2. Guide\n", encoding="utf-8")
    manifest_path = docs / "manifest.yaml"
    manifest_path.write_text(
        """
surfaces: [repo, site, wiki]
numbering: baked
index: overview
sections:
  - {id: overview, number: "1", title: Overview, source: docs/index.md}
  - {id: guide, number: "2", title: Guide, source: docs/guide.md}
diagrams: []
""",
        encoding="utf-8",
    )
    site = tmp_path / "generated" / "site"
    wiki = tmp_path / "generated" / "wiki"
    site.mkdir(parents=True)
    wiki.mkdir(parents=True)
    (site / "index.md").write_text("# 1. Overview\n", encoding="utf-8")
    (site / "guide.md").write_text("# 2. Guide\n", encoding="utf-8")
    (wiki / "Home.md").write_text("# 1. Overview\n", encoding="utf-8")
    (wiki / "2-Guide.md").write_text("# 2. Guide\n", encoding="utf-8")
    (wiki / "_Sidebar.md").write_text(
        "- [Overview](Home)\n- [Guide](2-Guide)\n", encoding="utf-8"
    )
    (tmp_path / "mkdocs.yml").write_text(
        "nav:\n  - Overview: index.md\n  - Guide: guide.md\n",
        encoding="utf-8",
    )
    manifest = load_manifest(manifest_path, tmp_path)

    findings = docs_checks.check_manifest_reachability(
        manifest, tmp_path, tmp_path / "generated"
    )
    repo_orphans = [item.path for item in findings if item.surface == "repo"]

    assert repo_orphans == ([] if reaches_guide else ["docs/guide.md"])
