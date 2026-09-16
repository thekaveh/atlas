"""Mutation tests for the critical-page completeness contract (#1052)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.docs import check_docs
from scripts.docs.critical_pages import (
    ContractError,
    SurfaceRoots,
    check_critical_pages,
    load_contract,
    parse_contract,
    section_has_body,
)
from scripts.docs.manifest import load_manifest
from tests.three_surface_test_utils import PROJECTION_ROOT, ensure_generated_docs


ROOT = Path(__file__).resolve().parents[2]

_MANIFEST = """
surfaces: [repo, site, wiki]
numbering: baked
index: overview
sections:
  - {id: overview, number: "1", title: Overview, source: docs/index.md}
  - {id: policy, number: "2", title: Policy, source: docs/policy.md}
diagrams: []
"""
_CONTRACT = """
pages:
  - id: policy
    role: security
    required_sections: [Reporting]
external_references:
  - https://github.com/example/project/issues
"""
_POLICY = "# 2. Policy\n\n## 1. Reporting\n\nOpen a private advisory.\n"


def _fixture(tmp_path: Path, *, index_links_policy: bool = True) -> tuple:
    docs = tmp_path / "docs"
    docs.mkdir()
    link = "[Policy](policy.md)\n" if index_links_policy else "Policy\n"
    (docs / "index.md").write_text(f"# 1. Overview\n\n{link}", encoding="utf-8")
    (docs / "policy.md").write_text(_POLICY, encoding="utf-8")
    (docs / "manifest.yaml").write_text(_MANIFEST, encoding="utf-8")
    (docs / "critical-pages.yaml").write_text(_CONTRACT, encoding="utf-8")
    site = tmp_path / "generated" / "site"
    wiki = tmp_path / "generated" / "wiki"
    site.mkdir(parents=True)
    wiki.mkdir(parents=True)
    (site / "index.md").write_text("# 1. Overview\n", encoding="utf-8")
    (site / "policy.md").write_text(_POLICY, encoding="utf-8")
    (wiki / "Home.md").write_text("# 1. Overview\n", encoding="utf-8")
    (wiki / "2-Policy.md").write_text(_POLICY, encoding="utf-8")
    manifest = load_manifest(docs / "manifest.yaml", tmp_path)
    contract = load_contract(docs / "critical-pages.yaml")
    return manifest, contract


def _run(tmp_path: Path, manifest, contract) -> list[tuple[str, str]]:
    reachable = check_docs.reachable_sources(manifest, tmp_path)
    findings = check_critical_pages(
        contract, manifest, reachable, SurfaceRoots(tmp_path, tmp_path / "generated")
    )
    return [(item.surface, item.message) for item in findings]


def test_contract_passes_when_every_surface_carries_the_section(tmp_path: Path) -> None:
    manifest, contract = _fixture(tmp_path)

    assert _run(tmp_path, manifest, contract) == []


def test_removing_the_page_from_the_manifest_fails(tmp_path: Path) -> None:
    manifest, contract = _fixture(tmp_path)
    (tmp_path / "docs" / "manifest.yaml").write_text(
        _MANIFEST.replace('  - {id: policy, number: "2", title: Policy, source: docs/policy.md}\n', ""),
        encoding="utf-8",
    )
    manifest = load_manifest(tmp_path / "docs" / "manifest.yaml", tmp_path)

    findings = _run(tmp_path, manifest, contract)

    assert findings == [("repo", "critical security page 'policy' is not declared in docs/manifest.yaml")]


def test_stripping_the_only_navigation_link_fails(tmp_path: Path) -> None:
    manifest, contract = _fixture(tmp_path, index_links_policy=False)

    findings = _run(tmp_path, manifest, contract)

    assert ("repo", "critical security page is not reachable from the documentation index") in findings


@pytest.mark.parametrize("surface, filename", [("site", "policy.md"), ("wiki", "2-Policy.md")])
def test_a_surface_rendering_only_the_title_fails(tmp_path: Path, surface: str, filename: str) -> None:
    manifest, contract = _fixture(tmp_path)
    (tmp_path / "generated" / surface / filename).write_text(
        "# 2. Policy\n\n## 1. Reporting\n\n## 2. Other\n\nbody\n", encoding="utf-8"
    )

    findings = _run(tmp_path, manifest, contract)

    assert findings == [
        (surface, "critical security page lacks a substantive section 'Reporting'")
    ]


def test_a_missing_generated_page_fails(tmp_path: Path) -> None:
    manifest, contract = _fixture(tmp_path)
    (tmp_path / "generated" / "wiki" / "2-Policy.md").unlink()

    assert _run(tmp_path, manifest, contract) == [("wiki", "critical security page is missing")]


def test_section_detection_ignores_numbering_and_requires_a_body() -> None:
    assert section_has_body("## 3. Getting help\n\nOpen an issue.\n", "Getting help")
    assert section_has_body("### Getting help\ntext\n", "Getting help")
    assert not section_has_body("## 3. Getting help\n\n## 4. Next\ntext\n", "Getting help")
    assert not section_has_body("## 3. Getting help\n", "Getting help")
    assert not section_has_body("Getting help is easy\n", "Getting help")


def test_contract_rejects_urls_and_paths_as_pages() -> None:
    for bad in ("https://github.com/thekaveh/atlas/issues", "docs/policy.md"):
        with pytest.raises(ContractError, match="self-contained manifest page"):
            parse_contract(
                f"pages:\n  - id: {bad}\n    role: support\n    required_sections: [Help]\n"
            )


def test_contract_rejects_documentation_surfaces_as_external_references() -> None:
    for url in (
        "https://thekaveh.github.io/atlas/security-policy/",
        "https://github.com/thekaveh/atlas/wiki/9.8-Security-Policy",
        "https://github.com/thekaveh/atlas/blob/main/SECURITY.md",
    ):
        with pytest.raises(ContractError, match="documentation surface"):
            parse_contract(
                "pages:\n  - id: policy\n    role: security\n    required_sections: [Reporting]\n"
                f"external_references:\n  - {url}\n"
            )
    with pytest.raises(ContractError, match="absolute http"):
        parse_contract(
            "pages:\n  - id: policy\n    role: security\n    required_sections: [Reporting]\n"
            "external_references:\n  - issues\n"
        )
    # Community destinations under the repository host are permitted.
    contract = parse_contract(
        "pages:\n  - id: policy\n    role: security\n    required_sections: [Reporting]\n"
        "external_references:\n  - https://github.com/thekaveh/atlas/issues\n"
        "  - https://github.com/thekaveh/atlas/security/advisories\n"
    )
    assert len(contract.external_references) == 2


def test_contract_rejects_duplicate_ids_and_empty_sections() -> None:
    with pytest.raises(ContractError, match="Duplicate"):
        parse_contract(
            "pages:\n  - {id: a, role: x, required_sections: [S]}\n"
            "  - {id: a, role: y, required_sections: [S]}\n"
        )
    with pytest.raises(ContractError, match="required_sections"):
        parse_contract("pages:\n  - {id: a, role: x, required_sections: []}\n")


def test_live_contract_holds_on_every_surface() -> None:
    """The committed contract passes against the real manifest and projections."""
    manifest = ensure_generated_docs()
    contract = load_contract(ROOT / "docs" / "critical-pages.yaml")
    roles = {page.role for page in contract.pages}

    assert {"security", "prerequisites", "support", "release", "recovery"} <= roles
    reachable = check_docs.reachable_sources(manifest, ROOT)
    findings = check_critical_pages(
        contract, manifest, reachable, SurfaceRoots(ROOT, PROJECTION_ROOT)
    )
    assert findings == []


def test_docs_gate_runs_the_critical_page_contract(tmp_path: Path, monkeypatch) -> None:
    """`check_docs.check` must surface contract findings, not only its own."""
    calls: list[str] = []

    def fake_check(*args, **kwargs):
        calls.append("docs/critical-pages.yaml")
        return [check_docs.Finding("error", calls[-1], "critical page contract broken")]

    monkeypatch.setattr(check_docs, "check_critical_pages", fake_check)
    monkeypatch.setattr(check_docs, "load_contract", lambda path: object())
    monkeypatch.setattr(check_docs, "build", lambda *args, **kwargs: None)
    monkeypatch.setattr(check_docs, "sync_canonical_references", lambda root, check: [])
    monkeypatch.setattr(check_docs, "check_self_containment", lambda *a, **k: [])
    monkeypatch.setattr(check_docs, "check_wiki_links", lambda *a, **k: [])
    monkeypatch.setattr(check_docs, "check_manifest_reachability", lambda *a, **k: [])
    monkeypatch.setattr(check_docs, "check_completeness", lambda *a, **k: [])
    monkeypatch.setattr(check_docs, "check_placeholders", lambda *a, **k: [])
    monkeypatch.setattr(check_docs, "load_manifest", lambda path, root: _fixture_manifest(tmp_path))

    findings = check_docs.check(tmp_path, tmp_path / "docs" / "manifest.yaml")

    assert calls == ["docs/critical-pages.yaml"]
    assert [item.message for item in findings] == ["critical page contract broken"]


def _fixture_manifest(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "index.md").write_text("# 1. Overview\n", encoding="utf-8")
    (docs / "manifest.yaml").write_text(
        "surfaces: [repo, site, wiki]\nnumbering: baked\nindex: overview\n"
        "sections:\n  - {id: overview, number: '1', title: Overview, source: docs/index.md}\n"
        "diagrams: []\n",
        encoding="utf-8",
    )
    return load_manifest(docs / "manifest.yaml", tmp_path)
