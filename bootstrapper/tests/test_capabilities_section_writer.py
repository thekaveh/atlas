"""Tests for capability-contract resolution and Markdown rendering."""

from __future__ import annotations

from services.manifests import Capability, Manifest


def _capability(
    name: str,
    *,
    status: str = "supported",
    verification: str = "tested",
    note: str = "Atlas exercises this contract.",
) -> Capability:
    return Capability(
        name=name,
        status=status,
        verification=verification,
        note=note,
    )


def _manifest(name: str, *capabilities: Capability) -> Manifest:
    return Manifest(
        name=name,
        label=name,
        category="llm",
        env=[],
        capabilities=list(capabilities),
    )


def test_singleton_resolver_preserves_manifest_declaration_order():
    from docs.capabilities_resolver import resolve_capability_rows

    manifests = [
        _manifest(
            "comfyui",
            _capability("Primary generation"),
            _capability(
                "Output upload",
                status="stubbed",
                verification="documented",
                note="Atlas exposes placeholders only.",
            ),
        )
    ]

    rows = resolve_capability_rows("comfyui", manifests)

    assert [row.capability for row in rows] == [
        "Primary generation",
        "Output upload",
    ]
    assert [row.service for row in rows] == ["comfyui", "comfyui"]
    assert rows[1].status == "stubbed"
    assert rows[1].verification == "documented"
    assert rows[1].notes == "Atlas exposes placeholders only."


def test_aggregate_resolver_preserves_member_order_and_removes_duplicate_members(
    monkeypatch,
):
    import docs.capabilities_resolver as resolver

    monkeypatch.setattr(
        resolver,
        "doc_folder_to_manifests",
        lambda _doc_name: ("speaches", "parakeet", "speaches"),
    )
    manifests = [
        _manifest("parakeet", _capability("Transcription")),
        _manifest(
            "speaches",
            _capability("Speech synthesis"),
            _capability("Speech recognition", status="partial"),
        ),
    ]

    rows = resolver.resolve_capability_rows("stt-provider", manifests)

    assert [(row.service, row.capability) for row in rows] == [
        ("speaches", "Speech synthesis"),
        ("speaches", "Speech recognition"),
        ("parakeet", "Transcription"),
    ]


def test_resolver_returns_no_rows_for_an_empty_or_pointer_contract():
    from docs.capabilities_resolver import resolve_capability_rows

    assert resolve_capability_rows("redis", [_manifest("redis")]) == ()
    assert resolve_capability_rows("multi2vec-clip", [_manifest("weaviate")]) == ()


def test_singleton_table_omits_service_column_and_escapes_markdown_cells():
    from docs.capabilities_resolver import CapabilityRow
    from docs.capabilities_section_writer import render_capabilities_section

    rows = (
        CapabilityRow(
            service="comfyui",
            capability="Images | video",
            status="partial",
            verification="documented",
            notes=r"Use C:\models | host models.",
        ),
    )

    rendered = render_capabilities_section(rows, position=8, aggregate=False)

    assert rendered == (
        "## 8. Capabilities & limitations\n\n"
        "| Capability | Status | Verification | Notes |\n"
        "|---|---|---|---|\n"
        r"| Images \| video | partial | documented | Use C:\\models \| host models. |"
        "\n"
    )
    assert "| Service |" not in rendered


def test_aggregate_table_includes_service_column_even_for_one_member():
    from docs.capabilities_resolver import CapabilityRow
    from docs.capabilities_section_writer import render_capabilities_section

    rows = (
        CapabilityRow(
            service="docling",
            capability="Document extraction",
            status="supported",
            verification="tested",
            notes="Atlas routes supported documents through Docling.",
        ),
    )

    rendered = render_capabilities_section(rows, position=6, aggregate=True)

    assert "| Service | Capability | Status | Verification | Notes |" in rendered
    assert "| docling | Document extraction | supported | tested |" in rendered


def test_empty_contract_renders_an_explicit_placeholder():
    from docs.capabilities_section_writer import render_capabilities_section

    assert render_capabilities_section((), position=4, aggregate=False) == (
        "## 4. Capabilities & limitations\n\n"
        "_No capability contract declared._\n"
    )
