from pathlib import Path

from scripts.docs.check_docs import (
    check_completeness,
    check_placeholders,
    check_self_containment,
)
from scripts.docs.manifest import load_manifest


def _manifest(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.md").write_text("# Overview\n", encoding="utf-8")
    path = tmp_path / "docs" / "manifest.yaml"
    path.write_text(
        """
surfaces: [repo, site, wiki]
numbering: baked
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


def test_placeholders_ignore_internal_plans(tmp_path: Path) -> None:
    (tmp_path / "docs" / "superpowers").mkdir(parents=True)
    (tmp_path / "docs" / "public.md").write_text("TODO publish", encoding="utf-8")
    (tmp_path / "docs" / "superpowers" / "plan.md").write_text("TODO implement", encoding="utf-8")

    findings = check_placeholders(tmp_path)

    assert [finding.path for finding in findings] == ["docs/public.md"]
