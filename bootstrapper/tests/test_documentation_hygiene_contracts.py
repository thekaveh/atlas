from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_public_documentation_map_excludes_publication_mechanics() -> None:
    text = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    for forbidden in (
        "thekaveh.github.io",
        "gh repo edit",
        "Maintainer checks",
        "Maintainer publication",
    ):
        assert forbidden not in text


def test_current_docs_do_not_reference_retired_external_locations() -> None:
    paths = [
        ROOT / "README.md",
        ROOT / "docs/README.md",
        ROOT / "docs/ROADMAP.md",
        *sorted((ROOT / "docs/deployment").glob("*.md")),
        *sorted((ROOT / "docs/quick-start").glob("*.md")),
        *sorted((ROOT / "services").glob("*/README.md")),
        ROOT / "services/docling/provider/localhost/README.md",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for retired in (
        "docs.searxng.org/admin/searx.botdetection.html",
        "ds4sd.github.io/docling",
        "neo4j.com/docs/graph-data-modeling",
        "Hunyuan3D-2/blob/9cd649ba6913f7a852e3286bad86bfa9a2d83dcf/LICENSE.txt",
        "github.com/anthropics/claude-code/tree/main/skills/architecture-diagram",
        "github.com/thekaveh/tableau",
    ):
        assert retired not in text


def test_hand_maintained_docs_do_not_duplicate_dynamic_service_counts() -> None:
    roadmap = (ROOT / "docs/ROADMAP.md").read_text(encoding="utf-8")
    service_readme = (ROOT / "services/README.md").read_text(encoding="utf-8")
    assert "53 service families" not in roadmap
    assert "53 manifests" not in service_readme


def test_populated_research_catalogs_have_no_placeholder_files() -> None:
    for directory in (
        ROOT / "docs/research/candidates",
        ROOT / "docs/research/rows",
    ):
        assert any(path.suffix == ".md" for path in directory.iterdir())
        assert not (directory / ".gitkeep").exists()

