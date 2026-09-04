"""Service-authoring documentation policy and generated-catalog contracts."""

from __future__ import annotations

import re
import inspect
import ast
from datetime import date
from pathlib import Path

import pytest
import yaml

from services.manifests import ManifestLoadError, load_manifests
from services import manifest_validator
from scripts.docs.canonical_references import (
    render_plan_archive_line,
    render_validator_catalog,
)


ROOT = Path(__file__).resolve().parents[2]


def _emitted_validator_diagnostics() -> set[str]:
    tree = ast.parse(inspect.getsource(manifest_validator))
    return {
        keyword.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ValidationIssue"
        for keyword in node.keywords
        if keyword.arg == "kind"
        and isinstance(keyword.value, ast.Constant)
        and isinstance(keyword.value.value, str)
    }


def _registered_validator_diagnostics() -> list[str]:
    return [
        diagnostic
        for rule in manifest_validator.VALIDATOR_RULES
        for diagnostic in rule.diagnostics
    ]


def test_qdrant_example_does_not_claim_a_stale_source_count() -> None:
    guide = (ROOT / "docs" / "CONTRIBUTING-services.md").read_text(encoding="utf-8")

    assert "The declarative `runtime_sc` covers all four sources" not in guide
    assert "The declarative `runtime_sc` covers every declared source" in guide


def test_qdrant_worked_manifest_runtime_slices_match_its_declared_sources() -> None:
    guide = (ROOT / "docs" / "CONTRIBUTING-services.md").read_text(encoding="utf-8")
    sample = re.search(
        r"### 11\.1\. `services/qdrant/service\.yml`\n\n```yaml\n"
        r"(?P<yaml>.*?)\n```",
        guide,
        flags=re.DOTALL,
    )

    assert sample is not None
    manifest = yaml.safe_load(sample.group("yaml"))
    declared = {option["id"] for option in manifest["sources"]["options"]}
    runtime = set(manifest["runtime_sc"]["qdrant"])
    assert declared == runtime == {"container", "localhost", "disabled"}


def test_validator_catalog_is_generated_from_the_live_rule_registry() -> None:
    rules = getattr(manifest_validator, "VALIDATOR_RULES", ())
    guide = (ROOT / "docs" / "CONTRIBUTING-services.md").read_text(encoding="utf-8")
    match = re.search(
        r"<!-- BEGIN GENERATED MANIFEST VALIDATOR CATALOG -->\n"
        r"(?P<body>.*?)"
        r"<!-- END GENERATED MANIFEST VALIDATOR CATALOG -->",
        guide,
        flags=re.DOTALL,
    )

    assert rules, "manifest-validator rule registry is missing"
    assert match is not None, "generated validator catalog markers are missing"
    assert match.group("body") == render_validator_catalog() + "\n"
    for rule in rules:
        assert f"`{rule.name}`" in match.group("body")
        for diagnostic in rule.diagnostics:
            assert f"`{diagnostic}`" in match.group("body")


def test_validator_registry_covers_every_emitted_diagnostic_once() -> None:
    emitted = _emitted_validator_diagnostics()
    registered = _registered_validator_diagnostics()

    assert len(registered) == len(set(registered))
    assert emitted == set(registered)
    assert len({rule.name for rule in manifest_validator.VALIDATOR_RULES}) == len(
        manifest_validator.VALIDATOR_RULES
    )
    assert all(rule.description.endswith(".") for rule in manifest_validator.VALIDATOR_RULES)


def test_validator_execution_uses_registry_order(monkeypatch) -> None:
    calls = []

    def first(_manifests):
        calls.append("first")
        return []

    def root_only(_manifests, _services_root):
        calls.append("root")
        return []

    monkeypatch.setattr(
        manifest_validator,
        "VALIDATOR_RULES",
        (
            manifest_validator.ValidatorRule("first", (), "First.", first),
            manifest_validator.ValidatorRule(
                "root", (), "Root.", root_only, needs_services_root=True
            ),
        ),
    )

    manifest_validator.validate_manifests([])
    assert calls == ["first"]
    calls.clear()
    manifest_validator.validate_manifests([], Path("/tmp/services"))
    assert calls == ["first", "root"]


def test_plan_archive_range_matches_dated_plan_and_spec_filenames() -> None:
    dates = []
    for folder in (ROOT / "docs" / "superpowers" / "plans", ROOT / "docs" / "superpowers" / "specs"):
        for path in folder.glob("*.md"):
            match = re.match(r"(\d{4}-\d{2}-\d{2})-", path.name)
            if match:
                dates.append(date.fromisoformat(match.group(1)))
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")

    expected_range = f"{min(dates).isoformat()} through {max(dates).isoformat()}"
    assert expected_range in index
    assert "larger 2026-05/06 feature tracks" not in index


def test_plan_archive_range_rejects_an_undated_entry(tmp_path: Path) -> None:
    for name in ("plans", "specs"):
        folder = tmp_path / "docs" / "superpowers" / name
        folder.mkdir(parents=True)
        (folder / "2026-08-01-example.md").write_text("# Example\n")
    (tmp_path / "docs" / "superpowers" / "plans" / "undated.md").write_text(
        "# Undated\n"
    )

    with pytest.raises(ValueError, match="not date-prefixed"):
        render_plan_archive_line(tmp_path)


def _write_archive_roots(tmp_path: Path) -> tuple[Path, Path]:
    plans = tmp_path / "docs" / "superpowers" / "plans"
    specs = tmp_path / "docs" / "superpowers" / "specs"
    for folder in (plans, specs):
        folder.mkdir(parents=True)
        (folder / "2026-08-01-example.md").write_text("# Example\n")
    return plans, specs


def test_plan_archive_range_requires_both_real_roots(tmp_path: Path) -> None:
    plans = tmp_path / "docs" / "superpowers" / "plans"
    plans.mkdir(parents=True)
    (plans / "2026-08-01-example.md").write_text("# Example\n")

    with pytest.raises(ValueError, match="archive root is not a real directory"):
        render_plan_archive_line(tmp_path)


def test_plan_archive_range_rejects_a_symlinked_root(tmp_path: Path) -> None:
    actual = tmp_path / "actual-plans"
    actual.mkdir()
    (actual / "2026-08-01-example.md").write_text("# Example\n")
    plans = tmp_path / "docs" / "superpowers" / "plans"
    plans.parent.mkdir(parents=True)
    plans.symlink_to(actual, target_is_directory=True)
    specs = tmp_path / "docs" / "superpowers" / "specs"
    specs.mkdir()
    (specs / "2026-08-01-example.md").write_text("# Example\n")

    with pytest.raises(ValueError, match="archive root is not a real directory"):
        render_plan_archive_line(tmp_path)


def test_plan_archive_range_rejects_a_symlinked_root_ancestor(
    tmp_path: Path,
) -> None:
    actual = tmp_path / "actual-superpowers"
    for name in ("plans", "specs"):
        folder = actual / name
        folder.mkdir(parents=True)
        (folder / "2026-08-01-example.md").write_text("# Example\n")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "superpowers").symlink_to(actual, target_is_directory=True)

    with pytest.raises(ValueError, match="archive root is not a real directory"):
        render_plan_archive_line(tmp_path)


def test_plan_archive_range_rejects_a_dated_directory_entry(tmp_path: Path) -> None:
    plans, _ = _write_archive_roots(tmp_path)
    (plans / "2026-08-02-directory.md").mkdir()

    with pytest.raises(ValueError, match="archive entry is not a real file"):
        render_plan_archive_line(tmp_path)


def test_plan_archive_range_rejects_a_dated_symlink_entry(tmp_path: Path) -> None:
    plans, _ = _write_archive_roots(tmp_path)
    target = tmp_path / "outside.md"
    target.write_text("# Outside\n")
    (plans / "2026-08-02-linked.md").symlink_to(target)

    with pytest.raises(ValueError, match="archive entry is not a real file"):
        render_plan_archive_line(tmp_path)


def test_manifest_field_reference_documents_the_exception_policy() -> None:
    reference = (ROOT / "docs" / "reference" / "manifest-fields.md").read_text(
        encoding="utf-8"
    )
    service_guide = (ROOT / "services" / "README.md").read_text(encoding="utf-8")
    normalized_service_guide = " ".join(service_guide.split())

    assert "| docs |" in reference
    assert "| docs_exception |" in reference
    assert "explicit `because` clause" in reference
    assert "four substantive words" in reference
    assert "explicit `because` clause" in normalized_service_guide
    assert "four substantive words" in normalized_service_guide


def test_virtual_manifest_without_documentation_is_rejected(
    services_root, write_manifest, minimal_manifest_dict
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
    }
    write_manifest("virtualdemo", manifest)

    issues = manifest_validator.validate_manifests(
        load_manifests(services_root), services_root
    )

    assert any(issue.kind == "missing_documentation" for issue in issues)


def test_specific_documentation_exception_is_schema_valid_and_satisfies_policy(
    services_root, write_manifest, minimal_manifest_dict
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs_exception": (
            "This compatibility sentinel has no operator-facing behavior because "
            "legacy environment upgrades must preserve the retired source variable."
        ),
    }
    write_manifest("virtualdemo", manifest)

    manifests = load_manifests(services_root)
    issues = manifest_validator.validate_manifests(manifests, services_root)

    assert manifests[0].docs_exception.startswith("This compatibility sentinel")
    assert not any("documentation" in issue.kind for issue in issues)


@pytest.mark.parametrize(
    "reason", ["", " " * 30, "N/A", "No docs needed", "TODO: document later"]
)
def test_documentation_exception_rejects_empty_or_generic_reasons(
    reason, services_root, write_manifest, minimal_manifest_dict
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs_exception": reason,
    }
    write_manifest("virtualdemo", manifest)

    with pytest.raises(ManifestLoadError):
        load_manifests(services_root)


def test_long_generic_documentation_exception_is_rejected_semantically(
    services_root, write_manifest, minimal_manifest_dict
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs_exception": (
            "Documentation is not required because this service is internal only."
        ),
    }
    write_manifest("virtualdemo", manifest)

    issues = manifest_validator.validate_manifests(
        load_manifests(services_root), services_root
    )

    assert any(
        issue.kind == "invalid_documentation_exception" for issue in issues
    )


def test_url_only_documentation_exception_is_rejected_semantically(
    services_root, write_manifest, minimal_manifest_dict
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs_exception": "https://example.com/why-this-manifest-has-no-documentation",
    }
    write_manifest("virtualdemo", manifest)

    issues = manifest_validator.validate_manifests(
        load_manifests(services_root), services_root
    )

    assert any(
        issue.kind == "invalid_documentation_exception" for issue in issues
    )


@pytest.mark.parametrize(
    "reason",
    [
        "This manifest has no documentation despite being deliberately undocumented.",
        "Because documentation service internal not applicable required needed only.",
        "............................................................",
        "See https://example.com/tracking/reason because https://example.com/issue/1",
        "See https://example.com/because/compatibility-migration-sentinels-preserve-upgrades",
        "See https://example.com/?reason=because%20compatibility%20migration%20sentinels%20preserve%20upgrades",
        "See ftp://example.com/because/compatibility-migration-sentinels-preserve-upgrades",
        "See ftp://example.com/(because/compatibility-migration-sentinels-preserve-upgrades)",
        "See file:/tmp/because/compatibility-migration-sentinels-preserve-upgrades",
        "See urn:atlas:because:compatibility:migration:sentinels:preserve:upgrades",
        "See atlas+docs:because:compatibility:migration:sentinels:preserve:upgrades",
        "See mailto:because@example.com",
        "See <https://example.com/because/compatibility-migration-sentinels-preserve-upgrades>",
        "See <because.compatibility.migration.sentinels.preserve@example.com>",
        "See because.compatibility.migration.sentinels.preserve@upgrades.example.com",
        "See example.com/because/compatibility-migration-sentinels-preserve-upgrades",
        "See example.test:443/because/compatibility-migration-sentinels-preserve-upgrades",
        "See example.photography/because/compatibility-migration-sentinels-preserve-upgrades",
        "See WWW.Example.photography/because/compatibility-migration-sentinels-preserve-upgrades",
        "See Example.COM/path/because/compatibility-migration-sentinels-preserve-upgrades",
        "See EXAMPLE.PHOTOGRAPHY:443/path/because/compatibility-migration-sentinels-preserve-upgrades",
        "See 例え.テスト/because/compatibility-migration-sentinels-preserve-upgrades",
        "See MÜNICH.example/path/because/compatibility-migration-sentinels-preserve-upgrades",
        "See redis.atlas.internal/path/because/compatibility-migration-sentinels-preserve-upgrades",
        "See 192.0.2.1:443/because/compatibility-migration-sentinels-preserve-upgrades",
        "See [2001:db8::1]/because/compatibility-migration-sentinels-preserve-upgrades",
        "See [IPV6:2001:db8::1]/because/compatibility-migration-sentinels-preserve-upgrades",
        "See [fe80::1%eth0]/because/compatibility-migration-sentinels-preserve-upgrades",
        "See [fe80::1%en0.1_~x-2]/because/compatibility-migration-sentinels-preserve-upgrades",
        "See [IPv6:fe80::1%25eth0]:443/because/compatibility-migration-sentinels-preserve-upgrades",
        "See [fe80::1%]/because/compatibility-migration-sentinels-preserve-upgrades",
        "See [fe80::1%eth 0]/because/compatibility-migration-sentinels-preserve-upgrades",
        "See [fe80::1%eth/0]/because/compatibility-migration-sentinels-preserve-upgrades",
        "See [fe80::1%%eth0]/because/compatibility-migration-sentinels-preserve-upgrades",
        "See [fe80::1%eth!]/because/compatibility-migration-sentinels-preserve-upgrades",
        "See [fe80::zz%eth0]/because/compatibility-migration-sentinels-preserve-upgrades",
        "See because.compatibility.migration.sentinels.preserve@例え.テスト",
        "See \"because compatibility migration sentinels preserve\"@example.com",
        "See \"because compatibility migration sentinels preserve\"@[IPv6:2001:db8::1]",
        "See because.compatibility.migration.sentinels.preserve.example.photography",
        "See [because compatibility migration sentinels preserve upgrades](https://example.com)",
        "See [because compatibility migration sentinels preserve upgrades](https://example.com \"tracking\")",
        "See [because compatibility migration sentinels preserve upgrades][tracking]",
        "See [because compatibility migration sentinels preserve upgrades][]",
        "See [because compatibility migration sentinels preserve upgrades]",
        "Exception exists because compatibility compatibility compatibility compatibility.",
        "Exception exists because x y z q.",
        "Exception exists because " + "\u0301" * 20,
        "Exception exists because " + "🎯" * 20,
        "Exception exists because documentаtion service internal required needed.",
        (
            "Ｄｏｃｕｍｅｎｔａｔｉｏｎ is not required ｂｅｃａｕｓｅ this "
            "ｓｅｒｖｉｃｅ is internal only."
        ),
    ],
)
def test_documentation_exception_requires_a_positive_concrete_because_clause(
    reason, services_root, write_manifest, minimal_manifest_dict
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs_exception": reason,
    }
    write_manifest("virtualdemo", manifest)

    issues = manifest_validator.validate_manifests(
        load_manifests(services_root), services_root
    )

    assert any(
        issue.kind == "invalid_documentation_exception" for issue in issues
    )


@pytest.mark.parametrize("control", ["\u0000", "\t", "\u0085"])
def test_documentation_exception_schema_rejects_c0_controls(
    control, services_root, write_manifest, minimal_manifest_dict
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs_exception": (
            "No guide is published because compatibility migration"
            f"{control}sentinels preserve legacy environment upgrades."
        ),
    }
    write_manifest("virtualdemo", manifest)

    with pytest.raises(ManifestLoadError):
        load_manifests(services_root)


@pytest.mark.parametrize("control", ["\u200b", "\ue000"])
def test_documentation_exception_rejects_unicode_category_c(
    control, services_root, write_manifest, minimal_manifest_dict
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs_exception": (
            "No guide is published because compatibility migration"
            f"{control}"
            "sentinels preserve legacy environment upgrades."
        ),
    }
    write_manifest("virtualdemo", manifest)

    issues = manifest_validator.validate_manifests(
        load_manifests(services_root), services_root
    )
    assert any(
        issue.kind == "invalid_documentation_exception" for issue in issues
    )


@pytest.mark.parametrize(
    "reason",
    [
        (
            "No guide is published because compatibility migration sentinels "
            "preserve legacy environment upgrades."
        ),
        (
            "No guide is published BECAUSE compatibility migration sentinels "
            "preserve legacy environment upgrades; tracking: https://example.com/42"
        ),
        (
            "No guide is published because compatibility migration sentinels "
            "preserve legacy environment upgrades; tracking: example.com/issues/42"
        ),
        (
            "No guide is published because bootstrapper.migrations.v5 and "
            "module.Class preserve legacy v1.2.3 environment upgrades."
        ),
        (
            "No guide is published because redis.atlas.internal and "
            "worker.service.local preserve legacy session migration state."
        ),
        (
            "No guide is published because www.redis.atlas.internal and "
            "www.worker.service.local preserve legacy session migration state."
        ),
        (
            "No guide is published because redis:6379 localhost:5432 "
            "coordinate migrations."
        ),
    ],
)
def test_documentation_exception_accepts_concrete_reason_with_optional_url(
    reason, services_root, write_manifest, minimal_manifest_dict
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs_exception": reason,
    }
    write_manifest("virtualdemo", manifest)

    issues = manifest_validator.validate_manifests(
        load_manifests(services_root), services_root
    )
    assert not [issue for issue in issues if "documentation" in issue.kind]


def test_link_stripping_preserves_technical_dotted_identifiers_and_service_dns() -> None:
    technical = (
        "v1.2.3 1.2.3 bootstrapper.migrations.v5 module.Class "
        "localhost redis:6379 localhost:5432 redis.atlas.internal "
        "worker.service.local foo.localhost www.redis.atlas.internal "
        "www.worker.service.local"
    )

    assert manifest_validator._strip_link_like_text(technical) == technical


def test_link_stripping_preserves_bare_valid_ip_mentions() -> None:
    technical = (
        "192.0.2.1 192.0.2.1. 2001:db8::1, [2001:db8::1]. "
        "[IPV6:2001:db8::1], [fe80::1%eth0], "
        "[fe80::1%en0.1_~x-2], [IPv6:fe80::1%25eth0] "
        "999.0.2.1:443/path"
    )

    assert manifest_validator._strip_link_like_text(technical) == technical


def test_bare_domain_stripping_uses_the_documented_syntax_criterion() -> None:
    stripped = manifest_validator._strip_link_like_text(
        "example.com example.test:443/path package.dev/issues/4 "
        "example.photography www.example.com Example.COM/path "
        "EXAMPLE.PHOTOGRAPHY:443/path "
        "例え.テスト/path MÜNICH.example/path redis.atlas.internal/path "
        "module.foo module.Class "
        "bootstrapper.migrations.v5 redis.atlas.internal"
    )
    removed = (
        "example.com",
        "example.test",
        "package.dev",
        "example.photography",
        "www.example.com",
        "Example.COM",
        "EXAMPLE.PHOTOGRAPHY",
        "例え.テスト",
        "MÜNICH.example",
        "redis.atlas.internal/path",
        "module.foo",
    )
    preserved = (
        "module.Class",
        "bootstrapper.migrations.v5",
        "redis.atlas.internal",
    )

    assert not [candidate for candidate in removed if candidate in stripped]
    assert not [candidate for candidate in preserved if candidate not in stripped]


@pytest.mark.parametrize(
    "candidate",
    [
        "[fe80::1%eth0]/because/compatibility-migration-sentinels-preserve-upgrades",
        "[fe80::1%en0.1_~x-2]/because/compatibility-migration-sentinels-preserve-upgrades",
        "[IPv6:fe80::1%25eth0]:443/because/compatibility-migration-sentinels-preserve-upgrades",
        "[fe80::1%]/because/compatibility-migration-sentinels-preserve-upgrades",
        "[fe80::1%eth 0]/because/compatibility-migration-sentinels-preserve-upgrades",
        "[fe80::1%eth/0]/because/compatibility-migration-sentinels-preserve-upgrades",
        "[fe80::1%%eth0]/because/compatibility-migration-sentinels-preserve-upgrades",
        "[fe80::1%eth!]/because/compatibility-migration-sentinels-preserve-upgrades",
        "[fe80::zz%eth0]/because/compatibility-migration-sentinels-preserve-upgrades",
    ],
)
def test_bracketed_address_like_links_do_not_leave_suffix_text(candidate) -> None:
    assert not manifest_validator._strip_link_like_text(candidate).strip()


def test_documentation_exception_schema_enforces_a_maximum_length(
    services_root, write_manifest, minimal_manifest_dict
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs_exception": "Because " + "compatibility " * 80,
    }
    write_manifest("virtualdemo", manifest)

    with pytest.raises(ManifestLoadError):
        load_manifests(services_root)


def test_contributor_guide_states_the_executable_exception_contract() -> None:
    guide = (ROOT / "docs" / "CONTRIBUTING-services.md").read_text(encoding="utf-8")
    normalized = " ".join(guide.split())
    required = (
        "explicit `because` clause",
        "at least four substantive rationale words",
        "at least three distinct terms",
        "applies NFKC and case-folding",
        "rejects every Unicode category-C character",
        "link-like text before locating and scoring",
        "Markdown links/labels",
        "RFC URI schemes",
        "email-like tokens",
        "atom or quoted local part",
        "deliberately not a full RFC email parser",
        "Host candidates are IDNA-normalized",
        "stripped regardless of case",
        "alphabetic 2–63 character final label",
        "ambiguous lowercase identifier such as `module.foo`",
        "uppercase `module.Class`",
        "numeric host-port pairs",
        "Validated IPv4 and bracketed IPv6",
        "non-empty zone identifier",
        "raw `%eth0`",
        "RFC 6874 `%25eth0`",
        "`[A-Za-z0-9._~-]+`",
        "malformed bracketed colon/address-like",
        "does not assert that those fallbacks are valid addresses",
        "`www`-prefixed service DNS",
        "suffix-bearing reserved DNS is stripped",
        "bare IP mentions remain",
        "bounded authoring gate, not proof",
    )

    assert not [fragment for fragment in required if fragment not in normalized]


def test_contributor_guide_exception_example_satisfies_the_live_policy(
    services_root, write_manifest, minimal_manifest_dict
) -> None:
    guide = (ROOT / "docs" / "CONTRIBUTING-services.md").read_text(encoding="utf-8")
    match = re.search(r'^# docs_exception: "(?P<reason>[^"]+)"$', guide, re.MULTILINE)
    assert match is not None
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs_exception": match.group("reason"),
    }
    write_manifest("virtualdemo", manifest)

    issues = manifest_validator.validate_manifests(
        load_manifests(services_root), services_root
    )
    assert not [issue for issue in issues if "documentation" in issue.kind]


def test_documentation_exception_is_rejected_when_readme_exists(
    services_root, write_manifest, minimal_manifest_dict
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs_exception": (
            "This compatibility sentinel has no operator-facing behavior because "
            "legacy environment upgrades must preserve the retired source variable."
        ),
    }
    write_manifest("virtualdemo", manifest)
    (services_root / "virtualdemo" / "README.md").write_text("# Virtual demo\n")

    issues = manifest_validator.validate_manifests(
        load_manifests(services_root), services_root
    )

    assert any(issue.kind == "documentation_exception_conflict" for issue in issues)


@pytest.mark.parametrize(
    "docs_path",
    [
        "/tmp/outside.md",
        "../outside.md",
        "services/virtualdemo/missing.md",
        "services/virtualdemo",
    ],
)
def test_declared_documentation_path_must_be_safe_existing_markdown(
    docs_path, services_root, write_manifest, minimal_manifest_dict
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs": docs_path,
    }
    write_manifest("virtualdemo", manifest)

    issues = manifest_validator.validate_manifests(
        load_manifests(services_root), services_root
    )

    assert any(issue.kind == "invalid_documentation" for issue in issues)


def test_declared_documentation_rejects_symlinks(
    services_root, write_manifest, minimal_manifest_dict, tmp_path
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs": "services/virtualdemo/README.md",
    }
    write_manifest("virtualdemo", manifest)
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n")
    (services_root / "virtualdemo" / "README.md").symlink_to(outside)

    issues = manifest_validator.validate_manifests(
        load_manifests(services_root), services_root
    )

    assert any(issue.kind == "invalid_documentation" for issue in issues)


def test_declared_documentation_rejects_a_symlinked_ancestor(
    services_root, write_manifest, minimal_manifest_dict, tmp_path
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs": "docs-alias/README.md",
    }
    write_manifest("virtualdemo", manifest)
    actual = tmp_path / "actual-docs"
    actual.mkdir()
    (actual / "README.md").write_text("# Outside\n")
    (tmp_path / "docs-alias").symlink_to(actual, target_is_directory=True)

    issues = manifest_validator.validate_manifests(
        load_manifests(services_root), services_root
    )

    assert any(issue.kind == "invalid_documentation" for issue in issues)


def test_valid_declared_documentation_satisfies_policy(
    services_root, write_manifest, minimal_manifest_dict
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs": "docs/virtual-demo.md",
    }
    write_manifest("virtualdemo", manifest)
    docs = services_root.parent / "docs"
    docs.mkdir()
    (docs / "virtual-demo.md").write_text("# Virtual demo\n")

    issues = manifest_validator.validate_manifests(
        load_manifests(services_root), services_root
    )

    assert not [issue for issue in issues if "documentation" in issue.kind]


def test_declared_documentation_requires_canonical_lowercase_md_extension(
    services_root, write_manifest, minimal_manifest_dict
) -> None:
    manifest = minimal_manifest_dict("virtualdemo") | {
        "virtual": True,
        "containers": [],
        "docs": "docs/virtual-demo.MD",
    }
    write_manifest("virtualdemo", manifest)
    docs = services_root.parent / "docs"
    docs.mkdir()
    (docs / "virtual-demo.MD").write_text("# Virtual demo\n")

    issues = manifest_validator.validate_manifests(
        load_manifests(services_root), services_root
    )

    assert any(issue.kind == "invalid_documentation" for issue in issues)


def test_real_repository_manifests_satisfy_documentation_policy() -> None:
    services_root = ROOT / "services"
    issues = manifest_validator.validate_manifests(
        load_manifests(services_root), services_root
    )

    assert not [issue for issue in issues if "documentation" in issue.kind]
