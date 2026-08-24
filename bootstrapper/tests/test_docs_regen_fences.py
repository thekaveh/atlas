"""Capability-section upsert and regen integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from docs.capabilities_resolver import CapabilityRow


_ROW = CapabilityRow(
    service="comfyui",
    capability="Image generation",
    status="supported",
    verification="tested",
    notes="Atlas exercises the configured generation path.",
)


def test_section_only_help_names_all_generated_readme_sections(capsys):
    from docs.regen import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    assert "generated README sections" in capsys.readouterr().out


def test_first_insertion_uses_one_more_than_highest_real_numbered_heading():
    from docs.capabilities_section_writer import upsert_capabilities_section

    readme = (
        "# Service\n\n"
        "## 1. Overview\n\nBody.\n\n"
        "```markdown\n## 99. Example only\n```\n\n"
        "## 7. References\n\nLinks.\n"
    )

    updated = upsert_capabilities_section(readme, (_ROW,), aggregate=False)

    assert updated.startswith(readme.rstrip())
    assert updated.endswith(
        "## 8. Capabilities & limitations\n\n"
        "| Capability | Status | Verification | Notes |\n"
        "|---|---|---|---|\n"
        "| Image generation | supported | tested | "
        "Atlas exercises the configured generation path. |\n"
    )
    assert "## 100. Capabilities" not in updated


def test_existing_section_is_replaced_in_place_and_preserves_its_number():
    from docs.capabilities_section_writer import upsert_capabilities_section

    readme = (
        "# Service\n\n"
        "## 1. Overview\n\nBody.\n\n"
        "## 6. Capabilities & limitations\n\nSTALE\n\n"
        "## 7. Troubleshooting\n\nKEEP THIS BODY\n"
    )

    updated = upsert_capabilities_section(readme, (_ROW,), aggregate=False)

    assert "STALE" not in updated
    assert updated.index("## 6. Capabilities") < updated.index("## 7. Troubleshooting")
    assert "## 6. Capabilities & limitations" in updated
    assert "KEEP THIS BODY" in updated


def test_fenced_lookalike_heading_is_ignored_during_replacement():
    from docs.capabilities_section_writer import upsert_capabilities_section

    readme = (
        "# Service\n\n"
        "~~~markdown\n"
        "## 4. Capabilities & limitations\n\n"
        "EXAMPLE MUST SURVIVE\n"
        "~~~\n\n"
        "## 5. Capabilities & limitations\n\n"
        "REAL STALE SECTION\n\n"
        "## 6. References\n\nKEEP\n"
    )

    updated = upsert_capabilities_section(readme, (_ROW,), aggregate=False)

    assert "EXAMPLE MUST SURVIVE" in updated
    assert "REAL STALE SECTION" not in updated
    assert updated.count("## 4. Capabilities & limitations") == 1
    assert updated.count("## 5. Capabilities & limitations") == 1
    assert "## 6. References\n\nKEEP" in updated


def test_second_upsert_is_byte_identical():
    from docs.capabilities_section_writer import upsert_capabilities_section

    readme = "# Service\n\n## 1. Overview\n\nBody.\n"
    first = upsert_capabilities_section(readme, (_ROW,), aggregate=False)
    second = upsert_capabilities_section(first, (_ROW,), aggregate=False)

    assert second == first


def test_regen_adds_capabilities_for_supported_docs_and_is_idempotent(tmp_path: Path):
    from docs.regen import _process

    service_dir = tmp_path / "comfyui"
    service_dir.mkdir()
    readme = service_dir / "README.md"
    readme.write_text("# ComfyUI\n\n## 1. Overview\n\nBody.\n", encoding="utf-8")

    assert _process("comfyui", tmp_path, False, True, False) == 0
    first = readme.read_bytes()
    assert b"Capabilities & limitations" in first

    assert _process("comfyui", tmp_path, False, True, False) == 0
    assert readme.read_bytes() == first


def test_regen_explicitly_skips_only_multi2vec_clip_pointer(tmp_path: Path):
    from docs.regen import _process

    service_dir = tmp_path / "multi2vec-clip"
    service_dir.mkdir()
    readme = service_dir / "README.md"
    readme.write_text("# Multi2Vec CLIP\n\n## 1. Overview\n\nPointer.\n", encoding="utf-8")

    assert _process("multi2vec-clip", tmp_path, False, True, False) == 0

    rendered = readme.read_text(encoding="utf-8")
    assert "Dependencies & Integrations" in rendered
    assert "Capabilities & limitations" not in rendered
