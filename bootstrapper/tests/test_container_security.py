from __future__ import annotations

from datetime import date, timedelta
import json
import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from scripts import container_security
from scripts.upstream_drift_watch import (
    load_expected_remote_base_digests,
    load_manifest_image_refs,
    load_remote_build_contexts,
)


ROOT = Path(__file__).resolve().parents[2]
# Anchor every expiry fixture and every validator call to one date. Building a
# document at import time and comparing it against a fresh call-time value
# silently shifts the horizon by a day when the suite spans UTC midnight, which
# turned the 91-day case into an exactly-90-day one and stopped it raising.
_TODAY = date.today()
GPU_DOCKERFILE_EXCLUSIONS = {
    "services/docling/provider/gpu/Dockerfile",
    "services/parakeet/provider/gpu/Dockerfile",
}
REVIEWED_REMOTE_BASE_DIGESTS = {
    "nginx:alpine": "sha256:72ba65eb42c10344912a84ff42408db7d34f2feb642204570ab8fc5ffd29f1d3",
    "node:20": "sha256:8f693eaa7e0a8e71560c9a82b55fd54c2ae920a2ba5d2cde28bac7d1c01c9ba5",
    "python:3.12-slim": "sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea",
}


def _compose_build_base_images() -> set[str]:
    return set().union(
        *(
            build.base_images
            for build in container_security.load_compose_builds(ROOT / "services")
        )
    )


def _container_security_ledger_row() -> str:
    ledger = (ROOT / "docs/maintenance/external-contract-ledger.md").read_text()
    return next(
        line for line in ledger.splitlines()
        if line.startswith("| Container image vulnerability gate |")
    )
TRIVY_REMOTE_SCAN = ROOT / "scripts/run_trivy_remote_scan.sh"


def test_container_security_inventory_covers_every_manifest_default() -> None:
    expected = load_manifest_image_refs(ROOT / "services")

    assert container_security.load_image_inventory(ROOT / "services") == expected
    assert json.loads(container_security.render_images_json(expected)) == list(expected)


def test_container_security_summary_labels_platform_expansion(
    tmp_path, monkeypatch, capsys
) -> None:
    scans = (
        container_security.ImageScan("vendor/example:1.2.3", "linux/amd64"),
        container_security.ImageScan("vendor/example:1.2.3", "linux/arm64"),
    )
    monkeypatch.setattr(container_security, "load_image_scans", lambda _path: scans)
    monkeypatch.setattr(container_security, "load_exceptions", lambda _path: ())
    monkeypatch.setattr(
        container_security, "load_build_exclusions", lambda _path: ()
    )

    assert container_security.main(["--services-dir", str(tmp_path)]) == 0
    assert "2 image-platform scan(s)" in capsys.readouterr().out


def test_container_security_ledger_does_not_claim_registry_discovery() -> None:
    row = _container_security_ledger_row()

    assert "attempted on both" in row
    assert "not registry discovery" in row
    assert "registry-derived" not in row
    assert "registry-supported" not in row
    images = container_security.load_image_inventory(ROOT / "services")
    scans = container_security.load_image_scans(ROOT / "services")
    build_only = _compose_build_base_images() - set(images)
    remote_bases = load_expected_remote_base_digests(
        ROOT / ".container-scan-exclusions.yml"
    )
    assert f"All {len(images)} manifest-owned image defaults" in row
    assert f"{len(build_only)} Dockerfile-only build inputs" in row
    assert f"{len(remote_bases)} digest-qualified bases" in row
    assert f"currently producing {len(scans)} isolated image-platform scans" in row


def test_container_scan_job_name_distinguishes_platforms() -> None:
    workflow = (ROOT / ".github/workflows/container-security.yml").read_text()

    assert "name: Scan ${{ matrix.image }} (${{ matrix.platform }})" in workflow




def test_security_workflows_use_bounded_remote_scan_wrapper() -> None:
    services_lint, scheduled = _workflow_sources()
    assert all(
        call in workflow
        for workflow, call in (
            (scheduled, 'bash scripts/run_trivy_remote_scan.sh "$IMAGE_REF" "$IMAGE_PLATFORM"'),
            (services_lint, 'bash scripts/run_trivy_remote_scan.sh "$image_ref" "$image_platform"'),
        )
    )


@pytest.mark.parametrize(
    "reference",
    [
        "vendor/example:latest",
        "vendor/example:1",
        "vendor/example",
        "vendor/example:1.2.3x",
        "vendor/example:python-3.12.13latest",
    ],
)
def test_manifest_image_inventory_rejects_floating_defaults(
    tmp_path: Path, reference: str
) -> None:
    service = tmp_path / "example"
    service.mkdir()
    (service / "service.yml").write_text(
        "images:\n"
        "  - var: EXAMPLE_IMAGE\n"
        "    container: example\n"
        f"    default: {reference}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="floating or untagged"):
        container_security.load_image_inventory(tmp_path)


def test_container_security_scans_inventory_and_build_inputs_on_both_platforms() -> None:
    expected = set(load_manifest_image_refs(ROOT / "services"))
    build_images = {
        image
        for build in container_security.load_compose_builds(ROOT / "services")
        for image in build.base_images
    }
    remote_base_images = {
        f"{reference}@{digest}"
        for reference, digest in load_expected_remote_base_digests(
            ROOT / ".container-scan-exclusions.yml"
        ).items()
    }
    scans = container_security.load_image_scans(ROOT / "services")

    assert {scan.image for scan in scans} == (
        expected | build_images | remote_base_images
    )
    assert build_images - expected
    assert remote_base_images
    multiarch_image = next(image for image in expected if image.startswith("redis:"))
    assert {
        scan.platform for scan in scans if scan.image == multiarch_image
    } == {"linux/amd64", "linux/arm64"}


def test_container_security_preserves_explicit_single_arch_platforms() -> None:
    expected = load_manifest_image_refs(ROOT / "services")
    scans = container_security.load_image_scans(ROOT / "services")

    arm_image = next(image for image in expected if "cpu-arm64-latest" in image)
    assert {
        scan.platform for scan in scans if scan.image == arm_image
    } == {"linux/arm64"}
    amd_image = next(image for image in expected if "cpu-1.9@sha256" in image)
    assert {
        scan.platform for scan in scans if scan.image == amd_image
    } == {"linux/amd64"}


def test_changed_manifest_schedules_only_images_the_diff_touches() -> None:
    """Scanning follows the image references a diff moves, not the file it lands in.

    A manifest edit that leaves every pin alone deploys exactly what it did
    before, so re-auditing its images reports pre-existing findings the diff
    can neither introduce nor fix. A header-only entry still selects everything,
    because a rename carries no content identifying which images moved.
    """
    redis_manifest = yaml.safe_load(
        (ROOT / "services/redis/service.yml").read_text(encoding="utf-8")
    )
    image = redis_manifest["images"][0]["default"]

    touched = container_security.load_changed_image_scans(
        ROOT / "services",
        [
            "diff --git a/services/redis/service.yml b/services/redis/service.yml",
            "+    description: an unrelated metadata line",
            f'+    default: "{image}"',
        ],
    )
    assert {scan.image for scan in touched} == {image}

    metadata_only = container_security.load_changed_image_scans(
        ROOT / "services",
        [
            "diff --git a/services/redis/service.yml b/services/redis/service.yml",
            "+    description: metadata-only changes select no image",
        ],
    )
    assert metadata_only == ()

    owned = {row["default"] for row in redis_manifest["images"]}
    header_only = container_security.load_changed_image_scans(
        ROOT / "services",
        ["diff --git a/services/redis/service.yml b/services/redis/service.yml"],
    )
    assert {scan.image for scan in header_only} == owned


@pytest.mark.parametrize(
    "changed_path",
    (
        "services/llm-graph-builder/service.yml",
        "services/llm-graph-builder/compose.yml",
        ".container-scan-exclusions.yml",
    ),
)
def test_changed_remote_build_input_schedules_reviewed_digest_bases(
    changed_path: str,
) -> None:
    scans = container_security.load_changed_image_scans(
        ROOT / "services",
        [f"diff --git a/{changed_path} b/{changed_path}"],
    )
    expected = {
        f"{reference}@{digest}"
        for reference, digest in load_expected_remote_base_digests(
            ROOT / ".container-scan-exclusions.yml"
        ).items()
    }

    assert {scan.image for scan in scans} == expected
    assert {scan.platform for scan in scans} == {
        "linux/amd64",
        "linux/arm64",
    }


def test_changed_compose_defers_local_build_base_to_final_image_scan() -> None:
    scans = container_security.load_changed_image_scans(
        ROOT / "services",
        [
            "diff --git a/services/backup/compose.yml b/services/backup/compose.yml",
            "+    image: ${PROJECT_NAME}-backup:local",
        ],
    )
    assert scans == ()


def test_changed_dockerfile_defers_base_to_final_image_scan() -> None:
    dockerfile_scans = container_security.load_changed_image_scans(
        ROOT / "services",
        [
            "diff --git a/services/backup/init/Dockerfile b/services/backup/init/Dockerfile",
            "+RUN true",
        ],
    )
    assert dockerfile_scans == container_security.load_changed_image_scans(
        ROOT / "services",
        [
            "diff --git a/services/airflow/service.yml b/services/airflow/service.yml",
            "+    default: \"apache/airflow:3.3.1\"",
        ],
    ) == ()


def test_changed_service_rename_scans_current_manifest(tmp_path: Path) -> None:
    service = tmp_path / "renamed"
    service.mkdir()
    (service / "service.yml").write_text(
        "images:\n"
        "  - var: RENAMED_IMAGE\n"
        "    container: renamed\n"
        "    default: vendor/renamed:1.2.3\n",
        encoding="utf-8",
    )
    (service / "compose.yml").write_text(
        "services:\n  renamed:\n    image: ${RENAMED_IMAGE}\n",
        encoding="utf-8",
    )

    scans = container_security.load_changed_image_scans(
        tmp_path,
        [
            'diff --git "a/fixtures/original service.yml" '
            "b/services/renamed/service.yml",
        ],
    )

    assert {scan.image for scan in scans} == {"vendor/renamed:1.2.3"}


@pytest.mark.parametrize(
    ("filename", "old_path"),
    (
        ("compose.yml", "a/fixtures/compose.yml"),
        ("build/Dockerfile", '"a/fixtures/old Dockerfile"'),
    ),
)
def test_outside_to_service_build_rename_defers_base_to_final_image_scan(
    tmp_path: Path, filename: str, old_path: str
) -> None:
    service = tmp_path / "renamed"
    build_dir = service / "build"
    build_dir.mkdir(parents=True)
    (service / "service.yml").write_text(
        "images:\n"
        "  - var: RENAMED_IMAGE\n"
        "    container: renamed\n"
        "    default: vendor/runtime:4.5.6\n",
        encoding="utf-8",
    )
    (service / "compose.yml").write_text(
        "services:\n"
        "  renamed:\n"
        "    image: ${PROJECT_NAME}-renamed:local\n"
        "    build: build\n",
        encoding="utf-8",
    )
    (build_dir / "Dockerfile").write_text(
        "FROM vendor/base:1.2.3\n", encoding="utf-8"
    )

    scans = container_security.load_changed_image_scans(
        tmp_path,
        [
            f"diff --git {old_path} "
            f"b/services/renamed/{filename}",
        ],
    )

    assert scans == ()


def test_changed_service_deletion_does_not_read_removed_file(tmp_path: Path) -> None:
    scans = container_security.load_changed_image_scans(
        tmp_path,
        [
            "diff --git a/services/removed/service.yml "
            "b/services/removed/service.yml",
        ],
    )

    assert scans == ()


@pytest.mark.parametrize(
    "image",
    (
        "${PROJECT_NAME:-atlas}-backup:local",
        "${OTHER_PROJECT}-backup:local",
        "${PROJECT_NAME}-Backup:local",
        "${PROJECT_NAME}-backup:latest",
        "${PROJECT_NAME}-backup-:local",
    ),
)
def test_changed_scan_rejects_other_unresolved_image_expressions(image: str) -> None:
    with pytest.raises(ValueError, match="unsupported image expression"):
        container_security._resolve_compose_image(
            image, {}, owner="changed Compose line"
        )


def test_changed_compose_scan_includes_cross_service_inventory_image(
    tmp_path: Path,
) -> None:
    for name, image in (("owner", "vendor/owner:1.2.3"), ("consumer", "vendor/consumer:2.3.4")):
        service = tmp_path / name
        service.mkdir()
        (service / "service.yml").write_text(
            "images:\n"
            f"  - var: {name.upper()}_IMAGE\n"
            f"    container: {name}\n"
            f"    default: {image}\n",
            encoding="utf-8",
        )
    (tmp_path / "owner/compose.yml").write_text(
        "services:\n  owner:\n    image: ${OWNER_IMAGE}\n",
        encoding="utf-8",
    )
    (tmp_path / "consumer/compose.yml").write_text(
        "services:\n  consumer:\n    image: vendor/owner:1.2.3\n",
        encoding="utf-8",
    )

    scans = container_security.load_changed_image_scans(
        tmp_path,
        [
            "diff --git a/services/consumer/compose.yml b/services/consumer/compose.yml",
            "+    image: vendor/owner:1.2.3",
        ],
    )

    assert {scan.image for scan in scans} == {"vendor/owner:1.2.3"}


def test_changed_compose_build_arg_defers_inline_base_to_final_image_scan(
    tmp_path: Path,
) -> None:
    service = tmp_path / "example"
    build_dir = service / "build"
    build_dir.mkdir(parents=True)
    (service / "service.yml").write_text(
        "images:\n"
        "  - var: EXAMPLE_IMAGE\n"
        "    container: example\n"
        "    default: vendor/example:1.2.3\n",
        encoding="utf-8",
    )
    (service / "compose.yml").write_text(
        "services:\n"
        "  example:\n"
        "    build:\n"
        "      context: build\n"
        "      args:\n"
        "        BASE_IMAGE: vendor/base:2.3.4\n",
        encoding="utf-8",
    )
    (build_dir / "Dockerfile").write_text(
        "ARG BASE_IMAGE=vendor/base:2.3.4\nFROM ${BASE_IMAGE}\n",
        encoding="utf-8",
    )

    scans = container_security.load_changed_image_scans(
        tmp_path,
        [
            "diff --git a/services/example/compose.yml b/services/example/compose.yml",
            "+        BASE_IMAGE: vendor/base:2.3.4",
        ],
    )

    assert scans == ()


def test_changed_compose_build_arg_rejects_floating_remote_image(
    tmp_path: Path,
) -> None:
    service = tmp_path / "example"
    build_dir = service / "build"
    build_dir.mkdir(parents=True)
    (service / "service.yml").write_text(
        "images:\n"
        "  - var: EXAMPLE_IMAGE\n"
        "    container: example\n"
        "    default: vendor/example:1.2.3\n",
        encoding="utf-8",
    )
    (service / "compose.yml").write_text(
        "services:\n"
        "  example:\n"
        "    build:\n"
        "      context: build\n"
        "      args:\n"
        "        BASE_IMAGE: vendor/base:latest\n",
        encoding="utf-8",
    )
    (build_dir / "Dockerfile").write_text(
        "ARG BASE_IMAGE=vendor/base:latest\nFROM ${BASE_IMAGE}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="floating or untagged"):
        container_security.load_changed_image_scans(
            tmp_path,
            [
                "diff --git a/services/example/compose.yml b/services/example/compose.yml",
                "+        BASE_IMAGE: vendor/base:latest",
            ],
        )


def test_changed_inline_compose_build_arg_defers_base_to_final_image_scan(
    tmp_path: Path,
) -> None:
    service = tmp_path / "example"
    build_dir = service / "build"
    build_dir.mkdir(parents=True)
    (service / "service.yml").write_text(
        "images:\n"
        "  - var: EXAMPLE_IMAGE\n"
        "    container: example\n"
        "    default: vendor/example:1.2.3\n",
        encoding="utf-8",
    )
    (service / "compose.yml").write_text(
        "services:\n"
        "  example:\n"
        "    build: {context: build, args: {BASE_REF: vendor/base:2.3.4}}\n",
        encoding="utf-8",
    )
    (build_dir / "Dockerfile").write_text(
        "ARG BASE_REF=vendor/base:2.3.4\nFROM ${BASE_REF}\n",
        encoding="utf-8",
    )

    scans = container_security.load_changed_image_scans(
        tmp_path,
        [
            "diff --git a/services/example/compose.yml b/services/example/compose.yml",
            "+    build: {args: {BASE_IMAGE: vendor/base:2.3.4}}",
        ],
    )

    assert scans == ()


def test_compose_build_image_args_must_be_exactly_pinned(tmp_path: Path) -> None:
    service = tmp_path / "example"
    build_dir = service / "build"
    build_dir.mkdir(parents=True)
    (service / "service.yml").write_text(
        "images:\n"
        "  - var: EXAMPLE_IMAGE\n"
        "    container: example\n"
        "    default: vendor/example:1.2.3\n",
        encoding="utf-8",
    )
    (build_dir / "Dockerfile").write_text(
        "ARG BASE_IMAGE=python:latest\nFROM ${BASE_IMAGE}\n",
        encoding="utf-8",
    )
    (service / "compose.yml").write_text(
        "services:\n"
        "  example:\n"
        "    build:\n"
        "      context: build\n"
        "      args:\n"
        "        BASE_IMAGE: python:latest\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="floating or untagged"):
        container_security.load_compose_builds(tmp_path)


@pytest.mark.parametrize(
    ("option", "value"),
    (
        ("dockerfile_inline", "FROM vendor/hidden:latest"),
        ("target", "builder"),
        ("additional_contexts", {"tool": "docker-image://vendor/tool:latest"}),
    ),
)
def test_unmodeled_compose_build_forms_fail_closed(
    tmp_path: Path, option: str, value: object
) -> None:
    service = tmp_path / "example"
    build_dir = service / "build"
    build_dir.mkdir(parents=True)
    (service / "service.yml").write_text("images: []\n", encoding="utf-8")
    (service / "compose.yml").write_text(
        yaml.safe_dump(
            {
                "services": {
                    "example": {
                        "image": "${PROJECT_NAME}-example:local",
                        "build": {"context": "build", option: value},
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (build_dir / "Dockerfile").write_text(
        "FROM vendor/base:1.2.3 AS builder\nFROM builder\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=option):
        container_security.load_compose_builds(tmp_path)
    with pytest.raises(ValueError, match=option):
        container_security.load_changed_image_scans(
            tmp_path,
            [
                "diff --git a/services/example/compose.yml "
                "b/services/example/compose.yml",
            ],
        )


def test_external_copy_source_must_be_exactly_pinned(tmp_path: Path) -> None:
    service = tmp_path / "example"
    build_dir = service / "build"
    build_dir.mkdir(parents=True)
    (service / "service.yml").write_text("images: []\n", encoding="utf-8")
    (service / "compose.yml").write_text(
        "services:\n"
        "  example:\n"
        "    image: ${PROJECT_NAME}-example:local\n"
        "    build: build\n",
        encoding="utf-8",
    )
    (build_dir / "Dockerfile").write_text(
        "FROM vendor/base:1.2.3 AS runtime\n"
        "COPY --from=vendor/tool:latest /tool /tool\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="COPY --from.*floating or untagged"):
        container_security.load_compose_builds(tmp_path)


def test_external_copy_and_run_mount_sources_are_scanned(tmp_path: Path) -> None:
    service = tmp_path / "example"
    build_dir = service / "build"
    build_dir.mkdir(parents=True)
    (service / "service.yml").write_text("images: []\n", encoding="utf-8")
    (service / "compose.yml").write_text(
        "services:\n"
        "  example:\n"
        "    image: ${PROJECT_NAME}-example:local\n"
        "    build: build\n",
        encoding="utf-8",
    )
    (build_dir / "Dockerfile").write_text(
        "FROM vendor/base:1.2.3 AS runtime\n"
        "COPY \\\n"
        "    --from=vendor/tool:2.3.4 /tool /tool\n"
        "RUN --mount=type=bind,from=vendor/assets:3.4.5,target=/assets true\n"
        "COPY --from=runtime /tool /copy\n",
        encoding="utf-8",
    )

    builds = container_security.load_compose_builds(tmp_path)

    assert builds[0].base_images == (
        "vendor/assets:3.4.5",
        "vendor/base:1.2.3",
        "vendor/tool:2.3.4",
    )


def test_dockerfile_comment_cannot_hide_a_floating_from(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "# comments do not continue \\\n"
        "FROM vendor/hidden:latest\n"
        "FROM vendor/safe:1.2.3\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="vendor/hidden:latest"):
        container_security._validate_dockerfile_build_contract(
            dockerfile, {}, {}
        )


def test_dockerfile_alternate_escape_continuation_is_scanned(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "# escape=`\n"
        "FROM vendor/safe:1.2.3 AS runtime\n"
        "COPY `\n"
        "  --from=vendor/hidden:latest /tool /tool\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="vendor/hidden:latest"):
        container_security._validate_dockerfile_build_contract(
            dockerfile, {}, {}
        )


def test_dockerfile_syntax_frontend_override_fails_closed(
    tmp_path: Path,
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    for frontend in ("vendor/frontend:2.3.4", "docker/dockerfile:1.7.0"):
        dockerfile.write_text(
            f"# syntax={frontend}\nFROM vendor/safe:1.2.3\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="default BuildKit Dockerfile grammar"):
            container_security._validate_dockerfile_build_contract(
                dockerfile, {}, {}
            )


def test_dockerfile_syntax_frontend_error_names_the_reference(
    tmp_path: Path,
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "# syntax=vendor/frontend:latest\nFROM vendor/safe:1.2.3\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="vendor/frontend:latest"):
        container_security._validate_dockerfile_build_contract(
            dockerfile, {}, {}
        )


def test_utf8_bom_cannot_hide_a_floating_syntax_frontend(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "\ufeff# syntax=vendor/frontend:latest\nFROM vendor/safe:1.2.3\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="vendor/frontend:latest"):
        container_security._validate_dockerfile_build_contract(
            dockerfile, {}, {}
        )


def test_dockerfile_heredoc_body_is_not_parsed_as_an_instruction(
    tmp_path: Path,
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM vendor/safe:1.2.3\n"
        "RUN <<'SCRIPT'\n"
        "FROM vendor/not-an-image:latest\n"
        "COPY --from=vendor/not-an-image:latest /tool /tool\n"
        "SCRIPT\n",
        encoding="utf-8",
    )

    bases = container_security._validate_dockerfile_build_contract(
        dockerfile, {}, {}
    )

    assert bases == ("vendor/safe:1.2.3",)


def test_numeric_heredoc_cannot_create_a_false_internal_stage_alias(
    tmp_path: Path,
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM vendor/safe:1.2.3\n"
        "RUN <<123\n"
        "FROM scratch AS trusted\n"
        "123\n"
        "COPY --from=trusted /x /x\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="trusted"):
        container_security._validate_dockerfile_build_contract(
            dockerfile, {}, {}
        )


def test_concatenated_quoted_heredoc_word_cannot_create_a_false_alias(
    tmp_path: Path,
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM vendor/safe:1.2.3\n"
        "RUN <<'E'OF\n"
        "E\n"
        "FROM scratch AS trusted\n"
        "EOF\n"
        "COPY --from=trusted /x /x\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="trusted"):
        container_security._validate_dockerfile_build_contract(
            dockerfile, {}, {}
        )


def test_quoted_heredoc_text_does_not_consume_real_copy_instruction(
    tmp_path: Path,
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM vendor/safe:1.2.3\n"
        "RUN echo ' <<\"COPY --from=vendor/hidden:latest /x /x\"'\n"
        "COPY --from=vendor/hidden:latest /x /x\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="vendor/hidden:latest"):
        container_security._validate_dockerfile_build_contract(
            dockerfile, {}, {}
        )


def test_empty_continuation_line_cannot_hide_external_copy_source(
    tmp_path: Path,
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM vendor/safe:1.2.3\n"
        "COPY \\\n"
        "\n"
        "  --from=vendor/hidden:latest /x /x\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="vendor/hidden:latest"):
        container_security._validate_dockerfile_build_contract(
            dockerfile, {}, {}
        )


def test_escaped_escape_does_not_hide_following_from(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM vendor/safe:1.2.3\n"
        "RUN printf '\\\\' \\\\\n"
        "FROM vendor/hidden:latest\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="vendor/hidden:latest"):
        container_security._validate_dockerfile_build_contract(
            dockerfile, {}, {}
        )


@pytest.mark.parametrize("escape", ("\\", "`"))
@pytest.mark.parametrize(
    ("count", "expected"),
    ((1, True), (2, False), (3, False), (4, False)),
)
def test_dockerfile_continuation_matches_buildkit_escape_handling(
    escape: str, count: int, expected: bool
) -> None:
    assert (
        container_security._dockerfile_line_continues(
            "COPY " + escape * count, escape
        )
        is expected
    )


@pytest.mark.parametrize("separator", ("\v", "\f", "\u0085", "\u2028"))
def test_non_newline_control_character_cannot_hide_following_from(
    tmp_path: Path, separator: str
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM vendor/safe:1.2.3\n"
        f"RUN printf x\\{separator}\n"
        "FROM vendor/hidden:latest\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="vendor/hidden:latest"):
        container_security._validate_dockerfile_build_contract(
            dockerfile, {}, {}
        )


@pytest.mark.parametrize(
    ("dockerfile_text", "expected"),
    (
        (
            "FROM vendor/base:1.2.3 AS build\nFROM BuIlD AS runtime\n",
            ("vendor/base:1.2.3",),
        ),
        (
            "FROM vendor/base:1.2.3 AS build\nFROM scratch AS runtime\n",
            ("vendor/base:1.2.3",),
        ),
        ("FROM scratch\n", ()),
    ),
)
def test_internal_stages_and_scratch_are_not_external_sources(
    dockerfile_text: str, expected: tuple[str, ...]
) -> None:
    assert (
        container_security.load_dockerfile_source_images(dockerfile_text)
        == expected
    )


def test_dockerfile_without_from_still_fails_closed() -> None:
    with pytest.raises(ValueError, match="no external FROM"):
        container_security.load_dockerfile_source_images("RUN true\n")


@pytest.mark.parametrize(
    "trigger",
    (
        "ONBUILD COPY --from=vendor/hidden:latest /tool /tool",
        "ONBUILD RUN --mount=type=bind,from=vendor/hidden:latest,target=/tool true",
    ),
)
def test_onbuild_external_source_must_be_exactly_pinned(
    tmp_path: Path, trigger: str
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        f"FROM vendor/safe:1.2.3\n{trigger}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="vendor/hidden:latest"):
        container_security._validate_dockerfile_build_contract(
            dockerfile, {}, {}
        )


@pytest.mark.parametrize(
    "trigger",
    (
        "ONBUILD COPY --from=build /tool /tool",
        "ONBUILD RUN --mount=type=bind,from=build,target=/tool true",
    ),
)
def test_onbuild_source_cannot_reuse_a_parent_stage_alias(
    tmp_path: Path, trigger: str
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        f"FROM vendor/safe:1.2.3 AS build\n{trigger}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="ONBUILD.*build"):
        container_security._validate_dockerfile_build_contract(
            dockerfile, {}, {}
        )


def test_compose_build_image_arg_must_match_dockerfile_default(tmp_path: Path) -> None:
    service = tmp_path / "example"
    build_dir = service / "build"
    build_dir.mkdir(parents=True)
    (service / "service.yml").write_text(
        "images:\n"
        "  - var: EXAMPLE_IMAGE\n"
        "    container: example\n"
        "    default: vendor/example:1.2.3\n",
        encoding="utf-8",
    )
    (service / "compose.yml").write_text(
        "services:\n"
        "  example:\n"
        "    build:\n"
        "      context: build\n"
        "      args:\n"
        "        BASE_IMAGE: vendor/base:2.3.4\n",
        encoding="utf-8",
    )
    (build_dir / "Dockerfile").write_text(
        "ARG BASE_IMAGE=vendor/base:1.2.3\nFROM ${BASE_IMAGE}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Dockerfile default resolves"):
        container_security.load_compose_builds(tmp_path)


def test_compose_build_rejects_remote_final_image_tag(tmp_path: Path) -> None:
    service = tmp_path / "example"
    build_dir = service / "build"
    build_dir.mkdir(parents=True)
    (service / "service.yml").write_text(
        "images:\n"
        "  - var: BASE_IMAGE\n"
        "    container: example\n"
        "    default: vendor/base:1.2.3\n",
        encoding="utf-8",
    )
    (service / "compose.yml").write_text(
        "services:\n"
        "  example:\n"
        "    build:\n"
        "      context: build\n"
        "      args:\n"
        "        BASE_IMAGE: ${BASE_IMAGE}\n"
        "    image: registry.example/foreign:9.9.9\n",
        encoding="utf-8",
    )
    (build_dir / "Dockerfile").write_text(
        "ARG BASE_IMAGE=vendor/base:1.2.3\nFROM ${BASE_IMAGE}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="local project build tag"):
        container_security.load_compose_builds(tmp_path)


def test_compose_build_rejects_duplicate_dockerfile_image_args(tmp_path: Path) -> None:
    service = tmp_path / "example"
    build_dir = service / "build"
    build_dir.mkdir(parents=True)
    (service / "service.yml").write_text(
        "images:\n"
        "  - var: BASE_IMAGE\n"
        "    container: example\n"
        "    default: vendor/unsafe:9.9.9\n",
        encoding="utf-8",
    )
    (service / "compose.yml").write_text(
        "services:\n"
        "  example:\n"
        "    build:\n"
        "      context: build\n"
        "      args:\n"
        "        BASE_IMAGE: ${BASE_IMAGE}\n",
        encoding="utf-8",
    )
    (build_dir / "Dockerfile").write_text(
        "ARG BASE_IMAGE=vendor/safe:1.2.3\n"
        "FROM ${BASE_IMAGE}\n"
        "ARG BASE_IMAGE=vendor/unsafe:9.9.9\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate build arg BASE_IMAGE"):
        container_security.load_compose_builds(tmp_path)


def test_compose_build_rejects_floating_dockerfile_image_arg_default(
    tmp_path: Path,
) -> None:
    service = tmp_path / "example"
    build_dir = service / "build"
    build_dir.mkdir(parents=True)
    (service / "service.yml").write_text(
        "images:\n"
        "  - var: EXAMPLE_IMAGE\n"
        "    container: example\n"
        "    default: vendor/example:1.2.3\n",
        encoding="utf-8",
    )
    (service / "compose.yml").write_text(
        "services:\n"
        "  example:\n"
        "    build:\n"
        "      context: build\n",
        encoding="utf-8",
    )
    (build_dir / "Dockerfile").write_text(
        "ARG BASE_IMAGE=vendor/base:latest\nFROM ${BASE_IMAGE}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="floating or untagged"):
        container_security.load_compose_builds(tmp_path)


def test_compose_build_rejects_direct_floating_from_image(tmp_path: Path) -> None:
    service = tmp_path / "example"
    build_dir = service / "build"
    build_dir.mkdir(parents=True)
    (service / "service.yml").write_text("images: []\n", encoding="utf-8")
    (service / "compose.yml").write_text(
        "services:\n  example:\n    build:\n      context: build\n",
        encoding="utf-8",
    )
    (build_dir / "Dockerfile").write_text(
        "FROM vendor/base:latest\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="floating or untagged"):
        container_security.load_compose_builds(tmp_path)


def test_compose_build_compares_arbitrarily_named_base_arg(tmp_path: Path) -> None:
    service = tmp_path / "example"
    build_dir = service / "build"
    build_dir.mkdir(parents=True)
    (service / "service.yml").write_text("images: []\n", encoding="utf-8")
    (service / "compose.yml").write_text(
        "services:\n"
        "  example:\n"
        "    build:\n"
        "      context: build\n"
        "      args:\n"
        "        BASE_REF: vendor/runtime:9.9.9\n",
        encoding="utf-8",
    )
    (build_dir / "Dockerfile").write_text(
        "ARG BASE_REF=vendor/ci-safe:1.2.3\nFROM ${BASE_REF}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Dockerfile default resolves"):
        container_security.load_compose_builds(tmp_path)


@pytest.mark.parametrize(
    "args_yaml",
    (
        "        BASE_REF:\n          vendor/base:2.3.4\n",
        "        ? BASE_REF\n        : vendor/base:2.3.4\n",
        "        BASE_REF: *reviewed_base\n",
    ),
)
def test_changed_compose_validates_complete_build_yaml_not_diff_fragments(
    tmp_path: Path, args_yaml: str
) -> None:
    service = tmp_path / "example"
    build_dir = service / "build"
    build_dir.mkdir(parents=True)
    (service / "service.yml").write_text(
        "images:\n"
        "  - var: EXAMPLE_IMAGE\n"
        "    container: example\n"
        "    default: vendor/example:1.2.3\n",
        encoding="utf-8",
    )
    anchor = "x-reviewed-base: &reviewed_base vendor/base:2.3.4\n" if "*" in args_yaml else ""
    (service / "compose.yml").write_text(
        anchor
        + "services:\n"
        "  example:\n"
        "    build:\n"
        "      context: build\n"
        "      args:\n"
        + args_yaml,
        encoding="utf-8",
    )
    (build_dir / "Dockerfile").write_text(
        "ARG BASE_REF=vendor/base:2.3.4\nFROM ${BASE_REF}\n",
        encoding="utf-8",
    )

    scans = container_security.load_changed_image_scans(
        tmp_path,
        [
            "diff --git a/services/example/compose.yml b/services/example/compose.yml",
            "+        BASE_REF:",
            "+          vendor/base:2.3.4",
        ],
    )

    assert scans == ()


@pytest.mark.parametrize(
    "image_var", ["CHATTERBOX_IMAGE", "COMFYUI_IMAGE", "DOCLING_GPU_IMAGE"]
)
def test_registry_verified_amd64_images_do_not_schedule_arm64(image_var: str) -> None:
    image_by_var = container_security._manifest_image_variables(ROOT / "services")
    image = image_by_var[image_var]
    scans = container_security.load_image_scans(ROOT / "services")

    assert {scan.platform for scan in scans if scan.image == image} == {"linux/amd64"}


def test_compose_only_remote_image_cannot_escape_scan_inventory(tmp_path: Path) -> None:
    service = tmp_path / "example"
    service.mkdir()
    (service / "service.yml").write_text(
        "images:\n  - var: KNOWN_IMAGE\n    container: known\n    default: vendor/known:1.2.3\n",
        encoding="utf-8",
    )
    (service / "compose.yml").write_text(
        "services:\n  known:\n    image: ${KNOWN_IMAGE}\n"
        "  missed:\n    image: vendor/missed:1.2.3\n",
        encoding="utf-8",
    )

    assert container_security.load_compose_image_refs(tmp_path) == (
        "vendor/known:1.2.3",
        "vendor/missed:1.2.3",
    )
    with pytest.raises(ValueError, match="vendor/missed:1.2.3"):
        container_security.load_image_scans(tmp_path)


def test_container_security_exception_file_is_empty_or_narrow_and_reviewed() -> None:
    exceptions = container_security.load_exceptions(
        ROOT / ".trivyignore.yaml", today=_TODAY
    )

    assert isinstance(exceptions, tuple)


def test_local_final_image_inventory_is_scanned_or_explicitly_excluded() -> None:
    _, scheduled = _workflow_sources()
    build_spec = re.compile(r'^\s+"(services/[^"|]+\|[^"|]+)"', re.MULTILINE)
    scanned = set(build_spec.findall(scheduled))
    excluded = set(
        container_security.load_build_exclusions(
            ROOT / ".container-scan-exclusions.yml", today=_TODAY
        )
    )
    composed = {
        build.target
        for build in container_security.load_compose_builds(ROOT / "services")
    }

    assert not scanned & excluded
    assert composed == scanned | excluded


def test_local_image_workflow_does_not_override_reviewed_dockerfile_defaults() -> None:
    required, scheduled = _workflow_sources()

    assert "extra_args" not in required
    assert "extra_args" not in scheduled
    assert "--build-arg" not in required
    assert "--build-arg" not in scheduled


def test_remote_build_exclusions_have_executable_drift_watch_controls() -> None:
    excluded = set(
        container_security.load_build_exclusions(
            ROOT / ".container-scan-exclusions.yml", today=_TODAY
        )
    )
    remote_exclusions = {target for target in excluded if target.startswith("https://")}
    controlled = {
        (
            f"https://github.com/{context.repository}.git#${{LLM_GRAPH_BUILDER_REF}}:"
            f"{context.subdir}|{context.dockerfile}"
        )
        for context in load_remote_build_contexts(ROOT / "services")
    }

    assert remote_exclusions == controlled


def test_remote_base_digest_baseline_matches_reviewed_registry_indexes() -> None:
    assert load_expected_remote_base_digests(
        ROOT / ".container-scan-exclusions.yml"
    ) == REVIEWED_REMOTE_BASE_DIGESTS


def test_contract_ledger_records_remote_base_digest_review_provenance() -> None:
    ledger = (ROOT / "docs/maintenance/external-contract-ledger.md").read_text(
        encoding="utf-8"
    )

    assert "LLM Graph Builder remote base images" in ledger
    assert "4a412f4688cf4096976045c019edc0a7f6ddcb6b" in ledger
    assert REVIEWED_REMOTE_BASE_DIGESTS["python:3.12-slim"] in ledger
    assert "docker-library/python@688a0b86bb44289df16a363e9f41d90514c1a5f9" in ledger
    assert "sha256:2fe5997d249a808b8eeea52c58a1dbffbba28754dc11699ef5c029f2d818ce79" in ledger
    assert "sha256:3949e4271b0a3ff82afac7306764c313dcc8edeeb89c0376a3c2ac6007c66b1d" in ledger


_POLICY_REJECTION_CASES = (
        (
            {"vulnerabilities": [{"id": "CVE-2099-0001", "statement": "reviewed"}]},
            "substantive",
        ),
        (
            {
                "vulnerabilities": [
                    {
                        "id": "CVE-2099-0001",
                        "paths": ["usr/lib/example"],
                        "expired_at": (_TODAY + timedelta(days=1)).isoformat(),
                    }
                ]
            },
            "statement",
        ),
        (
            {
                "vulnerabilities": [
                    {
                        "id": "CVE-2099-0001",
                        "purls": ["pkg:pypi/example@1.0"],
                        "statement": "not reachable in Atlas",
                        "expired_at": (_TODAY - timedelta(days=1)).isoformat(),
                    }
                ]
            },
            "expired",
        ),
        (
            {
                "vulnerabilities": [
                    {
                        "id": "CVE-2099-0001",
                        "paths": ["**"],
                        "statement": "The affected parser is unreachable in Atlas.",
                        "expired_at": (_TODAY + timedelta(days=1)).isoformat(),
                    }
                ]
            },
            "exact relative paths",
        ),
        (
            {
                "vulnerabilities": [
                    {
                        "id": "CVE-2099-0001",
                        "purls": ["pkg:pypi/example"],
                        "statement": "The affected parser is unreachable in Atlas.",
                        "expired_at": (_TODAY + timedelta(days=1)).isoformat(),
                    }
                ]
            },
            "exact versioned PURLs",
        ),
        (
            {
                "vulnerabilities": [
                    {
                        "id": "CVE-2099-0001",
                        "purls": ["pkg:pypi/example@1.0"],
                        "statement": "The affected parser is unreachable in Atlas.",
                        "expired_at": (_TODAY + timedelta(days=91)).isoformat(),
                    }
                ]
            },
            "review horizon",
        ),
)


@pytest.mark.parametrize(("document", "expected"), _POLICY_REJECTION_CASES)
def test_container_security_rejects_broad_or_stale_exceptions(
    tmp_path: Path, document: dict, expected: str
) -> None:
    path = tmp_path / "exceptions.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValueError, match=expected):
        container_security.load_exceptions(path, today=_TODAY)


def _workflow_sources() -> tuple[str, str]:
    services_lint = (ROOT / ".github/workflows/services-lint.yml").read_text(
        encoding="utf-8"
    )
    scheduled = (ROOT / ".github/workflows/container-security.yml").read_text(
        encoding="utf-8"
    )
    return services_lint, scheduled


def test_required_workflow_validates_container_policy() -> None:
    services_lint, _ = _workflow_sources()
    assert "python -m scripts.container_security" in services_lint


def test_required_workflow_scans_final_images_with_pinned_trivy() -> None:
    services_lint, _ = _workflow_sources()

    required_fragments = (
        "aquasecurity/setup-trivy@81e514348e19b6112ce2a7e3ecbafe19c1e1f567",
        "version: v0.74.0",
        "Build and scan local Compose and init images",
        "trivy image",
        "--image-src docker",
        "--changed-diff-stdin",
        "--severity HIGH,CRITICAL",
        "--ignorefile .trivyignore.yaml",
        "--exit-code 1",
    )
    assert all(fragment in services_lint for fragment in required_fragments)
    assert "fetch-depth: 0" in services_lint
    assert "Validate pinned remote build contexts" in services_lint
    assert "--remote-contexts-only" in services_lint
    # The digest baseline is refreshed whenever a floating upstream tag moves and
    # changes no image Atlas ships, so it must not feed the changed-image set.
    assert "'.container-scan-exclusions.yml'" not in services_lint
    assert "'services/*/service.yml' 'services/*/compose.yml'" in services_lint
    assert "'services/**/Dockerfile'" in services_lint
    assert "bash scripts/run_trivy_remote_scan.sh" in services_lint


def test_required_final_image_scan_covers_every_built_context() -> None:
    services_lint, _ = _workflow_sources()

    build_step = services_lint.split(
        "- name: Build and scan local Compose and init images", 1
    )[1]
    assert "git diff" not in build_step
    assert "scan_image=false" not in build_step
    assert 'if [ "$scan_image"' not in build_step
    assert "atlas-ci-scan-images.txt" not in build_step
    assert build_step.index("docker buildx build") < build_step.index("trivy image")
    assert build_step.index("trivy image") < build_step.rindex(
        'docker image rm "$image_tag"'
    )


def test_local_final_image_scans_gate_every_fixable_high_or_critical_finding() -> None:
    services_lint, container_security = _workflow_sources()
    build_scans = (
        services_lint.split("- name: Build and scan local Compose and init images", 1)[1],
        container_security.split(
            "- name: Build and scan every CI-supported local runtime image", 1
        )[1],
    )

    assert all(
        all(
            token in build_scan
            for token in (
                "--severity HIGH,CRITICAL",
                "--ignore-unfixed",
                "--ignorefile .trivyignore.yaml",
                "--exit-code 1",
            )
        )
        for build_scan in build_scans
    )
def test_required_image_validation_reclaims_ephemeral_runner_storage() -> None:
    services_lint, _ = _workflow_sources()

    assert "docker buildx prune --force" in services_lint
    build_scan = services_lint.split(
        "- name: Build and scan local Compose and init images", 1
    )[1]
    assert "trap cleanup_loaded_image EXIT" in build_scan
    assert 'docker image rm "$image_tag"' in build_scan
    assert build_scan.rindex('docker image rm "$image_tag"') < build_scan.index(
        "docker buildx prune --force"
    )


def test_scheduled_image_validation_reclaims_ephemeral_runner_storage() -> None:
    _, scheduled = _workflow_sources()

    scheduled_build = scheduled.split(
        "- name: Build and scan every CI-supported local runtime image", 1
    )[1]
    assert "atlas-ci-images.txt" not in scheduled_build
    assert "trap cleanup_loaded_image EXIT" in scheduled_build
    assert scheduled_build.index("docker buildx build") < scheduled_build.index(
        "trivy image"
    )
    assert scheduled_build.index("trivy image") < scheduled_build.rindex(
        'docker image rm "$image_tag"'
    )
    assert scheduled_build.rindex('docker image rm "$image_tag"') < (
        scheduled_build.index("docker buildx prune --force")
    )
    scheduled_job = scheduled.split("  local-images:", 1)[1]
    assert "timeout-minutes: 360" in scheduled_job


def test_backend_runtime_excludes_ci_artifacts_and_build_installer() -> None:
    dockerignore = (ROOT / "services/backend/app/.dockerignore").read_text(
        encoding="utf-8"
    )
    dockerfile = (ROOT / "services/backend/app/Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "**/.ci-venv" in dockerignore
    assert "**/__pycache__" in dockerignore
    assert "apt-get upgrade" not in dockerfile
    assert "reviewed BASE_IMAGE refresh" in dockerfile
    assert "pip uninstall --yes uv" in dockerfile


def test_changed_debian_images_use_reviewed_base_refreshes_not_mutable_upgrades() -> None:
    for relative in (
        "services/backend/app/Dockerfile",
        "services/jupyterhub/build/Dockerfile",
        "services/litellm/init/Dockerfile",
        "services/open-webui/init/Dockerfile",
    ):
        dockerfile = (ROOT / relative).read_text(encoding="utf-8")
        assert "apt-get upgrade" not in dockerfile, relative


def test_jupyter_runtime_pins_patched_python_and_node_tooling() -> None:
    requirements = (ROOT / "services/jupyterhub/build/requirements.txt").read_text(
        encoding="utf-8"
    )
    locked = (ROOT / "services/jupyterhub/build/requirements-locked.txt").read_text(
        encoding="utf-8"
    )
    dockerfile = (ROOT / "services/jupyterhub/build/Dockerfile").read_text(
        encoding="utf-8"
    )

    for package in (
        "brotli==1.2.0",
        "jupyterlab==4.6.3",
        "jupyterlab-git==0.54.0",
        "notebook==7.6.2",
        "wheel==0.46.2",
    ):
        assert package in requirements.lower()
        assert package in locked.lower()
    assert "ARG NPM_VERSION=11.19.1" in dockerfile
    assert 'npm install --global "npm@${NPM_VERSION}"' in dockerfile


def test_jupyter_scala_kernels_pin_patched_transitive_libraries() -> None:
    dockerfile = (ROOT / "services/jupyterhub/build/Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "com.google.protobuf:protobuf-java:3.25.5" in dockerfile
    assert dockerfile.count("at.yawk.lz4:lz4-java:1.10.3") == 2
    assert dockerfile.count("org.lz4:lz4-java:1.8.1") == 2
    assert "at/yawk/lz4/lz4-java/1.8.1" in dockerfile
    assert "org/lz4/lz4-java/1.8.0" in dockerfile
    assert "find /home/jovyan/.cache/coursier" in dockerfile
    assert "*-sources.jar" in dockerfile


def test_jupyter_julia_runtime_uses_a_pinned_clean_environment() -> None:
    dockerfile = (ROOT / "services/jupyterhub/build/Dockerfile").read_text(
        encoding="utf-8"
    )

    lockfiles = (
        ROOT / "services/jupyterhub/build/julia/Project.toml",
        ROOT / "services/jupyterhub/build/julia/Manifest.toml",
    )
    expected_fragments = (
        "ARG JULIA_VERSION=1.12.7",
        "9243c0b524c7f300883240a1ee5ea3916a30e070bff718acf8ccaee31a731ef2",
        "4e7e9e776634d24835250de67cde39b0d4af15bc432eb20697e6be6c28ea69e8",
        "COPY julia/Project.toml julia/Manifest.toml",
        "/opt/julia/environments/v1.12/",
        "Pkg.instantiate(; allow_autoprecomp=false)",
        "JUPYTER_DATA_DIR=/opt/conda/share/jupyter",
        "chown -R ${NB_UID}:${NB_GID} /opt/julia",
        "find /opt/julia -name Manifest.toml",
        "! -path /opt/julia/environments/v1.12/Manifest.toml -delete",
        'find "/opt/julia-${JULIA_VERSION}" -name Manifest.toml -delete',
    )

    assert all(path.is_file() for path in lockfiles)
    assert all(fragment in dockerfile for fragment in expected_fragments)
    assert "Pkg.precompile()" not in dockerfile


def test_container_security_workflow_pins_scanner_and_failure_policy() -> None:
    _, scheduled = _workflow_sources()
    remote_wrapper = TRIVY_REMOTE_SCAN.read_text(encoding="utf-8")
    scan_policy = scheduled + remote_wrapper
    local_build_scan = scheduled.split(
        "- name: Build and scan every CI-supported local runtime image", 1
    )[1]

    setup_ref = (
        "aquasecurity/setup-trivy@81e514348e19b6112ce2a7e3ecbafe19c1e1f567"
    )
    assert setup_ref in scheduled
    assert "version: v0.74.0" in scheduled
    assert (
        scan_policy.count("--severity HIGH,CRITICAL"),
        scan_policy.count("--ignorefile .trivyignore.yaml"),
        scan_policy.count("--exit-code 1"),
        scan_policy.count("--exit-code 0"),
        local_build_scan.count("trivy image"),
        local_build_scan.count("--ignore-unfixed"),
        "Unfixed High/Critical findings (informational)" in local_build_scan,
    ) == (3, 3, 2, 1, 2, 1, True)
    assert '--platform "$image_platform"' in remote_wrapper


def test_container_security_workflow_builds_every_supported_architecture() -> None:
    _, scheduled = _workflow_sources()

    assert "docker/setup-qemu-action@c7c53464625b32c7a7e944ae62b3e17d2b600130" in scheduled
    assert "platforms=\"linux/amd64 linux/arm64\"" in scheduled
    assert "services/asset-baker/app|Dockerfile" in scheduled
    assert 'platforms="linux/amd64"' in scheduled
    assert '--platform "$platform"' in scheduled


def test_required_build_validation_builds_every_supported_architecture() -> None:
    required, _ = _workflow_sources()

    assert "docker/setup-qemu-action@c7c53464625b32c7a7e944ae62b3e17d2b600130" in required
    assert 'platforms="linux/amd64 linux/arm64"' in required
    assert 'platforms="linux/amd64"' in required
    assert '--platform "$platform"' in required


def test_container_security_workflow_covers_both_image_inventories() -> None:
    _, scheduled = _workflow_sources()

    assert "atlas-ci-images.txt" not in scheduled
    assert "docker buildx build --load --platform" in scheduled
    assert "trivy image" in scheduled
    assert 'docker image rm "$image_tag"' in scheduled
    assert "python -m scripts.container_security --images-json" in scheduled
    assert "max-parallel: 4" in scheduled


def test_scheduled_and_required_workflows_build_the_same_local_images() -> None:
    services_lint, scheduled = _workflow_sources()
    build_spec = re.compile(r'^\s+"(services/[^"|]+\|[^"|]+)"', re.MULTILINE)
    assert build_spec.findall(scheduled) == build_spec.findall(services_lint)


def _workflow_dockerfiles(source: str) -> set[str]:
    build_spec = re.compile(
        r'^\s+"(services/[^"|]+)\|([^"|]+)"', re.MULTILINE
    )
    return {
        str((ROOT / context / dockerfile).resolve().relative_to(ROOT))
        for context, dockerfile in build_spec.findall(source)
    }


def test_both_build_workflows_cover_every_tracked_non_gpu_dockerfile() -> None:
    tracked = set(
        subprocess.run(
            ["git", "ls-files", "services/**/Dockerfile"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )
    assert GPU_DOCKERFILE_EXCLUSIONS <= tracked
    expected = tracked - GPU_DOCKERFILE_EXCLUSIONS
    services_lint, scheduled = _workflow_sources()

    assert _workflow_dockerfiles(services_lint) == expected
    assert _workflow_dockerfiles(scheduled) == expected
