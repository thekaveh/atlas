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


def test_capability_section_exception_set_is_closed_and_repository_grounded():
    from docs.capabilities_resolver import (
        CAPABILITY_SECTION_EXCEPTIONS,
        capability_section_enabled,
        is_aggregate_capability_doc,
    )

    services_dir = Path(__file__).resolve().parents[2] / "services"
    readme_only_folders = {
        folder.name
        for folder in services_dir.iterdir()
        if folder.is_dir()
        and (folder / "README.md").is_file()
        and not (folder / "service.yml").exists()
    }

    assert CAPABILITY_SECTION_EXCEPTIONS == frozenset({"multi2vec-clip"})
    assert readme_only_folders == {
        "doc-processor",
        "stt-provider",
        "multi2vec-clip",
    }
    assert capability_section_enabled("doc-processor")
    assert capability_section_enabled("stt-provider")
    assert not capability_section_enabled("multi2vec-clip")
    assert capability_section_enabled("tts-provider")
    assert is_aggregate_capability_doc("tts-provider")


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


def test_prefixed_user_heading_is_preserved_and_does_not_count_as_generated():
    from docs.capabilities_section_writer import upsert_capabilities_section

    readme = (
        "# Service\n\n"
        "## 2. Capabilities & limitations by deployment mode\n\n"
        "USER AUTHORED MATRIX\n\n"
        "## 7. References\n\nLinks.\n"
    )

    updated = upsert_capabilities_section(readme, (_ROW,), aggregate=False)

    assert updated.startswith(readme.rstrip() + "\n\n")
    assert "USER AUTHORED MATRIX" in updated
    assert "## 8. Capabilities & limitations\n" in updated


def test_canonical_heading_match_does_not_cross_lines():
    from docs.capabilities_section_writer import upsert_capabilities_section

    readme = (
        "# Service\n\n"
        "## 2. Capabilities\n"
        "& limitations\n\n"
        "USER AUTHORED PROSE\n\n"
        "## 7. References\n\nLinks.\n"
    )

    updated = upsert_capabilities_section(readme, (_ROW,), aggregate=False)

    assert updated.startswith(readme.rstrip() + "\n\n")
    assert "USER AUTHORED PROSE" in updated
    assert "## 8. Capabilities & limitations\n" in updated


def test_complete_canonical_heading_allows_closing_hashes_and_horizontal_space():
    from docs.capabilities_section_writer import upsert_capabilities_section

    readme = (
        "# Service\n\n"
        "## 5. Capabilities & limitations ### \t\n\nSTALE\n\n"
        "## 6. References\n\nKEEP\n"
    )

    updated = upsert_capabilities_section(readme, (_ROW,), aggregate=False)

    assert "STALE" not in updated
    assert "## 5. Capabilities & limitations\n" in updated
    assert "## 6. References\n\nKEEP" in updated


def test_duplicate_real_canonical_sections_fail_clearly():
    from docs.capabilities_section_writer import (
        CapabilitySectionError,
        upsert_capabilities_section,
    )

    readme = (
        "# Service\n\n"
        "## 2. Capabilities & limitations\n\nCURRENT\n\n"
        "## 3. Capabilities & limitations\n\nSTALE DUPLICATE\n"
    )

    with pytest.raises(CapabilitySectionError, match="multiple.*canonical"):
        upsert_capabilities_section(readme, (_ROW,), aggregate=False)


def test_check_mode_fails_on_duplicate_canonical_sections(
    tmp_path: Path,
    capsys,
):
    from docs.regen import main

    service_dir = tmp_path / "comfyui"
    service_dir.mkdir()
    readme = service_dir / "README.md"
    original = (
        "# ComfyUI\n\n"
        "## 2. Capabilities & limitations\n\nCURRENT\n\n"
        "## 3. Capabilities & limitations\n\nSTALE DUPLICATE\n"
    )
    readme.write_text(original, encoding="utf-8")

    result = main(
        [
            "comfyui",
            "--out-root",
            str(tmp_path),
            "--section-only",
            "--check",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "multiple canonical capability sections" in captured.err
    assert readme.read_text(encoding="utf-8") == original


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


@pytest.mark.parametrize("indent", ["", " ", "  ", "   "])
def test_root_fences_with_zero_to_three_spaces_hide_example_headings(indent):
    from docs.capabilities_section_writer import upsert_capabilities_section

    readme = (
        "# Service\n\n"
        "## 1. Overview\n\n"
        f"{indent}```markdown\n"
        "## 99. Capabilities & limitations\n"
        f"{indent}```\n\n"
        "## 7. References\n\nKEEP\n"
    )

    updated = upsert_capabilities_section(readme, (_ROW,), aggregate=False)

    assert updated.startswith(readme.rstrip() + "\n\n")
    assert updated.endswith("Atlas exercises the configured generation path. |\n")
    assert updated.count("## 8. Capabilities & limitations") == 1


def test_four_space_root_pseudo_opener_is_indented_code_and_numbering_stays_exact():
    from docs.capabilities_section_writer import render_capabilities_section
    from docs.capabilities_section_writer import upsert_capabilities_section

    readme = (
        "# Service\n\n"
        "## 1. Overview\n\n"
        "    ```\n\n"
        "## 7. References\n\nKEEP THIS TAIL\n"
    )

    updated = upsert_capabilities_section(readme, (_ROW,), aggregate=False)

    assert updated == (
        readme.rstrip()
        + "\n\n"
        + render_capabilities_section((_ROW,), position=8, aggregate=False)
    )


def test_four_space_pseudo_closer_inside_real_fence_preserves_exact_tail():
    from docs.capabilities_section_writer import render_capabilities_section
    from docs.capabilities_section_writer import upsert_capabilities_section

    fenced_prefix = (
        "# Service\n\n"
        "```markdown\n"
        "literal marker follows\n"
        "    ```\n"
        "## 99. Capabilities & limitations\n"
        "```\n\n"
    )
    readme = (
        fenced_prefix
        + "## 5. Capabilities & limitations\n\nREAL STALE\n\n"
        + "## 6. Troubleshooting\n\nKEEP THIS BODY\n"
    )

    updated = upsert_capabilities_section(readme, (_ROW,), aggregate=False)

    assert updated == (
        fenced_prefix
        + render_capabilities_section((_ROW,), position=5, aggregate=False).rstrip()
        + "\n\n## 6. Troubleshooting\n\nKEEP THIS BODY\n"
    )


def test_list_context_fence_hides_example_heading_and_preserves_numbering():
    from docs.capabilities_section_writer import upsert_capabilities_section

    readme = (
        "# Service\n\n"
        "## 1. Overview\n\n"
        "1. Example\n"
        "    ```markdown\n"
        "    ## 99. Capabilities & limitations\n"
        "    ```\n\n"
        "## 7. References\n\nKEEP\n"
    )

    updated = upsert_capabilities_section(readme, (_ROW,), aggregate=False)

    assert updated.startswith(readme.rstrip() + "\n\n")
    assert "## 8. Capabilities & limitations\n" in updated


def test_regen_and_capability_writer_share_the_same_fence_scanner():
    from docs import capabilities_section_writer, regen
    from docs.markdown_blocks import fenced_code_spans

    assert capabilities_section_writer._fenced_spans is fenced_code_spans
    assert regen._fenced_spans is fenced_code_spans


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


def test_all_loads_one_manifest_snapshot_for_graphs_and_capabilities(
    tmp_path: Path,
    monkeypatch,
):
    import docs.deps_resolver as deps_resolver
    import docs.regen as regen

    real_load_manifests = regen.load_manifests
    load_calls = 0

    def counted_load_manifests(services_dir):
        nonlocal load_calls
        load_calls += 1
        return real_load_manifests(services_dir)

    def unexpected_graph_reload(_services_dir):
        raise AssertionError("graph path reloaded manifests instead of reusing the snapshot")

    monkeypatch.setattr(regen, "load_manifests", counted_load_manifests)
    monkeypatch.setattr(deps_resolver, "load_manifests", unexpected_graph_reload)

    result = regen.main(
        [
            "--all",
            "--out-root",
            str(tmp_path),
            "--section-only",
            "--dry-run",
        ]
    )

    assert result == 0
    assert load_calls == 1
