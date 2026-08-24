"""Tests for bootstrapper.services.manifest_validator (cross-manifest checks)."""

from __future__ import annotations

import pytest

from services.manifests import load_manifests
from services.manifest_validator import (
    ValidationIssue,
    validate_manifests,
)


# ────────────────────────────────────────────────────────────────────────────
# Happy paths
# ────────────────────────────────────────────────────────────────────────────


def test_clean_pair_of_manifests_returns_no_issues(
    services_root, write_manifest, minimal_manifest_dict
):
    write_manifest("redis", minimal_manifest_dict("redis"))
    write_manifest("backend", minimal_manifest_dict("backend") | {"category": "apps"})
    manifests = load_manifests(services_root)
    issues = validate_manifests(manifests)
    assert issues == []


def test_empty_manifest_list_returns_no_issues():
    assert validate_manifests([]) == []


def test_duplicate_capability_names_within_manifest_flagged(
    services_root, write_manifest, minimal_manifest_dict
):
    manifest = minimal_manifest_dict("redis") | {
        "capabilities": [
            {
                "name": "Primary cache operations",
                "status": "supported",
                "verification": "tested",
                "note": "Atlas configures the in-stack Redis cache.",
            },
            {
                "name": "Primary cache operations",
                "status": "partial",
                "verification": "documented",
                "note": "A duplicate label would make the contract ambiguous.",
            },
        ]
    }
    write_manifest("redis", manifest)

    issues = validate_manifests(load_manifests(services_root))

    duplicates = [i for i in issues if i.kind == "duplicate_capability"]
    assert len(duplicates) == 1
    assert duplicates[0].manifest == "redis"
    assert "Primary cache operations" in duplicates[0].message


def test_full_manifest_with_sources_is_clean(
    services_root, write_manifest, full_manifest_dict, minimal_manifest_dict
):
    write_manifest("ollama", full_manifest_dict("ollama"))
    # The ollama fixture exports OLLAMA_ENDPOINT to litellm + weaviate; provide stubs
    # so the consumer-closure rule has manifests to resolve against.
    write_manifest("litellm", minimal_manifest_dict("litellm") | {"category": "llm"})
    write_manifest("weaviate", minimal_manifest_dict("weaviate") | {"category": "data"})
    manifests = load_manifests(services_root)
    assert validate_manifests(manifests) == []


# ────────────────────────────────────────────────────────────────────────────
# Cross-manifest violations
# ────────────────────────────────────────────────────────────────────────────


def test_duplicate_env_var_name_across_manifests_flagged(
    services_root, write_manifest, minimal_manifest_dict
):
    # Both redis and another service declare REDIS_PORT — exactly one owner allowed.
    write_manifest("redis", minimal_manifest_dict("redis"))
    other = minimal_manifest_dict("backend")
    other["category"] = "apps"
    other["env"].append({"name": "REDIS_PORT", "default": 9999})
    write_manifest("backend", other)
    manifests = load_manifests(services_root)
    issues = validate_manifests(manifests)
    assert any("REDIS_PORT" in i.message for i in issues)
    assert any(i.kind == "duplicate_env_var" for i in issues)


def test_duplicate_container_across_manifests_flagged(
    services_root, write_manifest, minimal_manifest_dict
):
    write_manifest("redis", minimal_manifest_dict("redis"))
    other = minimal_manifest_dict("backend")
    other["category"] = "apps"
    other["containers"] = ["redis"]  # collides with the redis manifest
    write_manifest("backend", other)
    manifests = load_manifests(services_root)
    issues = validate_manifests(manifests)
    assert any(i.kind == "duplicate_container" for i in issues)


def test_depends_on_pointing_to_unknown_manifest_flagged(
    services_root, write_manifest, minimal_manifest_dict
):
    bad = minimal_manifest_dict("backend")
    bad["category"] = "apps"
    bad["depends_on"] = {"required": ["does-not-exist"], "optional": []}
    write_manifest("backend", bad)
    manifests = load_manifests(services_root)
    issues = validate_manifests(manifests)
    assert any(i.kind == "unknown_dependency" for i in issues)
    assert any("does-not-exist" in i.message for i in issues)


def test_export_must_be_declared_by_owning_manifest(
    services_root, write_manifest, full_manifest_dict
):
    bad = full_manifest_dict("ollama")
    # Add an export that isn't declared as env or in source effects.
    bad["exports"].append({"name": "UNDECLARED_VAR", "consumers": ["litellm"]})
    write_manifest("ollama", bad)
    manifests = load_manifests(services_root)
    issues = validate_manifests(manifests)
    assert any(i.kind == "undeclared_export" for i in issues)
    assert any("UNDECLARED_VAR" in i.message for i in issues)


# Removed: test_source_effects_target_must_be_declared — the
# `sources.options[].effects` field was dropped in favor of
# `runtime_sc.<key>.<source>.environment` (single source of truth). The
# `undeclared_effect` validator rule is gone with it. The closure check
# (every exported name produced by env[] OR runtime_sc) lives in
# test_export_must_be_declared_by_owning_manifest below.


def test_source_var_must_be_declared_as_env(
    services_root, write_manifest, full_manifest_dict
):
    """The SOURCE env var name itself (e.g. LLM_PROVIDER_SOURCE) must appear in env[]."""
    bad = full_manifest_dict("ollama")
    # Strip the LLM_PROVIDER_SOURCE entry so the source-var rule fires.
    bad["env"] = [e for e in bad["env"] if e["name"] != "LLM_PROVIDER_SOURCE"]
    write_manifest("ollama", bad)
    manifests = load_manifests(services_root)
    issues = validate_manifests(manifests)
    assert any(i.kind == "undeclared_source_var" for i in issues)


def test_export_consumers_must_be_known_manifests(
    services_root, write_manifest, full_manifest_dict, minimal_manifest_dict
):
    bad = full_manifest_dict("ollama")
    bad["exports"] = [{"name": "OLLAMA_ENDPOINT", "consumers": ["nonexistent-service"]}]
    write_manifest("ollama", bad)
    manifests = load_manifests(services_root)
    issues = validate_manifests(manifests)
    assert any(i.kind == "unknown_consumer" for i in issues)


def test_validation_issue_carries_manifest_name():
    """Each ValidationIssue should record which manifest produced the issue."""
    issue = ValidationIssue(kind="x", manifest="y", message="z")
    assert issue.manifest == "y"
    assert issue.kind == "x"
    assert issue.message == "z"


# ────────────────────────────────────────────────────────────────────────────
# Tier-member rule (catches dangling refs like the post-Tier-3-move XTTS leftover)
# ────────────────────────────────────────────────────────────────────────────


def test_tier_member_matching_a_real_container_is_clean(
    services_root, write_manifest, minimal_manifest_dict
):
    """Tier members that match a known container should not be flagged."""
    write_manifest("redis", minimal_manifest_dict("redis"))
    write_manifest(
        "globals",
        {
            "name": "globals",
            "label": "globals",
            "category": "infra",
            "virtual": True,
            "containers": [],
            "env": [{"name": "PROJECT_NAME", "default": "atlas"}],
            "runtime_dependency_tiers": {
                "data_tier": ["redis"],
            },
        },
    )
    issues = validate_manifests(load_manifests(services_root))
    assert not any(i.kind == "undeclared_tier_member" for i in issues)


def test_dangling_tier_member_flagged(
    services_root, write_manifest, minimal_manifest_dict
):
    """A tier entry naming a non-existent container should fail validation
    (this is the rule that would have caught the dangling XTTS reference)."""
    write_manifest("redis", minimal_manifest_dict("redis"))
    write_manifest(
        "globals",
        {
            "name": "globals",
            "label": "globals",
            "category": "infra",
            "virtual": True,
            "containers": [],
            "env": [{"name": "PROJECT_NAME", "default": "atlas"}],
            "runtime_dependency_tiers": {
                "core_services": ["redis", "xtts"],
            },
        },
    )
    issues = validate_manifests(load_manifests(services_root))
    tier_issues = [i for i in issues if i.kind == "undeclared_tier_member"]
    assert len(tier_issues) == 1
    assert "xtts" in tier_issues[0].message


# ────────────────────────────────────────────────────────────────────────────
# Fragment-vs-manifest containers rule (requires services_root + on-disk fragment)
# ────────────────────────────────────────────────────────────────────────────


def test_fragment_containers_match_passes(
    services_root, write_manifest, minimal_manifest_dict
):
    """When compose.yml's services keys match containers[], no drift is reported."""
    write_manifest("redis", minimal_manifest_dict("redis"))
    (services_root / "redis" / "compose.yml").write_text(
        "services:\n  redis:\n    image: redis:latest\n"
    )
    issues = validate_manifests(
        load_manifests(services_root), services_root=services_root
    )
    assert not any(i.kind == "fragment_container_drift" for i in issues)


def test_fragment_with_extra_service_flagged(
    services_root, write_manifest, minimal_manifest_dict
):
    """A compose.yml service the manifest does not declare should fail validation."""
    write_manifest("redis", minimal_manifest_dict("redis"))
    (services_root / "redis" / "compose.yml").write_text(
        "services:\n  redis:\n    image: redis:latest\n"
        "  redis-undeclared:\n    image: alpine\n"
    )
    issues = validate_manifests(
        load_manifests(services_root), services_root=services_root
    )
    drift = [i for i in issues if i.kind == "fragment_container_drift"]
    assert len(drift) == 1
    assert "redis-undeclared" in drift[0].message


def test_manifest_container_missing_from_fragment_flagged(
    services_root, write_manifest, minimal_manifest_dict
):
    """A containers[] entry with no matching compose service should fail."""
    bad = minimal_manifest_dict("redis") | {"containers": ["redis", "redis-init"]}
    write_manifest("redis", bad)
    (services_root / "redis" / "compose.yml").write_text(
        "services:\n  redis:\n    image: redis:latest\n"
    )
    issues = validate_manifests(
        load_manifests(services_root), services_root=services_root
    )
    drift = [i for i in issues if i.kind == "fragment_container_drift"]
    assert len(drift) == 1
    assert "redis-init" in drift[0].message


def test_non_virtual_manifest_without_fragment_flagged(
    services_root, write_manifest, minimal_manifest_dict
):
    """A non-virtual manifest with no sibling compose.yml should fail."""
    write_manifest("redis", minimal_manifest_dict("redis"))
    # Intentionally do NOT write compose.yml.
    issues = validate_manifests(
        load_manifests(services_root), services_root=services_root
    )
    assert any(i.kind == "missing_fragment" for i in issues)


def test_virtual_manifest_with_fragment_flagged(services_root, write_manifest):
    """A virtual: true manifest MUST NOT have a compose.yml."""
    write_manifest(
        "globals",
        {
            "name": "globals",
            "label": "globals",
            "category": "infra",
            "virtual": True,
            "containers": [],
            "env": [{"name": "PROJECT_NAME", "default": "atlas"}],
        },
    )
    (services_root / "globals" / "compose.yml").write_text("services: {}\n")
    issues = validate_manifests(
        load_manifests(services_root), services_root=services_root
    )
    assert any(i.kind == "unexpected_fragment" for i in issues)


def test_virtual_manifest_without_fragment_clean(services_root, write_manifest):
    """The normal virtual-manifest case: no compose.yml, no issue."""
    write_manifest(
        "globals",
        {
            "name": "globals",
            "label": "globals",
            "category": "infra",
            "virtual": True,
            "containers": [],
            "env": [{"name": "PROJECT_NAME", "default": "atlas"}],
        },
    )
    issues = validate_manifests(
        load_manifests(services_root), services_root=services_root
    )
    assert not any(
        i.kind in ("fragment_container_drift", "missing_fragment", "unexpected_fragment")
        for i in issues
    )


def test_services_root_none_skips_fragment_checks(
    services_root, write_manifest, minimal_manifest_dict
):
    """When services_root is None, fragment-level checks must be skipped
    (preserves the unit-test path for in-memory manifests)."""
    write_manifest("redis", minimal_manifest_dict("redis"))
    # No compose.yml on disk; calling without services_root should NOT flag it.
    issues = validate_manifests(load_manifests(services_root))
    assert not any(
        i.kind in ("fragment_container_drift", "missing_fragment", "unexpected_fragment")
        for i in issues
    )


# ────────────────────────────────────────────────────────────────────────────
# Cycle / alias-uniqueness / category-overflow / engine-orphan rules
# ────────────────────────────────────────────────────────────────────────────


def test_cycle_rule_flags_cycles():
    """A manifest dep cycle triggers a topology_cycle issue."""
    from services.manifests import Manifest, DependsOn
    from services.manifest_validator import validate_manifests

    manifests = [
        Manifest(name="a", label="A", category="data", env=[], depends_on=DependsOn(required=["b"])),
        Manifest(name="b", label="B", category="data", env=[], depends_on=DependsOn(required=["a"])),
    ]
    issues = validate_manifests(manifests)
    kinds = {i.kind for i in issues}
    assert "topology_cycle" in kinds


def test_alias_uniqueness_rule():
    """Duplicate alias across manifests is flagged."""
    from services.manifests import Manifest, DependsOn, Row as MRow
    from services.manifest_validator import validate_manifests

    common_alias = "duplicate.localhost"
    manifests = [
        Manifest(name="a", label="A", category="data", env=[], rows=[MRow(display_name="A", source_var="A_SOURCE", alias=common_alias)]),
        Manifest(name="b", label="B", category="data", env=[], rows=[MRow(display_name="B", source_var="B_SOURCE", alias=common_alias)]),
    ]
    issues = validate_manifests(manifests)
    kinds = {i.kind for i in issues}
    assert "duplicate_alias" in kinds


def test_engine_orphan_fires_for_unreferenced_engine_only_manifest():
    """An engine-only manifest (no rows, has containers, depends on a sources-owning parent)
    whose name is not a prefix of any of that parent's source option ids is an orphan."""
    from services.manifests import (
        DependsOn,
        EnvVarDecl,
        Manifest,
        Row as MRow,
        SourceOption,
        SourcesBlock,
    )
    from services.manifest_validator import validate_manifests

    parent = Manifest(
        name="parent-with-sources",
        label="Parent",
        category="media",
        env=[EnvVarDecl(name="PARENT_SOURCE", default="other")],
        sources=SourcesBlock(
            var="PARENT_SOURCE",
            default="other",
            options=[SourceOption(id="other", label="Other Engine")],
        ),
        rows=[MRow(display_name="Parent", source_var="PARENT_SOURCE")],
    )
    myengine = Manifest(
        name="myengine",
        label="My Engine",
        category="media",
        env=[],
        containers=["myengine-container"],
        depends_on=DependsOn(required=["parent-with-sources"]),
    )
    issues = validate_manifests([parent, myengine])
    orphans = [i for i in issues if i.kind == "engine_orphan"]
    assert len(orphans) == 1
    assert orphans[0].manifest == "myengine"


def test_engine_orphan_does_not_fire_when_referenced():
    """If a parent's sources options include an id beginning with the engine's
    manifest name, the engine is not an orphan."""
    from services.manifests import (
        DependsOn,
        EnvVarDecl,
        Manifest,
        Row as MRow,
        SourceOption,
        SourcesBlock,
    )
    from services.manifest_validator import validate_manifests

    parent = Manifest(
        name="parent-with-sources",
        label="Parent",
        category="media",
        env=[EnvVarDecl(name="PARENT_SOURCE", default="myengine-container-gpu")],
        sources=SourcesBlock(
            var="PARENT_SOURCE",
            default="myengine-container-gpu",
            options=[SourceOption(id="myengine-container-gpu", label="MyEngine (GPU)")],
        ),
        rows=[MRow(display_name="Parent", source_var="PARENT_SOURCE")],
    )
    myengine = Manifest(
        name="myengine",
        label="My Engine",
        category="media",
        env=[],
        containers=["myengine-container"],
        depends_on=DependsOn(required=["parent-with-sources"]),
    )
    issues = validate_manifests([parent, myengine])
    assert not any(i.kind == "engine_orphan" for i in issues)


def test_category_overflow_fires_when_block_exceeded():
    """If a category's *_PORT count exceeds its block size, fire category_overflow.

    The infra block size is 10 (per CATEGORY_SLOTS) — declare 11 *_PORT
    env vars in a single infra manifest and the rule must trip.
    """
    from services.manifests import EnvVarDecl, Manifest
    from services.manifest_validator import validate_manifests
    from services.topology import CATEGORY_SLOTS

    _, infra_block = CATEGORY_SLOTS["infra"]
    overflow = Manifest(
        name="hog",
        label="Hog",
        category="infra",
        env=[
            EnvVarDecl(name=f"HOG_{i}_PORT", default=63000 + i)
            for i in range(infra_block + 1)
        ],
    )
    issues = validate_manifests([overflow])
    overflows = [i for i in issues if i.kind == "category_overflow"]
    assert len(overflows) == 1
    assert "infra" in overflows[0].message
    assert str(infra_block) in overflows[0].message
# ─── runtime_sc source coverage (pass 53) ────────────────────────────

def test_main_slice_missing_variant_flagged(write_manifest, full_manifest_dict, services_root):
    """A sources option with no main-slice runtime_sc entry is flagged —
    get_service_config() would silently return {} for it."""
    m = full_manifest_dict("ollama")
    m["sources"]["options"].append({"id": "ollama-container-gpu", "label": "GPU"})
    # NOTE: runtime_sc.llm_provider deliberately NOT extended.
    write_manifest("ollama", m)
    from services.manifests import load_manifests
    from services.manifest_validator import validate_manifests
    issues = validate_manifests(load_manifests(services_root))
    hits = [i for i in issues if i.kind == "runtime_sc_missing_variant"]
    assert any(
        i.manifest == "ollama" and "ollama-container-gpu" in i.message
        for i in hits
    ), [(*hh,) for hh in [(i.kind, i.manifest, i.message) for i in issues]]


def test_main_slice_full_coverage_clean(write_manifest, full_manifest_dict, services_root):
    write_manifest("ollama", full_manifest_dict("ollama"))
    from services.manifests import load_manifests
    from services.manifest_validator import validate_manifests
    issues = validate_manifests(load_manifests(services_root))
    assert not [i for i in issues if i.kind == "runtime_sc_missing_variant"]


# ────────────────────────────────────────────────────────────────────────────
# data_flow.calls target existence (the n8n->comfyui/backend bug class)
# ────────────────────────────────────────────────────────────────────────────


def test_data_flow_unknown_target_flagged(
    services_root, write_manifest, minimal_manifest_dict
):
    """A data_flow.calls target that is neither a manifest name nor an
    aggregate doc-folder is flagged — the typo / renamed / deleted-service
    class that would otherwise render as a phantom 'external' node."""
    write_manifest("redis", minimal_manifest_dict("redis"))
    backend = minimal_manifest_dict("backend") | {
        "category": "apps",
        "data_flow": {"calls": ["redis", "does-not-exist"]},
    }
    write_manifest("backend", backend)
    from services.manifests import load_manifests

    issues = validate_manifests(load_manifests(services_root))
    hits = [i for i in issues if i.kind == "data_flow_unknown_target"]
    assert any(i.manifest == "backend" and "does-not-exist" in i.message for i in hits), hits
    # The real target 'redis' must NOT be flagged.
    assert not any("'redis'" in i.message for i in hits)


def test_data_flow_aggregate_doc_folder_target_accepted(
    services_root, write_manifest, minimal_manifest_dict
):
    """An aggregate doc-folder name (e.g. stt-provider) is a valid target
    even though it owns no manifest of its own."""
    write_manifest("redis", minimal_manifest_dict("redis"))
    backend = minimal_manifest_dict("backend") | {
        "category": "apps",
        "data_flow": {"calls": ["redis", "stt-provider"]},
    }
    write_manifest("backend", backend)
    from services.manifests import load_manifests

    issues = validate_manifests(load_manifests(services_root))
    assert not [i for i in issues if i.kind == "data_flow_unknown_target"]


def test_aggregate_doc_folder_names_match_deps_resolver():
    """The validator's local doc-folder set must track the canonical
    deps_resolver._AGGREGATE_DOC_FOLDERS keys; if they drift, the data_flow
    guard would false-positive on a legitimately-aggregated target."""
    from services.manifest_validator import _AGGREGATE_DOC_FOLDER_NAMES
    from docs.deps_resolver import _AGGREGATE_DOC_FOLDERS

    assert _AGGREGATE_DOC_FOLDER_NAMES == set(_AGGREGATE_DOC_FOLDERS.keys())
