"""Support tiers and qualification evidence for every advertised integration (#1050)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from docs.capabilities_resolver import CapabilityRow, SupportLine, resolve_support_lines
from docs.capabilities_section_writer import render_capabilities_section
from docs.sitegen.model import load_docs_model
from docs.sitegen.services import service_pages
from services.manifest_validator import validate_manifests
from services.manifests import SUPPORT_TIERS, Support, load_manifests
from wizard.model.service_discovery import ServiceInfo
from ui.textual.integration import _support_subtitle


ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"
RELEASING = ROOT / "docs" / "operations" / "releasing.md"


def _release_tags() -> set[str]:
    text = RELEASING.read_text(encoding="utf-8")
    record = text.split("<!-- atlas-release-record:start -->", 1)[1].split("<!-- atlas-release-record:end -->", 1)[0]
    return set(re.findall(r"`(v\d+\.\d+\.\d+)`", record))


def _support(**overrides) -> dict:
    block = {
        "tier": "experimental",
        "evidence": "Capability contract declared; no qualification run yet",
        "evidence_revision": "v0.1.0",
    }
    block.update(overrides)
    return block


def test_schema_declares_the_support_block() -> None:
    schema = json.loads((ROOT / "bootstrapper/schemas/service.schema.json").read_text(encoding="utf-8"))
    support = schema["properties"]["support"]

    assert support["required"] == ["tier", "evidence", "evidence_revision"]
    assert tuple(support["properties"]["tier"]["enum"]) == SUPPORT_TIERS
    assert support["additionalProperties"] is False
    assert "support" not in schema["required"]  # presence is enforced repo-wide below, not on fixtures


def test_manifest_parses_the_support_block(services_root, write_manifest, minimal_manifest_dict) -> None:
    data = minimal_manifest_dict("redis")
    data["support"] = _support(owner="Atlas maintainers", limitations=["No cold-cache validation"])
    write_manifest("redis", data)

    manifest = load_manifests(services_root)[0]

    assert manifest.support == Support(
        tier="experimental",
        evidence="Capability contract declared; no qualification run yet",
        evidence_revision="v0.1.0",
        owner="Atlas maintainers",
        limitations=("No cold-cache validation",),
        declared=True,
    )


def test_manifest_without_support_defaults_to_an_undeclared_experimental_tier(
    services_root, write_manifest, minimal_manifest_dict
) -> None:
    write_manifest("redis", minimal_manifest_dict("redis"))
    manifest = load_manifests(services_root)[0]

    assert manifest.support.tier == "experimental"
    assert manifest.support.declared is False


@pytest.mark.parametrize(
    "bad",
    [
        {"tier": "gold"},
        {"evidence_revision": "main"},
        {"evidence_revision": "abc123"},
        {"evidence": "tbd"},
        {"limitations": ["line one\nline two"]},
        {"extra": "field"},
    ],
)
def test_schema_rejects_malformed_support_blocks(bad, services_root, write_manifest, minimal_manifest_dict) -> None:
    data = minimal_manifest_dict("redis")
    data["support"] = _support(**bad)
    write_manifest("redis", data)

    with pytest.raises(Exception):
        load_manifests(services_root)


def test_validator_rejects_a_stable_tier_without_release_evidence(
    services_root, write_manifest, minimal_manifest_dict
) -> None:
    data = minimal_manifest_dict("redis")
    data["support"] = _support(tier="stable", evidence_revision="0" * 40)
    write_manifest("redis", data)

    issues = validate_manifests(list(load_manifests(services_root)))

    assert [issue.kind for issue in issues if issue.manifest == "redis"] == [
        "support_stable_without_release_evidence"
    ]

    data["support"] = _support(tier="stable", evidence_revision="v0.1.0", evidence="Qualified cold-start run in #1038")
    write_manifest("redis", data)
    issues = validate_manifests(list(load_manifests(services_root)))
    assert not [issue for issue in issues if issue.kind.startswith("support_")]


def test_every_manifest_declares_a_support_tier_and_promotions_cite_release_evidence() -> None:
    """Every advertised integration has an explicit tier; stable ones cite a recorded release."""
    tags = _release_tags()
    assert tags, "the release record must list at least one tag"
    for manifest in load_manifests(SERVICES):
        assert manifest.support.declared, f"{manifest.name} declares no support: block"
        assert manifest.support.tier in SUPPORT_TIERS, manifest.name
        if manifest.support.tier == "stable":
            assert manifest.support.evidence_revision in tags, (
                f"{manifest.name} is stable but its evidence revision "
                f"{manifest.support.evidence_revision} is not a recorded release"
            )


def test_readme_capability_section_states_the_support_tier() -> None:
    rows = (CapabilityRow("speaches", "TTS", "partial", "tested", "needs model download"),)
    support = (
        SupportLine("speaches", "experimental", "Contract declared (#967); no cold-cache run", "v0.1.0", ("Preload list empty",)),
    )

    singleton = render_capabilities_section(rows, position=7, aggregate=False, support=support)
    aggregate = render_capabilities_section(rows, position=7, aggregate=True, support=support)

    assert "Support tier: **experimental** — Contract declared (#967); no cold-cache run (evidence at `v0.1.0`)." in singleton
    assert "  - Limitation: Preload list empty" in singleton
    assert singleton.index("Support tier") < singleton.index("| Capability |")
    assert "`speaches` — Support tier: **experimental**" in aggregate
    assert render_capabilities_section(rows, position=7, aggregate=False) == render_capabilities_section(
        rows, position=7, aggregate=False, support=()
    )


def test_support_lines_follow_aggregate_member_order() -> None:
    manifests = list(load_manifests(SERVICES))
    lines = resolve_support_lines("stt-provider", manifests)

    assert [line.service for line in lines] == ["parakeet", "speaches"]
    assert all(line.tier in SUPPORT_TIERS for line in lines)
    assert resolve_support_lines("redis", manifests)[0].service == "redis"


def test_generated_catalog_and_reference_expose_the_tier() -> None:
    model = load_docs_model(ROOT)
    pages = service_pages(model)
    index = pages[ROOT / "docs" / "site" / "services" / "index.md"]

    assert "| Service | Title | Support | Tracks |" in index
    assert "[speaches](speaches.md) | Speaches" in index
    assert "| experimental |" in index
    assert "selectable is not the same as validated" in index
    reference = (ROOT / "docs" / "reference" / "manifest-fields.md").read_text(encoding="utf-8")
    assert "| support |" in reference


def test_wizard_marks_unqualified_families_before_launch() -> None:
    def info(tier: str, description: str = "Vector database") -> ServiceInfo:
        return ServiceInfo(
            key="weaviate", display_name="Weaviate", description=description, options=["container", "disabled"],
            option_labels={}, current_value="", env_var_name="WEAVIATE_SOURCE", support_tier=tier,
        )

    assert _support_subtitle(info("experimental")) == "Vector database  [support: experimental]"
    assert _support_subtitle(info("community", "")) == "[support: community]"
    assert _support_subtitle(info("stable")) == "Vector database"


def test_contributor_guide_documents_the_support_block() -> None:
    guide = (ROOT / "docs" / "CONTRIBUTING-services.md").read_text(encoding="utf-8")

    assert "`support:`" in guide
    assert "evidence_revision" in guide
    assert "support_stable_without_release_evidence" in guide
